"""What the result of an automation tells you (the notification, and with « Show in history » the whole answer in Discord), and
Clara's own `discord_say`: a message in #general that you can answer, as opposed to a ping in #notification."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from clara.agent.turn import ChatRequest
from clara.automations import AutomationError
from clara.automations.message import INSTRUCTIONS, INSTRUCTIONS_EVENT, INSTRUCTIONS_EVENT_TALKING, INSTRUCTIONS_TALKING
from clara.automations.message import instructions_of as instructions
from clara.events import TARGETS
from clara.events.routing import category_of, route
from clara.events.store import MAX_SAID, MAX_TEXT
from clara.llm.types import LlmError
from fakes import calls
from mailworld import connect_mailbox, gmail_message
from modeset import default, line
from test_automation_events import chat_calls, event, make, work
from test_automation_runs import Clock, build, plan, run_due, runs

LONG = "Résumé : la facture 42 est de 120 EUR, à payer avant le 1er novembre.\n\n" + "Détail de la facture. " * 60


@pytest.fixture
def environ(environ):
    return {**environ, "CLARA_GOOGLE_CLIENT_ID": "gid", "CLARA_PUBLIC_URL": "https://clara.test"}


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-10-18T10:00")


@pytest.fixture
def s(environ, llm, clock):
    services, db = build(environ, llm, clock)
    yield services
    services.traffic.close()
    db.close()


def said(s) -> list:
    return s.events.recent(20, ["ask"])


def notices(s) -> list:
    return s.events.recent(20, ["schedule"])


# ----------------------------------------------------------------------
# An automation that runs at a time
# ----------------------------------------------------------------------
async def test_an_automation_in_the_history_says_its_whole_answer_to_discord_and_notifies_every_client(s, llm, clock):
    schedule = plan(s, packs=["web"], per_run=True, in_history=True)
    llm.replies = [LONG]
    await run_due(s, clock, "2026-10-18T21:59:30")
    run = runs(s, schedule.id)[0]
    [message] = said(s)
    assert message.targets == ("discord",) and message.payload["stage"] == "said" and message.payload["forum"] is True
    assert message.text == LONG.strip() and len(message.text) > 700  # the whole answer, not the first paragraph
    assert message.payload["conversation"] == run["conversation"] == message.conversation_id  # the run's own, even per run
    [notice] = notices(s)  # and the short notification, to every client (Discord's #notification too)
    assert notice.targets == () and notice.text == run["summary"] and len(notice.text) <= 700
    assert notice.title == "Automation: Bilan" and notice.source == "schedules"


async def test_an_automation_not_in_the_history_only_notifies(s, llm, clock):
    plan(s, name="Plain", packs=["web"])
    llm.replies = ["Fait pour tous."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    [notice] = notices(s)
    assert (notice.title, notice.targets) == ("Automation: Plain", ())  # empty: every client, Discord's #notification too
    assert not said(s)


async def test_a_run_that_failed_only_notifies_even_when_it_is_in_the_history(s, llm, clock):
    schedule = plan(s, packs=["web"], attempts=1, in_history=True)
    llm.replies = [LlmError("busy", 429)]
    await run_due(s, clock, "2026-10-18T21:59:30")
    assert runs(s, schedule.id)[0]["status"] == "failed" and not said(s)
    [notice] = notices(s)
    assert notice.targets == () and "could not run" in notice.text


async def test_a_run_in_the_history_is_told_that_its_answer_is_a_message_to_answer(s, llm, clock):
    plan(s, name="Talk", packs=["web"], in_history=True)
    llm.replies = ["a"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    system = llm.seen[0]["messages"][0]["content"]
    assert "a message they can answer" in system and "Nobody can answer a question now" not in system
    clock.at("2026-10-18T10:00")
    s.automations.delete(s.automations.listing()[0].id)
    plan(s, name="Ping", packs=["web"])
    llm.replies = ["b"]
    await run_due(s, clock, "2026-10-19T21:59:30")
    assert "Nobody can answer a question now" in llm.seen[-1]["messages"][0]["content"]


def test_where_the_result_goes_is_gone_and_a_local_only_automation_may_still_be_in_the_history(s):
    with pytest.raises(AutomationError, match="Unknown fields: deliver"):
        plan(s, name="A", deliver="web")
    local = plan(s, name="B", in_history=True, local_only=True)  # the mirror never shows a local-only conversation
    assert local.in_history and local.local_only


# ----------------------------------------------------------------------
# An automation on an event
# ----------------------------------------------------------------------
class Talker:
    """A stand-in for the model that gives a run a conversation of its own, like the real one."""

    def __init__(self, services) -> None:
        self.services = services

    async def __call__(self, trigger, run_id, due, events):
        conversation = self.services.conversations.create("server", title="Invoices — Billing")
        self.services.db.execute("UPDATE automation_runs SET conversation_id = ? WHERE id = ?", (conversation.id, run_id))
        return LONG


async def test_an_automation_in_the_history_says_its_whole_answer_in_the_conversation_of_the_run(services):
    services.automations.runner = Talker(services)
    trigger = make(services, in_history=True)
    services.automations.ingest([event("m1")])
    await work(services)
    run = services.automations.runs(trigger.id)[0]
    [message] = said(services)
    assert run["status"] == "ok" and message.text == LONG.strip()
    assert message.targets == ("discord",) and message.title == "Automation: Invoices" and message.source == "triggers"
    assert message.payload["conversation"] == run["conversation"] and message.payload["stage"] == "said"
    [notice] = notices(services)
    assert notice.targets == () and notice.source == "triggers"


async def test_a_quiet_automation_says_nothing_on_discord_either_but_a_failed_run_always_tells(services):
    runner = Talker(services)
    services.automations.runner = runner
    trigger = make(services, in_history=True, announce=False)
    services.automations.ingest([event("m1")])
    await work(services)
    assert not said(services) and not notices(services)
    runner.__class__ = type("Failing", (Talker,), {"__call__": _fail})
    services.automations.ingest([event("m2")])
    await work(services)
    assert services.automations.runs(trigger.id)[0]["status"] == "failed"
    assert not said(services) and [n.targets for n in notices(services)] == [()]


async def _fail(self, trigger, run_id, due, events):
    raise RuntimeError("the model is down")


async def test_an_automation_that_only_notifies_cannot_be_in_the_history(services):
    with pytest.raises(AutomationError, match="no conversation"):
        make(services, "N", action="notify", prompt="{subject}", in_history=True)
    hook = make(services, "W", action="notify", prompt="{subject}")
    services.automations.ingest([event("m1")])
    await work(services)
    [notice] = services.events.recent(5, ["notification"])
    assert notice.targets == () and notice.source == "triggers"  # every client
    talker = make(services, "T", filter={"subject": "other"}, in_history=True)
    with pytest.raises(AutomationError, match="no conversation"):
        services.automations.update(talker.id, action="notify", prompt="x")
    assert services.automations.update(talker.id, in_history=False, action="notify", prompt="x").action == "notify"
    assert hook.in_history is False


async def test_a_mail_is_summed_up_on_discord_and_your_answer_there_can_use_every_tool_of_clara(services, llm, world):
    """The whole way: a mail arrives, the run (only its own read tools) says its answer in #general, you answer in the
    same conversation and Clara, who still has the mail in front of her, sets the reminder."""
    connect_mailbox(services)
    trigger = make(services, in_history=True)
    watch = services.connectors.watch
    await watch.poll()
    when = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    world.gmail["m1"] = gmail_message("m1", "Billing <billing@acme.example>", "Invoice 42", f"Meeting on {when} about it.",
                                      int(time.time() * 1000) + 5_000)
    llm.replies = [calls(("mail_read", {"message": "m1"})), "Invoice 42: a meeting is planned. Shall I add it to your reminders?"]
    await watch.poll()
    await work(services)
    run = services.automations.runs(trigger.id)[0]
    [message] = said(services)
    assert message.text.endswith("Shall I add it to your reminders?") and message.payload["conversation"] == run["conversation"]
    first = chat_calls(llm)[0]
    assert "remind" not in {tool["function"]["name"] for tool in first["tools"]}  # the run had only the automation's tools
    assert "a message they can answer" in first["messages"][0]["content"]
    llm.seen.clear()
    llm.replies = [calls(("remind", {"text": "Meeting about invoice 42", "when": when})), "Done: an hour before."]
    reply = await services.agent.answer(ChatRequest(message="Yes, an hour before.", client="discord", conversation=run["conversation"]))
    assert reply == "Done: an hour before."
    assert "remind" in {tool["function"]["name"] for tool in llm.seen[0]["tools"]}  # your answer is an ordinary turn
    assert any("Invoice 42" in str(m["content"]) for m in llm.seen[0]["messages"])  # with the mail still in front of her
    assert [r.text for r in services.reminders.upcoming()] == ["Meeting about invoice 42"]


def test_a_run_is_told_what_kind_of_answer_it_gives(services):
    plain, shown = make(services, "Plain"), make(services, "Shown", in_history=True)
    assert instructions(plain, [object()]) == INSTRUCTIONS_EVENT and instructions(shown, [object()]) == INSTRUCTIONS_EVENT_TALKING
    cron = {"type": "cron", "expr": "0 9 * * *"}
    ticking = services.automations.create("Tick", cron, prompt="x")
    assert instructions(ticking, []) == INSTRUCTIONS and instructions(services.automations.update(ticking.id, in_history=True), []) == INSTRUCTIONS_TALKING
    assert "ask none" in INSTRUCTIONS_EVENT and "ask none" not in INSTRUCTIONS_EVENT_TALKING


# ----------------------------------------------------------------------
# What an event may say
# ----------------------------------------------------------------------
def test_what_is_said_in_general_may_be_longer_than_a_notification(s):
    long = "x" * 7_000
    assert len(s.events.publish("notification", "t", long).text) == MAX_TEXT
    assert len(s.events.publish("ask", "t", long).text) == MAX_SAID > MAX_TEXT
    assert "discord" in TARGETS


# ----------------------------------------------------------------------
# discord_say
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def everything_runs(s):
    """discord_say runs without asking."""
    default(s, line("approve", klass="discord"))


def connect_bot(s) -> None:
    s.clients["discord"] = time.monotonic()


def offered(llm) -> set[str]:
    return {tool["function"]["name"] for tool in llm.seen[0]["tools"]}


async def test_clara_can_say_something_on_discord_and_the_conversation_goes_on_there(s, llm):
    connect_bot(s)
    llm.replies = [calls(("discord_say", {"text": "Facture 42 : 120 EUR, à payer avant le 1er novembre.", "title": "Facture"})),
                   "Je te l'ai envoyé sur Discord."]
    conversation = s.conversations.create("web")
    reply = await s.agent.answer(ChatRequest(message="Envoie-moi ça sur Discord.", client="web", conversation=conversation.id))
    assert reply == "Je te l'ai envoyé sur Discord." and "discord_say" in offered(llm)
    [message] = said(s)
    assert (message.title, message.targets, message.source) == ("Facture", ("discord",), "clara")
    assert message.payload == {"conversation": conversation.id, "stage": "said"} and message.conversation_id == conversation.id
    assert not s.events.recent(5, ["notification"])  # it is not a ping
    assert ("discord_say", "ran") in [(row["tool"], row["outcome"]) for row in s.audit.recent()]


async def test_it_says_so_when_the_bot_is_not_connected_and_sends_nothing(s, llm):
    llm.replies = [calls(("discord_say", {"text": "Salut"})), "Je ne peux pas pour l'instant."]
    await s.agent.answer(ChatRequest(message="Dis-le sur Discord.", client="web"))
    assert not said(s)
    result = next(m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool")
    assert "not connected" in result


async def test_it_is_not_offered_from_discord_itself_nor_in_an_incognito_or_a_local_only_conversation(s, llm):
    connect_bot(s)
    for request in (ChatRequest(message="a", client="discord"), ChatRequest(message="b", client="web", incognito=True),
                    ChatRequest(message="c", client="web", local_only=True)):
        llm.seen.clear()
        await s.agent.answer(request)
        assert "discord_say" not in offered(llm), request
    llm.seen.clear()
    await s.agent.answer(ChatRequest(message="d", client="web"))
    assert "discord_say" in offered(llm) and "notify" in offered(llm)


async def test_a_message_is_never_empty_nor_too_long_and_there_are_at_most_three_a_turn(s, llm):
    connect_bot(s)
    llm.replies = [calls(("discord_say", {"text": "  "}), ("discord_say", {"text": "y" * (MAX_SAID + 1)}),
                         *[("discord_say", {"text": f"m{n}"}) for n in range(4)]), "ok"]
    await s.agent.answer(ChatRequest(message="go", client="web"))
    assert [m.text for m in said(s)][::-1] == ["m0", "m1", "m2"]
    results = [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool"]
    assert "empty" in results[0] and "characters" in results[1] and "At most 3 messages" in results[-1]


# ----------------------------------------------------------------------
# A run that delivers to Discord lives in one forum thread
# ----------------------------------------------------------------------
def starts(s) -> list:
    return [e for e in s.events.recent(20, ["notification"]) if e.source == "automations"]


async def test_a_run_in_the_history_tells_when_it_starts_and_the_bot_adds_the_thread(s, llm, clock):
    schedule = plan(s, packs=["web"], per_run=True, in_history=True)
    llm.replies = ["ok"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    run = runs(s, schedule.id)[0]
    [start] = starts(s)
    assert (start.title, start.text, start.targets) == ("Automation: Bilan", "Started.", ("discord",))
    assert start.conversation_id == run["conversation"]
    assert start.payload == {"automation": schedule.id, "run": run["id"], "conversation": run["conversation"], "thread": ""}
    assert category_of(start.kind, start.source, start.payload) == "automation_started"
    assert route(start, {}) == "notification" and route(start, {"automation_started": "off"}) == "off"


async def test_only_a_run_that_lives_in_a_forum_thread_tells_it_started(s, llm, clock):
    plan(s, name="Notice", packs=["web"])
    llm.replies = ["a"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    assert not starts(s)


async def test_an_automation_on_an_event_in_the_history_tells_when_it_starts_too(services, llm):
    trigger = make(services, in_history=True)
    assert trigger.in_history and not make(services, "Plain", filter={"subject": "other"}).in_history
    llm.replies = ["Done."]
    services.automations.ingest([event("m1")])
    await work(services)
    run = services.automations.runs(trigger.id)[0]
    [start] = starts(services)
    assert (start.title, start.targets, start.source) == ("Automation: Invoices", ("discord",), "automations")
    assert start.payload["run"] == run["id"] and start.payload["conversation"] == run["conversation"]


async def test_a_run_in_the_history_asks_in_its_forum_thread_and_opens_none_in_general(s, llm, clock):
    connect_bot(s)
    schedule = plan(s, packs=["ask_person", "discord_thread"], per_run=True, in_history=True)
    llm.replies = [calls(("ask_person", {"question": "Et ce mail ?"})), "J'attends."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    asked = s.events.recent(1, ["ask"])[0]
    assert asked.payload["stage"] == "asked" and asked.payload["forum"] is True and asked.payload["thread"] is False
    assert "ask_person" in offered(llm) and "discord_thread" not in offered(llm)  # its thread is the forum's
    clock.at("2026-10-18T10:00")
    other = plan(s, name="Other", packs=["ask_person", "discord_thread"], per_run=True)
    llm.replies = ["ok"]
    await run_due(s, clock, "2026-10-19T21:59:30")
    assert "discord_thread" in {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}  # the others keep it
    assert schedule.id != other.id


async def test_the_end_and_the_reminder_of_a_forum_run_are_marked_as_its_own(s, llm, clock):
    schedule = plan(s, packs=["ask_person"], per_run=True, in_history=True)
    llm.replies = [calls(("ask_person", {"question": "Et ce mail ?"})), "J'attends."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    clock.at("2026-10-19T09:30")
    s.automations._remind(s.automations._clock())
    assert s.events.recent(1, ["ask"])[0].payload["stage"] == "reminder" and s.events.recent(1, ["ask"])[0].payload["forum"] is True
    s.automations.finish_run(runs(s, schedule.id)[0]["id"], "Fini.")
    assert s.events.recent(1, ["ask"])[0].payload["stage"] == "finished" and s.events.recent(1, ["ask"])[0].payload["forum"] is True
    s.automations.delete(schedule.id)
    plan(s, name="Plain", packs=["ask_person"], per_run=True)
    llm.replies = [calls(("ask_person", {"question": "Et ?"})), "J'attends."]
    await run_due(s, clock, "2026-10-19T21:59:30")
    assert "forum" not in s.events.recent(1, ["ask"])[0].payload


def test_a_database_upgraded_from_version_14_keeps_the_automations_that_delivered_to_discord_in_the_history(tmp_path):
    import sqlite3

    from clara.db import migrate
    from clara.db.migrate import migrations

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    for migration in migrations()[:14]:
        connection.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;")
    for conversation in ("c-talk", "c-ping", "c-hook"):
        connection.execute("INSERT INTO conversations (id, client, created_at, updated_at, archived_at, archived_by)"
                           " VALUES (?, 'server', 'x', 'x', 'x', 'group')", (conversation,))
    connection.execute("INSERT INTO schedules (id, name, prompt, local_at, timezone, created_at, updated_at, deliver)"
                       " VALUES (1, 'Talk', 'p', '2026-10-18T08:00', 'Europe/Paris', 'x', 'x', 'discord')")
    connection.execute("INSERT INTO schedules (id, name, prompt, local_at, timezone, created_at, updated_at)"
                       " VALUES (2, 'Ping', 'p', '2026-10-18T08:00', 'Europe/Paris', 'x', 'x')")
    connection.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id) VALUES (1, 'x', 'ok', 'c-talk')")
    connection.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id) VALUES (2, 'x', 'ok', 'c-ping')")
    connection.execute("INSERT INTO triggers (id, name, event_kind, filter, action, prompt, tools, model, catch_up, max_per_hour,"
                       " max_age_hours, attempts, deliver, created_at, updated_at) VALUES"
                       " (1, 'Hook', 'mail.received', '{}', 'run', 'p', '[]', '', 'latest', 6, 24, 3, 'both', 'x', 'x')")
    connection.execute("INSERT INTO trigger_runs (trigger_id, status, conversation_id, event_ids, queued_at) VALUES (1, 'ok', 'c-hook', '[]', 'x')")
    connection.commit()
    connection.close()
    assert migrate(path, tmp_path / "backups")[:2] == (14, len(migrations()))
    connection = sqlite3.connect(path)
    assert [row[0] for row in connection.execute("SELECT in_history FROM automations ORDER BY id")] == [1, 0, 1]  # the hook is #3
    assert connection.execute("SELECT new_id FROM legacy_ids WHERE kind = 'hook' AND old_id = 1").fetchone()[0] == 3
    archived = dict(connection.execute("SELECT id, archived_at IS NOT NULL FROM conversations"))
    assert archived == {"c-talk": 0, "c-ping": 1, "c-hook": 0}  # the switch had archived them: they are back
    revisions = dict(connection.execute("SELECT id, mirror_rev FROM conversations"))
    assert revisions["c-talk"] == revisions["c-hook"] > revisions["c-ping"]  # and the mirror will see it
