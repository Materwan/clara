"""School events: a poll of Moodle and Auriga (stand-ins of schoolworld.py) that hands quiz times, new activities, grades and changes
of planning to the automations, on a clock the test moves."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, time, timedelta

import pytest

from clara.automations.setup import limits_of
from clara.connectors import school_watch
from clara.connectors.auriga import Auriga, AurigaAuth
from clara.connectors.moodle import Moodle
from clara.connectors.school import school_year
from clara.db.connection import connect
from clara.db.migrate import _apply, migrations
from clara.settings import SettingsError, load_settings
from clara.timeutil import iso, parse_iso, zone
from fakes import calls
from modeset import at_level
from schoolworld import (
    World,
    connect_school,
    grade_item,
    intervention,
    keep_password,
    lapse,
    module,
    quiz,
    section,
    synthese,
)
from test_automation_events import chat_calls, runs, work

SCHOOL = ("moodle.quiz_opens", "moodle.quiz_closes", "moodle.activity_added", "moodle.grade_posted", "auriga.grade_posted", "auriga.planning_changed")


class Clock:
    """The server's clock, which a test moves."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)

    def stamp(self, **delta) -> int:
        return int((self.now + timedelta(**delta)).timestamp())


@pytest.fixture(autouse=True)
def school_level(services):
    at_level(services, 4)


