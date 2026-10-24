"""The Automations pack: Clara reads, creates, changes and deletes schedules and hooks; the gate asks you first."""

from __future__ import annotations

import json

import pytest

from clara.agent.turn import ChatRequest
from clara.packs import PACKS
from clara.packs.automations import automation_tools
from clara.tools.base import ToolContext, TurnState
from fakes import calls

NAMES = {"automation_list", "automation_create", "automation_update", "automation_delete"}
LATER = "2099-01-05T09:00"
SCHEDULE = {"kind": "schedule", "name": "Weekly review", "prompt": "Summarise my week.", "at": LATER, "repeat": "weekly"}
HOOK = {"kind": "hook", "name": "Invoices", "on": "mail.received", "prompt": "Add a task for each invoice.",
        "filter": {"subject": {"contains": "invoice"}}}


def context(services, **turn) -> ToolContext:
    return ToolContext(services, TurnState("t1", "", "web", timezone="Europe/Paris", **turn))


async def call(services, name: str, arguments: dict, **turn):
    return await services.toolbox.call(name, context(services, **turn), arguments, services.toolbox.names)


def allow(services, *tools: str) -> None:
    for tool in tools:
        services.rules.add("allow", tool=tool)


def asked(services) -> list:
    return services.approvals.list("pending")


# ----------------------------------------------------------------------
# The pack
# ----------------------------------------------------------------------
def test_the_pack_is_for_chats_only_and_off_in_a_new_conversation(services):
    pack = next(pack for pack in PACKS if pack.id == "automations")
    assert pack.where == "chat" and not pack.default and pack.tools == NAMES
    assert {tool.name for tool in automation_tools()} == NAMES
    assert "automations" in [item["id"] for item in services.packs.listing("chat")]
    assert "automations" not in [item["id"] for item in services.packs.listing("schedule")]
    assert "automations" not in services.packs.active(None)
    assert services.packs.check(["automations"], "chat") == ["automations"]
    with pytest.raises(ValueError, match="Not for a schedule"):
        services.packs.check(["automations"], "schedule")


async def test_the_tools_are_offered_only_where_the_pack_is_on(services, llm):
    cid = services.conversations.create("web", title="Plain").id
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="hello", client="web", conversation=cid))
    assert not NAMES & {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}
    services.conversations.update(cid, packs=["web", "automations"])
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="hello", client="web", conversation=cid))
    assert NAMES <= {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}


async def test_a_scheduled_run_is_never_offered_them_even_if_its_conversation_has_the_pack(services, llm):
    schedule = services.schedules.create("Run", "x", LATER, timezone="Europe/Paris")
    cid = services.conversations.create("web", title="Run", packs=["web", "automations"]).id
    services.db.execute(
        "INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id, started_at) VALUES (?, ?, 'running', ?, ?)",
        (schedule.id, "2099-01-05T08:00:00+00:00", cid, "2099-01-05T08:00:00+00:00"),
    )
    assert NAMES <= services.packs.hidden(services.conversations.get(cid))


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------
async def test_a_creation_asks_and_makes_nothing_until_you_allow_it(services):
    result = await call(services, "automation_create", SCHEDULE)
    assert result.approval is not None and not result.ran
    assert services.schedules.listing() == []
    summary = result.approval.summary
    assert summary.startswith("Create the schedule “Weekly review”")
    assert "first run: 2099-01-05T09:00+01:00" in summary and "Summarise my week." in summary
    assert (result.approval.level, result.approval.action_class) == ("write", "automation")

    tester = services.tokens.create("tester", "cli")[0]
    settled = await services.approvals.decide(result.approval.id, "allow_once", tester)
    assert settled.status == "done"
    made = services.schedules.listing()
    assert [(item.name, item.repeat, item.packs, item.enabled) for item in made] == [("Weekly review", "weekly", ("web",), True)]


