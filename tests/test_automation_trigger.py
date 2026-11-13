"""The trigger of an automation at work: events and instants judged against the tree, what an event outside a window becomes,
what a run is told and may use by what started it, dates, notifications, the zone of a cron expression, and the Test dry run."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clara.automations import AutomationError, SourceEvent
from clara.automations.setup import limits_of
from test_automation_runs import Clock

PARIS = "Europe/Paris"
EVENING = {"type": "window", "from": "18:00", "to": "20:00", "days": [0, 1, 2, 3, 4]}
PUSH = {"type": "event", "kind": "github.push", "filter": {"branch": "^main$"}}
HOURLY = {"type": "cron", "expr": "0 * * * *"}


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-11-13T12:00")  # 13:00 in Paris, a Friday


@pytest.fixture
def s(services, clock):
    services.automations._clock = clock
    return services


def push(key: str, at: datetime, branch: str = "main") -> SourceEvent:
    return SourceEvent(source="github", kind="github.push", key=key, occurred_at=at.isoformat(), title=f"push {key}",
                       data={"repo": "o/r", "branch": branch, "author": "alice", "message": "fix", "created": False, "forced": False})


def made(s, when, name: str = "A", **fields):
    fields.setdefault("prompt", "Handle it.")
    return s.automations.create(name, when, timezone=PARIS, **fields)


def statuses(s, automation) -> list[tuple[str, int]]:
    return sorted((run["status"], run["event_count"]) for run in s.automations.runs(automation.id, 100))


async def settle(s, clock: Clock, at: str) -> None:
    clock.at(at)
    s.automations.fire_due()
    await s.automations.wait_running()


# ----------------------------------------------------------------------
# An event outside the window of an AND
# ----------------------------------------------------------------------
def test_an_event_outside_the_window_is_recorded_as_skipped_and_starts_no_run(s, clock):
    automation = made(s, {"type": "and", "of": [EVENING, PUSH]})
    assert s.automations.ingest([push("k1", clock.now)]) == 0  # 13:00 in Paris: before the window
    assert statuses(s, automation) == [("skipped", 1)] and len(s.automations.recent_events()) == 1  # stored, and recorded
    note = s.automations.runs(automation.id)[0]["note"]
    assert "outside 18:00-20:00" in note and "Fri 13:00" in note
    assert s.automations.ingest([push("k2", clock.now, branch="dev")]) == 0
    assert statuses(s, automation) == [("skipped", 1)]  # the filter refused it: not this automation's, so not recorded
    clock.at("2026-11-13T17:30")  # 18:30 in Paris
    assert s.automations.ingest([push("k3", clock.now)]) == 1
    assert statuses(s, automation) == [("queued", 1), ("skipped", 1)]
    clock.at("2026-11-14T17:30")  # a Saturday: the days of the window
    assert s.automations.ingest([push("k4", clock.now)]) == 0
    assert statuses(s, automation) == [("queued", 1), ("skipped", 1), ("skipped", 1)]


def test_an_event_is_judged_at_the_time_it_happened_not_when_it_was_found(s, clock):
    automation = made(s, {"type": "and", "of": [EVENING, PUSH]})
    clock.at("2026-11-13T17:30")  # the server comes back at 18:30, inside the window
    assert s.automations.ingest([push("k1", datetime(2026, 11, 13, 17, 5, tzinfo=UTC))]) == 1  # the push was at 18:05: inside
    assert statuses(s, automation) == [("queued", 1)]
    assert s.automations.ingest([push("k2", datetime(2026, 11, 13, 16, 55, tzinfo=UTC))]) == 0  # at 17:55 it was outside
    assert statuses(s, automation) == [("queued", 1), ("skipped", 1)]


# ----------------------------------------------------------------------
# A cron instant, a window, a date
# ----------------------------------------------------------------------
async def test_a_cron_instant_inside_a_window_fires_only_inside_it(s, llm, clock):
    automation = made(s, {"type": "and", "of": [HOURLY, EVENING]}, catch_up="all")
    llm.replies = ["a", "b", "c"]
    await settle(s, clock, "2026-11-13T17:00:30")  # 18:00:30 in Paris: the 18:00 instant is inside, the ones before are not
    assert [run["due_at"][11:16] for run in s.automations.runs(automation.id)] == ["17:00"]
    await settle(s, clock, "2026-11-13T19:30:00")  # 20:30: 19:00 is inside (found 1 h 30 late: a backlog, caught up), 20:00 is not
    due = [run["due_at"][11:16] for run in reversed(s.automations.runs(automation.id))]
    assert due == ["17:00", "18:00"] and all(run["status"] == "ok" for run in s.automations.runs(automation.id))
    assert not [run for run in s.automations.runs(automation.id) if run["status"] in ("skipped", "missed")]  # no noise for instants outside


async def test_a_date_fires_once_and_the_automation_has_nothing_next(s, llm, clock):
    automation = made(s, {"type": "once", "at": "2026-11-13T15:00"})  # 14:00 UTC
    assert automation.next_at == datetime(2026, 11, 13, 14, 0, tzinfo=UTC)
    llm.replies = ["Done."]
    await settle(s, clock, "2026-11-13T14:00:10")
    assert statuses(s, automation) == [("ok", 0)] and s.automations.get(automation.id).next_at is None
    await settle(s, clock, "2026-11-14T14:00:10")
    assert statuses(s, automation) == [("ok", 0)]  # once
    with pytest.raises(AutomationError, match="already past"):
        made(s, {"type": "once", "at": "2026-11-13T09:00"}, name="Late")
    assert s.automations.update(automation.id, prompt="Else.").prompt == "Else."  # a date that has passed is no reason to refuse an edit


async def test_a_notification_at_a_time_needs_no_model(s, clock):
    automation = made(s, {"type": "cron", "expr": "0 15 * * *"}, action="notify", prompt="Take the pills.")
    await settle(s, clock, "2026-11-13T14:00:10")
    [event] = s.events.recent(5, ["notification"])
    assert (event.title, event.text, event.targets, event.source) == ("A", "Take the pills.", (), "triggers")
    assert statuses(s, automation) == [("ok", 0)]


def test_a_cron_expression_follows_the_clock_of_the_zone_through_the_change_of_season(s, clock):
    clock.at("2026-03-28T12:00")
    automation = s.automations.create("Night", {"type": "cron", "expr": "30 2 * * *"}, prompt="x", timezone=PARIS)
    assert automation.next_at == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)  # 02:30 does not exist on the 29th: 03:30 CEST
    tokyo = s.automations.create("Tokyo", {"type": "cron", "expr": "0 9 * * *"}, prompt="x", timezone="Asia/Tokyo")
    assert tokyo.next_at == datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
    assert s.automations.describe(tokyo)["next_local"] == "2026-03-29T09:00+09:00"


# ----------------------------------------------------------------------
# What started the run decides what it is told and may use
# ----------------------------------------------------------------------
async def test_a_run_is_tainted_and_limited_by_what_started_it(s, llm, clock):
    automation = made(s, {"type": "or", "of": [PUSH, {"type": "cron", "expr": "0 15 * * *"}]}, per_run=True)
    llm.replies = ["Pushed.", "Ticked."]
    s.automations.ingest([push("k1", clock.now)])
    await settle(s, clock, "2026-11-13T14:00:10")
    await settle(s, clock, "2026-11-13T14:00:10")  # the run of the instant waits for the push's run to end
    by_push, by_time = [run for run in reversed(s.automations.runs(automation.id))]
    assert (by_push["event_count"], by_time["event_count"]) == (1, 0)
    assert limits_of(s, by_push["conversation"]) == {"tainted": True, "only_tools": ("github_read", "notify")}
    assert limits_of(s, by_time["conversation"]) == {"only_tools": ("notify",)}  # an instant taints nothing and has notify alone
    offered = [{tool["function"]["name"] for tool in call["tools"]} for call in llm.seen if call["tools"]]
    assert offered[0] <= {"github_read", "notify"} and offered[-1] == {"notify"}
    s.automations.update(automation.id, tools=["notify", "remember"])
    assert limits_of(s, by_time["conversation"]) == {"only_tools": ("notify", "remember")}  # its own list, whatever started the run


async def test_listed_packs_decide_the_tools_as_a_schedule_always_did(s, llm, clock):
    automation = made(s, {"type": "cron", "expr": "0 15 * * *"}, packs=["web"], per_run=True)
    llm.replies = ["ok"]
    await settle(s, clock, "2026-11-13T14:00:10")
    run = s.automations.runs(automation.id)[0]
    assert limits_of(s, run["conversation"]) == {}  # no restriction: every tool of its packs
    assert "web_search" in {tool["function"]["name"] for call in llm.seen for tool in call["tools"]}


async def test_running_it_now_is_no_event(s, llm, clock):
    automation = made(s, PUSH, per_run=True)
    llm.replies = ["ok"]
    s.automations.run_now(automation.id)
    await s.automations.wait_running()
    run = s.automations.runs(automation.id)[0]
    assert run["status"] == "ok" and run["event_count"] == 0
    assert limits_of(s, run["conversation"]) == {"only_tools": ("notify",)}  # nothing that fired it: not tainted, never more than notify


# ----------------------------------------------------------------------
# Defaults, changes, limits
# ----------------------------------------------------------------------
def test_what_an_automation_does_when_not_told_follows_its_trigger(s):
    on_event = made(s, PUSH, name="Event")
    at_time = made(s, HOURLY, name="Time")
    both = made(s, {"type": "or", "of": [PUSH, HOURLY]}, name="Both")
    pick = lambda a: (a.per_run, a.catch_up, a.overlap, a.rerun_interrupted)  # noqa: E731
    assert pick(on_event) == pick(both) == (True, "all", "queue", False)  # what a hook was
    assert pick(at_time) == (False, "skip", "skip", True)  # what a schedule was
    assert pick(made(s, HOURLY, name="Own", per_run=True, catch_up="latest", overlap="replace", rerun_interrupted=False)) == (
        True, "latest", "replace", False)


def test_saving_the_trigger_unchanged_keeps_the_place_it_had_reached_and_changing_it_starts_from_now(s, clock):
    automation = made(s, HOURLY)
    reached = automation.reached_at
    clock.at("2026-11-13T15:00")
    same = s.automations.update(automation.id, when=HOURLY, prompt="Again.", timezone=PARIS)
    assert same.reached_at == reached  # the form sends everything each time: nothing was replayed or lost
    changed = s.automations.update(automation.id, when={"type": "cron", "expr": "30 * * * *"})
    assert changed.reached_at == clock.now and changed.next_at == datetime(2026, 11, 13, 15, 30, tzinfo=UTC)
    paused = s.automations.update(automation.id, enabled=False)
    assert paused.next_at is None
    clock.at("2026-11-13T18:00")
    assert s.automations.update(automation.id, enabled=True).reached_at == clock.now  # resuming never replays what it missed


async def test_the_pace_limits_instants_too(s, llm, clock):
    automation = made(s, {"type": "cron", "expr": "* * * * *"}, max_per_hour=2, packs=["web"], overlap="queue", per_run=True)
    llm.replies = ["ok"] * 10
    for minute in range(1, 6):
        await settle(s, clock, f"2026-11-13T12:0{minute}:10")
    done = [run for run in s.automations.runs(automation.id, 50) if run["status"] == "ok"]
    queued = [run for run in s.automations.runs(automation.id, 50) if run["status"] == "queued"]
    assert len(done) == 2 and len(queued) == 3  # two in the hour, the rest wait their turn
    assert s.automations.next_due() is not None and s.automations.next_due() >= clock.now


def test_a_digest_is_for_events_and_an_instant_found_late_counts_as_the_latest(s):
    with pytest.raises(AutomationError, match="needs an event"):
        made(s, HOURLY, catch_up="digest")
    assert made(s, {"type": "or", "of": [PUSH, HOURLY]}, name="Mixed", catch_up="digest").catch_up == "digest"


# ----------------------------------------------------------------------
# The dry run
# ----------------------------------------------------------------------
def test_the_dry_run_says_branch_by_branch_whether_the_trigger_holds_and_why(s, clock):
    automation = made(s, {"type": "or", "of": [{"type": "and", "of": [EVENING, PUSH]}, {"type": "cron", "expr": "0 9 * * *"}]})
    inside = s.automations.test(automation, push("k", datetime(2026, 11, 13, 17, 30, tzinfo=UTC)))
    assert inside["matches"] and inside["why_not"] == "" and inside["tainted"]
    [window, event] = inside["tree"]["of"][0]["of"]
    assert window["ok"] and "inside 18:00-20:00" in window["why"] and event["ok"] and "matches /^main$/" in event["why"]
    assert inside["tree"]["of"][1]["ok"] is False  # the cron branch is for instants
    outside = s.automations.test(automation, push("k", datetime(2026, 11, 13, 10, 0, tzinfo=UTC)))
    assert not outside["matches"] and outside["why_not"].startswith("It is outside the time conditions: Fri 11:00 is outside 18:00-20:00")
    wrong = s.automations.test(automation, push("k", clock.now, branch="dev"))
    assert wrong["why_not"] == "The trigger does not match." and not wrong["tree"]["of"][0]["of"][1]["ok"]
    moment = s.automations.test(automation, None, datetime(2026, 11, 14, 8, 0, tzinfo=UTC))  # an instant: 09:00 on a Saturday
    assert moment["matches"] and not moment["tainted"] and moment["tools"] == ["notify"]
    assert not s.automations.test(automation, None, datetime(2026, 11, 14, 9, 0, tzinfo=UTC))["matches"]
    sample = SourceEvent(source="github", kind="github.push", key="s", occurred_at="", title="x", data={"branch": "main"})
    assert s.automations.test(automation, sample)["at"]  # a sample with no time is judged now (13:00: outside)


async def test_the_dry_run_route_judges_an_instant_and_a_moment_given_in_the_zone_of_the_automation(client, s):
    made_ = (await client.post("/v1/automations", json={"name": "Evening", "prompt": "x", "when": {"type": "and", "of": [EVENING, HOURLY]},
                                                          "timezone": PARIS})).json()
    shown = (await client.post(f"/v1/automations/{made_['id']}/test", json={"at": "2026-11-13T19:00"})).json()  # 19:00 in Paris
    assert shown["matches"] and shown["event"] is None and shown["tree"]["of"][0]["of"][0]["ok"] and not shown["tainted"]
    assert not (await client.post(f"/v1/automations/{made_['id']}/test", json={"at": "2026-11-13T21:00"})).json()["matches"]
    assert not (await client.post(f"/v1/automations/{made_['id']}/test", json={"at": "2026-11-13T19:30"})).json()["matches"]  # not on the hour
    assert (await client.post(f"/v1/automations/{made_['id']}/test", json={"at": "yesterday-ish"})).status_code == 422
    sample = (await client.post(f"/v1/automations/{made_['id']}/test", json={"event": {"kind": "github.push"}})).json()
    assert not sample["matches"] and "waits for instants" in sample["why_not"]
    assert made_["kinds"] == [] and made_["when_words"] == "between 18:00 and 20:00 (Mon, Tue, Wed, Thu, Fri) and at cron “0 * * * *”"


def test_two_automations_that_shared_a_name_before_the_conversion_can_still_be_edited(s):
    one, two = made(s, PUSH, name="One"), made(s, HOURLY, name="Two")
    made(s, {"type": "once", "at": "2099-01-01T09:00"}, name="Third")
    s.db.execute("UPDATE automations SET name = 'Same' WHERE id IN (?, ?)", (one.id, two.id))  # as two old schedules could
    assert s.automations.update(two.id, prompt="Again.").prompt == "Again."  # the name did not change: nothing to refuse
    with pytest.raises(AutomationError, match="exists already"):
        s.automations.update(two.id, name="Third")
    assert s.automations.update(two.id, name="Fourth").name == "Fourth"
