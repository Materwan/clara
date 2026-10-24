"""The loop of a service that acts at set times: it fires what is due, then sleeps until the next due item, until
something changes (`wake`), or 30 seconds at most. Each service (reminders, tasks, schedules, approvals, upkeep) runs
its own loop and catches its own errors, so one failing never stops the others.

`heartbeat` is called on every pass: the server uses it to feed the systemd watchdog only while every loop turns.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from ..timeutil import utcnow

MAX_SLEEP = 30.0


class DueLoop:
    name = "loop"

    def __init__(self, clock: Callable[[], datetime] = utcnow):
        self._clock = clock
        self._wake = asyncio.Event()
        self.stopping = False  # the server is stopping: what comes due waits for the next start
        self.busy_firing = False  # what came due is being handled (a stop waits for it)
        self.last_pass = time.monotonic()

    def wake(self) -> None:
        self._wake.set()

    def fire_due(self) -> int | Awaitable[int]:
        raise NotImplementedError

    def next_due(self) -> datetime | None:
        raise NotImplementedError

    async def run(self) -> None:
        log = logging.getLogger(type(self).__module__)
        while True:
            self._wake.clear()
            self.last_pass = time.monotonic()
            try:
                if not self.stopping:
                    self.busy_firing = True
                    fired = self.fire_due()
                    if inspect.isawaitable(fired):
                        await fired
            except Exception:
                log.exception("%s: could not fire what is due", self.name)
            finally:
                self.busy_firing = False
            try:
                upcoming = self.next_due()
            except Exception:
                log.exception("%s: could not find what is due next", self.name)
                upcoming = None
            delay = MAX_SLEEP if upcoming is None or self.stopping else (upcoming - self._clock()).total_seconds()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), max(0.0, min(delay, MAX_SLEEP)))
