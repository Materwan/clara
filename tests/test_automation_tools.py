"""The Automations pack: Clara reads, creates, changes and deletes automations, with one `when` argument; the gate asks first."""

from __future__ import annotations

import json

import pytest

from clara.agent.turn import ChatRequest
from clara.packs import PACKS
from clara.packs.automations import automation_tools
from clara.tools.base import ToolContext, TurnState
from fakes import calls
from modeset import at_level, default, line

NAMES = {"automation_list", "automation_create", "automation_update", "automation_delete"}
LATER = "2099-01-05T09:00"
WEEKLY = {"type": "cron", "expr": "0 9 * * 1"}
SCHEDULE = {"name": "Weekly review", "prompt": "Summarise my week.", "when": WEEKLY, "packs": ["web"]}
INVOICE = {"type": "event", "kind": "mail.received", "filter": {"subject": {"pattern": "invoice"}}}
HOOK = {"name": "Invoices", "prompt": "Add a task for each invoice.", "when": INVOICE}


@pytest.fixture(autouse=True)
def level_four(services):
    """The automation tools ask in the default mode here (what the old level 4 did)."""
    at_level(services, 4)


def context(services, **turn) -> ToolContext:
    return ToolContext(services, TurnState("t1", "", "web", timezone="Europe/Paris", **turn))


async def call(services, name: str, arguments: dict, **turn):
    return await services.toolbox.call(name, context(services, **turn), arguments, services.toolbox.names)


def allow(services, *tools: str) -> None:
    """A mode that approves these tools, but asks about them in a tainted turn."""
    for tool in tools:
        default(services, line("approve", tool, tainted="ask"))


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
    assert "automations" not in [item["id"] for item in services.packs.listing("automation")]
    assert "automations" not in services.packs.active(None)
    assert services.packs.check(["automations"], "chat") == ["automations"]
    with pytest.raises(ValueError, match="Not for an automation"):
        services.packs.check(["automations"], "automation")


async def test_the_tools_are_offered_only_where_the_pack_is_on(services, llm):
    cid = services.conversations.create("web", title="Plain").id
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="hello", client="web", conversation=cid))
    assert not NAMES & {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}
    services.conversations.update(cid, packs=["web", "automations"])
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="hello", client="web", conversation=cid))
    assert NAMES <= {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}


async def test_a_run_is_never_offered_them_even_if_its_conversation_has_the_pack(services, llm):
    schedule = services.automations.create("Run", {"type": "once", "at": LATER}, prompt="x", timezone="Europe/Paris")
    cid = services.conversations.create("web", title="Run", packs=["web", "automations"]).id
    services.db.execute(
        "INSERT INTO automation_runs (automation_id, due_at, queued_at, status, conversation_id, started_at) VALUES (?, ?, ?, 'running', ?, ?)",
        (schedule.id, "2099-01-05T08:00:00+00:00", "2099-01-05T08:00:00+00:00", cid, "2099-01-05T08:00:00+00:00"),
    )
    assert NAMES <= services.packs.hidden(services.conversations.get(cid))


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------
async def test_a_creation_asks_and_makes_nothing_until_you_allow_it(services):
    result = await call(services, "automation_create", SCHEDULE)
    assert result.approval is not None and not result.ran
    assert services.automations.listing() == []
    summary = result.approval.summary
    assert summary.startswith("Create the automation “Weekly review”")
    assert "when: at cron “0 9 * * 1”" in summary and "timezone: Europe/Paris" in summary and "Summarise my week." in summary
    assert (result.approval.level, result.approval.action_class) == ("write", "automation")

    tester = services.tokens.create("tester", "cli")[0]
    settled = await services.approvals.decide(result.approval.id, "allow_once", tester)
    assert settled.status == "done"
    made = services.automations.listing()
    assert [(item.name, item.tree["of"][0]["expr"], item.packs, item.enabled) for item in made] == [("Weekly review", "0 9 * * 1", ("web",), True)]


async def test_an_answer_makes_no_rule_so_the_next_one_asks_again(services):
    first = await call(services, "automation_create", SCHEDULE)
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(first.approval.id, "allow_once", tester)
    second = await call(services, "automation_create", {**SCHEDULE, "name": "Another"})
    assert second.approval is not None and not second.ran and len(services.automations.listing()) == 1


