"""Reminders: a text and a moment, announced to you when the moment comes.

The moment is stored twice: the next firing as a UTC instant (`due_at`), and the first occurrence on the wall clock
with its IANA zone (`local_at`, `timezone`). A repeat (daily, weekly, monthly) is worked out on that wall clock, so
09:00 stays 09:00 across a daylight-saving change; a monthly reminder on the 31st fires on the last day of shorter
months. Bare offsets are refused: the database itself rejects them.

When one comes due Clara writes the announcement (a short, tool-less turn in the conversation where it was set;
announce.py); if she cannot within CLARA_REMINDER_AI_TIMEOUT, the stored text is announced and a note says why.
A reminder missed while the server was down fires once when it comes back (a repeating one too, then waits for its
next occurrence). One that fires more than CLARA_LATE_ALERT_SECONDS late raises an alert.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from ..db import Database
from ..events import Events, clean_targets
from ..timeutil import REPEATS, iso, next_local, now_iso, parse_iso, utcnow, zone
from .clock import MomentError, local_text, read_moment, zone_of
from .dueloop import DueLoop

log = logging.getLogger(__name__)

MAX_TEXT = 500
MAX_REMINDERS = 200


class ReminderError(ValueError):
    pass


class AnnounceFailed(Exception):
    """Clara could not write the announcement (the message says why)."""


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    due_at: datetime
    local_at: datetime
    timezone: str
    repeat: str
    targets: tuple[str, ...]
    conversation_id: str | None
    client: str
    system: bool

    def describe(self) -> dict:
        tz = zone(self.timezone)
        return {
            "id": self.id,
            "text": self.text,
            "due_at": iso(self.due_at),
            "local": local_text(self.due_at, tz),
            "timezone": self.timezone,
            "repeat": self.repeat,
            "targets": list(self.targets),
            "conversation": self.conversation_id,
            "system": self.system,
        }

    def line(self) -> str:
        again = f" ({self.repeat})" if self.repeat else ""
        where = ", ".join(self.targets) or "every client"
        return f"[{self.id}] {local_text(self.due_at, zone(self.timezone))}{again} on {where}: {self.text}"


def _reminder(row) -> Reminder:
    return Reminder(
        row["id"],
        row["text"],
        parse_iso(row["due_at"]),
        datetime.fromisoformat(row["local_at"]),
        row["timezone"],
        row["repeat"],
        tuple(t for t in row["targets"].split("|") if t),
        row["conversation_id"],
        row["client"],
        bool(row["system"]),
    )


Composer = Callable[[Reminder], Awaitable["str | None"]]
Lateness = Callable[[str, float], None]  # (what, seconds late)


class Reminders(DueLoop):
    name = "reminders"

    def __init__(self, db: Database, events: Events, home_zone: str, clock: Callable[[], datetime] = utcnow):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.home_zone = home_zone
        self.composer: Composer | None = None
        self.late: Lateness | None = None
        self.late_after = 300.0

    def create(
        self,
        text: str,
        when: str,
        *,
        repeat: str = "",
        timezone: str | None = None,
        targets: Iterable[str] | None = None,
        conversation_id: str | None = None,
        client: str = "",
        system: bool = False,
    ) -> Reminder:
        text = " ".join((text or "").split())
        if not text:
            raise ReminderError("A reminder needs a text.")
        if len(text) > MAX_TEXT:
            raise ReminderError(f"A reminder is at most {MAX_TEXT} characters long.")
        repeat = (repeat or "").strip().lower()
        if repeat and repeat not in REPEATS:
            raise ReminderError(f"repeat must be one of: {', '.join(REPEATS)} (or nothing).")
        try:
            zone_name, tz = zone_of(timezone, self.home_zone)
            due, local = read_moment(when, tz)
            shown = clean_targets(targets)
        except (MomentError, ValueError) as error:
            raise ReminderError(str(error)) from None
        if due <= self._clock():
            raise ReminderError("That moment is already past.")
        if int(self.db.value("SELECT COUNT(*) FROM reminders", (), 0)) >= MAX_REMINDERS:
            raise ReminderError(f"At most {MAX_REMINDERS} reminders at a time: cancel some first.")
        reminder_id = self.db.insert(
            "INSERT INTO reminders (text, due_at, local_at, timezone, repeat, targets, conversation_id, client, system,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                text,
                iso(due),
                local.isoformat(timespec="minutes"),
                zone_name,
                repeat,
                "|".join(shown),
                conversation_id,
                client,
                int(system),
                now_iso(),
            ),
        )
        self.wake()
        reminder = self.get(reminder_id)
        assert reminder is not None
        return reminder

    def get(self, reminder_id: int) -> Reminder | None:
        row = self.db.one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return _reminder(row) if row else None

    def upcoming(self) -> list[Reminder]:
        return [_reminder(row) for row in self.db.all("SELECT * FROM reminders ORDER BY due_at, id")]

    def cancel(self, reminder_id: int) -> bool:
        cancelled = self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0
        if cancelled:
            self.wake()
        return cancelled

    def ensure_system(self, text: str, when: str, repeat: str) -> None:
        """A reminder Clara keeps for herself (the monthly restore drill): created once."""
        if self.db.one("SELECT 1 FROM reminders WHERE system = 1 AND text = ?", (text,)) is None:
            self.create(text, when, repeat=repeat, system=True)

    # ------------------------------------------------------------------
    # The clock
    # ------------------------------------------------------------------
    def next_due(self) -> datetime | None:
        value = self.db.value("SELECT MIN(due_at) FROM reminders")
        return parse_iso(value) if value else None

    def following(self, reminder: Reminder, after: datetime) -> datetime | None:
        if not reminder.repeat:
            return None
        return next_local(reminder.local_at, reminder.repeat, after, zone(reminder.timezone))

    async def fire_due(self) -> int:
        now = self._clock()
        due = [
            _reminder(row)
            for row in self.db.all("SELECT * FROM reminders WHERE due_at <= ? ORDER BY due_at", (iso(now),))
        ]
        if not due:
            return 0
        written = await asyncio.gather(*(self._compose(reminder) for reminder in due))
        fired_at = self._clock()
        fired = 0
        for reminder, (message, failure) in zip(due, written):
            if self.get(reminder.id) is None:  # cancelled while Clara was writing
                continue
            following = self.following(reminder, fired_at)
            if following is None:
                self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder.id,))
            else:
                self.db.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (iso(following), reminder.id))
            text = message or reminder.text
            if failure:
                text += f"\n(Clara could not write this reminder herself: {failure}. Here it is as it was set.)"
            self.events.publish(
                "reminder",
                "Reminder",
                text,
                payload={"reminder": reminder.describe(), "message": message or "", "text": reminder.text},
                targets=reminder.targets,
                source="reminders",
                conversation_id=reminder.conversation_id,
            )
            late = (fired_at - reminder.due_at).total_seconds()
            if late > self.late_after and self.late is not None:
                self.late(f"reminder #{reminder.id} ({reminder.text[:60]})", late)
            fired += 1
        return fired

    async def _compose(self, reminder: Reminder) -> tuple[str | None, str]:
        if self.composer is None or self.stopping or reminder.system:
            return None, ""
        try:
            return await self.composer(reminder), ""
        except AnnounceFailed as error:
            return None, str(error)
        except Exception:
            log.exception("reminder %s: could not write the announcement", reminder.id)
            return None, "an unexpected error"
