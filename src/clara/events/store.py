"""Events pushed to your clients: reminders that fired, notifications, approval requests, alerts, server states.

Every event is stored, then handed to the listeners connected now. Each token that listens has a cursor: a client
that reconnects gets what it missed, oldest first, at least once. An event is kept until every token that listens
has read it (tokens unused for 30 days, or revoked, stop counting), and never longer than CLARA_EVENTS_MAX_DAYS.

`targets` names the clients an event is for (`discord`, `cli`, `web`…); empty means every client.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import timedelta

from ..db import Database
from ..timeutil import iso, now_iso, utcnow

KINDS = ("reminder", "notification", "approval", "approval_resolved", "alert", "server", "schedule", "task", "update", "checkin")
LISTENER_IDLE_DAYS = 30
MAX_TEXT = 2_000
MAX_TITLE = 100
TARGETS = ("web", "cli", "console", "discord", "script")


@dataclass(frozen=True)
class Event:
    id: int
    kind: str
    title: str
    text: str
    payload: dict
    targets: tuple[str, ...]
    source: str
    conversation_id: str | None
    created_at: str

    def for_client(self, client: str) -> bool:
        return not self.targets or client in self.targets

    def describe(self) -> dict:
        return {"id": self.id, "type": self.kind, "title": self.title, "text": self.text, "payload": self.payload,
                "targets": list(self.targets), "source": self.source, "conversation": self.conversation_id,
                "created_at": self.created_at}


def _event(row) -> Event:
    return Event(row["id"], row["kind"], row["title"], row["text"], json.loads(row["payload"] or "{}"),
                 tuple(target for target in row["targets"].split("|") if target), row["source"],
                 row["conversation_id"], row["created_at"])


def clean_targets(targets: Iterable[str] | None) -> tuple[str, ...]:
    found: list[str] = []
    for target in targets or ():
        target = str(target).strip().lower()
        if not target:
            continue
        if target not in TARGETS:
            raise ValueError(f"Unknown client {target!r}: {', '.join(TARGETS)}.")
        if target not in found:
            found.append(target)
    return tuple(found)


class Events:
    def __init__(self, db: Database, max_days: int = 90):
        self.db = db
        self.max_days = max_days
        self._listeners: set[asyncio.Queue] = set()
        self.closed = False

    def publish(self, kind: str, title: str = "", text: str = "", *, payload: dict | None = None,
                targets: Iterable[str] | None = None, source: str = "server", conversation_id: str | None = None) -> Event:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind}")
        event_id = self.db.insert(
            "INSERT INTO events (kind, title, text, payload, targets, source, conversation_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, title[:MAX_TITLE], text[:MAX_TEXT], json.dumps(payload or {}, ensure_ascii=False),
             "|".join(clean_targets(targets)), source, conversation_id, now_iso()),
        )
        event = self.get(event_id)
        assert event is not None
        for queue in list(self._listeners):
            queue.put_nowait(event)
        return event

    def get(self, event_id: int) -> Event | None:
        row = self.db.one("SELECT * FROM events WHERE id = ?", (event_id,))
        return _event(row) if row else None

    def after(self, last_id: int, limit: int = 500, kinds: Iterable[str] | None = None) -> list[Event]:
        kinds = tuple(kinds or ())
        where = f" AND kind IN ({','.join('?' * len(kinds))})" if kinds else ""
        rows = self.db.all(f"SELECT * FROM events WHERE id > ?{where} ORDER BY id LIMIT ?", (last_id, *kinds, limit))
        return [_event(row) for row in rows]

    def recent(self, limit: int = 100, kinds: Iterable[str] | None = None) -> list[Event]:
        kinds = tuple(kinds or ())
        where = f" WHERE kind IN ({','.join('?' * len(kinds))})" if kinds else ""
        rows = self.db.all(f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ?", (*kinds, limit))
        return [_event(row) for row in rows]

    def last_id(self) -> int:
        return int(self.db.value("SELECT MAX(id) FROM events", (), 0))

    # ------------------------------------------------------------------
    # Cursors
    # ------------------------------------------------------------------
    def cursor(self, token_id: int) -> int | None:
        value = self.db.value("SELECT last_event_id FROM event_cursors WHERE token_id = ?", (token_id,))
        return None if value is None else int(value)

    def acknowledge(self, token_id: int, event_id: int) -> None:
        self.db.execute(
            "INSERT INTO event_cursors (token_id, last_event_id, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (token_id) DO UPDATE SET last_event_id = MAX(last_event_id, excluded.last_event_id),"
            " updated_at = excluded.updated_at",
            (token_id, event_id, now_iso()),
        )

    async def listen(self, token_id: int, client: str, after: int | None = None) -> AsyncIterator[Event]:
        """The events for this client: what it missed since its cursor (or `after`), then each new one as it comes.
        A token that never listened starts from now. The caller acknowledges what it delivered."""
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.add(queue)
        try:
            start = after if after is not None else self.cursor(token_id)
            if start is None:
                start = self.last_id()
                self.acknowledge(token_id, start)
            seen = start
            for event in self.after(start):
                seen = event.id
                if event.for_client(client):
                    yield event
            while not self.closed:
                event = await queue.get()
                if event is None:  # the server is going away: the stream ends
                    return
                if event.id <= seen:
                    continue
                seen = event.id
                if event.for_client(client):
                    yield event
        finally:
            self._listeners.discard(queue)

    def close(self) -> None:
        """End every live stream (the server stops): clients reconnect when it is back."""
        self.closed = True
        for queue in list(self._listeners):
            queue.put_nowait(None)

    @property
    def listeners(self) -> int:
        return len(self._listeners)

    def prune(self) -> int:
        """Delete what every active listener has read (and is a day old), and anything older than max_days."""
        now = utcnow()
        active_since = iso(now - timedelta(days=LISTENER_IDLE_DAYS))
        floor = self.db.value(
            "SELECT MIN(c.last_event_id) FROM event_cursors c JOIN tokens t ON t.id = c.token_id"
            " WHERE t.revoked_at IS NULL AND c.updated_at >= ?",
            (active_since,),
        )
        deleted = self.db.execute(
            "DELETE FROM events WHERE created_at < ?", (iso(now - timedelta(days=self.max_days)),)
        ).rowcount
        if floor is not None:
            deleted += self.db.execute(
                "DELETE FROM events WHERE id <= ? AND created_at < ?", (floor, iso(now - timedelta(days=1)))
            ).rowcount
        return deleted
