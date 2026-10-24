"""Where the result of an automation goes (`deliver`), and Clara's own `discord_say`: a message in #general that you can answer,
as opposed to a ping in #notification."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from clara.agent.turn import ChatRequest
from clara.events import TARGETS
from clara.events.delivery import DELIVERIES, OTHER_CLIENTS
from clara.events.routing import category_of, route
from clara.events.store import MAX_SAID, MAX_TEXT
from clara.llm.types import LlmError
from clara.timekeeping import ScheduleError
from clara.triggers import TriggerError
from clara.triggers.message import INSTRUCTIONS, INSTRUCTIONS_TALKING
from clara.triggers.message import instructions_of as hook_instructions
from fakes import calls
from mailworld import connect_mailbox, gmail_message
from test_schedule_runs import Clock, build, plan, run_due, runs
from test_triggers import chat_calls, event, make, work

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
# A schedule
# ----------------------------------------------------------------------
async def test_a_schedule_that_delivers_to_discord_says_its_whole_answer_in_general_and_nothing_else(s, llm, clock):
    schedule = plan(s, packs=["web"], per_run=True, deliver="discord")
    llm.replies = [LONG]
    await run_due(s, clock, "2026-10-18T21:59:30")
    run = runs(s, schedule.id)[0]
    [message] = said(s)
    assert message.targets == ("discord",) and message.payload["stage"] == "said"
    assert message.text == LONG.strip() and len(message.text) > 700  # the whole answer, not the first paragraph
    assert message.payload["conversation"] == run["conversation_id"] == message.conversation_id  # the run's own, even per run
    assert not notices(s)  # no second message for Discord to ping about


async def test_both_says_it_on_discord_and_notifies_the_other_clients(s, llm, clock):
    schedule = plan(s, packs=["web"], deliver="both")
    llm.replies = [LONG]
    await run_due(s, clock, "2026-10-18T21:59:30")
    assert [m.targets for m in said(s)] == [("discord",)]
    [notice] = notices(s)
    assert notice.targets == OTHER_CLIENTS and "discord" not in notice.targets
    assert notice.text == runs(s, schedule.id)[0]["summary"] and len(notice.text) <= 700


async def test_web_leaves_discord_out_and_notice_is_what_it_always_was(s, llm, clock):
    plan(s, name="Web", packs=["web"], deliver="web")
    plan(s, name="Plain", packs=["web"])
    llm.replies = ["Fait pour le web.", "Fait pour tous."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    web, plain = sorted(notices(s), key=lambda e: e.title)[::-1]
    assert (web.title, web.targets) == ("Scheduled task: Web", OTHER_CLIENTS)
    assert (plain.title, plain.targets) == ("Scheduled task: Plain", ())  # empty: every client, Discord's #notification too
    assert not said(s)


async def test_a_run_that_failed_tells_every_client_even_when_it_delivers_to_discord(s, llm, clock):
    schedule = plan(s, packs=["web"], attempts=1, deliver="both")
    llm.replies = [LlmError("busy", 429)]
    await run_due(s, clock, "2026-10-18T21:59:30")
    assert runs(s, schedule.id)[0]["status"] == "failed" and not said(s)
    [notice] = notices(s)
    assert notice.targets == () and "could not run" in notice.text


async def test_a_run_that_delivers_to_discord_is_told_that_its_answer_is_a_message_to_answer(s, llm, clock):
    plan(s, name="Talk", packs=["web"], deliver="discord")
    llm.replies = ["a"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    system = llm.seen[0]["messages"][0]["content"]
    assert "a message they can answer" in system and "Nobody can answer a question now" not in system
    clock.at("2026-10-18T10:00")
    s.schedules.delete(s.schedules.listing()[0].id)
    plan(s, name="Ping", packs=["web"])
    llm.replies = ["b"]
    await run_due(s, clock, "2026-10-19T21:59:30")
    assert "Nobody can answer a question now" in llm.seen[-1]["messages"][0]["content"]


def test_the_setting_is_checked(s):
    assert DELIVERIES == ("notice", "web", "discord", "both")
    with pytest.raises(ScheduleError, match="deliver must be one of"):
        plan(s, name="A", deliver="sms")
    with pytest.raises(ScheduleError, match="outside the machine"):
        plan(s, name="B", deliver="discord", local_only=True)
    schedule = plan(s, name="C", deliver="both")
    with pytest.raises(ScheduleError, match="outside the machine"):  # it is the pair that is refused, whichever is changed last
        s.schedules.update(schedule.id, local_only=True)
    assert s.schedules.update(schedule.id, local_only=True, deliver="web").deliver == "web"
    assert s.schedules.describe(s.schedules.update(schedule.id, local_only=False, deliver="discord"))["deliver"] == "discord"


# ----------------------------------------------------------------------
# A hook
# ----------------------------------------------------------------------
class Talker:
    """A stand-in for the model that gives a run a conversation of its own, like the real one."""

    def __init__(self, services) -> None:
        self.services = services

    async def __call__(self, trigger, run_id, events):
        conversation = self.services.conversations.create("server", title="Invoices — Billing")
        self.services.db.execute("UPDATE trigger_runs SET conversation_id = ? WHERE id = ?", (conversation.id, run_id))
        return LONG


async def test_a_hook_that_delivers_to_discord_says_its_whole_answer_in_the_conversation_of_the_run(services):
    services.triggers.runner = Talker(services)
    trigger = make(services, deliver="discord")
    services.triggers.ingest([event("m1")])
    await work(services)
    run = services.triggers.runs(trigger.id)[0]
    [message] = said(services)
    assert run["status"] == "ok" and message.text == LONG.strip()
    assert message.targets == ("discord",) and message.title == "Hook: Invoices"
    assert message.payload["conversation"] == run["conversation"] and message.payload["stage"] == "said"
    assert not notices(services)


async def test_a_quiet_hook_says_nothing_on_discord_either_but_a_failed_run_always_tells(services):
    runner = Talker(services)
    services.triggers.runner = runner
    trigger = make(services, deliver="both", announce=False)
    services.triggers.ingest([event("m1")])
    await work(services)
    assert not said(services) and not notices(services)
    runner.__class__ = type("Failing", (Talker,), {"__call__": _fail})
    services.triggers.ingest([event("m2")])
    await work(services)
    assert services.triggers.runs(trigger.id)[0]["status"] == "failed"
    assert not said(services) and [n.targets for n in notices(services)] == [()]


async def _fail(self, trigger, run_id, events):
    raise RuntimeError("the model is down")


async def test_a_hook_that_only_notifies_cannot_talk_and_web_keeps_discord_out(services):
    with pytest.raises(TriggerError, match="no conversation to answer in"):
        make(services, "N", action="notify", prompt="{subject}", deliver="discord")
    with pytest.raises(TriggerError, match="outside the machine"):
        make(services, "L", local_only=True, deliver="both")
    hook = make(services, "W", action="notify", prompt="{subject}", deliver="web")
    services.triggers.ingest([event("m1")])
    await work(services)
    [notice] = services.events.recent(5, ["notification"])
    assert notice.targets == OTHER_CLIENTS and hook.deliver == "web"
    talker = make(services, "T", filter={"subject": "other"}, deliver="discord")
    with pytest.raises(TriggerError, match="no conversation to answer in"):
        services.triggers.update(talker.id, action="notify", prompt="x")
    assert services.triggers.update(talker.id, deliver="notice", action="notify", prompt="x").deliver == "notice"


async def test_a_mail_is_summed_up_on_discord_and_your_answer_there_can_use_every_tool_of_clara(services, llm, world):
    """The whole way: a mail arrives, the hook's run (only its own read tools) says its answer in #general, you answer in the
    same conversation and Clara, who still has the mail in front of her, sets the reminder."""
    connect_mailbox(services)
    trigger = make(services, deliver="discord")
    watch = services.connectors.watch
    await watch.poll()
    when = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    world.gmail["m1"] = gmail_message("m1", "Billing <billing@acme.example>", "Invoice 42", f"Meeting on {when} about it.",
                                      int(time.time() * 1000) + 5_000)
    llm.replies = [calls(("mail_read", {"message": "m1"})), "Invoice 42: a meeting is planned. Shall I add it to your reminders?"]
    await watch.poll()
    await work(services)
    run = services.triggers.runs(trigger.id)[0]
    [message] = said(services)
    assert message.text.endswith("Shall I add it to your reminders?") and message.payload["conversation"] == run["conversation"]
    first = chat_calls(llm)[0]
    assert "remind" not in {tool["function"]["name"] for tool in first["tools"]}  # the run had only the hook's tools
    assert "a message they can answer" in first["messages"][0]["content"]
    llm.seen.clear()
    llm.replies = [calls(("remind", {"text": "Meeting about invoice 42", "when": when})), "Done: an hour before."]
    reply = await services.agent.answer(ChatRequest(message="Yes, an hour before.", client="discord", conversation=run["conversation"]))
    assert reply == "Done: an hour before."
    assert "remind" in {tool["function"]["name"] for tool in llm.seen[0]["tools"]}  # your answer is an ordinary turn
    assert any("Invoice 42" in str(m["content"]) for m in llm.seen[0]["messages"])  # with the mail still in front of her
    assert [r.text for r in services.reminders.upcoming()] == ["Meeting about invoice 42"]


def test_a_hook_is_told_what_kind_of_answer_it_gives():
    assert hook_instructions("notice") == hook_instructions("web") == INSTRUCTIONS
    assert hook_instructions("discord") == hook_instructions("both") == INSTRUCTIONS_TALKING
    assert "ask none" in INSTRUCTIONS and "ask none" not in INSTRUCTIONS_TALKING


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


def test_delivering_to_discord_switches_show_in_history_on_once_and_you_may_switch_it_off(s):
    assert not plan(s, name="N").in_history and not plan(s, name="W", deliver="web").in_history
    assert plan(s, name="D", deliver="discord").in_history and plan(s, name="B", deliver="both").in_history
    later = plan(s, name="Later")
    assert s.schedules.update(later.id, deliver="discord").in_history  # chosen later: switched on then
    assert not s.schedules.update(later.id, in_history=False).in_history  # yours to switch off…
    assert not s.schedules.update(later.id, prompt="Autre chose.").in_history  # …and it stays off
    assert not s.schedules.update(later.id, deliver="both").in_history  # discord to both is not a new choice
    assert not s.schedules.update(later.id, deliver="notice").in_history


async def test_a_run_that_delivers_to_discord_tells_when_it_starts_and_the_bot_adds_the_thread(s, llm, clock):
    schedule = plan(s, packs=["web"], per_run=True, deliver="discord")
    llm.replies = ["ok"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    run = runs(s, schedule.id)[0]
    [start] = starts(s)
    assert (start.title, start.text, start.targets) == ("Scheduled task: Bilan", "Started.", ("discord",))
    assert start.conversation_id == run["conversation_id"]
    assert start.payload == {"schedule": schedule.id, "run": run["id"], "conversation": run["conversation_id"], "thread": ""}
    assert category_of(start.kind, start.source, start.payload) == "automation_started"
    assert route(start, {}) == "notification" and route(start, {"automation_started": "off"}) == "off"


async def test_only_a_run_that_lives_in_a_forum_thread_tells_it_started(s, llm, clock):
    plan(s, name="Notice", packs=["web"])
    plan(s, name="Web", packs=["web"], deliver="web")
    quiet = plan(s, name="Hidden", packs=["web"], deliver="both")
    s.schedules.update(quiet.id, in_history=False)  # no forum, so no thread to link to
    llm.replies = ["a", "b", "c"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    assert not starts(s)


async def test_a_hook_that_delivers_to_discord_tells_when_it_starts_too(services, llm):
    trigger = make(services, deliver="both")
    assert trigger.in_history and not make(services, "Plain", filter={"subject": "other"}).in_history
    llm.replies = ["Done."]
    services.triggers.ingest([event("m1")])
    await work(services)
    run = services.triggers.runs(trigger.id)[0]
    [start] = starts(services)
    assert (start.title, start.targets, start.source) == ("Hook: Invoices", ("discord",), "automations")
    assert start.payload["run"] == run["id"] and start.payload["conversation"] == run["conversation"]
    changed = services.triggers.update(services.triggers.by_name("Plain").id, deliver="discord")
    assert changed.in_history  # chosen later: switched on then


async def test_a_run_that_delivers_to_discord_asks_in_its_forum_thread_and_opens_none_in_general(s, llm, clock):
    connect_bot(s)
    schedule = plan(s, packs=["ask_person", "discord_thread"], per_run=True, deliver="discord")
    llm.replies = [calls(("ask_person", {"question": "Et ce mail ?"})), "J'attends."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    asked = s.events.recent(1, ["ask"])[0]
    assert asked.payload["stage"] == "asked" and asked.payload["forum"] is True and asked.payload["thread"] is False
    assert "ask_person" in offered(llm) and "discord_thread" not in offered(llm)  # its thread is the forum's
    clock.at("2026-10-18T10:00")
    other = plan(s, name="Other", packs=["ask_person", "discord_thread"], per_run=True, deliver="web")
    llm.replies = ["ok"]
    await run_due(s, clock, "2026-10-19T21:59:30")
    assert "discord_thread" in {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}  # the others keep it
    assert schedule.id != other.id


async def test_the_end_and_the_reminder_of_a_forum_run_are_marked_as_its_own(s, llm, clock):
    schedule = plan(s, packs=["ask_person"], per_run=True, deliver="discord")
    llm.replies = [calls(("ask_person", {"question": "Et ce mail ?"})), "J'attends."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    clock.at("2026-10-19T09:30")
    s.schedules._remind(s.schedules._clock())
    assert s.events.recent(1, ["ask"])[0].payload["stage"] == "reminder" and s.events.recent(1, ["ask"])[0].payload["forum"] is True
    s.schedules.finish_run(runs(s, schedule.id)[0]["id"], "Fini.")
    assert s.events.recent(1, ["ask"])[0].payload["stage"] == "finished" and s.events.recent(1, ["ask"])[0].payload["forum"] is True
    s.schedules.delete(schedule.id)
    plan(s, name="Plain", packs=["ask_person"], per_run=True)
    llm.replies = [calls(("ask_person", {"question": "Et ?"})), "J'attends."]
    await run_due(s, clock, "2026-10-19T21:59:30")
    assert "forum" not in s.events.recent(1, ["ask"])[0].payload


def test_a_database_upgraded_from_version_14_shows_the_automations_that_deliver_to_discord(tmp_path):
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
    assert [row[0] for row in connection.execute("SELECT in_history FROM schedules ORDER BY id")] == [1, 0]
    assert connection.execute("SELECT in_history FROM triggers").fetchone()[0] == 1
    archived = dict(connection.execute("SELECT id, archived_at IS NOT NULL FROM conversations"))
    assert archived == {"c-talk": 0, "c-ping": 1, "c-hook": 0}  # the switch had archived them: they are back
    revisions = dict(connection.execute("SELECT id, mirror_rev FROM conversations"))
    assert revisions["c-talk"] == revisions["c-hook"] > revisions["c-ping"]  # and the mirror will see it