@pytest.fixture
def school(monkeypatch):
    """Moodle, Auriga and Keycloak as stand-ins: every client the server makes talks to them."""
    found = World()
    transport = found.transport()
    for cls in (Moodle, Auriga, AurigaAuth):
        real = cls.__init__

        def init(self, *args, _real=real, **kwargs):
            kwargs["transport"] = transport
            _real(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", init)
    return found


@pytest.fixture
def connected(services, school):
    connect_school(services, school)
    return school


@pytest.fixture
def clock(services) -> Clock:
    found = Clock()
    services.automations._clock = found
    return found


def hook(services, kind: str, name: str = "", **fields):
    fields.setdefault("prompt", "Look at it.")
    when = {"type": "event", "kind": kind, "filter": fields.pop("filter", None)}
    return services.automations.create(name or kind, when, **fields)


async def poll(services) -> None:
    await services.connectors.school_watch.poll()


def stored(services, kind: str) -> list:
    return sorted(services.automations.recent_events(100, kind), key=lambda event: event.id)


def cursors(services) -> list[tuple[str, str]]:
    return [(row["site"], row["stream"]) for row in services.db.all("SELECT site, stream FROM school_cursors ORDER BY site, stream")]


def state_text(services) -> str:
    return " ".join(row["state"] for row in services.db.all("SELECT state FROM school_cursors"))


def local(services, text: str) -> str:
    return f"{parse_iso(text).astimezone(zone(services.automations.home_zone)):%H:%M}"


def entry(clock: Clock, ident: int, days: int, hour: int, title: str = "Algo", **options) -> dict:
    """A planning entry `days` after the clock's day, from `hour` for two hours (UTC)."""
    start = datetime.combine(clock.now.date() + timedelta(days=days), time(hour), UTC)
    text = "%Y-%m-%dT%H:%M:%SZ"
    return intervention(start.strftime(text), (start + timedelta(hours=2)).strftime(text), title, ident=ident, **options)


# ----------------------------------------------------------------------
# The first look
# ----------------------------------------------------------------------
async def test_the_first_look_only_notes_where_things_stand_for_every_kind(services, connected, clock):
    triggers = [hook(services, kind) for kind in SCHOOL]
    connected.quizzes = [quiz(7, 11, "Quiz graphes", clock.stamp(hours=-2), clock.stamp(hours=3))]  # opened before the first look
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum"))]
    connected.grade_items[11] = [grade_item(900, "Quiz 1", "14,00")]
    connected.synthese = [synthese(1, "12.5")]
    connected.evals = [[1, "14", 2, "2627_I_INF_001", "EXA"]]
    connected.interventions = [entry(clock, 101, 2, 8)]
    for _ in range(3):  # and a look that finds nothing changed adds nothing either
        await poll(services)
    assert services.automations.recent_events(50) == []
    assert all(runs(services, trigger) == [] for trigger in triggers)
    assert cursors(services) == [("auriga", "grades"), ("auriga", "planning"), ("moodle", "activities"), ("moodle", "grades"), ("moodle", "quizzes")]


async def test_each_kind_is_an_event_on_its_own_trigger(services, connected, clock):
    triggers = {kind: hook(services, kind) for kind in SCHOOL}
    connected.quizzes = [quiz(7, 11, "Quiz graphes", clock.stamp(hours=1), clock.stamp(hours=3))]
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum"))]
    connected.grade_items[11] = [grade_item(900, "Quiz 1", "-")]
    connected.synthese = [synthese(1, None)]
    connected.interventions = [entry(clock, 101, 2, 8)]
    await poll(services)
    clock.advance(hours=4)
    connected.contents[11][0]["modules"].append(module(501, "Cours 1"))
    connected.grade_items[11][0]["gradeformatted"] = "14,00"
    connected.synthese = [synthese(1, "12.5")]
    connected.interventions = [entry(clock, 101, 2, 9)]
    await poll(services)
    for kind in SCHOOL:
        assert len(stored(services, kind)) == 1, kind
        assert runs(services, triggers[kind]) == [("queued", 1)], kind
    [moved] = stored(services, "auriga.planning_changed")
    assert moved.data["change"] == "moved" and moved.source == "auriga"
    assert {event.source for kind in SCHOOL[:4] for event in stored(services, kind)} == {"moodle"}


# ----------------------------------------------------------------------
# Quiz times
# ----------------------------------------------------------------------
async def test_a_quiz_opens_and_closes_at_its_time_not_when_it_was_seen(services, connected, clock):
    opens, closes = hook(services, "moodle.quiz_opens"), hook(services, "moodle.quiz_closes")
    start, end = clock.stamp(hours=2), clock.stamp(hours=5)
    connected.quizzes = [quiz(7, 11, "Quiz graphes", start, end, 1800), quiz(8, 12, "Old quiz", clock.stamp(hours=-3), clock.stamp(hours=-1))]
    await poll(services)  # a time already past at the first look is not an event
    clock.advance(hours=1)
    await poll(services)
    assert services.automations.recent_events(10) == []  # not yet
    clock.advance(hours=1, minutes=10)
    await poll(services)
    [event] = stored(services, "moodle.quiz_opens")
    assert event.occurred_at == iso(datetime.fromtimestamp(start, UTC)) and event.key == f"moodle:quiz:7:opens:{start}"
    assert event.source == "moodle" and event.title == "Algorithmique avancée: quiz Quiz graphes opens"
    shown = zone(services.automations.home_zone)
    assert event.data == {
        "course": "Algorithmique avancée",
        "quiz": "Quiz graphes",
        "opens": f"{datetime.fromtimestamp(start, shown):%Y-%m-%d %H:%M}",
        "closes": f"{datetime.fromtimestamp(end, shown):%Y-%m-%d %H:%M}",
    }
    await poll(services)  # seen again: still one
    assert len(stored(services, "moodle.quiz_opens")) == 1 and stored(services, "moodle.quiz_closes") == []
    clock.advance(hours=3)
    await poll(services)
    [closing] = stored(services, "moodle.quiz_closes")
    assert closing.occurred_at == iso(datetime.fromtimestamp(end, UTC)) and closing.key == f"moodle:quiz:7:closes:{end}"
    assert runs(services, opens) == [("queued", 1)] and runs(services, closes) == [("queued", 1)]


async def test_quiz_times_that_passed_in_a_long_gap_are_each_one_event_dated_their_time(services, connected, clock):
    trigger = hook(services, "moodle.quiz_opens", catch_up="latest")
    connected.quizzes = [quiz(7, 11, "First", clock.stamp(hours=2)), quiz(8, 12, "Second", clock.stamp(hours=30))]
    await poll(services)
    clock.advance(days=3)  # the server was off, or the poll's interval very long
    await poll(services)
    events = stored(services, "moodle.quiz_opens")
    assert [(e.data["quiz"], e.occurred_at) for e in events] == [
        ("First", iso(datetime.fromtimestamp(connected.quizzes[0]["timeopen"], UTC))),
        ("Second", iso(datetime.fromtimestamp(connected.quizzes[1]["timeopen"], UTC))),
    ]
    assert sorted(runs(services, trigger)) == [("queued", 1), ("skipped", 1)]  # found late: a backlog, `catch_up` says what it becomes
    await poll(services)
    assert len(stored(services, "moodle.quiz_opens")) == 2


async def test_a_quiz_found_at_the_first_poll_after_its_time_is_not_a_backlog_but_one_found_much_later_is(services, connected, clock):
    assert services.settings.school_poll_seconds == 900  # the default interval is the 15 minutes after which an event is a backlog
    trigger = hook(services, "moodle.quiz_opens", catch_up="skip")
    connected.quizzes = [quiz(7, 11, "On time", clock.stamp(hours=1)), quiz(8, 12, "Much later", clock.stamp(hours=30))]
    await poll(services)
    clock.advance(hours=1, minutes=15, seconds=59)  # the next poll after "On time" opened: an interval and the poll's own time late
    await poll(services)
    assert runs(services, trigger) == [("queued", 1)]
    clock.advance(days=3)  # the server was off: now it is a backlog, and this automation skips a backlog
    await poll(services)
    assert sorted(runs(services, trigger)) == [("queued", 1), ("skipped", 1)]
    assert [e.data["quiz"] for e in stored(services, "moodle.quiz_opens")] == ["On time", "Much later"]


async def test_a_quiz_that_was_moved_opens_at_its_new_time(services, connected, clock):
    hook(services, "moodle.quiz_opens")
    connected.quizzes = [quiz(7, 11, "Quiz graphes", clock.stamp(hours=2))]
    await poll(services)
    connected.quizzes[0]["timeopen"] = clock.stamp(hours=4)  # the teacher moved it, before it opened
    clock.advance(hours=3)
    await poll(services)
    assert stored(services, "moodle.quiz_opens") == []
    clock.advance(hours=1, minutes=5)
    await poll(services)
    [event] = stored(services, "moodle.quiz_opens")
    assert event.key == f"moodle:quiz:7:opens:{connected.quizzes[0]['timeopen']}"


# ----------------------------------------------------------------------
# Activities
# ----------------------------------------------------------------------
async def test_a_new_visible_activity_is_an_event_and_a_label_a_hidden_one_or_a_new_course_is_not(services, connected, clock):
    trigger = hook(services, "moodle.activity_added")
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum")), section(1, "Week 2", module(510, "Doc"))]
    await poll(services)
    week = connected.contents[11][1]["modules"]
    week += [module(511, "Cours 2"), module(512, "Intro text", "label"), module(513, "Secret", visible=False)]
    await poll(services)
    [event] = stored(services, "moodle.activity_added")
    assert event.key == "moodle:activity:11:511" and event.source == "moodle"
    assert event.data == {"course": "Algorithmique avancée", "section": "Week 2", "name": "Cours 2", "type": "resource"}
    week[3]["uservisible"] = True  # a teacher shows it
    connected.courses.append({"id": 14, "fullname": "Chimie", "shortname": "CHI", "startdate": 1, "enddate": clock.stamp(days=90), "progress": 0, "hasprogress": False, "hidden": False})
    connected.contents[14] = [section(0, "General", module(600, "Syllabus"))]  # a course enrolled meanwhile: noted, not announced
    await poll(services)
    assert [e.key for e in stored(services, "moodle.activity_added")] == ["moodle:activity:11:511", "moodle:activity:11:513"]
    connected.contents[14][0]["modules"].append(module(601, "TP 1"))
    await poll(services)
    assert stored(services, "moodle.activity_added")[-1].key == "moodle:activity:14:601"
    assert runs(services, trigger) == [("queued", 1)] * 3


# ----------------------------------------------------------------------
# Grades
# ----------------------------------------------------------------------
async def test_a_moodle_grade_is_an_event_when_a_line_gets_a_grade_or_its_grade_changes_and_the_state_keeps_no_grade(services, connected, clock):
    hook(services, "moodle.grade_posted")
    connected.grade_items[11] = [grade_item(900, "Quiz 1", "14,00"), grade_item(901, "Project", "-"), grade_item(902, None, "13,2", "course")]
    await poll(services)
    items = connected.grade_items[11]
    items[1]["gradeformatted"] = "AB"  # a mark that is not a number is quoted as it is
    items[0]["gradeformatted"] = "15,50"
    items.append(grade_item(903, "Bonus", "12,00"))
    items.append(grade_item(904, "Later", "-"))  # a new line with no grade yet is not a grade posted
    await poll(services)
    got = {event.data["item"]: event.data["grade"] for event in stored(services, "moodle.grade_posted")}
    assert got == {"Quiz 1": "15,50", "Project": "AB", "Bonus": "12,00"}
    [quiz_one] = [e for e in stored(services, "moodle.grade_posted") if e.data["item"] == "Quiz 1"]
    assert quiz_one.data == {"course": "Algorithmique avancée", "item": "Quiz 1", "grade": "15,50", "range": "0–20"}
    assert quiz_one.key.startswith("moodle:grade:11:900:") and len(quiz_one.key.rsplit(":", 1)[1]) == 12
    await poll(services)  # the same grades again, and a grade taken away: no event
    items[0]["gradeformatted"] = "-"
    await poll(services)
    assert len(stored(services, "moodle.grade_posted")) == 3
    for text in ("14,00", "15,50", "AB", "12,00", "13,2"):
        assert text not in state_text(services)  # hashes, not grades


async def test_an_auriga_grade_is_an_event_for_a_new_line_or_a_changed_value_in_either_report(services, connected, clock):
    hook(services, "auriga.grade_posted")
    connected.synthese = [synthese(1, "12.5"), synthese(2, None), synthese(3, None, pre="8.0")]
    connected.evals = [[1, "14", 2, "2627_I_INF_001", "EXA"]]
    await poll(services)
    connected.synthese = [synthese(1, "13.0"), synthese(2, "9.0"), synthese(3, "10.0", pre="8.0")]
    connected.evals = [[1, "15", 2, "2627_I_INF_001", "EXA"], [2, "AB", 1, "2627_I_INF_001", "EXA"], [3, "", 1, "2627_I_INF_002", "TP"]]
    await poll(services)
    got = sorted((e.data["report"], e.data["code"], e.data["value"], e.data["kind"]) for e in stored(services, "auriga.grade_posted"))
    assert got == [
        ("eval", "2627_I_INF_001", "15", "EXA"),
        ("eval", "2627_I_INF_001", "AB", "EXA"),
        ("synthese", "2627_I_INF_001", "13.0", ""),
        ("synthese", "2627_I_INF_002", "9.0", ""),
        ("synthese", "2627_I_INF_003", "10.0", ""),
    ]
    [nine] = [e for e in stored(services, "auriga.grade_posted") if e.data["value"] == "9.0"]
    assert nine.data["title"] == "Matière 2" and nine.key.startswith("auriga:grade:synthese:2627_I_INF_002:")
    await poll(services)
    assert len(stored(services, "auriga.grade_posted")) == 5
    for text in ("12.5", "13.0", "9.0", "10.0", "AB"):
        assert text not in state_text(services)


async def test_a_new_school_year_is_noted_not_announced(services, connected, clock):
    hook(services, "auriga.grade_posted")
    connected.synthese = [synthese(1, "12.5")]
    await poll(services)
    clock.advance(days=400)  # the next school year at least: every line is a new code
    year = school_year(clock.now.date())
    connected.synthese = [synthese(1, "11.0", year=year)]
    await poll(services)
    assert stored(services, "auriga.grade_posted") == []
    connected.synthese = [synthese(1, "12.0", year=year)]
    await poll(services)
    assert [e.data["value"] for e in stored(services, "auriga.grade_posted")] == ["12.0"]


# ----------------------------------------------------------------------
# The planning
# ----------------------------------------------------------------------
async def test_a_change_of_start_room_or_teachers_a_new_entry_and_a_cancelled_one_are_events_and_a_new_title_is_not(services, connected, clock):
    trigger = hook(services, "auriga.planning_changed")
    connected.interventions = [entry(clock, 101, 2, 8), entry(clock, 102, 3, 8), entry(clock, 103, 4, 8), entry(clock, 104, 5, 8, "Maths")]
    await poll(services)
    before = parse_iso(connected.interventions[0]["startDateTime"])
    connected.interventions = [
        entry(clock, 101, 2, 9),  # an hour later
        entry(clock, 102, 3, 8, room="B202", title="Algorithmique avancée"),  # a room, and the title written otherwise
        entry(clock, 103, 4, 8, teacher=("Alan", "Turing")),
        # 104 is gone: cancelled
        entry(clock, 105, 6, 8, "Physics"),  # new, within the weeks looked at
        entry(clock, 106, 40, 8, "Far away"),  # beyond them: not seen at all
    ]
    await poll(services)
    got = {(e.data["change"], e.data["title"]): e for e in stored(services, "auriga.planning_changed")}
    assert sorted(got) == [("added", "Physics"), ("cancelled", "Maths"), ("moved", "Algo"), ("room", "Algorithmique avancée"), ("teachers", "Algo")]
    moved = got[("moved", "Algo")]
    assert moved.data["date"] == f"{before.astimezone(zone(services.automations.home_zone)):%Y-%m-%d}"
    assert moved.data["details"] == f"{before.astimezone(zone(services.automations.home_zone)):%Y-%m-%d} {local(services, iso(before))}–{local(services, iso(before + timedelta(hours=2)))}" \
        f" → {before.astimezone(zone(services.automations.home_zone)):%Y-%m-%d} {local(services, iso(before + timedelta(hours=1)))}–{local(services, iso(before + timedelta(hours=3)))}"
    assert got[("room", "Algorithmique avancée")].data["details"] == "room B101 → B202"
    assert got[("teachers", "Algo")].data["details"] == "teachers Grace Hopper → Alan Turing"
    assert got[("cancelled", "Maths")].data["details"].startswith("was ") and "B101" in got[("cancelled", "Maths")].data["details"]
    assert "B101" in got[("added", "Physics")].data["details"] and "Grace Hopper" in got[("added", "Physics")].data["details"]
    assert got[("moved", "Algo")].key.startswith("auriga:planning:101:moved:") and got[("room", "Algorithmique avancée")].key.startswith("auriga:planning:102:room:")
    assert len(runs(services, trigger)) == 5
    await poll(services)  # nothing changed
    assert len(stored(services, "auriga.planning_changed")) == 5
    clock.advance(days=20)  # the entry beyond the first window comes in sight: it is not new
    connected.interventions = [entry(clock, 106, 20, 8, "Far away")]
    await poll(services)
    assert len([e for e in stored(services, "auriga.planning_changed") if e.data["title"] == "Far away"]) == 0


async def test_a_class_that_moves_and_changes_room_at_once_is_two_events_and_an_empty_answer_is_not_believed(services, connected, clock):
    hook(services, "auriga.planning_changed")
    connected.interventions = [entry(clock, 101, 2, 8)]
    await poll(services)
    connected.interventions = []  # the site answered an empty planning: not taken for a cancelled week
    await poll(services)
    assert stored(services, "auriga.planning_changed") == []
    connected.interventions = [entry(clock, 101, 2, 10, room="C3")]
    await poll(services)
    assert sorted(e.data["change"] for e in stored(services, "auriga.planning_changed")) == ["moved", "room"]


async def test_an_entry_that_cannot_be_read_is_not_a_cancelled_one(services, connected, clock):
    hook(services, "auriga.planning_changed")
    connected.interventions = [entry(clock, 101, 2, 8), entry(clock, 102, 3, 8, "Maths")]
    await poll(services)
    broken = {"id": 9, "startDateTime": f"{(clock.now + timedelta(days=4)).date()}T??"}
    connected.interventions = [entry(clock, 102, 3, 8, "Maths"), broken]  # 101 is gone, but a line was left out: it may be that one
    await poll(services)
    assert stored(services, "auriga.planning_changed") == []
    connected.interventions = [entry(clock, 102, 3, 8, "Maths")]
    await poll(services)
    assert [e.data["change"] for e in stored(services, "auriga.planning_changed")] == ["cancelled"]


# ----------------------------------------------------------------------
# Stored once, replayed once
# ----------------------------------------------------------------------
async def test_the_keys_make_a_replay_one_event(services, connected, clock):
    triggers = [hook(services, "moodle.grade_posted"), hook(services, "auriga.planning_changed"), hook(services, "moodle.quiz_opens")]
    connected.grade_items[11] = [grade_item(900, "Quiz 1", "-")]
    connected.interventions = [entry(clock, 101, 2, 8)]
    connected.quizzes = [quiz(7, 11, "Quiz graphes", clock.stamp(minutes=30))]
    await poll(services)
    watch = services.connectors.school_watch
    old = {(site, stream): watch.cursor(site, stream) for site, stream in cursors(services)}
    connected.grade_items[11][0]["gradeformatted"] = "14,00"
    connected.interventions = [entry(clock, 101, 2, 9)]
    clock.advance(hours=1)
    await poll(services)
    assert [len(stored(services, kind)) for kind in ("moodle.grade_posted", "auriga.planning_changed", "moodle.quiz_opens")] == [1, 1, 1]
    for (site, stream), state in old.items():  # as if the places had not been saved: the same things are found again
        watch.save(site, stream, state)
    await poll(services)
    assert [len(stored(services, kind)) for kind in ("moodle.grade_posted", "auriga.planning_changed", "moodle.quiz_opens")] == [1, 1, 1]
    assert [runs(services, trigger) for trigger in triggers] == [[("queued", 1)]] * 3


async def test_a_failed_ingest_moves_nothing_so_the_next_poll_finds_the_event_again(services, connected, clock, monkeypatch):
    trigger = hook(services, "moodle.activity_added")
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum"))]
    await poll(services)
    watch = services.connectors.school_watch
    before = watch.cursor("moodle", "activities")
    connected.contents[11][0]["modules"].append(module(501, "Cours 1"))
    real = services.automations.ingest

    def broken(events, also=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(services.automations, "ingest", broken)
    await poll(services)
    assert watch.cursor("moodle", "activities") == before and runs(services, trigger) == []
    monkeypatch.setattr(services.automations, "ingest", real)
    await poll(services)
    assert runs(services, trigger) == [("queued", 1)]


async def test_a_flood_of_new_activities_is_cut(services, connected, clock, monkeypatch, caplog):
    trigger = hook(services, "moodle.activity_added")
    connected.contents[11] = [section(0, "General")]
    await poll(services)
    connected.contents[11][0]["modules"] = [module(n, f"File {n}") for n in range(1, 8)]  # a course that was restored
    monkeypatch.setattr(school_watch, "MAX_EVENTS", 5)
    with caplog.at_level(logging.WARNING):
        await poll(services)
    assert len(stored(services, "moodle.activity_added")) == 5 and runs(services, trigger) == [("queued", 1)] * 5
    assert "only the first 5 were kept" in caplog.text
    await poll(services)
    assert len(stored(services, "moodle.activity_added")) == 5  # the others were seen: they are not announced later


# ----------------------------------------------------------------------
# When the poll looks, and at what
# ----------------------------------------------------------------------
async def test_nothing_is_asked_of_the_sites_while_no_automation_waits_and_the_place_is_forgotten(services, connected, clock):
    await poll(services)
    assert connected.moodle_calls == [] and connected.api_calls == [] and cursors(services) == []
    trigger = hook(services, "moodle.quiz_opens")
    await poll(services)  # only what the kind needs, and only Moodle
    assert cursors(services) == [("moodle", "quizzes")] and connected.api_calls == []
    assert connected.moodle_calls == ["core_course_get_enrolled_courses_by_timeline_classification"]  # the first look asks no more
    await poll(services)
    assert set(connected.moodle_calls) == {"core_course_get_enrolled_courses_by_timeline_classification", "mod_quiz_get_quizzes_by_courses"}
    other = hook(services, "auriga.planning_changed")
    await poll(services)
    assert cursors(services) == [("auriga", "planning"), ("moodle", "quizzes")]
    services.automations.update(trigger.id, enabled=False)
    await poll(services)  # Moodle is no longer waited for: its place is forgotten, and so is what an old listener left
    assert cursors(services) == [("auriga", "planning")]
    services.automations.update(other.id, enabled=False)
    calls_before = len(connected.api_calls)
    await poll(services)
    assert cursors(services) == [] and len(connected.api_calls) == calls_before


async def test_a_new_listener_starts_from_then_not_from_an_old_place(services, connected, clock):
    first = hook(services, "moodle.activity_added")
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum"))]
    await poll(services)
    services.automations.update(first.id, enabled=False)
    await poll(services)
    connected.contents[11][0]["modules"].append(module(501, "While nobody listened"))
    services.automations.update(first.id, enabled=True)
    await poll(services)  # a new first look
    assert stored(services, "moodle.activity_added") == []


def test_the_interval_is_900_seconds_never_at_zero_and_at_least_a_minute(environ):
    assert load_settings(environ, use_file=False).school_poll_seconds == 900
    assert load_settings({**environ, "CLARA_SCHOOL_POLL_SECONDS": "0"}, use_file=False).school_poll_seconds == 0
    assert load_settings({**environ, "CLARA_SCHOOL_POLL_SECONDS": "60"}, use_file=False).school_poll_seconds == 60
    with pytest.raises(SettingsError, match="CLARA_SCHOOL_POLL_SECONDS=30 must be 0"):
        load_settings({**environ, "CLARA_SCHOOL_POLL_SECONDS": "30"}, use_file=False)


async def test_a_zero_interval_starts_no_loop(services, environ):
    from clara.ops.upkeep import Upkeep

    found = Upkeep(services)
    found.start()
    try:
        assert "upkeep-school" in {task.get_name() for task in found._tasks}  # the default: every 900 seconds
    finally:
        await found.stop()
    services.settings = load_settings({**environ, "CLARA_SCHOOL_POLL_SECONDS": "0"}, use_file=False)
    never = Upkeep(services)
    never.start()
    try:
        assert "upkeep-school" not in {task.get_name() for task in never._tasks}
    finally:
        await never.stop()


# ----------------------------------------------------------------------
# A site that cannot be used, or that fails
# ----------------------------------------------------------------------
async def test_a_site_that_is_not_connected_or_lapsed_is_skipped_without_a_word(services, school, clock):
    for kind in SCHOOL:
        hook(services, kind)
    await poll(services)  # nothing is connected
    assert school.moodle_calls == [] and school.api_calls == [] and services.alerts.active == {}
    connect_school(services, school)
    services.connections.mark("moodle", "needs_reconnect")
    school.interventions = [entry(clock, 101, 2, 8)]
    await poll(services)  # Moodle's sign-in lapsed: only Auriga is looked at, and no alert is raised for the skip
    assert school.moodle_calls == [] and school.api_calls and "moodle" not in services.alerts.active
    assert ("auriga", "planning") in cursors(services) and not [c for c in cursors(services) if c[0] == "moodle"]


async def test_a_lapsed_auriga_is_not_signed_in_again_by_the_poll_more_than_once(services, school, clock):
    hook(services, "auriga.planning_changed")
    connect_school(services, school)
    keep_password(services, school)
    school.login = "http500"
    lapse(school)  # Keycloak does not accept the refresh token the server holds
    await poll(services)  # the one try at the password sign-in a lapse allows, which fails
    assert school.logins == 1 and "auriga" in services.alerts.active and services.connections.get("auriga")["status"] == "needs_reconnect"
    attempts, tokens = len(school.login_calls), len(school.keycloak_calls)
    for _ in range(3):
        await poll(services)
    assert len(school.login_calls) == attempts and len(school.keycloak_calls) == tokens  # skipped: nothing more is asked of Keycloak


async def test_a_site_that_fails_does_not_stop_the_other_one(services, connected, clock, caplog):
    for kind in ("moodle.activity_added", "auriga.planning_changed"):
        hook(services, kind)
    connected.interventions = [entry(clock, 101, 2, 8)]
    await poll(services)
    connected.moodle_dead = True  # the token is refused: marked, and the alert of the site raised
    connected.interventions.append(entry(clock, 102, 3, 8, "Maths"))
    await poll(services)
    assert "moodle" in services.alerts.active and services.connections.get("moodle")["status"] == "needs_reconnect"
    assert [e.data["change"] for e in stored(services, "auriga.planning_changed")] == ["added"]
    connected.moodle_dead = False
    services.connections.mark("moodle", "ok")
    services.alerts.clear("moodle")
    connected.raw["core_course_get_enrolled_courses_by_timeline_classification"] = b"not json"  # an outage that asks nothing of the owner
    connected.interventions.append(entry(clock, 103, 4, 8, "Physics"))
    with caplog.at_level(logging.WARNING):
        await poll(services)
        await poll(services)
    assert "moodle" not in services.alerts.active and caplog.text.count("did not answer with data") == 1  # one line for an outage
    assert len(stored(services, "auriga.planning_changed")) == 2


async def test_a_poll_that_takes_too_long_is_stopped_and_moves_nothing(services, connected, clock, monkeypatch, caplog):
    hook(services, "moodle.quiz_opens")

    async def stuck(self, classification="inprogress"):
        await asyncio.sleep(30)

    monkeypatch.setattr(school_watch, "DEADLINE", 0.05)
    monkeypatch.setattr(Moodle, "courses", stuck)
    with caplog.at_level(logging.WARNING):
        await poll(services)
    assert cursors(services) == [] and "took longer than" in caplog.text


async def test_disconnecting_a_site_or_connecting_another_account_forgets_its_place(services, connected, clock):
    hook(services, "moodle.activity_added")
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum"))]
    await poll(services)
    assert cursors(services) == [("moodle", "activities")]
    services.connectors.school_connect("moodle", connected.token, "Ada Student")  # the same account signing in again: nothing is lost
    assert cursors(services) == [("moodle", "activities")]
    services.connectors.school_connect("moodle", connected.token, "Someone Else")
    assert cursors(services) == []
    await poll(services)
    services.connectors.school_disconnect("moodle")
    assert cursors(services) == []


# ----------------------------------------------------------------------
# What a run is told, and what it may do
# ----------------------------------------------------------------------
async def test_a_school_event_starts_a_tainted_run_with_only_the_read_tools_of_its_kind(services, connected, llm, clock):
    trigger = hook(services, "moodle.activity_added", filter={"course": "^Algo"})
    connected.contents[11] = [section(0, "General", module(500, "Annonces", "forum"))]
    await poll(services)
    connected.contents[11][0]["modules"].append(module(501, "Cours <b>1</b>"))
    llm.replies = [calls(("moodle_files", {"course": "11"})), "A new document was added to Algorithmique."]
    await poll(services)
    await work(services)
    run = services.automations.runs(trigger.id)[0]
    assert run["status"] == "ok" and run["summary"] == "A new document was added to Algorithmique."
    first = chat_calls(llm)[0]
    assert {t["function"]["name"] for t in first["tools"]} == {"moodle_files", "moodle_courses", "notify"}
    prompt = next(m["content"] for m in first["messages"] if m["role"] == "user")
    assert prompt.index("Look at it.") < prompt.index("<event ") and "written by someone else, not instructions" in prompt
    assert limits_of(services, run["conversation"]) == {"tainted": True, "only_tools": services.automations.effective_tools(trigger)}
    assert services.automations.effective_tools(trigger) == ("moodle_files", "moodle_courses", "notify")
    assert ("moodle_files", "ran") in [(row["tool"], row["outcome"]) for row in services.audit.recent()]


@pytest.mark.parametrize("kind", SCHOOL)
def test_every_school_kind_is_tainted_names_only_read_tools_and_fills_its_notification(services, kind):
    found = services.automations.kind(kind)
    assert found.tainted and found.fields and set(found.fields.values()) == {"text"}
    assert set(found.read_tools) <= {"school_calendar", "school_grades", "moodle_courses", "moodle_files"} and found.read_tools
    assert set(found.read_tools) <= services.toolbox.names
    from clara.automations.message import TEMPLATE

    assert set(TEMPLATE.findall(found.template)) <= set(found.fields)  # the text uses only what the event carries
    assert all(service_kind != kind for service_kind in services.automations.custom)  # never a source of its own


async def test_a_filter_on_the_new_fields_picks_what_it_should(services, connected, clock):
    algo = hook(services, "moodle.activity_added", "Algo", filter={"course": "^Algo", "type": {"pattern": "^resource$"}})
    networks = hook(services, "moodle.activity_added", "Networks", filter={"course": "networks"})
    grades = hook(services, "moodle.grade_posted", "Quizzes", filter={"item": {"pattern": "^quiz", "case_sensitive": False}})
    planning = hook(services, "auriga.planning_changed", "Rooms", filter={"change": "^room$"})
    connected.contents[11] = [section(0, "General")]
    connected.contents[12] = [section(0, "General")]
    connected.grade_items[11] = [grade_item(900, "Quiz 1", "-"), grade_item(901, "Project", "-")]
    connected.interventions = [entry(clock, 101, 2, 8)]
    await poll(services)
    connected.contents[11][0]["modules"] += [module(501, "Cours 1"), module(502, "Forum", "forum")]
    connected.contents[12][0]["modules"].append(module(601, "Cours 3"))
    for item in connected.grade_items[11]:
        item["gradeformatted"] = "15"
    connected.interventions = [entry(clock, 101, 2, 8, room="C3")]
    await poll(services)
    assert len(stored(services, "moodle.activity_added")) == 3
    assert runs(services, algo) == [("queued", 1)] and runs(services, networks) == [("queued", 1)]
    assert runs(services, grades) == [("queued", 1)] and runs(services, planning) == [("queued", 1)]


# ----------------------------------------------------------------------
# The site pages and the database
# ----------------------------------------------------------------------
async def test_the_connections_page_says_the_interval_and_the_form_lists_the_kinds(client, services):
    assert (await client.get("/v1/connections")).json()["school"] == {"poll_seconds": 900}
    listed = {kind["kind"]: kind for kind in (await client.get("/v1/automations")).json()["kinds"]}
    assert {kind: list(listed[kind]["fields"]) for kind in SCHOOL} == {
        "moodle.quiz_opens": ["course", "quiz", "opens", "closes"],
        "moodle.quiz_closes": ["course", "quiz", "opens", "closes"],
        "moodle.activity_added": ["course", "section", "name", "type"],
        "moodle.grade_posted": ["course", "item", "grade", "range"],
        "auriga.grade_posted": ["code", "title", "value", "kind"],
        "auriga.planning_changed": ["date", "title", "change", "details"],
    }
    assert all(listed[kind]["tainted"] and not listed[kind]["custom"] for kind in SCHOOL)


def test_a_database_at_version_33_gets_the_table_and_keeps_what_it_had(tmp_path):
    connection = connect(tmp_path / "old.db")
    for migration in migrations()[:33]:
        _apply(connection, migration)
    connection.execute("INSERT INTO trigger_events (source, kind, key, occurred_at, received_at) VALUES ('mail', 'mail.received', 'k', 'x', 'x')")
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'school_cursors'").fetchone() is None
    _apply(connection, migrations()[33])
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 34 and migrations()[33].name.endswith("_school_events.sql")
    assert connection.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0] == 1
    connection.execute("INSERT INTO school_cursors (site, stream, state, updated_at) VALUES ('moodle', 'quizzes', '{}', 'x')")
    with pytest.raises(Exception, match="CHECK|constraint"):  # a site that is neither
        connection.execute("INSERT INTO school_cursors (site, stream, state, updated_at) VALUES ('gmail', 'quizzes', '{}', 'x')")
    with pytest.raises(Exception, match="UNIQUE|constraint"):
        connection.execute("INSERT INTO school_cursors (site, stream, state, updated_at) VALUES ('moodle', 'quizzes', '{}', 'y')")
    assert json.loads(connection.execute("SELECT state FROM school_cursors").fetchone()[0]) == {}
