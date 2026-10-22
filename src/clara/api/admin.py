"""Administration: status, tokens, secrets (set or not, never their values), runtime settings, the debug switch, the
console, usage statistics, stop and restart, backups and updates, client heartbeats, the Discord page."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from .. import console
from ..auth import TokenError
from ..events.routing import CATEGORIES, CHANNELS, CHOICES, TEST_SOURCE, carried, channel_of
from ..ops.discord_report import TESTS_PER_MINUTE
from ..ops.lifecycle import EXIT_RESTART
from ..ops.logstream import LEVELS, RING, SOURCES
from ..ops.status import set_debug, status
from ..ops.upkeep import DISCORD_SILENCE
from ..runtime import KNOBS
from ..secrets import CONNECTION_TOKENS, KNOWN_SECRETS, SecretError
from .common import ApiError, caller, event_stream, services, sse, with_keepalive

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
    if name not in KNOWN_SECRETS or name in CONNECTION_TOKENS:
        raise ApiError(404, f"Unknown secret {name} (refresh tokens are set by connecting Drive or Gmail).")
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
def usage(
    request: Request,
    period: Literal["24h", "7d", "30d", "90d", "365d", "all"] = "30d",
    group: Literal["day", "model", "kind", "provider", "client"] = "day",
) -> dict:
    return services(request).usage.stats(period, group)


@router.get("/admin/usage/history")
def usage_history(request: Request, limit: int = Query(100, ge=1, le=1000), before: int | None = None) -> dict:
    return {"calls": services(request).usage.history(limit, before)}


# ----------------------------------------------------------------------
# The live log
# ----------------------------------------------------------------------
@router.get("/admin/logs/stream")
async def logs_stream(
    request: Request,
    source: Literal["all", "app", "traffic"] = "all",
    level: Literal["INFO", "WARNING", "ERROR"] = "INFO",
):
    """The last lines of the server's log and traffic log, then each new one (server-sent events, `log` events; the
    first event is `open`). Filtering is done here, so a line the page did not ask for is not sent."""
    hub = services(request).logs
    sources = frozenset(SOURCES if source == "all" else (source,))

    async def events() -> AsyncIterator[dict]:
        yield {"type": "open", "ring": RING}
        async with contextlib.aclosing(hub.listen(sources, LEVELS[level])) as lines:
            async for line in lines:
                yield line.describe()

    async def chunks() -> AsyncIterator[str]:
        async with contextlib.aclosing(with_keepalive(events())) as items:
            async for item in items:
                yield ": keepalive\n\n" if item is None else sse(item, item.get("id"))

    return event_stream(chunks())


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
class ChannelReport(BaseModel):
    name: str = Field(default="", max_length=100)
    missing: list[str] = Field(default_factory=list, max_length=12)  # the rights the bot lacks there


class TestReport(BaseModel):
    event: int = Field(ge=0)
    ok: bool
    error: str = Field(default="", max_length=300)


class Heartbeat(BaseModel):
    state: Literal["connected", "disconnected"] = "connected"
    # the Discord bot also tells who it is, the channels it found and how the last test message went
    bot: str = Field(default="", max_length=100)
    channels: dict[str, ChannelReport] = Field(default_factory=dict, max_length=8)
    test: TestReport | None = None


@router.post("/clients/heartbeat")
def heartbeat(body: Heartbeat, request: Request) -> dict:
    who = caller(request)
    s = services(request)
    if body.state == "connected":
        s.clients[who.client] = time.monotonic()
        s.alerts.clear(who.client)
        if who.client == "discord":
            s.discord_report.update(
                body.bot,
                {name: (found.name, found.missing) for name, found in body.channels.items()},
                (body.test.event, body.test.ok, body.test.error) if body.test else None,
            )
    else:
        s.clients.pop(who.client, None)
        if who.client == "discord":
            s.discord_report.clear()
    return {"ok": True}


# ----------------------------------------------------------------------
# The Discord page: the bot's state, where each kind of message goes, a test message per channel
# ----------------------------------------------------------------------
@router.get("/admin/discord")
def discord_page(request: Request) -> dict:
    s = services(request)
    routes = s.runtime.get("discord_routes")
    seen = s.clients.get("discord")
    age = None if seen is None else int(time.monotonic() - seen)
    carries = carried(routes)
    report = s.discord_report
    autostart = bool(s.settings.discord_autostart)
    return {
        "bot": {
            "state": "not seen" if age is None else "silent" if age >= DISCORD_SILENCE else "connected",
            "seen_seconds": age,
            "name": report.bot,
            "started_by_server": autostart,
            "expected": autostart or bool(s.settings.discord_expected),
            "problem": getattr(s.discord, "problem", "") if s.discord is not None else "",
            "alert": s.alerts.active.get("discord", ""),
        },
        "channels": [
            {
                "id": name,
                "known": name in report.channels,
                "name": report.channels.get(name, {}).get("name", ""),
                "missing": report.channels.get(name, {}).get("missing", []),
                "carries": carries[name],
            }
            for name in CHANNELS
        ],
        "sync": s.mirror.status(),
        "categories": [
            {
                "id": category.id,
                "label": category.label,
                "about": category.about,
                "default": category.default,
                "channel": channel_of(category.id, routes),
                "can_be_off": category.can_be_off,
            }
            for category in CATEGORIES
        ],
        "choices": list(CHOICES),
        "test": report.test,
    }


class DiscordTest(BaseModel):
    channel: Literal["general", "notification", "approval", "alerts"]


@router.post("/admin/discord/test", status_code=201)
def discord_test(body: DiscordTest, request: Request) -> dict:
    """A message for one channel, whatever the routes say. The bot tells in its next heartbeat how it went."""
    s = services(request)
    seen = s.clients.get("discord")
    if seen is None or time.monotonic() - seen >= DISCORD_SILENCE:
        raise ApiError(409, "The Discord bot is not connected: it would not post the message.")
    if not s.discord_report.allow_test():
        raise ApiError(429, f"At most {TESTS_PER_MINUTE} test messages a minute.")
    carries = carried(s.runtime.get("discord_routes"))[body.channel]
    event = s.events.publish(
        "notification",
        "Test from Clara's web site",
        f"This is #{body.channel}. It receives: {', '.join(carries) or 'nothing at the moment'}.",
        payload={"discord_test": body.channel},
        targets=("discord",),
        source=TEST_SOURCE,
    )
    return {"event": event.id, "channel": body.channel}
