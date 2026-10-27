"""The loop of the time services: a pass that raises is not repeated without a pause."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from clara.timekeeping import dueloop
from clara.timekeeping.dueloop import DueLoop
from clara.timeutil import utcnow


class Broken(DueLoop):
    name = "broken"

    def __init__(self):
        super().__init__()
        self.tries = 0
        self.fail = True

    def fire_due(self):
        self.tries += 1
        if self.fail:
            raise RuntimeError("boom")
        return 1

    def next_due(self):
        return utcnow() - timedelta(seconds=5)  # still due: firing it keeps failing


async def run_for(loop: DueLoop, seconds: float) -> None:
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_pass_that_keeps_failing_backs_off_instead_of_spinning(monkeypatch, caplog):
    monkeypatch.setattr(dueloop, "RETRY_FIRST", 0.05)
    loop = Broken()
    with caplog.at_level(logging.ERROR):
        await run_for(loop, 0.5)
    # 0.05, 0.1, 0.2 … between tries: a handful in half a second (thousands without the pause)
    assert 2 <= loop.tries <= 8
    assert loop.failures == loop.tries
    assert len([r for r in caplog.records if "could not fire" in r.getMessage()]) == loop.tries


async def test_a_pass_that_works_clears_the_failures(monkeypatch):
    monkeypatch.setattr(dueloop, "RETRY_FIRST", 0.01)
    loop = Broken()
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.1)
    assert loop.failures >= 1
    loop.fail = False
    loop.wake()
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert loop.failures == 0
