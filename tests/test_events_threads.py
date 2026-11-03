"""`publish` is called from worker threads too (alerts cleared in a sync route, the vault's git hook): a stream that
waits for events must wake at once, not at the next unrelated turn of the loop."""

from __future__ import annotations

import asyncio
import contextlib
import threading


async def test_an_event_published_from_a_thread_reaches_a_waiting_stream_at_once(services):
    events = services.events
    start = events.last_id()
    async with contextlib.aclosing(events.listen(1, "discord", start)) as listening:
        waiting = asyncio.ensure_future(anext(listening))
        await asyncio.sleep(0.05)  # the stream is now parked on its queue
        threading.Timer(0.05, events.publish, args=("alert_resolved", "Alert over", "disk"),
                        kwargs={"payload": {"key": "disk"}, "targets": ("discord",)}).start()
        event = await asyncio.wait_for(waiting, 1.5)
    assert event.kind == "alert_resolved" and event.payload == {"key": "disk"}


async def test_alerts_cleared_in_a_worker_thread_reach_a_waiting_stream(services):
    events = services.events
    start = events.last_id()
    async with contextlib.aclosing(events.listen(1, "discord", start)) as listening:
        waiting = asyncio.ensure_future(anext(listening))
        await asyncio.sleep(0.05)
        threading.Timer(0.05, services.alerts.clear, args=("vault_push",)).start()
        event = await asyncio.wait_for(waiting, 1.5)
    assert event.kind == "alert_resolved" and event.payload == {"key": "vault_push"}


async def test_publishing_after_the_loop_of_a_stream_is_closed_is_harmless(services):
    events = services.events
    gone = asyncio.new_event_loop()
    events._listeners[asyncio.Queue()] = gone
    gone.close()
    events.publish("notification", "after", "the loop of one stream is gone")
    events.close()