async def test_allow_always_writes_a_rule_that_makes_the_next_one_silent(services):
    first = await call(services, "automation_create", SCHEDULE)
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(first.approval.id, "allow_always", tester)
    rule = services.rules.list()[0]
    assert (rule.effect, rule.tool, rule.target) == ("allow", "automation_create", "automations")
    second = await call(services, "automation_create", {**SCHEDULE, "name": "Another"})
    assert second.ran and len(services.schedules.listing()) == 2


async def test_the_request_shows_the_whole_prompt(services):
    prompt = "Line one.\n" + "x" * 5_000 + "\nTHE LAST WORDS"
    result = await call(services, "automation_create", {**SCHEDULE, "prompt": prompt})
    assert result.approval.summary.endswith("THE LAST WORDS")


async def test_unattended_it_is_refused_and_asks_nothing(services):
    result = await call(services, "automation_create", SCHEDULE, mode="unattended")
    assert result.text.startswith("Refused") and result.approval is None
    assert services.schedules.listing() == [] and asked(services) == []


async def test_a_rule_can_allow_it_but_a_tainted_turn_asks_whatever_the_rule_says(services):
    allow(services, "automation_create")
    clean = await call(services, "automation_create", SCHEDULE)
    assert clean.ran and clean.text.startswith("Created. schedule ") and len(services.schedules.listing()) == 1
    tainted = await call(services, "automation_create", {**SCHEDULE, "name": "Second"}, tainted=True)
    assert tainted.approval is not None and not tainted.ran
    assert len(services.schedules.listing()) == 1
    assert any("untrusted" in reason or "tainted" in reason for reason in [tainted.approval.reason])


async def test_reading_is_allowed_and_asks_nothing(services):
    result = await call(services, "automation_list", {})
    assert result.ran and result.text == "No automation." and asked(services) == []


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
async def test_the_list_says_what_each_one_does_and_one_is_read_in_full(services):
    schedule = services.schedules.create("Bilan", "Recap the day.", "2099-01-05T23:59", timezone="Europe/Paris", repeat="daily")
    hook = services.triggers.create("Invoices", "mail.received", prompt="Add a task.", filter={"subject": "invoice"})
    text = (await call(services, "automation_list", {})).text
    assert f"schedule {schedule.id} “Bilan”: every day at 23:59 (Europe/Paris); enabled" in text
    assert f"hook {hook.id} “Invoices”: on mail.received where " in text and "runs Clara with mail_search" in text
    assert (await call(services, "automation_list", {"kind": "hook"})).text.count("\n") == 0
    detail = json.loads((await call(services, "automation_list", {"kind": "schedule", "id": schedule.id})).text)
    assert detail["prompt"] == "Recap the day." and detail["id"] == schedule.id
    missing = await call(services, "automation_list", {"kind": "hook", "id": 999})
    assert missing.text.startswith("Error: No hook 999")


async def test_a_local_only_automation_is_read_in_full_only_from_a_local_only_conversation(services):
    secret = services.schedules.create("Private", "My private plan.", LATER, timezone="Europe/Paris", local_only=True)
    arguments = {"kind": "schedule", "id": secret.id}
    assert "My private plan." not in (await call(services, "automation_list", arguments)).text
    assert "My private plan." in (await call(services, "automation_list", arguments, local_only=True)).text


# ----------------------------------------------------------------------
# Creating
# ----------------------------------------------------------------------
async def test_a_hook_is_made_with_its_filter_and_the_safe_default_tools(services):
    allow(services, "automation_create")
    result = await call(services, "automation_create", HOOK)
    assert result.ran and result.text.startswith("Created. hook ")
    hook = services.triggers.listing()[0]
    assert (hook.name, hook.on, hook.filter, hook.action, hook.enabled) == (
        "Invoices", "mail.received", {"subject": {"contains": ["invoice"]}}, "run", True)
    assert hook.tools == () and "notify" in hook.effective_tools


async def test_the_request_for_a_hook_says_which_tools_it_gets(services):
    result = await call(services, "automation_create", HOOK)
    assert "tools: by default mail_search, mail_read, mail_attachment_read, notify" in result.approval.summary
    chosen = await call(services, "automation_create", {**HOOK, "tools": ["mail_search", "add_task"]})
    assert "tools: mail_search, add_task" in chosen.approval.summary