async def test_the_request_shows_the_whole_prompt(services):
    prompt = "Line one.\n" + "x" * 5_000 + "\nTHE LAST WORDS"
    result = await call(services, "automation_create", {**SCHEDULE, "prompt": prompt})
    assert result.approval.summary.endswith("THE LAST WORDS")


async def test_unattended_it_is_refused_and_asks_nothing(services):
    at_level(services, 3)  # the default mode: an automation is not made by a run
    result = await call(services, "automation_create", SCHEDULE, mode="unattended")
    assert result.text.startswith("Refused") and result.approval is None
    assert services.automations.listing() == [] and asked(services) == []


async def test_a_mode_can_approve_it_but_a_tainted_turn_asks_when_the_mode_says_so(services):
    allow(services, "automation_create")
    clean = await call(services, "automation_create", SCHEDULE)
    assert clean.ran and clean.text.startswith("Created. automation ") and len(services.automations.listing()) == 1
    tainted = await call(services, "automation_create", {**SCHEDULE, "name": "Second"}, tainted=True)
    assert tainted.approval is not None and not tainted.ran
    assert len(services.automations.listing()) == 1
    assert any("untrusted" in reason or "tainted" in reason for reason in [tainted.approval.reason])


async def test_reading_is_allowed_and_asks_nothing(services):
    result = await call(services, "automation_list", {})
    assert result.ran and result.text == "No automation." and asked(services) == []


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
async def test_the_list_says_what_each_one_does_and_one_is_read_in_full(services):
    schedule = services.automations.create("Bilan", {"type": "cron", "expr": "59 23 * * *"}, prompt="Recap the day.", timezone="Europe/Paris")
    hook = services.automations.create("Invoices", INVOICE, prompt="Add a task.")
    text = (await call(services, "automation_list", {})).text
    assert f"automation {schedule.id} “Bilan”: at cron “59 23 * * *” (Europe/Paris); runs Clara with notify; enabled" in text
    assert f"automation {hook.id} “Invoices”: a mail where subject matches “invoice” (Europe/Paris);" in text and "runs Clara with mail_search" in text
    detail = json.loads((await call(services, "automation_list", {"id": schedule.id})).text)
    assert detail["prompt"] == "Recap the day." and detail["id"] == schedule.id
    assert detail["when"]["of"][0]["expr"] == "59 23 * * *" and detail["when_words"] == "at cron “59 23 * * *”"
    missing = await call(services, "automation_list", {"id": 999})
    assert missing.text.startswith("Error: No automation 999")


async def test_a_local_only_automation_is_read_in_full_only_from_a_local_only_conversation(services):
    secret = services.automations.create("Private", {"type": "once", "at": LATER}, prompt="My private plan.", timezone="Europe/Paris", local_only=True)
    arguments = {"id": secret.id}
    assert "My private plan." not in (await call(services, "automation_list", arguments)).text
    assert "My private plan." in (await call(services, "automation_list", arguments, local_only=True)).text


# ----------------------------------------------------------------------
# Creating
# ----------------------------------------------------------------------
async def test_an_automation_on_mail_is_made_with_its_filter_and_the_safe_default_tools(services):
    allow(services, "automation_create")
    result = await call(services, "automation_create", HOOK)
    assert result.ran and result.text.startswith("Created. automation ")
    hook = services.automations.listing()[0]
    assert (hook.name, hook.kinds, hook.action, hook.enabled) == ("Invoices", ("mail.received",), "run", True)
    assert hook.tree["of"][0]["filter"] == {"subject": {"pattern": "invoice", "not": False, "case_sensitive": False}}
    assert hook.tools == () and "notify" in services.automations.effective_tools(hook) and hook.per_run


async def test_the_request_says_when_in_words_and_which_tools_it_gets(services):
    result = await call(services, "automation_create", HOOK)
    assert "when: a mail where subject matches “invoice”" in result.approval.summary
    assert "tools: by default mail_search, mail_read, mail_attachment_read, notify" in result.approval.summary
    chosen = await call(services, "automation_create", {**HOOK, "tools": ["mail_search", "add_task"]})
    assert "tools: mail_search, add_task" in chosen.approval.summary
    tree = {"type": "or", "of": [{"type": "and", "of": [{"type": "window", "from": "18:00", "to": "20:00"}, {"type": "event", "kind": "github.push", "filter": {"branch": "^main$"}}]}, WEEKLY]}
    both = await call(services, "automation_create", {**HOOK, "name": "Evening pushes", "when": json.dumps(tree)})  # the JSON text works too
    assert ("when: (between 18:00 and 20:00 (every day) and a push where branch matches “^main$”) or at cron “0 9 * * 1”"
            in both.approval.summary)


