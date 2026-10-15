"""The server's state, in one place: `/v1/admin/status`, the console's /status and the Admin page read it."""

from __future__ import annotations

import os
import shutil
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from .. import __version__
from ..timeutil import iso, utcnow
from .traffic import DEBUG_HOURS

if TYPE_CHECKING:
    from ..services import Services


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


async def status(s: Services) -> dict:
    workspace = s.workspace
    sandbox = s.sandbox
    return {
        "version": __version__,
        "release": os.environ.get("CLARA_RELEASE", ""),
        "boot": s.boot_id,
        "state": s.lifecycle.state if s.lifecycle else "starting",
        "uptime": time.monotonic() - s.started,
        "lifecycle": s.lifecycle.progress() if s.lifecycle else {},
        "agent": {"active": s.agent.stats.active, "turns": s.agent.stats.turns,
                  "prompt_tokens": s.agent.stats.prompt_tokens, "completion_tokens": s.agent.stats.completion_tokens},
        "models": {"default": s.runtime.get("default_model"), "upkeep": s.runtime.get("upkeep_model"),
                   "fallbacks": s.runtime.get("fallbacks")},
        "providers": s.providers.status(),
        "storage": {
            "database_mb": _size_mb(s.settings.database),
            "workspace_mb": round(workspace.size_bytes() / 1_000_000, 1) if workspace else 0.0,
            "disk_percent": disk_percent(s.settings.data_dir),
        },
        "workspace": {"available": workspace is not None, "run_ready": bool(sandbox and sandbox.ready),
                      "run_problem": (sandbox.problem if sandbox else "no sandbox") or ""},
        "vault": s.vault.status() if s.vault else None,
        "tailscale": s.tailscale.describe() if s.tailscale else "off",
        "backups": s.backups.describe() if s.backups else {"last": None, "remote": ""},
        "events": {"listeners": s.events.listeners, "last_id": s.events.last_id()},
        "clients": {name: f"seen {int(time.monotonic() - seen)} s ago" for name, seen in s.clients.items()},
        "alerts": dict(s.alerts.active),
        "debug_until": s.runtime.get("debug_until") if s.traffic.debugging else "",
        "usage_today": s.usage.today(),
        "approvals_pending": len(s.approvals.list("pending")),
    }
