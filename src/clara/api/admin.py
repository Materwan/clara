"""Administration: status, tokens, secrets (set or not, never their values), runtime settings, the debug switch, the
console, usage statistics, stop and restart, backups and updates, client heartbeats."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from .. import console
from ..auth import TokenError
from ..ops.lifecycle import EXIT_RESTART
from ..ops.status import set_debug, status
from ..runtime import KNOBS
from ..secrets import KNOWN_SECRETS, SecretError
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1", tags=["admin"])


@router.get("/admin/status")
async def get_status(request: Request) -> dict:
    return await status(services(request))


# ----------------------------------------------------------------------
# Tokens
# ----------------------------------------------------------------------
@router.get("/admin/tokens")
def tokens(request: Request) -> dict:
    return {"tokens": [token.describe() for token in services(request).tokens.list()], "you": caller(request).name}


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    client: Literal["web", "cli", "console", "discord", "script"]
    approvals: Literal["all", "safe", "none"] | None = None


@router.post("/admin/tokens", status_code=201)
def create_token(body: NewToken, request: Request) -> dict:
    try:
        info, secret = services(request).tokens.create(body.name, body.client, body.approvals)
    except TokenError as error:
        raise ApiError(422, str(error)) from None
    return {**info.describe(), "token": secret}


class TokenChange(BaseModel):
    approvals: Literal["all", "safe", "none"]


@router.patch("/admin/tokens/{name}")
def change_token(name: str, body: TokenChange, request: Request) -> dict:
    try:
        return services(request).tokens.set_approvals(name, body.approvals).describe()
    except TokenError as error:
        raise ApiError(404, str(error)) from None


@router.delete("/admin/tokens/{name}")
def revoke_token(name: str, request: Request) -> dict:
    if name == caller(request).name:
        raise ApiError(409, "This is the token you are using: revoke it from another client.")
    try:
        return services(request).tokens.revoke(name).describe()
    except TokenError as error:
        raise ApiError(404, str(error)) from None


# ----------------------------------------------------------------------
# Secrets: set or not, never their values
# ----------------------------------------------------------------------
@router.get("/admin/secrets")
def secrets(request: Request) -> dict:
    return {"secrets": services(request).secrets.status()}


class SecretValue(BaseModel):
    value: str = Field(min_length=1, max_length=10_000)


@router.put("/admin/secrets/{name}")
def set_secret(name: str, body: SecretValue, request: Request) -> dict:
    s = services(request)
    if name not in KNOWN_SECRETS or name == "google_refresh_token":
        raise ApiError(404, f"Unknown secret {name} (the Google refresh token is set by connecting Drive).")
    try:
        s.secrets.set(name, body.value.strip())
    except SecretError as error:
        raise ApiError(422, str(error)) from None
    s.providers.forget_backends()
    return {"name": name, "set": True}


@router.delete("/admin/secrets/{name}")
def unset_secret(name: str, request: Request) -> dict:
    s = services(request)
    removed = s.secrets.unset(name)
    s.providers.forget_backends()
    return {"name": name, "set": False, "removed": removed}


# ----------------------------------------------------------------------
# Runtime settings, debug, console
# ----------------------------------------------------------------------
@router.get("/admin/settings")
def runtime_settings(request: Request) -> dict:
    return {"settings": services(request).runtime.describe()}


class SettingValue(BaseModel):
    value: object = None


@router.put("/admin/settings/{key}")
def set_setting(key: str, body: SettingValue, request: Request) -> dict:
    if key not in KNOBS or key in ("debug_until", "sandbox_image"):
        raise ApiError(404, f"Unknown or read-only setting {key}.")
    try:
        return {"key": key, "value": services(request).runtime.set(key, body.value)}
    except (KeyError, ValueError) as error:
        raise ApiError(422, str(error)) from None


class Debug(BaseModel):
    on: bool


@router.post("/admin/debug")
def debug(body: Debug, request: Request) -> dict:
    return {"debug_until": set_debug(services(request), body.on)}


@router.get("/admin/commands")
def commands() -> dict:
    return {"commands": [{"name": c.name, "usage": c.usage, "help": c.help} for c in console.COMMANDS]}


class CommandLine(BaseModel):
    line: str = Field(min_length=1, max_length=20_000)


@router.post("/admin/command")
async def command(body: CommandLine, request: Request) -> dict:
    return {"output": await console.run(services(request), body.line, caller(request).token)}


# ----------------------------------------------------------------------
# Usage
# ----------------------------------------------------------------------
@router.get("/admin/usage")
def usage(request: Request, period: Literal["24h", "7d", "30d", "90d", "365d", "all"] = "30d",
          group: Literal["day", "model", "kind", "provider", "client"] = "day") -> dict:
    return services(request).usage.stats(period, group)


@router.get("/admin/usage/history")
def usage_history(request: Request, limit: int = Query(100, ge=1, le=1000), before: int | None = None) -> dict:
    return {"calls": services(request).usage.history(limit, before)}


# ----------------------------------------------------------------------
# Stopping, restarting, backups, updates
# ----------------------------------------------------------------------
class Stop(BaseModel):
    now: bool = False


@router.post("/admin/stop")
def stop(body: Stop, request: Request) -> dict:
    return {"message": services(request).lifecycle.stop(force=body.now)}


@router.post("/admin/restart")
def restart(body: Stop, request: Request) -> dict:
    s = services(request)
    if s.updater is None or not s.updater.launched:
        raise ApiError(409, "Restarting needs the launcher (clara-launcher under systemd).")
    return {"message": s.lifecycle.stop(force=body.now, exit_code=EXIT_RESTART)}


@router.get("/admin/backups")
def backups(request: Request) -> dict:
    s = services(request)
    if s.backups is None:
        raise ApiError(404, "Backups are not available.")
    return {**s.backups.describe(), "files": s.backups.listing()}


@router.post("/admin/backups")
async def backup_now(request: Request) -> dict:
    s = services(request)
    if s.backups is None:
        raise ApiError(404, "Backups are not available.")
    return {"message": await s.backups.run(manual=True)}


@router.get("/admin/releases")
async def releases(request: Request) -> dict:
    s = services(request)
    if s.updater is None:
        raise ApiError(404, "Updates are not available.")
    if not s.settings.release_repo:
        raise ApiError(409, "Set CLARA_RELEASE_REPO (owner/name) to list releases.")
    try:
        return {"releases": await s.updater.releases(), "state": s.updater.describe()}
    except Exception as error:  # the release list is outside: say why it failed
        raise ApiError(502, f"Could not list the releases: {error}") from None


class Update(BaseModel):
    tag: str = Field(min_length=1, max_length=64)


@router.post("/admin/update")
async def update(body: Update, request: Request) -> dict:
    s = services(request)
    if s.updater is None:
        raise ApiError(404, "Updates are not available.")
    return {"message": await s.updater.start(body.tag)}


@router.get("/admin/alerts")
def alerts(request: Request) -> dict:
    s = services(request)
    return {"active": s.alerts.active, "recent": [e.describe() for e in s.events.recent(50, ["alert"])]}


# ----------------------------------------------------------------------
# Clients tell they are alive (the Discord bot)
# ----------------------------------------------------------------------
class Heartbeat(BaseModel):
    state: Literal["connected", "disconnected"] = "connected"


@router.post("/clients/heartbeat")
def heartbeat(body: Heartbeat, request: Request) -> dict:
    who = caller(request)
    s = services(request)
    if body.state == "connected":
        s.clients[who.client] = time.monotonic()
        s.alerts.clear(who.client)
    else:
        s.clients.pop(who.client, None)
    return {"ok": True}
