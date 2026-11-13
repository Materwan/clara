"""Cron expressions: the standard five fields, `minute hour day-of-month month day-of-week`.

Each field takes `*` (or `?`), a number, a range `a-b`, a list `a,b,c` of those, and a step `*/n`, `a-b/n` or `a/n` (from `a`
to the top of the field). Months and weekdays may be written as names (`jan`, `mon`). The day of the month also takes `L`, the
last day of the month (`0 9 L * *`), which a monthly schedule on the 31st needs on the shorter months. Weekdays count from Sunday as 0 (7 is
Sunday too), as in every cron. When both the day of the month and the day of the week are given (neither is `*`), a day
matches if either does, as in Vixie cron. The macros `@hourly`, `@daily` (`@midnight`), `@weekly`, `@monthly` and `@yearly`
(`@annually`) are accepted. There is no seconds field.

An expression is read on the **wall clock of a zone**: `0 9 * * *` is 09:00 there all year round. `next_instant` works out
the instants it names, resolving each wall-clock minute the way `timeutil.resolve_local` does (a minute that does not exist at
the spring-forward change moves forward by the gap; the hour that repeats in autumn happens once, the first time).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..timeutil import resolve_local

MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
MONTHS = {name: number for number, name in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
WEEKDAYS = {name: number for number, name in enumerate("sun mon tue wed thu fri sat".split())}
MAX_LENGTH = 100
SEARCH_DAYS = 366 * 9  # a 29 February is found within eight years; a date that never exists is refused when saved


class CronError(ValueError):
    pass


def _number(text: str, names: dict[str, int], low: int, high: int, field: str) -> int:
    if text.lower() in names:
        return names[text.lower()]
    if not text.isdecimal():
        raise CronError(f"{field}: {text!r} is not a number{' or a name' if names else ''}.")
    value = int(text)
    if not low <= value <= high:
        raise CronError(f"{field}: {value} is outside {low}-{high}.")
    return value


def _field(
    text: str, low: int, high: int, field: str, names: dict[str, int] | None = None
) -> tuple[frozenset[int], bool]:
    """(the numbers a field allows, whether it is a plain `*`)."""
    names = names or {}
    allowed: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"{field}: an empty item in {text!r}.")
        base, slash, step_text = part.partition("/")
        if slash and (not step_text.isdecimal() or int(step_text) < 1):
            raise CronError(f"{field}: the step in {part!r} is a number from 1.")
        step = int(step_text) if slash else 1
        if base in ("*", "?"):
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _number(first, names, low, high, field), _number(last, names, low, high, field)
            if start > end:
                raise CronError(f"{field}: the range {base!r} runs backwards.")
        else:
            start = _number(base, names, low, high, field)
            end = high if slash else start
        allowed.update(range(start, end + 1, step))
    return frozenset(allowed), text in ("*", "?")


@dataclass(frozen=True)
class Cron:
    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]  # of the month
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Monday, as datetime.weekday()
    any_day: bool  # the day of the month is `*`
    any_weekday: bool
    last_day: bool = False  # the day of the month lists `L`

    def _day_matches(self, moment: datetime) -> bool:
        by_date = moment.day in self.days or (
            self.last_day and moment.day == calendar.monthrange(moment.year, moment.month)[1]
        )
        by_weekday = moment.weekday() in self.weekdays
        if self.any_day or self.any_weekday:
            return (by_date or self.any_day) and (by_weekday or self.any_weekday)
        return by_date or by_weekday

    def matches(self, local: datetime) -> bool:
        """Does this wall-clock minute (a naive date-time) match?"""
        return (
            local.minute in self.minutes
            and local.hour in self.hours
            and local.month in self.months
            and self._day_matches(local)
        )

    def next_local(self, after: datetime) -> datetime | None:
        """The first matching wall-clock minute strictly after `after` (naive), or None within nine years."""
        moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = after + timedelta(days=SEARCH_DAYS)
        while moment <= limit:
            if moment.month not in self.months or not self._day_matches(moment):
                moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
            elif moment.hour not in self.hours:
                moment = (moment + timedelta(hours=1)).replace(minute=0)
            elif moment.minute not in self.minutes:
                moment += timedelta(minutes=1)
            else:
                return moment
        return None

    def next_instant(self, after: datetime, tz: ZoneInfo) -> datetime | None:
        """The first instant strictly after `after` (aware) this expression names on the clock of `tz`."""
        local = after.astimezone(tz).replace(tzinfo=None)
        while (found := self.next_local(local)) is not None:
            instant = resolve_local(found, tz).astimezone(UTC)
            if instant > after:
                return instant
            local = found  # a minute that moved forward by the gap, not after `after` yet
        return None


def parse(text: str) -> Cron:
    text = " ".join((text or "").split())
    if not text or len(text) > MAX_LENGTH:
        raise CronError(
            f"A cron expression is five fields (minute hour day month weekday), at most {MAX_LENGTH} characters."
        )
    expanded = MACROS.get(text.lower(), text)
    if expanded.startswith("@"):
        raise CronError(f"{text!r} is not a macro ({', '.join(MACROS)}).")
    fields = expanded.split(" ")
    if len(fields) != 5:
        raise CronError(f"A cron expression has five fields (minute hour day month weekday), not {len(fields)}.")
    minutes, _ = _field(fields[0], 0, 59, "minute")
    hours, _ = _field(fields[1], 0, 23, "hour")
    items = fields[2].split(",")
    last_day = "L" in (item.upper() for item in items)
    rest = ",".join(item for item in items if item.upper() != "L")
    days, any_day = _field(rest, 1, 31, "day of the month") if rest else (frozenset(), False)
    months, _ = _field(fields[3], 1, 12, "month", MONTHS)
    cron_days, any_weekday = _field(fields[4], 0, 7, "day of the week", WEEKDAYS)
    weekdays = frozenset((day - 1) % 7 for day in cron_days)  # cron's Sunday (0 or 7) is Python's 6
    cron = Cron(text, minutes, hours, days, months, weekdays, any_day, any_weekday, last_day)
    if cron.next_local(datetime(2024, 1, 1)) is None:
        raise CronError(f"{text!r} never happens (a date that does not exist, such as 31 February).")
    return cron