async def test_a_schedule_can_be_made_paused_with_packs_and_a_project(services):
    allow(services, "automation_create")
    project = services.projects.create("Home")
    result = await call(services, "automation_create", {**SCHEDULE, "enabled": False, "packs": ["web", "day_activity"],
                                                        "project": project.id, "deliver": "web", "weekdays": [0, 4]})
    assert result.ran, result.text
    made = services.schedules.listing()[0]
    assert (made.enabled, made.packs, made.project_id, made.deliver, made.weekdays) == (
        False, ("web", "day_activity"), project.id, "web", (0, 4))
    assert made.next_at is None


@pytest.mark.parametrize("arguments, error", [
    ({"kind": "thing", "name": "x"}, "kind must be schedule or hook"),
    ({"kind": "schedule", "name": "x", "prompt": "p"}, "needs `at`"),
    ({"kind": "schedule", "name": "x", "at": LATER}, "needs a prompt"),
    ({"kind": "schedule", "name": "x", "prompt": "p", "at": "2001-01-01T09:00"}, "already past"),
    ({"kind": "schedule", "name": "x", "prompt": "p", "at": LATER, "on": "mail.received"}, "not for a schedule"),
    ({"kind": "schedule", "name": "x", "prompt": "p", "at": LATER, "colour": "red"}, "Unknown field: colour"),
    ({"kind": "schedule", "name": "x", "prompt": "p", "at": LATER, "packs": ["ask_person", "automations"]}, "Not for"),
    ({"kind": "schedule", "name": "x", "prompt": "p", "at": LATER, "model": "nonsense"}, "provider"),
    ({"kind": "hook", "name": "x", "prompt": "p"}, "needs `on`"),
    ({"kind": "hook", "name": "x", "on": "mail.received", "prompt": "p", "filter": {"colour": "red"}}, "not a field"),
    ({"kind": "hook", "name": "x", "on": "mail.received"}, "needs a prompt"),
    ({"kind": "hook", "name": "x", "on": "mail.received", "prompt": "p", "tools": ["no_such_tool"]}, "matches no tool"),
    ({"kind": "hook", "name": "x", "on": "mail.received", "prompt": "p", "at": LATER}, "not for a hook"),
])
async def test_a_bad_creation_is_told_to_clara_before_you_are_asked(services, arguments, error):
    result = await call(services, "automation_create", arguments)
    assert result.text.startswith("Error:") and error in result.text, result.text
    assert result.approval is None and asked(services) == []


async def test_a_hook_name_already_taken_is_told_before_you_are_asked(services):
    services.triggers.create("Invoices", "mail.received", prompt="x")
    result = await call(services, "automation_create", HOOK)
    assert "exists already" in result.text and asked(services) == []


async def test_a_local_only_conversation_makes_local_only_automations_only(services):
    allow(services, "automation_create")
    result = await call(services, "automation_create", SCHEDULE, local_only=True)
    assert result.ran and services.schedules.listing()[0].local_only
    refused = await call(services, "automation_create", {**SCHEDULE, "name": "Open", "local_only": False}, local_only=True)
    assert "local only" in refused.text and len(services.schedules.listing()) == 1


# ----------------------------------------------------------------------
# Changing and deleting
# ----------------------------------------------------------------------
async def test_a_change_asks_and_shows_what_changes(services):
    schedule = services.schedules.create("Bilan", "Old words.", LATER, timezone="Europe/Paris")
    result = await call(services, "automation_update", {"kind": "schedule", "id": schedule.id, "name": "Review",
                                                        "prompt": "New words.", "per_run": True})
    assert result.approval is not None
    lines = result.approval.summary.splitlines()
    assert lines[0] == f"Change the schedule {schedule.id} “Bilan”"
    assert "name: Bilan → Review" in lines and "prompt: Old words. → New words." in lines and "per_run: no → yes" in lines
    assert services.schedules.get(schedule.id).name == "Bilan"
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(result.approval.id, "allow_once", tester)
    changed = services.schedules.get(schedule.id)
    assert (changed.name, changed.prompt, changed.per_run) == ("Review", "New words.", True)