async def test_an_automation_can_be_made_paused_with_packs_and_a_project(services):
    allow(services, "automation_create")
    project = services.projects.create("Home")
    result = await call(services, "automation_create", {**SCHEDULE, "enabled": False, "packs": ["web", "day_activity"],
                                                        "project": project.id, "in_history": True,
                                                        "when": {"type": "cron", "expr": "0 9 * * mon,fri"}})
    assert result.ran, result.text
    made = services.automations.listing()[0]
    assert (made.enabled, made.packs, made.project_id, made.in_history) == (False, ("web", "day_activity"), project.id, True)
    assert made.next_at is None


@pytest.mark.parametrize("arguments, error", [
    ({"name": "x", "prompt": "p"}, "needs `when`"),
    ({"name": "x", "when": WEEKLY}, "needs a prompt"),
    ({"name": "x", "prompt": "p", "when": {"type": "once", "at": "2001-01-01T09:00"}}, "already past"),
    ({"name": "x", "prompt": "p", "when": WEEKLY, "at": LATER}, "Unknown field: at"),
    ({"name": "x", "prompt": "p", "when": WEEKLY, "colour": "red"}, "Unknown field: colour"),
    ({"name": "x", "prompt": "p", "when": WEEKLY, "deliver": "web"}, "Unknown field: deliver"),
    ({"name": "x", "prompt": "p", "when": WEEKLY, "packs": ["ask_person", "automations"]}, "Not for"),
    ({"name": "x", "prompt": "p", "when": WEEKLY, "model": "nonsense"}, "provider"),
    ({"name": "x", "prompt": "p", "when": "not json"}, "when must be the trigger as JSON"),
    ({"name": "x", "prompt": "p", "when": {"type": "event", "kind": "mail.received", "filter": {"colour": "red"}}}, "not a field"),
    ({"name": "x", "prompt": "p", "when": {"type": "event", "kind": "mail.received", "filter": {"subject": "(?=x)"}}}, "lookahead"),
    ({"name": "x", "prompt": "p", "when": {"type": "and", "of": [INVOICE, {"type": "event", "kind": "github.push"}]}}, "at most one event"),
    ({"name": "x", "prompt": "p", "when": {"type": "window", "from": "18:00", "to": "20:00"}}, "holds no event"),
    ({"name": "x", "prompt": "p", "when": {"type": "cron", "expr": "0 9 * *"}}, "five fields"),
    ({"name": "x", "when": INVOICE}, "needs a prompt"),
    ({"name": "x", "prompt": "p", "when": INVOICE, "tools": ["no_such_tool"]}, "matches no tool"),
])
async def test_a_bad_creation_is_told_to_clara_before_you_are_asked(services, arguments, error):
    result = await call(services, "automation_create", arguments)
    assert result.text.startswith("Error:") and error in result.text, result.text
    assert result.approval is None and asked(services) == []


async def test_a_name_already_taken_is_told_before_you_are_asked(services):
    services.automations.create("Invoices", INVOICE, prompt="x")
    result = await call(services, "automation_create", HOOK)
    assert "exists already" in result.text and asked(services) == []


async def test_a_local_only_conversation_makes_local_only_automations_only(services):
    allow(services, "automation_create")
    result = await call(services, "automation_create", SCHEDULE, local_only=True)
    assert result.ran and services.automations.listing()[0].local_only
    refused = await call(services, "automation_create", {**SCHEDULE, "name": "Open", "local_only": False}, local_only=True)
    assert "local only" in refused.text and len(services.automations.listing()) == 1


