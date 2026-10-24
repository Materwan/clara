"""The live feed of the Discord mirror: what a turn in a mirrored conversation writes, as it is written. Nothing is kept: a
bot that was not listening (or that stops) misses it, and the stored answer comes back through `sync` all the same."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class Live:
    def __init__(self) -> None:
        self._listeners: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for queue in list(self._listeners):
            queue.put_nowait(event)

    async def listen(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._listeners.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._listeners)
