"""New-mail notifications: a poll of each connected mailbox's inbox, on its own loop (ops/upkeep.py).

Off until you turn it on (the runtime setting `mail_notify`, on the Connections page or the Admin page). A notification
names the sender and the subject, nothing more, and goes to every client like any other (Discord's `#notification` too:
the text then passes through Discord, which is why it is a choice and why the body is never in it). `mail_notify_senders`
narrows it to senders whose name or address contains one of its words.

Where it stopped is kept per mailbox (`mail_cursors`): the first poll only notes the newest message, so connecting a
mailbox does not announce the whole inbox; after a stop, what arrived meanwhile is announced (a few, then a count).
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from ..timeutil import now_iso
from .mail import LABELS, PROVIDERS, MailError, Summary

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

ONE_BY_ONE = 4  # more new messages than this in one poll make one notification with a count
REMEMBERED = 100  # message ids kept per mailbox, against announcing one twice


class MailWatch:
    def __init__(self, services: Services):
        self.s = services
        self._last_error: dict[str, str] = {}  # what was last logged for a mailbox: a long outage is one line, not one a poll

    # ------------------------------------------------------------------
    def cursor(self, provider: str) -> tuple[int, list[str]] | None:
        row = self.s.db.one("SELECT received_ms, seen FROM mail_cursors WHERE provider = ?", (provider,))
        return (int(row["received_ms"]), list(json.loads(row["seen"]))) if row else None

    def save(self, provider: str, received_ms: int, seen: list[str]) -> None:
        self.s.db.execute(
            "INSERT INTO mail_cursors (provider, received_ms, seen, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (provider) DO UPDATE SET received_ms = excluded.received_ms, seen = excluded.seen,"
            " updated_at = excluded.updated_at",
            (provider, received_ms, json.dumps(seen[-REMEMBERED:]), now_iso()))

    def forget(self, provider: str) -> None:
        """A mailbox is connected or disconnected: the next poll starts afresh."""
        self.s.db.execute("DELETE FROM mail_cursors WHERE provider = ?", (provider,))

    # ------------------------------------------------------------------
    async def poll(self) -> None:
        if not self.s.runtime.get("mail_notify"):
            return
        connectors = self.s.connectors
        for provider in PROVIDERS:
            if connectors is None or not connectors.mail_connected(provider):
                continue
            try:
                await self._poll(provider)
                self.s.alerts.clear(provider)
                self._last_error.pop(provider, None)
            except MailError as error:
                connectors.mail_failed(provider, error)
                if self._last_error.get(provider) != str(error):
                    self._last_error[provider] = str(error)
                    log.warning("mail %s: %s", provider, error)
            except Exception:
                log.exception("mail %s: the poll failed", provider)

    async def _poll(self, provider: str) -> None:
        client = self.s.connectors.mail_client(provider)
        if client is None:
            return
        found = self.cursor(provider)
        if found is None:
            # a minute back, so a message that arrives as this starts is not lost to a few seconds between two clocks
            self.save(provider, int(time.time() * 1000) - 60_000, [])
            return
        received_ms, seen = found
        arrived = [item for item in await client.new_since(received_ms)
                   if item.id not in seen and item.received_ms >= received_ms]
        if not arrived:
            return
        arrived.sort(key=lambda item: item.received_ms)
        shown = [item for item in arrived if self.wanted(item)]
        self.announce(provider, shown)
        self.save(provider, max(item.received_ms for item in arrived), [*seen, *(item.id for item in arrived)])

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
            self.s.events.publish("notification", f"New email ({label})", f"{item.sender}: {item.subject}", source="mail")