# ----------------------------------------------------------------------
# Changing and deleting
# ----------------------------------------------------------------------
async def test_a_change_asks_and_shows_what_changes(services):
    schedule = services.automations.create("Bilan", {"type": "cron", "expr": "0 9 * * *"}, prompt="Old words.", timezone="Europe/Paris")
    result = await call(services, "automation_update", {"id": schedule.id, "name": "Review", "prompt": "New words.",
                                                        "per_run": True, "when": {"type": "cron", "expr": "30 9 * * 1-5"}})
    assert result.approval is not None
    lines = result.approval.summary.splitlines()
    assert lines[0] == f"Change the automation {schedule.id} “Bilan”"
    assert "name: Bilan → Review" in lines and "prompt: Old words. → New words." in lines and "per_run: no → yes" in lines
    assert "when: at cron “0 9 * * *” → at cron “30 9 * * 1-5”" in lines
    assert services.automations.get(schedule.id).name == "Bilan"
    tester = services.tokens.create("tester", "cli")[0]
    await services.approvals.decide(result.approval.id, "allow_once", tester)
    changed = services.automations.get(schedule.id)
    assert (changed.name, changed.prompt, changed.per_run, changed.tree["of"][0]["expr"]) == ("Review", "New words.", True, "30 9 * * 1-5")
    same = await call(services, "automation_update", {"id": schedule.id, "when": {"type": "cron", "expr": "30 9 * * 1-5"}, "name": "Review"})
    assert "(nothing changes)" in same.approval.summary  # a trigger written the same is not a change


async def test_an_automation_is_paused_and_resumed(services):
    allow(services, "automation_update")
    schedule = services.automations.create("Bilan", {"type": "cron", "expr": "0 9 * * *"}, prompt="x", timezone="Europe/Paris")
    paused = await call(services, "automation_update", {"id": schedule.id, "enabled": False})
    assert paused.ran and "paused" in paused.text and not services.automations.get(schedule.id).enabled
    resumed = await call(services, "automation_update", {"id": schedule.id, "enabled": True})
    assert resumed.ran and services.automations.get(schedule.id).enabled and services.automations.get(schedule.id).next_at


async def test_an_automation_is_changed_and_its_filter_cleared(services):
    allow(services, "automation_update")
    hook = services.automations.create("Invoices", INVOICE, prompt="x")
    one = await call(services, "automation_update", {"id": hook.id, "prompt": "y", "max_per_hour": 5})
    assert one.ran, one.text
    assert (services.automations.get(hook.id).prompt, services.automations.get(hook.id).max_per_hour) == ("y", 5)
    assert services.automations.get(hook.id).tree["of"][0]["filter"]  # untouched
    cleared = await call(services, "automation_update", {"id": hook.id, "when": {"type": "event", "kind": "mail.received", "filter": {}}})
    assert cleared.ran and services.automations.get(hook.id).tree["of"][0]["filter"] == {}
    bad = await call(services, "automation_update", {"id": hook.id, "when": {"type": "and", "of": [INVOICE, {"type": "event", "kind": "github.push"}]}})
    assert "at most one event" in bad.text and bad.approval is None  # told before you are asked


async def test_a_change_that_names_nothing_or_no_one_is_an_error_not_a_request(services):
    nothing = await call(services, "automation_update", {"id": 1})
    assert "No automation 1" in nothing.text
    schedule = services.automations.create("Bilan", {"type": "once", "at": LATER}, prompt="x", timezone="Europe/Paris")
    empty = await call(services, "automation_update", {"id": schedule.id})
    assert "Say what to change" in empty.text and asked(services) == []


async def test_a_deletion_is_destructive_and_asks(services):
    hook = services.automations.create("Invoices", INVOICE, prompt="x")
    result = await call(services, "automation_delete", {"id": hook.id})
    assert result.approval is not None and result.approval.level == "destructive"
    assert result.approval.summary.startswith("Delete the automation ") and "Invoices" in result.approval.summary
    assert services.automations.find(hook.id) is not None
    allow(services, "automation_delete")
    done = await call(services, "automation_delete", {"id": hook.id})
    assert done.ran and done.text == f"Deleted the automation {hook.id} “Invoices”." and services.automations.find(hook.id) is None
    gone = await call(services, "automation_delete", {"id": hook.id})
    assert "No automation" in gone.text


# ----------------------------------------------------------------------
# In a conversation
# ----------------------------------------------------------------------
async def test_a_chat_creates_an_automation_once_you_allow_it(services, llm):
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
    assert [item.name for item in services.automations.listing()] == ["Weekly review"]
    assert ("automation_create", "done") in [(row["tool"], row["outcome"]) for row in services.audit.recent()]
