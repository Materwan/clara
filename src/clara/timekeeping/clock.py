"""Reading a moment the way a person or the model writes it, always against an IANA zone name.

`2026-10-05T09:00` is read on the wall clock of the zone; `2026-10-05T09:00+02:00` is an instant, kept as such (its
zone for repeats and display is still the zone name, never the offset); a date alone is 09:00 that day.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ..timeutil import ZoneError, parse_local, resolve_local, zone


class MomentError(ValueError):
    pass


def zone_of(name: str | None, home: str) -> tuple[str, ZoneInfo]:
    """The zone a client gave, else the home zone (CLARA_TIMEZONE). An offset is refused."""
    chosen = (name or "").strip() or home
    try:
        return chosen, zone(chosen)
    except ZoneError as error:
        raise MomentError(str(error)) from None


def read_moment(text: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """(the instant in UTC, the naive wall-clock moment in `tz`) for a text."""
    text = (text or "").strip()
    try:
        aware = datetime.fromisoformat(text.replace(" ", "T", 1).replace("Z", "+00:00"))
    except ValueError:
        aware = None
    if aware is not None and aware.tzinfo is not None:
        instant = aware.astimezone(UTC).replace(second=0, microsecond=0)
        return instant, instant.astimezone(tz).replace(tzinfo=None)
    try:
        local = parse_local(text)
    except ValueError as error:
        raise MomentError(str(error)) from None
    return resolve_local(local, tz).astimezone(UTC), local


def local_text(moment: datetime, tz: ZoneInfo) -> str:
    """`2026-10-05T09:00+02:00`: an instant on a zone's clock, for the model and the clients."""
    return moment.astimezone(tz).isoformat(timespec="minutes")