async def test_a_schedule_is_paused_and_resumed(services):
    allow(services, "automation_update")
    schedule = services.schedules.create("Bilan", "x", LATER, timezone="Europe/Paris", repeat="daily")
    paused = await call(services, "automation_update", {"kind": "schedule", "id": schedule.id, "enabled": False})
    assert paused.ran and "paused" in paused.text and not services.schedules.get(schedule.id).enabled
    resumed = await call(services, "automation_update", {"kind": "schedule", "id": schedule.id, "enabled": True})
    assert resumed.ran and services.schedules.get(schedule.id).enabled and services.schedules.get(schedule.id).next_at


async def test_a_hook_is_changed_and_its_filter_cleared(services):
    allow(services, "automation_update")
    hook = services.triggers.create("Invoices", "mail.received", prompt="x", filter={"subject": "invoice"})
    one = await call(services, "automation_update", {"kind": "hook", "id": hook.id, "prompt": "y", "max_per_hour": 5})
    assert one.ran, one.text
    assert (services.triggers.get(hook.id).prompt, services.triggers.get(hook.id).max_per_hour) == ("y", 5)
    assert services.triggers.get(hook.id).filter  # untouched
    cleared = await call(services, "automation_update", {"kind": "hook", "id": hook.id, "filter": {}})
    assert cleared.ran and services.triggers.get(hook.id).filter == {}
    kind = await call(services, "automation_update", {"kind": "hook", "id": hook.id, "on": "github.push"})
    assert "cannot change" in kind.text


async def test_a_change_that_names_nothing_or_no_one_is_an_error_not_a_request(services):
    nothing = await call(services, "automation_update", {"kind": "schedule", "id": 1})
    assert "No schedule 1" in nothing.text
    schedule = services.schedules.create("Bilan", "x", LATER, timezone="Europe/Paris")
    empty = await call(services, "automation_update", {"kind": "schedule", "id": schedule.id})
    assert "Say what to change" in empty.text and asked(services) == []


async def test_a_deletion_is_destructive_and_asks(services):
    hook = services.triggers.create("Invoices", "mail.received", prompt="x")
    result = await call(services, "automation_delete", {"kind": "hook", "id": hook.id})
    assert result.approval is not None and result.approval.level == "destructive"
    assert result.approval.summary.startswith("Delete the hook ") and "Invoices" in result.approval.summary
    assert services.triggers.find(hook.id) is not None
    allow(services, "automation_delete")
    done = await call(services, "automation_delete", {"kind": "hook", "id": hook.id})
    assert done.ran and done.text == f"Deleted the hook {hook.id} “Invoices”." and services.triggers.find(hook.id) is None
    gone = await call(services, "automation_delete", {"kind": "hook", "id": hook.id})
    assert "No hook" in gone.text


# ----------------------------------------------------------------------
# In a conversation
# ----------------------------------------------------------------------
async def test_a_chat_creates_a_schedule_once_you_allow_it(services, llm):
    cid = services.conversations.create("web", title="Planning", packs=["web", "automations"]).id
    llm.replies = [calls(("automation_create", SCHEDULE)), "Done, every Monday."]
    tester = services.tokens.create("tester", "cli")[0]
    reply = ""
    async for event in services.agent.turn(ChatRequest(message="Every week, summarise my week", client="web", conversation=cid)):
        if event["type"] == "approval":
            await services.approvals.decide(event["approval"]["id"], "allow_once", tester)
        if event["type"] == "done":
            reply = event["reply"]
    assert reply == "Done, every Monday."
    assert [item.name for item in services.schedules.listing()] == ["Weekly review"]
    assert ("automation_create", "done") in [(row["tool"], row["outcome"]) for row in services.audit.recent()]
