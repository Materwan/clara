"""Stopping without cutting anybody off, but never waiting for ever.

    running ──stop()──► stopping ──everything finished, or the drain limit──► stopped (the process exits)

While stopping, every connected client gets a `server` event, new questions get 503, and the server waits for what
runs: answers being written, turns waiting for a client's tools, summaries, announcements, scheduled runs. It waits
at most CLARA_DRAIN_SECONDS (with progress in the log and the status), then cuts what is left; `/stop now` cuts at
once. The exit code tells the launcher what to do next: 0 stop, 76 restart the same release, 75 switch to the
release prepared by the updater.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

EXIT_STOP = 0
EXIT_UPDATE = 75
EXIT_RESTART = 76
POLL_SECONDS = 0.1
REPORT_SECONDS = 15.0
FLUSH_SECONDS = 0.5


class Lifecycle:
    def __init__(self, services: Services, drain_seconds: float, poll: float = POLL_SECONDS, flush: float = FLUSH_SECONDS):
        self.s = services
        self.drain_seconds = drain_seconds
        self._poll, self._flush = poll, flush
        self._force = False
        self._task: asyncio.Task | None = None
        self.exit_code = EXIT_STOP
        self.on_exit: Callable[[int], None] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.started_stopping: float | None = None
        self.exited = False

    @property
    def stopping(self) -> bool:
        return self._task is not None

    @property
    def state(self) -> str:
        return "stopping" if self.stopping else "running"

    def busy(self) -> bool:
        s = self.s
        return (s.agent.busy or s.reminders_firing() or s.schedules.firing)

    def progress(self) -> dict:
        waited = time.monotonic() - self.started_stopping if self.started_stopping else 0.0
        return {"state": self.state, "running_turns": self.s.agent.stats.active, "waited": round(waited, 1),
                "limit": self.drain_seconds}

    def stop(self, force: bool = False, exit_code: int = EXIT_STOP) -> str:
        if self._task is None:
            self.exit_code = exit_code
            self.s.agent.accepting = False
            for loop in self.s.due_loops():
                loop.stopping = True
            self.started_stopping = time.monotonic()
            self._announce("stopping")
            self._force = force
            self._task = asyncio.ensure_future(self._drain())
            if force:
                return "Stopping now."
            if not self.busy():
                return "Stopping: nothing is running."
            return (f"Stopping: new questions are refused, waiting at most {self.drain_seconds:g} s for "
                    f"{self.s.agent.stats.active} running turn(s). /stop now cuts them.")
        if force and not self._force:
            self._force = True
            return "Stopping now, whatever is running."
        return f"Already stopping ({self.progress()['waited']:g} s so far)."

    def stop_threadsafe(self, force: bool = False) -> None:
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop, force)

    def _announce(self, state: str) -> None:
        try:
            self.s.events.publish("server", f"Clara is {state}", "", payload={"state": state})
        except Exception:
            log.exception("could not announce the server state")

    async def _drain(self) -> None:
        started = last_report = time.monotonic()
        while self.busy() and not self._force:
            if time.monotonic() - started >= self.drain_seconds:
                log.warning("stopping: the drain limit (%g s) is reached, cutting what runs", self.drain_seconds)
                break
            await asyncio.sleep(self._poll)
            if time.monotonic() - last_report >= REPORT_SECONDS:
                last_report = time.monotonic()
                log.info("stopping: waiting for %d running turn(s), %d s so far", self.s.agent.stats.active,
                         last_report - started)
        self._announce("stopped")
        await asyncio.sleep(self._flush)
        self.s.events.close()
        self.exited = True
        log.info("stopped (exit code %d)", self.exit_code)
        if self.on_exit is not None:
            self.on_exit(self.exit_code)
