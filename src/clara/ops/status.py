"""The server's state, in one place: `/v1/admin/status`, the console's /status and the Admin page read it."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from .. import __version__
from ..timeutil import iso, utcnow
from .traffic import DEBUG_HOURS

if TYPE_CHECKING:
    from ..services import Services


class StatusProbe:
    """The slow part of the status, one probe at a time. The apps ask every 3 s, and a vault locked by a long push would
    otherwise hold one worker thread per ask until the push ends. While a probe is still running, an ask gets the answer of
    the last probe that finished; only an ask that starts a probe, or comes before any answer, waits for it."""

    def __init__(self) -> None:
        self._task: asyncio.Task[dict] | None = None
        self._answer: dict | None = None

    async def read(self, work: Callable[[], dict]) -> dict:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(asyncio.to_thread(work))
            self._task.add_done_callback(self._keep)
        elif self._answer is not None:
            return self._answer
        # shielded: an ask that gives up (its client went away) does not stop the probe, whose answer the next ask uses
        return await asyncio.shield(self._task)

    def _keep(self, task: asyncio.Task[dict]) -> None:
        if not task.cancelled() and task.exception() is None:
            self._answer = task.result()


def _size_mb(path) -> float:
    try:
        total = sum(entry.stat().st_size for entry in path.parent.glob(path.name + "*"))
    except OSError:
        return 0.0
    return round(total / 1_000_000, 1)


def disk_percent(path) -> float:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return 0.0
    return round(100 * usage.used / usage.total, 1) if usage.total else 0.0


def set_debug(s: Services, on: bool) -> str:
    """Turn the debug content log on for an hour, or off (its file is deleted). Returns until when ('' off)."""
    if on:
        until = iso(utcnow() + timedelta(hours=DEBUG_HOURS))
        s.runtime.set("debug_until", until)
        return until
    s.runtime.set("debug_until", "")
    s.traffic.end_debug()
    return ""


def _from_disk_and_git(s: Services) -> dict:
    """What `status` reads from the disk or from git: a walk over the workspace, the backup files, `git` run in the
    vault. They take from a few milliseconds to a second, and the vault's git lock is held by a pull or a push for as long
    as the network takes, so they run in a thread and never on the event loop (the apps ask for the status every 3 s)."""
    workspace = s.workspace
    return {
        "storage": {
            "database_mb": _size_mb(s.settings.database),
            "workspace_mb": round(workspace.size_bytes() / 1_000_000, 1) if workspace else 0.0,
            "disk_percent": disk_percent(s.settings.data_dir),
        },
        "vault": s.vault.status() if s.vault else None,
        "backups": s.backups.describe() if s.backups else {"last": None, "remote": ""},
    }


async def status(s: Services) -> dict:
    sandbox = s.sandbox
    slow = await s.status_probe.read(lambda: _from_disk_and_git(s))
    return {
        "version": __version__,
        "release": os.environ.get("CLARA_RELEASE", ""),
        "boot": s.boot_id,
        "state": s.lifecycle.state if s.lifecycle else "starting",
        "uptime": time.monotonic() - s.started,
        "lifecycle": s.lifecycle.progress() if s.lifecycle else {},
        "agent": {
            "active": s.agent.stats.active,
            "turns": s.agent.stats.turns,
            "prompt_tokens": s.agent.stats.prompt_tokens,
            "completion_tokens": s.agent.stats.completion_tokens,
        },
        "models": {
            "default": s.runtime.get("default_model"),
            "upkeep": s.runtime.get("upkeep_model"),
            "fallbacks": s.runtime.get("fallbacks"),
        },
        "providers": s.providers.status(),
        "storage": slow["storage"],
        "workspace": {
            "available": s.workspace is not None,
            "run_ready": bool(sandbox and sandbox.ready),
            "run_problem": (sandbox.problem if sandbox else "no sandbox") or "",
        },
        "vault": slow["vault"],
        "tailscale": s.tailscale.describe() if s.tailscale else "off",
        "backups": slow["backups"],
        "events": {"listeners": s.events.listeners, "last_id": s.events.last_id()},
        "clients": {name: f"seen {int(time.monotonic() - seen)} s ago" for name, seen in s.clients.items()},
        "alerts": dict(s.alerts.active),
        "debug_until": s.runtime.get("debug_until") if s.traffic.debugging else "",
        "usage_today": s.usage.today(),
        "approvals_pending": len(s.approvals.list("pending")),
    }
