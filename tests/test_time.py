"""Wall-clock moments across the 25 October 2026 change (Paris: 03:00 CEST becomes 02:00 CET)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clara.timeutil import ZoneError, next_local, parse_local, resolve_local, zone

PARIS = zone("Europe/Paris")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_offsets_are_refused_as_zones():
    for bad in ("+02:00", "-0500", "UTC+2", ""):
        with pytest.raises(ZoneError):
            zone(bad)


def test_the_repeated_hour_is_the_first_one():
    moment = resolve_local(datetime(2026, 10, 25, 2, 30), PARIS)
    assert moment.astimezone(UTC) == utc("2026-10-25T00:30")  # 02:30 CEST, not 02:30 CET


def test_the_missing_hour_moves_forward():
    moment = resolve_local(datetime(2026, 3, 29, 2, 30), PARIS)
    assert moment.astimezone(UTC) == utc("2026-03-29T01:30")  # 03:30 CEST


def test_daily_nine_stays_nine_across_the_october_change():
    anchor = datetime(2026, 10, 20, 9, 0)
    first = next_local(anchor, "daily", utc("2026-10-24T08:00"), PARIS)
    second = next_local(anchor, "daily", first, PARIS)
    assert first == utc("2026-10-25T08:00")  # 09:00 CET on the 25th... no: the 25th is already CET
    assert first.astimezone(PARIS).hour == 9
    assert second == utc("2026-10-26T08:00")
    before = next_local(anchor, "daily", utc("2026-10-23T08:00"), PARIS)
    assert before == utc("2026-10-24T07:00")  # still summer time on the 24th
    assert before.astimezone(PARIS).hour == 9


def test_weekly_on_chosen_days_and_monthly_clamps():
    anchor = datetime(2026, 10, 19, 8, 0)  # a Monday
    following = next_local(anchor, "weekly", utc("2026-10-19T07:00"), PARIS, (0, 3))
    assert following.astimezone(PARIS).date().isoformat() == "2026-10-22"  # Thursday
    end_of_month = datetime(2026, 1, 31, 10, 0)
    february = next_local(end_of_month, "monthly", utc("2026-02-01T00:00"), PARIS)
    assert february.astimezone(PARIS).date().isoformat() == "2026-02-28"
    march = next_local(end_of_month, "monthly", february, PARIS)
    assert march.astimezone(PARIS).date().isoformat() == "2026-03-31"


def test_parse_local_refuses_offsets():
    assert parse_local("2026-10-05 09:00") == datetime(2026, 10, 5, 9, 0)
    assert parse_local("2026-10-05") == datetime(2026, 10, 5, 9, 0)
    with pytest.raises(ValueError):
        parse_local("2026-10-05T09:00+02:00")
