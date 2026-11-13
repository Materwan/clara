"""The tree of triggers: AND and OR over events, instants and conditions of time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clara.automations import cron, tree
from clara.automations.events import KINDS

PARIS = ZoneInfo("Europe/Paris")
PUSH = {"type": "event", "kind": "github.push", "filter": {"branch": "^main$"}}
MAIL = {"type": "event", "kind": "mail.received"}
EVENING = {"type": "window", "from": "18:00", "to": "20:00", "days": [0, 1, 2, 3, 4]}


def make(raw):
    return tree.normalize(raw, KINDS.get, PARIS)


def paris(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=PARIS).astimezone(UTC)


def event(kind, at, **data):
    return tree.Arrival(kind, at, data)


def holds(root, arrival, **options):
    return tree.evaluate(root, arrival, PARIS, **options).ok


# ----------------------------------------------------------------------
# Checking a tree
# ----------------------------------------------------------------------
def test_the_top_is_an_or_and_a_single_node_is_a_tree_of_one_branch():
    assert make(PUSH)["type"] == "or" and len(make(PUSH)["of"]) == 1
    root = make({"type": "and", "of": [EVENING, PUSH]})
    assert root["type"] == "or" and root["of"][0]["type"] == "and"
    assert make([PUSH, MAIL])["of"][1]["kind"] == "mail.received"


def test_an_and_with_two_events_is_refused_but_two_events_can_be_joined_with_or():
    with pytest.raises(tree.TreeError, match="at most one event"):
        make({"type": "and", "of": [PUSH, MAIL]})
    with pytest.raises(tree.TreeError, match="at most one event"):  # inside an OR inside an AND: still two at once
        make({"type": "and", "of": [{"type": "or", "of": [PUSH, MAIL]}, {"type": "cron", "expr": "0 9 * * *", "as": "instant"}]})
    assert make({"type": "or", "of": [PUSH, MAIL]})
    assert make({"type": "and", "of": [{"type": "or", "of": [PUSH, MAIL]}, EVENING]})  # one event at a time, in a window


def test_a_branch_of_conditions_only_never_starts_a_run_and_is_refused():
    with pytest.raises(tree.TreeError, match="holds no event"):
        make(EVENING)
    with pytest.raises(tree.TreeError, match="holds no event"):
        make({"type": "or", "of": [PUSH, {"type": "and", "of": [EVENING, {"type": "cron", "expr": "* * * * *", "as": "condition"}]}]})


def test_a_cron_is_a_condition_beside_an_event_and_an_instant_otherwise():
    beside = make({"type": "and", "of": [PUSH, {"type": "cron", "expr": "* 18-19 * * 1-5"}]})["of"][0]["of"][1]
    assert beside["as"] == "condition"
    alone = make({"type": "cron", "expr": "0 9 * * *"})["of"][0]
    assert alone["as"] == "instant"
    window = make({"type": "and", "of": [{"type": "cron", "expr": "0 * * * *"}, EVENING]})["of"][0]["of"][0]
    assert window["as"] == "instant"  # a window is a condition, it does not make the cron one
    explicit = make({"type": "and", "of": [{"type": "cron", "expr": "0 9 * * *", "as": "instant"}, EVENING]})
    assert explicit["of"][0]["of"][0]["as"] == "instant"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"type": "nope"}, "not a node"),
        ({"type": "and", "of": []}, "list of branches"),
        ({"type": "event", "kind": "mail.nope"}, "not a kind of event"),
        ({"type": "event", "kind": "mail.received", "filter": {"subject": "(?!x)"}}, "lookahead"),
        ({"type": "cron", "expr": "0 9 * *"}, "five fields"),
        ({"type": "cron", "expr": "0 9 * * *", "as": "maybe"}, "instant"),
        ({"type": "window", "from": "25:00", "to": "20:00"}, "HH:MM"),
        ({"type": "window", "from": "18:00", "to": "18:00"}, "another time"),
        ({"type": "window", "from": "18:00", "to": "19:00", "days": [7]}, "from 0"),
        ({"type": "once", "at": "yesterday"}, "once"),
    ],
)
def test_a_node_that_is_wrong_is_refused_with_the_reason(raw, message):
    with pytest.raises(tree.TreeError, match=message):
        make({"type": "or", "of": [PUSH, raw]})


def test_a_tree_is_not_too_big():
    deep = PUSH
    for _ in range(tree.MAX_DEPTH + 1):
        deep = {"type": "or", "of": [deep]}
    with pytest.raises(tree.TreeError, match="levels"):
        make(deep)
    with pytest.raises(tree.TreeError, match="nodes"):
        make({"type": "or", "of": [PUSH] * (tree.MAX_NODES + 1)})


# ----------------------------------------------------------------------
# Evaluating
# ----------------------------------------------------------------------
def test_an_event_inside_and_outside_the_window():
    root = make({"type": "and", "of": [EVENING, PUSH]})
    inside = event("github.push", paris(2026, 11, 13, 19, 30), branch="main")  # a Friday
    assert holds(root, inside)
    assert not holds(root, event("github.push", paris(2026, 11, 13, 21, 0), branch="main"))
    assert not holds(root, event("github.push", paris(2026, 11, 14, 19, 0), branch="main"))  # Saturday
    late = event("github.push", paris(2026, 11, 13, 21, 0), branch="main")
    verdict = tree.evaluate(root, late, PARIS)
    assert not verdict.ok and "outside 18:00-20:00" in verdict.failed_times()[0].why
    assert holds(root, late, ignore_time=True)  # the filters accept it: it is the time that refuses
    wrong = event("github.push", paris(2026, 11, 13, 21, 0), branch="dev")
    assert not holds(root, wrong, ignore_time=True)  # the filters refuse it: not an event this automation looks at
    assert not holds(root, event("mail.received", paris(2026, 11, 13, 19, 0)), ignore_time=True)


def test_a_window_may_cross_midnight_and_its_days_are_the_days_it_starts_on():
    root = make({"type": "and", "of": [{"type": "window", "from": "18:30", "to": "02:15", "days": [4]}, MAIL]})  # from Friday
    assert holds(root, event("mail.received", paris(2026, 11, 13, 18, 30)))  # Friday evening
    assert holds(root, event("mail.received", paris(2026, 11, 13, 23, 59)))
    assert holds(root, event("mail.received", paris(2026, 11, 14, 2, 14)))  # Saturday, still Friday's window
    assert not holds(root, event("mail.received", paris(2026, 11, 14, 2, 15)))  # the end is exclusive
    assert not holds(root, event("mail.received", paris(2026, 11, 14, 18, 30)))  # Saturday evening is not Friday's
    assert not holds(root, event("mail.received", paris(2026, 11, 13, 18, 29)))


def test_a_window_is_read_on_the_clock_of_the_zone_whatever_the_season():
    root = make({"type": "and", "of": [{"type": "window", "from": "09:00", "to": "10:00"}, MAIL]})
    assert holds(root, event("mail.received", paris(2026, 7, 1, 9, 30)))  # CEST, 07:30 UTC
    assert holds(root, event("mail.received", paris(2026, 1, 5, 9, 30)))  # CET, 08:30 UTC
    assert not holds(root, event("mail.received", datetime(2026, 7, 1, 9, 30, tzinfo=UTC)))  # 11:30 in Paris


def test_a_cron_condition_is_a_window_to_the_minute():
    root = make({"type": "and", "of": [MAIL, {"type": "cron", "expr": "* 18-19 * * 1-5"}]})
    assert holds(root, event("mail.received", paris(2026, 11, 13, 19, 59)))
    assert not holds(root, event("mail.received", paris(2026, 11, 13, 20, 0)))


def test_instants_come_from_cron_and_once_nodes_in_the_zone_of_the_automation():
    root = make({"type": "or", "of": [{"type": "cron", "expr": "30 9 * * mon-fri"}, {"type": "once", "at": "2026-11-13T12:00"}]})
    start = paris(2026, 11, 12, 10, 0)
    found, more = tree.instants_between(root, start, paris(2026, 11, 17, 0, 0), PARIS, 10)
    assert [moment.astimezone(PARIS).strftime("%a %H:%M") for moment in found] == ["Fri 09:30", "Fri 12:00", "Mon 09:30"]
    assert more == 0
    assert tree.next_instant(root, paris(2026, 11, 17, 9, 30), PARIS) == paris(2026, 11, 18, 9, 30)
    assert holds(root, tree.Arrival(tree.TIME, found[1]))  # the date holds at its own instant
    assert not holds(root, tree.Arrival(tree.TIME, found[1] + timedelta(minutes=1)))
    assert not holds(root, event("github.push", found[0]))  # an event is not an instant


def test_an_instant_inside_a_window_and_a_window_that_the_instant_misses():
    root = make({"type": "and", "of": [{"type": "cron", "expr": "0 * * * *"}, EVENING]})
    assert holds(root, tree.Arrival(tree.TIME, paris(2026, 11, 13, 19, 0)))
    assert not holds(root, tree.Arrival(tree.TIME, paris(2026, 11, 13, 21, 0)))
    assert not holds(root, tree.Arrival(tree.TIME, paris(2026, 11, 13, 19, 30)))


def test_a_cron_follows_the_clock_through_the_changes_of_season():
    root = make({"type": "cron", "expr": "30 2 * * *"})
    spring = tree.next_instant(root, paris(2026, 3, 28, 12, 0), PARIS)  # 02:30 does not exist on 2026-03-29
    assert spring.astimezone(PARIS).strftime("%m-%d %H:%M") == "03-29 03:30"
    autumn = tree.next_instant(root, paris(2026, 10, 24, 12, 0), PARIS)  # 02:30 happens twice on 2026-10-25: the first
    assert autumn == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert tree.next_instant(root, autumn, PARIS) == paris(2026, 10, 26, 2, 30)  # and the 26th is a plain day again
    daily = make({"type": "cron", "expr": "0 9 * * *"})
    assert tree.next_instant(daily, paris(2026, 3, 28, 9, 0), PARIS) == datetime(2026, 3, 29, 7, 0, tzinfo=UTC)  # 09:00 CEST


def test_a_backlog_is_counted_and_cut():
    root = make({"type": "cron", "expr": "* * * * *"})
    start = paris(2026, 11, 13, 9, 0)
    found, more = tree.instants_between(root, start, start + timedelta(minutes=100), PARIS, 31)
    assert len(found) == 31 and more == 69


def test_the_trigger_in_words():
    root = make({"type": "or", "of": [{"type": "and", "of": [EVENING, PUSH]}, {"type": "cron", "expr": "0 9 * * *"}]})
    assert tree.words(root, KINDS.get) == (
        "(between 18:00 and 20:00 (Mon, Tue, Wed, Thu, Fri) and a push where branch matches “^main$”) or at cron “0 9 * * *”"
    )
    assert tree.words(make({"type": "once", "at": "2026-12-01T09:00"}), KINDS.get) == "once, on 2026-12-01 at 09:00"
    assert tree.kinds_of(root) == ["github.push"]


# ----------------------------------------------------------------------
# Cron
# ----------------------------------------------------------------------
def test_cron_expressions():
    expression = cron.parse("*/15 8-10 1,15 jan-mar *")
    assert expression.matches(datetime(2026, 2, 15, 9, 45)) and not expression.matches(datetime(2026, 2, 15, 9, 50))
    assert expression.matches(datetime(2026, 1, 1, 8, 0)) and not expression.matches(datetime(2026, 4, 1, 8, 0))
    assert cron.parse("0 0 * * 7").weekdays == frozenset({6}) == cron.parse("0 0 * * sun").weekdays  # Sunday, 0 or 7
    either = cron.parse("0 0 13 * fri")  # the 13th, or a Friday
    assert either.matches(datetime(2026, 11, 13, 0, 0)) and either.matches(datetime(2026, 11, 20, 0, 0))
    assert either.matches(datetime(2026, 12, 13, 0, 0)) and not either.matches(datetime(2026, 11, 14, 0, 0))
    assert cron.parse("@daily").matches(datetime(2026, 1, 1, 0, 0))
    last = cron.parse("0 9 L * *")  # the last day of each month, short or long
    assert [last.matches(datetime(2026, month, day, 9, 0)) for month, day in ((2, 28), (2, 27), (4, 30), (12, 31))] == [
        True, False, True, True,
    ]
    assert cron.parse("0 9 1,L * *").matches(datetime(2026, 11, 1, 9, 0)) and cron.parse("0 9 1,L * *").matches(datetime(2024, 2, 29, 9, 0))
    assert cron.parse("0 0 29 2 *").next_instant(datetime(2026, 3, 1, tzinfo=UTC), ZoneInfo("UTC")) == datetime(
        2028, 2, 29, tzinfo=UTC
    )


@pytest.mark.parametrize("text", ["", "* * * *", "61 * * * *", "0 0 31 2 *", "@weekly2", "5-1 * * * *", "*/0 * * * *", "a * * * *", "0 0 * * funday"])
def test_a_cron_expression_that_is_wrong_is_refused(text):
    with pytest.raises(cron.CronError):
        cron.parse(text)
