"""The event stream (reminders, notifications, approvals, alerts, server states) and notifications sent by clients.

GET  /v1/events/stream     server-sent events: what this token missed since its cursor, then each new one.
                           `Last-Event-ID` (or `after`) resumes from an event; a token that never listened
                           starts from now. To the Discord bot each event also says its `channel`.
GET  /v1/events            the recent events (a client's inbox)
POST /v1/events/ack        moves this token's cursor (a client that shows events without streaming them)
POST /v1/notifications     a client notifies you (at most 10 a minute)
"""

from __future__ import annotations

import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field

from ..events import KINDS
from ..events.routing import route
from .common import ApiError, caller, event_stream, services, sse, with_keepalive

router = APIRouter(prefix="/v1", tags=["events"])

NOTIFICATIONS_PER_MINUTE = 10
_sent: deque[float] = deque()


@router.get("/events/stream")
async def stream(
    request: Request,
    after: int | None = Query(None, ge=0),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    who = caller(request)
    s = services(request)
    start = after
    if start is None and last_event_id and last_event_id.isdigit():
        start = int(last_event_id)

    async def events() -> AsyncIterator[dict]:
        yield {"type": "server", "state": s.lifecycle.state, "boot": s.boot_id}
        async for event in s.events.listen(who.token.id, who.client, start):
            item = {**event.describe(), "_id": event.id}
            if (
                who.client == "discord"
            ):  # the channel is told at delivery, so a change of routes applies to a stream already open
                channel = route(event, s.runtime.get("discord_routes"))
                if channel is not None:
                    item["channel"] = channel
            yield item

    async def lines() -> AsyncIterator[str]:
        async with contextlib.aclosing(with_keepalive(events())) as items:
            async for item in items:
                if item is None:
                    yield ": keepalive\n\n"
                    continue
                event_id = item.pop("_id", None)
                yield sse(item, event_id)
                if event_id is not None:
                    s.events.acknowledge(who.token.id, event_id)

    return event_stream(lines())


@router.get("/events")
def recent(request: Request, limit: int = Query(100, ge=1, le=500), kinds: str = Query("", max_length=200)) -> dict:
    wanted = [kind for kind in kinds.split(",") if kind]
    unknown = set(wanted) - set(KINDS)
    if unknown:
        raise ApiError(422, f"Unknown kinds: {', '.join(sorted(unknown))}.")
    who = caller(request)
    s = services(request)
    found = [event for event in s.events.recent(limit, wanted) if event.for_client(who.client)]
    return {"events": [event.describe() for event in found], "cursor": s.events.cursor(who.token.id)}


class Ack(BaseModel):
    id: int = Field(ge=0)


@router.post("/events/ack")
def ack(body: Ack, request: Request) -> dict:
    services(request).events.acknowledge(caller(request).token.id, body.id)
    return {"ok": True}


class Notification(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=100)
    targets: list[str] = Field(default_factory=list, max_length=5)


@router.post("/notifications", status_code=201)
def notify(body: Notification, request: Request) -> dict:
    now = time.monotonic()
    while _sent and _sent[0] < now - 60:
        _sent.popleft()
    if len(_sent) >= NOTIFICATIONS_PER_MINUTE:
        raise ApiError(429, f"At most {NOTIFICATIONS_PER_MINUTE} notifications a minute.")
    who = caller(request)
    try:
        event = services(request).events.publish(
            "notification", body.title or "Notification", body.text, targets=body.targets, source=f"client:{who.name}"
        )
    except ValueError as error:
        raise ApiError(422, str(error)) from None
    _sent.append(now)
    return event.describe()
