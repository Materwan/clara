"""A client that was away reads every event it missed, not only the first 500 of them."""

from __future__ import annotations

import asyncio
import contextlib


async def test_a_long_absence_is_caught_up_page_after_page(services):
    events = services.events
    start = events.last_id()
    for number in range(1_230):  # two full pages and a part
        events.publish("notification", f"n{number}", "text")
    got: list[int] = []
    async with contextlib.aclosing(events.listen(1, "web", start)) as listening:
        while len(got) < 1_230:
            event = await asyncio.wait_for(anext(listening), 5)
            got.append(event.id)
    assert got == sorted(got) and len(set(got)) == 1_230
    assert got[-1] == events.last_id()
