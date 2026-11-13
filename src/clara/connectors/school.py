"""What Moodle and Auriga (EPITA) have in common: an error that may ask for a new sign-in, an event of the calendar, the way
a school year is written, and the small helpers that clean what the sites say.

What a site says (a course name, an assignment, a room) is written by other people. It is shown as data, one cleaned line at
a time; nothing here follows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .mail import clean_line

SITES = ("moodle", "auriga")
LABELS = {"moodle": "Moodle", "auriga": "Auriga"}
SECRETS = {"moodle": "moodle_token", "auriga": "auriga_refresh_token"}  # what the store keeps for each site


class SchoolError(ValueError):
    """A problem the model should read. `reconnect` is True when only a new sign-in fixes it (the token is no longer
    accepted)."""

    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


@dataclass(frozen=True)
class Event:
    """One line of the calendar, from either site. Times are aware datetimes in UTC."""

    start: datetime
    end: datetime | None
    site: str  # moodle or auriga
    kind: str  # CM, TD, TP, EX…, or due, opens, closes…
    title: str
    details: tuple[str, ...] = ()  # the room, the teacher, the course
    flags: tuple[str, ...] = ()  # exam, overdue


class Noted(list):
    """A list that also says what was left out of it (a line that did not fit, pages not read): the tool prints these notes
    before the lines, so that a partial list never looks complete."""

    def __init__(self, items=(), notes=()):
        super().__init__(items)
        self.notes: list[str] = list(notes)


def school_year(today: date) -> str:
    """The school year `today` is in, written as Auriga's component codes begin: 2627 from August 2026 to July 2027."""
    first = today.year if today.month >= 8 else today.year - 1
    return f"{first % 100:02d}{(first + 1) % 100:02d}"


def line(value: Any, limit: int = 160) -> str:
    return clean_line(str(value if value is not None else ""), limit)
