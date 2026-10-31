"""Automatic deletion of the conversations automations make: the scope, the limits (age, count, whichever comes first), what
is kept, an automation's own limits, the preview, the routes, Clara's tool and the upkeep loop."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clara.memory.retention import Limits, Own, RetentionError, check_own, effective
from clara.ops.upkeep import Upkeep
from clara.timekeeping import ScheduleError
from clara.timeutil import iso, utcnow
from clara.tools.base import ToolContext, TurnState
from clara.triggers import TriggerError

LATER = "2099-01-05T09:00"


def stamp(hours_ago: float) -> str:
    return iso(utcnow() - timedelta(hours=hours_ago))


def schedule(s, name: str = "Digest", **fields):
    fields.setdefault("per_run", True)
    return s.schedules.create(name, "Summarise.", LATER, timezone="Europe/Paris", **fields)


def hook(s, name: str = "Invoices", **fields):
    return s.triggers.create(name, "mail.received", prompt="Handle it.", filter={"subject": "invoice"}, **fields)


def run_of(s, owner, hours_ago: float, status: str = "ok", **conversation) -> str:
    """A conversation made by a run of `owner` (a schedule or a hook), idle for `hours_ago` hours."""
    pinned = conversation.pop("pinned", False)
    cid = s.conversations.create("server", title=f"run {hours_ago}h", **conversation).id
    s.db.execute("UPDATE conversations SET updated_at = ?, pinned = ? WHERE id = ?", (stamp(hours_ago), int(pinned), cid))
    if hasattr(owner, "per_run"):
        s.db.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id, started_at) VALUES (?, ?, ?, ?, ?)",
                     (owner.id, stamp(hours_ago), status, cid, stamp(hours_ago)))
    else:
        s.db.execute("INSERT INTO trigger_runs (trigger_id, event_ids, status, queued_at, conversation_id) VALUES (?, '[]', ?, ?, ?)",
                     (owner.id, status, stamp(hours_ago), cid))
    return cid


def left(s, *ids: str) -> list[bool]:
    return [s.conversations.get(cid) is not None for cid in ids]


def limits(s, days: int = 0, hours: int = 0, last: int = 0) -> None:
    s.runtime.set("automation_keep_days", days)
    s.runtime.set("automation_keep_hours", hours)
    s.runtime.set("automation_keep_last", last)


# ----------------------------------------------------------------------
# The rule
# ----------------------------------------------------------------------
def test_nothing_is_deleted_until_a_limit_is_set(services):
    mine = run_of(services, schedule(services), 5000)
    assert services.retention.sweep() == 0 and left(services, mine) == [True]


def test_the_age_limit_deletes_the_conversations_of_hook_runs_and_per_run_schedules(services):
    digest, invoices = schedule(services), hook(services)
    old_s, new_s = run_of(services, digest, 30), run_of(services, digest, 2)
    old_h, new_h = run_of(services, invoices, 47), run_of(services, invoices, 1)
    limits(services, days=1)
    assert services.retention.sweep() == 2
    assert left(services, old_s, new_s, old_h, new_h) == [False, True, False, True]
    # the run keeps its row; only its conversation link goes
    rows = services.db.all("SELECT conversation_id FROM schedule_runs WHERE schedule_id = ? ORDER BY id", (digest.id,))
    assert [row[0] for row in rows] == [None, new_s]


def test_days_and_hours_add_up(services):
    digest = schedule(services)
    young, old = run_of(services, digest, 29), run_of(services, digest, 31)
    limits(services, days=1, hours=6)
    services.retention.sweep()
    assert left(services, young, old) == [True, False]
    limits(services, hours=2)
    services.retention.sweep()
    assert left(services, young) == [False]


def test_the_count_limit_keeps_the_latest_of_each_automation(services):
    digest, invoices, other = schedule(services), hook(services), schedule(services, "Other")
    a = [run_of(services, digest, hours) for hours in (1, 2, 3, 4)]
    b = [run_of(services, invoices, hours) for hours in (1, 2, 3)]
    c = [run_of(services, other, 9)]
    limits(services, last=2)
    assert services.retention.sweep() == 3
    assert left(services, *a) == [True, True, False, False]
    assert left(services, *b) == [True, True, False]
    assert left(services, *c) == [True]


def test_whichever_comes_first(services):
    digest, invoices = schedule(services), hook(services)
    young = [run_of(services, digest, hours) for hours in (1, 2, 3, 4)]  # all young: only the count can delete the 4th
    mixed = [run_of(services, invoices, hours) for hours in (1, 50)]  # within the count: only the age can delete the 50 h one
    limits(services, days=2, last=3)
    services.retention.sweep()
    assert left(services, *young) == [True, True, True, False]
    assert left(services, *mixed) == [True, False]


def test_the_shared_conversation_of_a_schedule_a_chat_and_a_reminder_chat_are_not_in_scope(services):
    shared_schedule = schedule(services, "Shared", per_run=False)
    shared = services.conversations.create("server", kind="schedule", title="Shared").id
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (shared, shared_schedule.id))
    services.db.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id) VALUES (?, ?, 'ok', ?)",
                        (shared_schedule.id, stamp(900), shared))
    chat = services.conversations.create("web", title="A chat").id
    project = services.projects.create("Work").id
    in_project = services.conversations.create("web", title="Project chat", project_id=project).id
    for cid in (shared, chat, in_project):
        services.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (stamp(5000), cid))
    limits(services, days=1, last=1)
    assert services.retention.sweep() == 0
    assert left(services, shared, chat, in_project) == [True, True, True]


def test_a_schedule_that_stopped_running_one_per_run_keeps_its_old_ones_in_scope(services):
    digest = schedule(services)
    old = run_of(services, digest, 100)
    services.schedules.update(digest.id, per_run=False)
    limits(services, days=1)
    services.retention.sweep()
    assert left(services, old) == [False]


# ----------------------------------------------------------------------
# What is kept
# ----------------------------------------------------------------------
def test_pinned_archived_by_you_running_waiting_and_pending_ones_are_kept(services):
    digest, invoices = schedule(services), hook(services)
    pinned = run_of(services, digest, 100, pinned=True)
    mine = run_of(services, digest, 100)
    services.mirror.set_archived(mine, True, "you")
    running = run_of(services, digest, 100, status="running")
    waiting = run_of(services, digest, 100, status="waiting")
    hook_running = run_of(services, invoices, 100, status="running")
    asked = run_of(services, invoices, 100)
    services.db.execute(
        "INSERT INTO approvals (conversation_id, tool, arguments, args_digest, level, action_class, situation, summary, status,"
        " created_at, expires_at) VALUES (?, 'mail_send', '{}', 'd', 'write', 'mail', 'unattended', 'x', 'pending', ?, ?)",
        (asked, stamp(100), iso(utcnow() + timedelta(days=1))))
    plain = run_of(services, digest, 100)
    limits(services, days=1)
    assert services.retention.sweep() == 1
    assert left(services, pinned, mine, running, waiting, hook_running, asked, plain) == [True] * 6 + [False]


def test_archives_made_by_discord_or_a_history_switch_do_not_save_a_conversation(services):
    digest = schedule(services)
    by_discord, by_group = run_of(services, digest, 100), run_of(services, digest, 100)
    services.mirror.set_archived(by_discord, True, "discord")
    services.mirror.set_archived(by_group, True, "group")
    limits(services, days=1)
    assert services.retention.sweep() == 2


def test_a_kept_conversation_takes_its_place_in_the_count(services):
    digest = schedule(services)
    pinned, second, third = run_of(services, digest, 1, pinned=True), run_of(services, digest, 2), run_of(services, digest, 3)
    limits(services, last=2)
    services.retention.sweep()
    assert left(services, pinned, second, third) == [True, True, False]


def test_a_conversation_that_was_answered_counts_from_its_last_message(services):
    digest = schedule(services)
    answered = run_of(services, digest, 100, status="ok")
    services.conversations.touch(answered)  # your answer arrived in it
    limits(services, days=1)
    assert services.retention.sweep() == 0


def test_deleting_is_the_usual_deletion(services):
    digest = schedule(services)
    cid = run_of(services, digest, 100)
    services.conversations.add_turn(cid, "hello", [])
    services.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (stamp(100), cid))  # add_turn made it fresh
    fact, _ = services.facts.add("Likes short answers.", "model", conversation_id=cid)
    services.db.execute("INSERT INTO discord_threads (conversation_id, place_key, thread_id) VALUES (?, 'schedule:1', '777')", (cid,))
    limits(services, days=1)
    services.retention.sweep()
    assert services.db.value("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (cid,)) == 0
    assert services.db.value("SELECT thread_id FROM discord_trash") == "777"  # the bot removes the thread
    assert services.facts.get(fact.id).conversation_id is None  # what the automation learned stays


def test_a_disabled_automation_is_swept_too(services):
    digest = schedule(services)
    old = run_of(services, digest, 100)
    services.schedules.update(digest.id, enabled=False)
    limits(services, days=1)
    services.retention.sweep()
    assert left(services, old) == [False]


# ----------------------------------------------------------------------
# An automation's own limits
# ----------------------------------------------------------------------
def test_an_automations_own_age_replaces_the_global_age(services):
    follows = schedule(services, "Follows")
    never = schedule(services, "Never", keep_days=0, keep_hours=0)  # its own age: none
    shorter = schedule(services, "Short", keep_days=0, keep_hours=3)
    a, b, c = (run_of(services, one, 10) for one in (follows, never, shorter))
    limits(services, days=1)
    services.retention.sweep()
    assert left(services, a, b, c) == [True, True, False]


def test_an_automations_own_count_replaces_the_global_count(services):
    limits(services, last=1)
    follows, own, free = hook(services, "Follows"), hook(services, "Own", keep_last=2), hook(services, "Free", keep_last=0)
    ids = {one.name: [run_of(services, one, hours) for hours in (1, 2, 3)] for one in (follows, own, free)}
    services.retention.sweep()
    assert left(services, *ids["Follows"]) == [True, False, False]
    assert left(services, *ids["Own"]) == [True, True, False]
    assert left(services, *ids["Free"]) == [True, True, True]


def test_own_values_are_checked_and_stored_together():
    assert check_own() == Own(None, None, None)
    assert check_own(2, None, 5) == Own(2, 0, 5) and check_own(None, 4, None) == Own(0, 4, None)
    for bad in ({"days": -1}, {"days": 3651}, {"hours": 8761}, {"last": 10_001}, {"days": True}, {"last": "3"}):
        with pytest.raises(RetentionError):
            check_own(**bad)
    general = Limits(5, 0, 7)
    assert effective(Own(), general) == general
    assert effective(Own(0, 0, 0), general) == Limits(0, 0, 0)
    assert effective(Own(None, None, 2), general) == Limits(5, 0, 2)


def test_the_services_store_describe_and_reset_them(services):
    s = schedule(services, keep_days=2, keep_last=4)
    assert (s.keep_days, s.keep_hours, s.keep_last) == (2, 0, 4)
    assert services.schedules.describe(s)["keep_last"] == 4
    changed = services.schedules.update(s.id, keep_hours=5)
    assert (changed.keep_days, changed.keep_hours) == (2, 5)
    reset = services.schedules.update(s.id, keep_days=None, keep_hours=None, keep_last=None)
    assert (reset.keep_days, reset.keep_hours, reset.keep_last) == (None, None, None)
    with pytest.raises(ScheduleError):
        services.schedules.update(s.id, keep_days=-3)
    h = hook(services, keep_hours=36)
    assert (h.keep_days, h.keep_hours, h.keep_last) == (0, 36, None)
    assert services.triggers.update(h.id, keep_last=3).keep_last == 3
    assert services.triggers.update(h.id, keep_days=None, keep_hours=None).keep_hours is None
    with pytest.raises(TriggerError):
        services.triggers.update(h.id, keep_last=99999)


def test_the_knobs_refuse_nonsense(services):
    for key, value in (("automation_keep_days", -1), ("automation_keep_hours", "3"), ("automation_keep_last", 10_001)):
        with pytest.raises(ValueError):
            services.runtime.set(key, value)


# ----------------------------------------------------------------------
# The preview
# ----------------------------------------------------------------------
def test_the_preview_does_not_blame_a_change_for_what_is_already_going_to_be_deleted(services):
    digest, invoices = schedule(services, keep_days=0, keep_hours=3), hook(services)
    run_of(services, digest, 30), run_of(services, digest, 100), run_of(services, digest, 1)  # two are past its own, saved limit
    run_of(services, invoices, 50), run_of(services, invoices, 1)
    same = services.retention.preview(general=Limits(0, 0, 0))  # a global limit that changes nothing
    assert (same["would_delete"], same["more"]) == (2, 0)
    assert [(item["name"], item["count"], item["more"]) for item in same["automations"]] == [("Digest", 2, 0)]
    lower = services.retention.preview(general=Limits(1, 0, 0))  # the hook's 50 h run is new; the digest has its own age
    assert (lower["would_delete"], lower["more"]) == (3, 1)
    assert {(item["name"], item["count"], item["more"]) for item in lower["automations"]} == {("Digest", 2, 0), ("Invoices", 1, 1)}
    # for one automation: lowering what it has itself adds only what it did not delete before
    mine = services.retention.preview(only=("schedule", digest.id), own=check_own(0, 1, None))
    assert (mine["would_delete"], mine["more"]) == (3, 1)


def test_the_preview_counts_what_the_sweep_would_delete_and_deletes_nothing(services):
    digest, invoices = schedule(services), hook(services)
    ids = [run_of(services, digest, 30), run_of(services, digest, 2), run_of(services, invoices, 40)]
    found = services.retention.preview(general=Limits(1, 0, 0))
    assert found["would_delete"] == 2 and found["more"] == 2  # nothing was going to be deleted before
    assert {(item["kind"], item["name"], item["count"]) for item in found["automations"]} == {("schedule", "Digest", 1), ("hook", "Invoices", 1)}
    assert left(services, *ids) == [True] * 3 and services.retention.sweep() == 0  # nothing saved, nothing to delete
    only = services.retention.preview(only=("schedule", digest.id), own=check_own(0, 1, None))
    assert only["would_delete"] == 2 and [item["id"] for item in only["automations"]] == [digest.id]
    assert services.retention.preview_change("schedule", digest.id, keep_days=0, keep_hours=1)["would_delete"] == 2


async def test_the_preview_route(services, client):
    digest = schedule(services)
    run_of(services, digest, 30), run_of(services, digest, 2)
    found = (await client.get("/v1/retention/preview", params={"keep_days": 1})).json()
    assert found["would_delete"] == 1 and found["more"] == 1 and found["automations"][0]["name"] == "Digest"
    assert (await client.get("/v1/retention/preview")).json()["would_delete"] == 0  # the saved limits: off
    one = (await client.get("/v1/retention/preview", params={"kind": "schedule", "id": digest.id, "keep_days": 0, "keep_hours": 1})).json()
    assert one["would_delete"] == 2
    assert (await client.get("/v1/retention/preview", params={"kind": "schedule", "id": digest.id})).json()["would_delete"] == 0  # follows
    assert (await client.get("/v1/retention/preview", params={"kind": "schedule", "id": 99})).status_code == 404
    assert (await client.get("/v1/retention/preview", params={"kind": "hook"})).status_code == 422
    assert (await client.get("/v1/retention/preview", params={"keep_days": -2})).status_code == 422


async def test_the_routes_change_an_automations_limits_and_null_follows_again(services, client):
    digest, invoices = schedule(services), hook(services)
    patched = (await client.patch(f"/v1/schedules/{digest.id}", json={"keep_days": 3, "keep_hours": 0, "keep_last": 10})).json()
    assert (patched["keep_days"], patched["keep_hours"], patched["keep_last"]) == (3, 0, 10)
    follows = (await client.patch(f"/v1/schedules/{digest.id}", json={"keep_days": None, "keep_hours": None, "keep_last": None})).json()
    assert (follows["keep_days"], follows["keep_hours"], follows["keep_last"]) == (None, None, None)
    assert (await client.patch(f"/v1/schedules/{digest.id}", json={"keep_days": 99999})).status_code == 422
    made = (await client.post("/v1/triggers", json={"name": "Receipts", "on": "mail.received", "prompt": "x", "keep_last": 5})).json()
    assert made["keep_last"] == 5 and made["keep_days"] is None
    hooked = (await client.patch(f"/v1/triggers/{invoices.id}", json={"keep_hours": 12})).json()
    assert (hooked["keep_days"], hooked["keep_hours"]) == (0, 12)
    cleared = (await client.patch(f"/v1/triggers/{invoices.id}", json={"keep_days": None, "keep_hours": None})).json()
    assert cleared["keep_hours"] is None
    new = (await client.post("/v1/schedules", json={"name": "Weekly", "prompt": "x", "at": LATER, "keep_days": 7})).json()
    assert (new["keep_days"], new["keep_hours"]) == (7, 0)


# ----------------------------------------------------------------------
# Upkeep and Clara's tool
# ----------------------------------------------------------------------
async def test_the_upkeep_loop_runs_the_sweep(services):
    digest = schedule(services)
    old = run_of(services, digest, 100)
    limits(services, days=1)
    upkeep = Upkeep(services)
    upkeep.retention()
    assert left(services, old) == [False]


async def call(services, name: str, arguments: dict):
    context = ToolContext(services, TurnState("t1", "", "web", timezone="Europe/Paris"))
    return await services.toolbox.call(name, context, arguments, services.toolbox.names)


@pytest.fixture
def level_four(services):
    services.runtime.set("auto_level_default", 4)


async def test_the_request_says_how_many_conversations_a_change_deletes(services, level_four):
    digest = schedule(services)
    run_of(services, digest, 30), run_of(services, digest, 2)
    asked = await call(services, "automation_update", {"kind": "schedule", "id": digest.id, "keep_days": 1, "keep_hours": 0})
    assert asked.approval is not None
    assert "keep_days: the global setting → 1" in asked.approval.summary
    assert "deletes 1 more conversation of its runs for good" in asked.approval.summary
    quiet = await call(services, "automation_update", {"kind": "schedule", "id": digest.id, "keep_days": 30, "keep_hours": 0})
    assert "This deletes no conversation that the limits now saved would not delete." in quiet.approval.summary
    assert services.schedules.get(digest.id).keep_days is None  # nothing changes until you allow it
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(asked.approval.id, "allow_once", tester)
    changed = services.schedules.get(digest.id)
    assert (changed.keep_days, changed.keep_hours) == (1, 0)


async def test_the_tool_wants_the_age_whole_and_follows_with_minus_one(services, level_four):
    invoices = hook(services, keep_days=2, keep_hours=0)
    lonely = await call(services, "automation_update", {"kind": "hook", "id": invoices.id, "keep_days": 1})
    assert lonely.approval is None and "keep_days and keep_hours are one age" in lonely.text
    mixed = await call(services, "automation_update", {"kind": "hook", "id": invoices.id, "keep_days": -1, "keep_hours": 3})
    assert mixed.approval is None and "one age" in mixed.text
    out_of_range = await call(services, "automation_update", {"kind": "hook", "id": invoices.id, "keep_last": 20000})
    assert out_of_range.approval is None and "keep_last must be from 0" in out_of_range.text
    follow = await call(services, "automation_update", {"kind": "hook", "id": invoices.id, "keep_days": -1, "keep_hours": -1})
    assert "keep_days: 2 → the global setting" in follow.approval.summary
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(follow.approval.id, "allow_once", tester)
    assert services.triggers.get(invoices.id).keep_days is None


async def test_a_created_automation_can_carry_limits(services, level_four):
    asked = await call(services, "automation_create", {"kind": "schedule", "name": "Weekly", "prompt": "x", "at": LATER,
                                                         "per_run": True, "keep_days": 7, "keep_hours": 0, "keep_last": 4})
    assert "keep_last: 4" in asked.approval.summary
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(asked.approval.id, "allow_once", tester)
    made = services.schedules.listing()[0]
    assert (made.keep_days, made.keep_hours, made.keep_last, made.per_run) == (7, 0, 4, True)


def test_the_migration_adds_nullable_columns(services):
    for table in ("schedules", "triggers"):
        columns = {row["name"]: row for row in services.db.all(f"PRAGMA table_info({table})")}
        for name in ("keep_days", "keep_hours", "keep_last"):
            assert name in columns and columns[name]["notnull"] == 0 and columns[name]["dflt_value"] is None
