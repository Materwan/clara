"""New mail: a poll of each connected mailbox's inbox, on its own loop (ops/upkeep.py). What it finds goes to two listeners.

* **Notifications**, off until you turn them on (the runtime setting `mail_notify`, on the Connections page or the Admin
  page): see below.
* **Hooks** (triggers/): every new message is an event `mail.received`, stored and matched against the hooks you wrote, in the
  same transaction that moves the place forward, so a crash loses none and repeats none. The poll only looks while a listener
  exists; with none, the place is forgotten, so the first look after one appears starts from now and never replays old mail.

A notification
names the sender and the subject, nothing more, and goes to every client like any other (Discord's `#notification` too:
the text then passes through Discord, which is why it is a choice and why the body is never in it). `mail_notify_senders`
narrows it to senders whose name or address contains one of its words.

Where it stopped is kept per mailbox (`mail_cursors`): the first poll only notes the newest message, so connecting a
mailbox does not announce the whole inbox; after a stop, what arrived meanwhile is announced (a few, then a count).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..timeutil import iso, now_iso
from ..triggers.events import SourceEvent
from .mail import LABELS, NEW_BATCH, PROVIDERS, MailError, Summary

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

ONE_BY_ONE = 4  # more new messages than this in one poll make one notification with a count
REMEMBERED = 100  # message ids kept per mailbox, against announcing one twice
MAX_ROUNDS = 5  # batches of NEW_BATCH looked at in one poll; a bigger backlog goes on at the next poll, from where this one stopped


def mail_event(provider: str, address: str, item: Summary) -> SourceEvent:
    """A new message as an event of hooks. What it carries was written by the sender, and is cleaned already (mail.py)."""
    found = re.search(r"<([^<>]+)>", item.sender)
    from_address = (found.group(1) if found else item.sender).strip().lower()
    occurred = iso(datetime.fromtimestamp(item.received_ms / 1000, UTC))
    return SourceEvent(
        source="mail",
        kind="mail.received",
        key=f"{provider}:{item.id}",
        occurred_at=occurred,
        actor=from_address,
        self_caused=bool(address) and from_address == address.lower(),
        title=f"{item.sender}: {item.subject}"[:300],
        data={
            "account": provider,
            "mailbox": address,
            "message": item.id,
            "thread": item.thread,
            "from": item.sender,
            "from_address": from_address,
            "subject": item.subject,
            "snippet": item.snippet,
            "has_attachment": item.attachments,
            "unread": item.unread,
            "received": occurred,
        },
    )


class MailWatch:
    def __init__(self, services: Services):
        self.s = services
        self._last_error: dict[
            str, str
        ] = {}  # what was last logged for a mailbox: a long outage is one line, not one a poll

    # ------------------------------------------------------------------
    def cursor(self, provider: str) -> tuple[int, list[str]] | None:
        row = self.s.db.one("SELECT received_ms, seen FROM mail_cursors WHERE provider = ?", (provider,))
        return (int(row["received_ms"]), list(json.loads(row["seen"]))) if row else None

    def save(self, provider: str, received_ms: int, seen: list[str]) -> None:
        self.s.db.execute(
            "INSERT INTO mail_cursors (provider, received_ms, seen, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (provider) DO UPDATE SET received_ms = excluded.received_ms, seen = excluded.seen,"
            " updated_at = excluded.updated_at",
            (provider, received_ms, json.dumps(seen[-REMEMBERED:]), now_iso()),
        )

    def forget(self, provider: str) -> None:
        """A mailbox is connected or disconnected: the next poll starts afresh."""
        self.s.db.execute("DELETE FROM mail_cursors WHERE provider = ?", (provider,))

    # ------------------------------------------------------------------
    async def poll(self) -> None:
        notify = bool(self.s.runtime.get("mail_notify"))
        hooks = self.s.triggers is not None and self.s.triggers.wants("mail.received")
        connectors = self.s.connectors
        for provider in PROVIDERS:
            if connectors is None or not connectors.mail_connected(provider):
                continue
            if not notify and not hooks:
                if self.cursor(provider) is not None:
                    self.forget(provider)  # nobody listens: the next listener starts from then, not from an old place
                continue
            try:
                await self._poll(provider, notify, hooks)
                self.s.alerts.clear(provider)
                self._last_error.pop(provider, None)
            except MailError as error:
                connectors.mail_failed(provider, error)
                if self._last_error.get(provider) != str(error):
                    self._last_error[provider] = str(error)
                    log.warning("mail %s: %s", provider, error)
            except Exception:
                log.exception("mail %s: the poll failed", provider)

    async def _poll(self, provider: str, notify: bool, hooks: bool) -> None:
        client = self.s.connectors.mail_client(provider)
        if client is None:
            return
        found = self.cursor(provider)
        if found is None:
            # a minute back, so a message that arrives as this starts is not lost to a few seconds between two clocks
            self.save(provider, int(time.time() * 1000) - 60_000, [])
            return
        received_ms, seen = found
        known = set(seen)
        arrived: list[Summary] = []
        cursor = received_ms
        for _ in range(MAX_ROUNDS):
            batch = await client.new_since(cursor, NEW_BATCH)
            arrived += [item for item in batch if item.id not in known and item.received_ms >= received_ms]
            known.update(item.id for item in batch)
            if len(batch) < NEW_BATCH:
                break  # the backlog is over
            newest = max(item.received_ms for item in batch)
            cursor = newest if newest > cursor else cursor + 1  # always on: a full batch of one instant cannot stall it
        if not arrived:
            return
        arrived.sort(key=lambda item: item.received_ms)
        if notify:
            self.announce(provider, [item for item in arrived if self.wanted(item)])
        newest, remembered = max(item.received_ms for item in arrived), [*seen, *(item.id for item in arrived)]

        def move() -> None:
            self.save(provider, newest, remembered)

        # the place moves only now, with the events stored and the runs planned (one transaction): a poll cut by an error
        # starts again from the old place, and a crash after this repeats nothing
        if hooks:
            address = (self.s.connections.get(provider) or {}).get("account", "")
            self.s.triggers.ingest([mail_event(provider, address, item) for item in arrived], also=move)
        else:
            move()

    def wanted(self, item: Summary) -> bool:
        words = [word.lower() for word in self.s.runtime.get("mail_notify_senders") if word]
        return not words or any(word in item.sender.lower() for word in words)

    def announce(self, provider: str, items: list[Summary]) -> None:
        if not items:
            return
        label = LABELS[provider]
        if len(items) > ONE_BY_ONE:
            names = ", ".join(dict.fromkeys(item.sender.split("<")[0].strip() or item.sender for item in items))[:300]
            self.s.events.publish("notification", f"{len(items)} new emails ({label})", names, source="mail")
            return
        for item in items:
            self.s.events.publish(
                "notification", f"New email ({label})", f"{item.sender}: {item.subject}", source="mail"
            )
