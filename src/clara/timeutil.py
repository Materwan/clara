"""Time, in the two forms Clara needs.

* **Instants** are UTC and stored as ISO text with the offset (`2026-10-25T01:30:00+00:00`), so that text order is
  time order.
* **Wall-clock moments** (a reminder at 09:00, a schedule every Monday at 08:00) are stored as a local date-time with
  an IANA zone name (`Europe/Paris`), never a bare offset: the next occurrence is worked out in the zone, so a
  daylight-saving change never moves "09:00".

`resolve_local` turns a wall-clock moment into an instant: a time that does not exist (the spring-forward gap) moves
forward by the gap, and a time that happens twice (the autumn fall-back hour) is the first of the two.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPEATS = ("daily", "weekly", "monthly")
_OFFSET = re.compile(r"^[+-]\d{2}:?\d{2}$|^(?:UTC|GMT)[+-]\d")


class ZoneError(ValueError):
    """Not an IANA zone name."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """An instant as stored: UTC, to the second, with its offset."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime: give an aware one")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(utcnow())


def parse_iso(text: str) -> datetime:
    """A stored instant back (always aware, in UTC)."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def zone(name: str) -> ZoneInfo:
    """The zone of an IANA name. Bare offsets (`+02:00`) are refused: they do not follow daylight saving."""
    name = (name or "").strip()
    if not name or _OFFSET.match(name):
        raise ZoneError(f"{name or '(empty)'} is not a time zone name: give one like Europe/Paris.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ZoneError(f"Unknown time zone: {name}.") from None


def system_zone_name() -> str:
    """The machine's own zone name, from /etc/timezone or the /etc/localtime link; UTC when neither says."""
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if text:
            zone(text)
            return text
    except (OSError, ZoneError):
        pass
    try:
        target = str(Path("/etc/localtime").resolve())
        marker = "/zoneinfo/"
        if marker in target:
            name = target.split(marker, 1)[1]
            zone(name)
            return name
    except (OSError, ZoneError):
        pass
    return "UTC"


def resolve_local(local: datetime, tz: ZoneInfo) -> datetime:
    """A naive wall-clock date-time in `tz`, as an aware instant.

    The gap of a spring-forward change has no such wall clock: the moment moves forward by the size of the gap
    (02:30 becomes 03:30). The hour that repeats in autumn happens twice: the first (summer time) is meant.
    """
    if local.tzinfo is not None:
        raise ValueError("resolve_local takes a naive wall-clock date-time")
    first = local.replace(tzinfo=tz, fold=0)
    # A wall clock that does not exist does not survive the round trip through UTC
    back = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if back != local:
        later = local.replace(tzinfo=tz, fold=1)
        gap = later.utcoffset() - first.utcoffset() if later.utcoffset() and first.utcoffset() else timedelta(0)
        return (local + abs(gap)).replace(tzinfo=tz, fold=0)
    return first


def parse_local(text: str) -> datetime:
    """A wall-clock moment as the model or a client writes it: `2026-10-05T09:00`, `2026-10-05 09:00`, or a date
    alone (09:00 that day). An offset or a `Z` is refused: the zone gives the offset."""
    text = (text or "").strip()
    if not text:
        raise ValueError("No date and time given.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            moment = datetime.combine(date.fromisoformat(text), time(9, 0))
        else:
            moment = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        raise ValueError(f"{text!r} is not a date and time like 2026-10-05T09:00.") from None
    if moment.tzinfo is not None:
        raise ValueError(f"{text!r} carries an offset: give the local time without one, the time zone is known.")
    return moment.replace(second=0, microsecond=0)


def add_months(local: datetime, months: int, day: int) -> datetime:
    """`local` moved by `months`, on `day` of that month (clamped to its length: the 31st becomes the 30th)."""
    month_index = local.month - 1 + months
    year, month = local.year + month_index // 12, month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return local.replace(year=year, month=month, day=min(day, last))


def next_local(
    anchor: datetime, repeat: str, after: datetime, tz: ZoneInfo, weekdays: tuple[int, ...] = ()
) -> datetime:
    """The first occurrence of a repeating wall-clock moment that falls strictly after the instant `after`.

    `anchor` is the naive local first occurrence: it gives the time of day, the weekday (weekly) and the day of the
    month (monthly, clamped on short months). `weekdays` (0 = Monday) widens a weekly repeat to several days. The
    occurrences are counted on the wall clock, then each is resolved in `tz`.
    """
    if repeat not in REPEATS:
        raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
    after_local = after.astimezone(tz).replace(tzinfo=None)
    if repeat == "monthly":
        months = max(0, (after_local.year - anchor.year) * 12 + after_local.month - anchor.month - 1)
        while True:
            candidate = add_months(anchor, months, anchor.day)
            if candidate >= anchor and resolve_local(candidate, tz) > after:
                return resolve_local(candidate, tz)
            months += 1
    days_from = max(0, (after_local.date() - anchor.date()).days - 1)
    candidate = anchor + timedelta(days=days_from)
    allowed = set(weekdays) if repeat == "weekly" and weekdays else ({anchor.weekday()} if repeat == "weekly" else None)
    for _ in range(800):  # at most two years of days; a weekly repeat has a hit every seven
        if candidate >= anchor and (allowed is None or candidate.weekday() in allowed):
            instant = resolve_local(candidate, tz)
            if instant > after:
                return instant
        candidate += timedelta(days=1)
    raise ValueError("no next occurrence found")  # pragma: no cover - the loop always finds one


def human(moment: datetime, tz: ZoneInfo) -> str:
    """An instant as a person reads it in their zone: `Sun 2026-10-25 09:00 CET`."""
    return moment.astimezone(tz).strftime("%a %Y-%m-%d %H:%M %Z")
