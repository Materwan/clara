"""Booked calls: Clara books a tool call in a chat, you allow the exact call and its date once, and the server runs it at
the due time with the saved arguments (no model turn). The rules are asked again then."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from clara.agent.turn import ChatRequest
from clara.gate import Decision, Situation
from clara.gate.actions import Action
from clara.packs import PACKS
from clara.packs.deferred import deferred_tools
from clara.timekeeping.deferred import KEEP_DAYS, MAX_PENDING
from clara.timeutil import iso
from clara.tools.base import Tool, ToolContext, ToolError, TurnState
from fakes import calls
from modeset import at_level, default, line

NAMES = {"deferred_book", "deferred_list", "deferred_cancel"}
SOON = "2026-11-02T09:00"  # 08:00 UTC: Paris is back on winter time
REMIND = {"text": "Call Anna", "when": "2099-01-05T09:00"}


class Clock:
    def __init__(self, text: str):
        self.now = datetime.fromisoformat(text).replace(tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def clock(services):
    """The booked calls' own clock, and a default mode where booking asks (what the old level 4 did) and the probe runs."""
    clock = Clock("2026-10-31T12:00")
    services.deferred._clock = clock
    at_level(services, 4)
    return clock


def held_when_due(services) -> None:
    """The runner of a call booked before the modes, whose rule asked at the due time: it hands back an ordinary request. (A mode's
    ask no longer asks again: the booking was the answer.)"""

    async def runner(booked):
        ctx = ToolContext(services, TurnState(f"deferred-{booked.id}", booked.conversation_id or "", "server", mode="unattended", timezone=booked.timezone))
        action = services.toolbox.action(booked.tool, ctx, booked.arguments)
        request = services.approvals.create(action, Situation("unattended"), ctx, booked.arguments, Decision("ask", "a rule asked"))
        return "held", f"A rule asks about this call: request #{request.id} waits for your answer.", request.id

    services.deferred.runner = runner


class Probe:
    """A tool whose summary and behaviour the test controls."""

    def __init__(self):
        self.summary = "Probe: one"
        self.ran: list[dict] = []
        self.fail = ""


@pytest.fixture
def probe(services):
    state = Probe()

    def describe(context, arguments) -> Action:
        return Action("", "write", "memory", ("memory",), state.summary)

    def run(context, **arguments) -> str:
        if state.fail:
            raise ToolError(state.fail)
        state.ran.append({"arguments": arguments, "turn": context.turn})
        return "probed"

    services.toolbox.add(Tool("probe", "A probe.", run, describe, {"x": {"type": "integer"}}))
    return state


def context(services, **turn) -> ToolContext:
    return ToolContext(services, TurnState("t1", "", "web", timezone="Europe/Paris", **turn))


async def call(services, name: str, arguments: dict, **turn):
    return await services.toolbox.call(name, context(services, **turn), arguments, services.toolbox.names)


def owner(services):
    """A token that may answer every request (made once per test)."""
    if not hasattr(services, "owner_token"):
        services.owner_token = services.tokens.create("approver", "cli")[0]
    return services.owner_token


async def book(services, tool="probe", arguments=None, at=SOON, **turn):
    """Ask Clara's way, allow the request, and give the call that was booked."""
    result = await call(services, "deferred_book", {"tool": tool, "arguments": arguments or {}, "at": at}, **turn)
    assert result.approval is not None, result.text
    settled = await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    assert settled.status == "done", settled.result
    return max(services.deferred.listing(), key=lambda item: item.id)


async def fire(services) -> int:
    started = services.deferred.fire_due()
    await asyncio.gather(*list(services.deferred._running.values()))
    return started


# ----------------------------------------------------------------------
# The pack
# ----------------------------------------------------------------------
def test_the_pack_is_for_chats_and_runs_and_off_in_a_new_conversation(services):
    pack = next(pack for pack in PACKS if pack.id == "deferred_calls")
    assert pack.where == "both" and not pack.default and pack.tools == NAMES
    assert {tool.name for tool in deferred_tools()} == NAMES
    assert "deferred_calls" in [item["id"] for item in services.packs.listing("chat")]
    assert "deferred_calls" in [item["id"] for item in services.packs.listing("automation")]
    assert "deferred_calls" not in services.packs.active(None)
    assert services.packs.check(["deferred_calls"], "automation") == ["deferred_calls"]


def test_a_hook_gets_the_pack_only_when_its_allow_list_names_a_booking_tool(services):
    assert "deferred_calls" not in services.packs.starting_with(["notify"])
    assert "deferred_calls" in services.packs.starting_with(["deferred_book", "notify"])
    assert "deferred_calls" in services.packs.starting_with(["deferred_*"])
    assert "automations" not in services.packs.starting_with(["automation_*", "deferred_*"])  # still never a run's


async def test_the_tools_are_offered_only_where_the_pack_is_on(services, llm):
    cid = services.conversations.create("web", title="Plain").id
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="hello", client="web", conversation=cid))
    assert not NAMES & {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}
    services.conversations.update(cid, packs=["web", "deferred_calls"])
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="hello", client="web", conversation=cid))
    assert NAMES <= {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}


async def test_a_scheduled_run_is_offered_them_when_its_pack_is_on_and_still_not_the_automations(services):
    schedule = services.automations.create("Run", {"type": "once", "at": "2099-01-05T09:00"}, prompt="x", timezone="Europe/Paris")
    cid = services.conversations.create("web", title="Run", packs=["web", "deferred_calls", "automations"]).id
    services.db.execute(
        "INSERT INTO automation_runs (automation_id, due_at, queued_at, status, conversation_id, started_at) VALUES (?, ?, ?, 'running', ?, ?)",
        (schedule.id, "2099-01-05T08:00:00+00:00", "2099-01-05T08:00:00+00:00", cid, "2099-01-05T08:00:00+00:00"),
    )
    hidden = services.packs.hidden(services.conversations.get(cid))
    assert not NAMES & hidden
    assert "automation_create" in hidden
    off = services.conversations.create("web", title="Off", packs=["web"]).id  # a run whose schedule did not list the pack
    services.db.execute(
        "INSERT INTO automation_runs (automation_id, due_at, queued_at, status, conversation_id, started_at) VALUES (?, ?, ?, 'running', ?, ?)",
        (schedule.id, "2099-01-06T08:00:00+00:00", "2099-01-06T08:00:00+00:00", off, "2099-01-06T08:00:00+00:00"),
    )
    assert NAMES <= services.packs.hidden(services.conversations.get(off))


async def test_a_run_books_a_call_and_it_waits_for_you_like_any_request(services, probe):
    """Nobody holds the request of an unattended run: it stays pending, and the call is booked when you allow it."""
    result = await call(services, "deferred_book", {"tool": "probe", "arguments": {"x": 1}, "at": SOON}, mode="unattended")
    assert result.approval is not None and result.approval.status == "pending" and services.deferred.listing() == []
    settled = await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    assert settled.status == "done", settled.result
    booked = services.deferred.listing()[0]
    assert (booked.tool, booked.arguments, booked.status) == ("probe", {"x": 1}, "pending")


async def test_a_run_that_read_untrusted_content_is_asked_whatever_the_mode_says_when_tainted(services, probe):
    default(services, line("approve", "deferred_book", tainted="ask"))
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON}, mode="unattended", tainted=True)
    assert result.approval is not None and result.approval.status == "pending"


# ----------------------------------------------------------------------
# Booking
# ----------------------------------------------------------------------
async def test_a_booking_asks_and_books_nothing_until_you_allow_it(services):
    result = await call(services, "deferred_book", {"tool": "remind", "arguments": REMIND, "at": SOON})
    assert result.approval is not None and not result.ran
    assert services.deferred.listing() == []
    summary = result.approval.summary
    assert "Run the tool remind on 2026-11-02T09:00+01:00 (Europe/Paris)" in summary
    assert "What it would do:" in summary and '"text": "Call Anna"' in summary
    assert (result.approval.level, result.approval.action_class) == ("write", "deferred")

    settled = await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    assert settled.status == "done"
    [booked] = services.deferred.listing()
    assert (booked.tool, booked.status, booked.arguments) == ("remind", "pending", REMIND)
    assert booked.due_at.isoformat() == "2026-11-02T08:00:00+00:00" and booked.timezone == "Europe/Paris"
    assert booked.approval_id == result.approval.id
    assert services.reminders.upcoming() == []  # nothing runs now


async def test_the_request_has_the_level_of_the_booked_call(services):
    """A destructive call cannot be booked from Discord's approval scope, because it could not be allowed there either."""
    services.toolbox.add(
        Tool("shred", "x", lambda context, **a: "gone", lambda context, arguments: Action("", "destructive", "memory", ("memory",), "Shred it"))
    )
    result = await call(services, "deferred_book", {"tool": "shred", "at": SOON})
    assert result.approval.level == "destructive"
    discord = services.tokens.create("bot", "discord")[0]
    with pytest.raises(ValueError, match="may not answer destructive"):
        await services.approvals.decide(result.approval.id, "allow_once", discord)


async def test_a_long_request_is_shown_whole(services, probe):
    probe.summary = "Probe: " + "word " * 1_000
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert probe.summary in result.approval.summary


async def test_the_request_shows_every_argument(services):
    text = "Anna " * 400
    result = await call(services, "deferred_book", {"tool": "remind", "arguments": {"text": text, "when": "2099-01-05T09:00"}, "at": SOON})
    assert text.strip() in result.approval.summary


async def test_a_call_too_long_for_the_request_is_not_booked(services):
    arguments = {"text": "y" * 23_800, "when": "2099-01-05T09:00"}
    result = await call(services, "deferred_book", {"tool": "remind", "arguments": arguments, "at": SOON})
    assert result.approval is None and "too long to be read in full" in result.text
    assert services.deferred.listing() == []


async def test_a_mode_that_refuses_when_tainted_refuses_a_booking_made_in_one(services, probe):
    default(services, line("approve", "probe", tainted="refuse"))
    assert (await call(services, "deferred_book", {"tool": "probe", "at": SOON})).approval is not None  # not tainted: it can be booked
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON}, tainted=True)
    assert result.approval is None and "would be refused when it is due" in result.text


@pytest.mark.parametrize(
    ("at", "problem"),
    [
        ("2026-10-31T12:00:30", "less than a minute"),
        ("2026-10-01T09:00", "past"),
        ("2027-11-02T09:00", "at most 366 days"),
        ("tomorrow", "not a date and time"),
    ],
)
async def test_a_date_that_cannot_be_booked_is_said_before_you_are_asked(services, at, problem):
    result = await call(services, "deferred_book", {"tool": "remind", "arguments": REMIND, "at": at})
    assert result.approval is None and result.text.startswith("Error:") and problem in result.text


async def test_at_most_fifty_calls_wait(services, probe):
    for number in range(MAX_PENDING):
        services.deferred.book("probe", {"x": number}, target="memory", level="write", action_class="memory", summary="Probe: one", at=SOON, timezone="Europe/Paris")
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert result.approval is None and f"At most {MAX_PENDING}" in result.text


@pytest.mark.parametrize("name", ["deferred_book", "ask_person", "qcm", "finish_run", "discord_say", "open_second_brain", "present"])
async def test_a_tool_that_needs_a_person_or_a_conversation_cannot_be_booked(services, name):
    result = await call(services, "deferred_book", {"tool": name, "at": SOON})
    assert result.approval is None and "cannot be booked" in result.text


async def test_a_tool_that_runs_in_your_browser_cannot_be_booked(services):
    result = await call(services, "deferred_book", {"tool": "folder_list", "at": SOON})
    assert result.approval is None and "cannot be booked" in result.text


async def test_an_unknown_tool_and_bad_arguments_are_said(services):
    assert "No tool is named nothing" in (await call(services, "deferred_book", {"tool": "nothing", "at": SOON})).text
    bad = await call(services, "deferred_book", {"tool": "remind", "arguments": {"text": "x"}, "at": SOON})
    assert bad.approval is None and bad.text.startswith("Error: remind:")
    assert (await call(services, "deferred_book", {"tool": "remind", "arguments": "[1]", "at": SOON})).text.startswith("Error:")


async def test_arguments_may_come_as_a_json_text(services):
    result = await call(services, "deferred_book", {"tool": "remind", "arguments": '{"text": "Call Anna", "when": "2099-01-05T09:00"}', "at": SOON})
    assert result.approval is not None


async def test_a_tool_the_chat_is_not_offered_cannot_be_booked(services):
    ctx = context(services)
    result = await services.toolbox.call("deferred_book", ctx, {"tool": "remind", "arguments": REMIND, "at": SOON}, frozenset({"deferred_book"}))
    assert result.approval is None and "not offered in this conversation" in result.text


async def test_a_call_a_mode_refuses_is_not_booked(services, probe):
    default(services, line("refuse", "probe"))
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert result.approval is None and "would be refused when it is due" in result.text


async def test_a_call_the_floor_refuses_is_not_booked(services):
    result = await call(services, "deferred_book", {"tool": "ws_write", "arguments": {"path": ".git/config", "content": "x"}, "at": SOON})
    assert result.approval is None and result.text.startswith("Error:")


async def test_in_a_local_only_chat_a_call_that_leaves_the_machine_is_not_booked(services):
    result = await call(services, "deferred_book", {"tool": "web_search", "arguments": {"query": "weather"}, "at": SOON}, local_only=True)
    assert result.approval is None and "refused when it is due" in result.text


async def test_a_mode_that_asks_about_the_call_does_not_ask_again_when_it_is_due(services, clock, probe):
    """The request is the approval: the mode's ask, approve and (if tainted) ask have nothing more to say at the due time."""
    default(services, line("ask", "probe", tainted="ask"))
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert "asked again" not in result.approval.summary and "asks you" not in result.approval.summary.replace("nothing asks you again", "")
    await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    assert len(probe.ran) == 1 and services.approvals.list("pending") == []


async def test_a_request_that_changed_before_you_allowed_it_books_nothing(services, probe):
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    probe.summary = "Probe: two"  # the call is not what you read any more
    settled = await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    assert settled.status == "failed" and "Nothing was booked" in settled.result
    assert services.deferred.listing() == []


async def test_allowing_after_the_date_has_passed_books_nothing(services, clock, probe):
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    clock.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    settled = await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    assert settled.status == "failed" and "past" in settled.result
    assert services.deferred.listing() == []


# ----------------------------------------------------------------------
# What asks, and what does not
# ----------------------------------------------------------------------
async def test_a_mode_that_approves_books_without_asking_but_a_tainted_turn_asks_when_it_says_so(services, probe):
    default(services, line("approve", "deferred_book", tainted="ask"))
    quiet = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert quiet.ran and quiet.approval is None and len(services.deferred.listing()) == 1
    tainted = await call(services, "deferred_book", {"tool": "probe", "at": SOON}, tainted=True)
    assert tainted.approval is not None and len(services.deferred.listing()) == 1


async def test_a_mode_that_approves_everything_books_without_asking(services, probe):
    at_level(services, 5)
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert result.ran and result.approval is None
    [booked] = services.deferred.listing()
    assert booked.approval_id is None


async def test_a_mode_that_refuses_booking_refuses_it(services, probe):
    at_level(services, 1)
    default(services, line("approve", "probe"))  # the call itself is allowed: it is the booking that the mode refuses
    result = await call(services, "deferred_book", {"tool": "probe", "at": SOON})
    assert result.text.startswith("Refused") and services.deferred.listing() == []


# ----------------------------------------------------------------------
# At the due time
# ----------------------------------------------------------------------
async def test_a_due_call_runs_by_itself_with_the_saved_arguments(services, clock, probe):
    booked = await book(services, "probe", {"x": 7})
    assert await fire(services) == 0  # not yet
    assert probe.ran == []
    clock.now = datetime(2026, 11, 2, 8, 0, 1, tzinfo=UTC)
    assert await fire(services) == 1
    assert [item["arguments"] for item in probe.ran] == [{"x": 7}]
    turn = probe.ran[0]["turn"]
    assert (turn.mode, turn.client, turn.timezone, turn.turn_id) == ("unattended", "server", "Europe/Paris", f"deferred-{booked.id}")
    done = services.deferred.get(booked.id)
    assert (done.status, done.result) == ("done", "probed") and done.finished_at
    assert await fire(services) == 0  # once
    [notice] = services.events.recent(5, ["notification"])
    assert (notice.source, notice.title) == ("deferred", "Booked call done")
    assert "probe (call" in notice.text and "probed" in notice.text
    ran = [row for row in services.audit.recent() if row["tool"] == "probe"]
    assert ran[0]["outcome"] == "ran" and ran[0]["detail"] == "booked call" and ran[0]["situation"] == "unattended"


async def test_a_real_tool_runs_when_due(services, clock):
    await book(services, "remind", REMIND)
    clock.now = datetime(2026, 11, 2, 8, 0, 1, tzinfo=UTC)
    await fire(services)
    assert [reminder.text for reminder in services.reminders.upcoming()] == ["Call Anna"]


async def test_the_loop_fires_it_at_the_due_time(services, clock, probe):
    await book(services, "probe")
    assert services.deferred.next_due().isoformat() == "2026-11-02T08:00:00+00:00"
    clock.now = datetime(2026, 11, 2, 8, 0, 1, tzinfo=UTC)
    task = asyncio.create_task(services.deferred.run())
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if probe.ran:
                break
    finally:
        task.cancel()
    assert len(probe.ran) == 1


async def test_a_call_that_fails_is_reported_with_its_error(services, clock, probe):
    booked = await book(services)
    probe.fail = "the mail is gone"
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    failed = services.deferred.get(booked.id)
    assert (failed.status, failed.result) == ("failed", "Error: the mail is gone")
    [notice] = services.events.recent(5, ["notification"])
    assert notice.title == "Booked call failed" and "the mail is gone" in notice.text


async def test_a_tool_that_is_gone_fails(services, clock, probe):
    booked = await book(services)
    del services.toolbox._tools["probe"]
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    assert services.deferred.get(booked.id).status == "failed"


async def test_a_call_that_now_says_something_else_is_not_run(services, clock, probe):
    booked = await book(services)
    probe.summary = "Probe: two"
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    stopped = services.deferred.get(booked.id)
    assert stopped.status == "refused" and "Probe: one" in stopped.result and "Probe: two" in stopped.result
    assert probe.ran == []
    [notice] = services.events.recent(5, ["notification"])
    assert notice.title == "Booked call not run"


async def test_a_mode_that_refuses_stops_it_when_it_is_due(services, clock, probe):
    booked = await book(services)
    default(services, line("refuse", "probe"))
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    stopped = services.deferred.get(booked.id)
    assert stopped.status == "refused" and "mode default refuses probe" in stopped.result and probe.ran == []


async def test_a_mode_that_now_asks_does_not_stop_what_you_allowed_but_one_that_refuses_does(services, clock, probe):
    """The request is the approval: a mode that would ask about the tool now has nothing to say. A refusal is final."""
    first = await book(services, "probe", tainted=False)
    default(services, line("ask", "probe", tainted="refuse"))
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    assert len(probe.ran) == 1 and services.deferred.get(first.id).status == "done"
    later = await book(services, "probe", at="2026-11-04T09:00", tainted=False)
    default(services, line("refuse", "probe"))
    clock.now = datetime(2026, 11, 4, 8, 1, tzinfo=UTC)
    await fire(services)
    assert len(probe.ran) == 1 and services.deferred.get(later.id).status == "refused"


async def test_a_held_call_waits_on_an_ordinary_request(services, clock, probe):
    booked = await book(services)
    held_when_due(services)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    held = services.deferred.get(booked.id)
    assert held.status == "held" and held.request_id and probe.ran == []
    request = services.approvals.get(held.request_id)
    assert (request.status, request.tool, request.situation) == ("pending", "probe", "unattended")
    assert services.events.recent(5, ["notification"]) == []  # the request is the news
    assert services.deferred.cancel(booked.id) is False  # it is answered on its request

    await services.approvals.decide(request.id, "allow_once", owner(services))
    assert len(probe.ran) == 1
    await fire(services)  # the loop settles it on its next pass
    assert services.deferred.get(booked.id).status == "done"


async def test_refusing_the_request_ends_it_refused(services, clock, probe):
    booked = await book(services)
    held_when_due(services)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    request_id = services.deferred.get(booked.id).request_id
    await services.approvals.decide(request_id, "refuse_once", owner(services))
    await fire(services)
    refused = services.deferred.get(booked.id)
    assert refused.status == "refused" and "You refused" in refused.result and probe.ran == []


async def test_an_unanswered_request_expires_the_call(services, clock, probe):
    booked = await book(services)
    held_when_due(services)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    request_id = services.deferred.get(booked.id).request_id
    services.db.execute("UPDATE approvals SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (request_id,))
    services.approvals.sweep()
    await fire(services)
    assert services.deferred.get(booked.id).status == "expired"


async def test_a_held_call_answered_later_starts_no_turn_and_is_not_told(services, clock, probe):
    """Its request is answered after the turn that booked it has ended: nobody is in the conversation, so no follow-up
    turn is made and Clara is not told how it ended (DECISIONS: booked calls)."""
    conversation = services.conversations.create("web", title="Held")
    chat = ToolContext(services, TurnState("t1", conversation.id, "web", timezone="Europe/Paris"))
    booking = await services.toolbox.call("deferred_book", chat, {"tool": "probe", "at": SOON}, services.toolbox.names)
    await services.approvals.decide(booking.approval.id, "allow_once", owner(services))
    booked = max(services.deferred.listing(), key=lambda item: item.id)
    held_when_due(services)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    request_id = services.deferred.get(booked.id).request_id
    turns: list[str] = []

    async def follow(conversation: str, message: str) -> None:
        turns.append(message)

    services.approvals.followup = follow
    services.approvals.followup_delay = 0
    await services.approvals.decide(request_id, "allow_once", owner(services))
    await asyncio.sleep(0.05)  # a follow-up turn, if one were made, runs after its delay
    assert probe.ran and turns == []
    assert services.approvals.get(request_id).told
    assert request_id not in [item.id for item in services.approvals.news(conversation.id)]
    await fire(services)  # the loop settles the call on its next pass, and the row says how it ended
    assert services.deferred.get(booked.id).status == "done"


async def test_a_call_missed_while_the_server_was_off_runs_once_and_raises_an_alert(services, clock, probe):
    await book(services)
    services.deferred.late_after = 300.0
    clock.now = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)  # two days late
    assert await fire(services) == 1 and await fire(services) == 0
    assert len(probe.ran) == 1
    assert any("booked call" in event.text for event in services.events.recent(10, ["alert"]))


async def test_a_slow_call_does_not_hold_the_loop(services, clock):
    release = asyncio.Event()

    async def slow(context, **arguments):
        await release.wait()
        return "late"

    services.toolbox.add(Tool("slow", "x", slow, lambda context, arguments: Action("", "write", "memory", ("memory",), "Slow")))
    booked = await book(services, "slow")
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    assert services.deferred.fire_due() == 1  # returns at once
    assert services.deferred.firing and services.deferred.get(booked.id).status == "running"
    release.set()
    await asyncio.gather(*list(services.deferred._running.values()))
    assert not services.deferred.firing and services.deferred.get(booked.id).status == "done"


async def test_a_call_that_was_running_when_the_server_stopped_is_never_run_again(services, clock, probe):
    booked = await book(services)
    services.db.execute("UPDATE deferred_calls SET status = 'running' WHERE id = ?", (booked.id,))
    assert services.deferred.recover() == 1
    stopped = services.deferred.get(booked.id)
    assert stopped.status == "failed" and "may or may not have been done" in stopped.result
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    assert await fire(services) == 0 and probe.ran == []
    assert services.events.recent(5, ["notification"])[0].title == "Booked call failed"


async def test_it_runs_in_the_conditions_it_was_booked_in(services, clock, probe):
    cid = services.conversations.create("web", title="Private", local_only=True).id
    ctx = ToolContext(services, TurnState("t1", cid, "web", timezone="Asia/Tokyo", local_only=True, project_id=None))
    result = await services.toolbox.call("deferred_book", ctx, {"tool": "probe", "at": "2026-11-02T09:00"}, services.toolbox.names)
    await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    [booked] = services.deferred.listing()
    assert (booked.timezone, booked.local_only, booked.conversation_id) == ("Asia/Tokyo", True, cid)
    assert booked.due_at.isoformat() == "2026-11-02T00:00:00+00:00"
    clock.now = datetime(2026, 11, 2, 0, 1, tzinfo=UTC)
    await fire(services)
    turn = probe.ran[0]["turn"]
    assert (turn.timezone, turn.local_only, turn.conversation_id) == ("Asia/Tokyo", True, cid)
    [notice] = services.events.recent(5, ["notification"])
    assert "discord" not in notice.targets and notice.targets == ("web", "cli", "console")


async def test_a_request_answered_by_the_server_keeps_the_zone_it_was_read_in(services, probe):
    """The turn is gone when you answer: the server runs the booking, and the date must still mean what the request said."""
    ctx = ToolContext(services, TurnState("t1", "", "web", timezone="Asia/Tokyo"))
    result = await services.toolbox.call("deferred_book", ctx, {"tool": "probe", "at": "2026-11-02T09:00"}, services.toolbox.names)
    assert result.approval.timezone == "Asia/Tokyo" and "(Asia/Tokyo)" in result.approval.summary
    settled = await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    assert settled.status == "done", settled.result
    [booked] = services.deferred.listing()
    assert (booked.timezone, booked.due_at.isoformat()) == ("Asia/Tokyo", "2026-11-02T00:00:00+00:00")


async def test_an_incognito_call_keeps_no_link_to_its_conversation(services, clock, probe):
    cid = services.conversations.create("web", title="Incognito", incognito=True).id
    ctx = ToolContext(services, TurnState("t1", cid, "web", timezone="Europe/Paris", incognito=True))
    result = await services.toolbox.call("deferred_book", ctx, {"tool": "probe", "at": SOON}, services.toolbox.names)
    await services.approvals.decide(result.approval.id, "allow_once", owner(services))
    [booked] = services.deferred.listing()
    assert booked.conversation_id is None and booked.incognito
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    assert probe.ran[0]["turn"].incognito


# ----------------------------------------------------------------------
# Listing and cancelling
# ----------------------------------------------------------------------
async def test_the_list_shows_what_waits_then_what_ended(services, clock, probe):
    first = await book(services, "probe", at="2026-11-03T09:00")
    second = await book(services, "probe", at="2026-11-02T09:00")
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    assert [item.id for item in services.deferred.listing()] == [first.id, second.id]
    assert [item.status for item in services.deferred.listing()] == ["pending", "done"]
    text = (await call(services, "deferred_list", {})).text
    assert f"[{first.id}]" in text and "(pending)" in text and "(done)" in text and "Probe: one" in text


async def test_an_empty_list_says_so(services):
    assert (await call(services, "deferred_list", {})).text == "No booked call."


async def test_a_local_only_call_is_read_in_full_only_from_a_local_only_chat(services, probe):
    await book(services, "probe", local_only=True)
    hidden = (await call(services, "deferred_list", {})).text
    assert "Probe: one" not in hidden and "local only" in hidden
    assert "Probe: one" in (await call(services, "deferred_list", {}, local_only=True)).text


async def test_a_call_that_waits_can_be_cancelled_by_clara_and_never_runs(services, clock, probe):
    booked = await book(services)
    result = await call(services, "deferred_cancel", {"id": booked.id})
    assert result.ran and "Cancelled" in result.text
    assert services.deferred.get(booked.id).status == "cancelled"
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    assert await fire(services) == 0 and probe.ran == []
    again = await call(services, "deferred_cancel", {"id": booked.id})
    assert "is cancelled" in again.text and "only a call that still waits" in again.text
    assert "No booked call 99" in (await call(services, "deferred_cancel", {"id": 99})).text


async def test_a_cancelled_call_frees_its_place(services, probe):
    for _ in range(MAX_PENDING):
        services.deferred.book("probe", {}, target="memory", level="write", action_class="memory", summary="Probe: one", at=SOON, timezone="Europe/Paris")
    assert services.deferred.cancel(services.deferred.listing()[0].id)
    assert (await call(services, "deferred_book", {"tool": "probe", "at": SOON})).approval is not None


async def test_ended_calls_are_forgotten_after_a_month(services, clock, probe):
    booked = await book(services)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    clock.now += timedelta(days=KEEP_DAYS - 1)
    services.deferred.sweep()
    assert services.deferred.get(booked.id) is not None
    clock.now += timedelta(days=2)
    services.deferred.sweep()
    assert services.deferred.get(booked.id) is None


async def test_what_waits_is_never_swept(services, clock, probe):
    booked = await book(services)
    clock.now += timedelta(days=KEEP_DAYS * 3)
    services.db.execute("UPDATE deferred_calls SET booked_at = ? WHERE id = ?", (iso(clock.now - timedelta(days=100)), booked.id))
    services.deferred.sweep()
    assert services.deferred.get(booked.id) is not None


# ----------------------------------------------------------------------
# The API
# ----------------------------------------------------------------------
async def test_the_api_lists_and_cancels(services, client, probe):
    booked = await book(services)
    listed = (await client.get("/v1/deferred")).json()
    assert [item["id"] for item in listed["calls"]] == [booked.id] and listed["max_pending"] == MAX_PENDING
    call_ = listed["calls"][0]
    assert (call_["tool"], call_["status"], call_["local"], call_["summary"]) == ("probe", "pending", "2026-11-02T09:00+01:00", "Probe: one")
    assert (await client.delete("/v1/deferred/999")).status_code == 404
    assert (await client.delete(f"/v1/deferred/{booked.id}")).json() == {"cancelled": True}
    again = await client.delete(f"/v1/deferred/{booked.id}")
    assert again.status_code == 409 and "cancelled" in again.json()["error"]["message"]


async def test_the_api_has_no_way_to_book(client):
    assert (await client.post("/v1/deferred", json={"tool": "remind", "at": SOON})).status_code == 405


async def test_the_api_says_where_a_held_call_is_answered(services, client, clock, probe):
    booked = await book(services)
    held_when_due(services)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    refused = await client.delete(f"/v1/deferred/{booked.id}")
    assert refused.status_code == 409 and "request #" in refused.json()["error"]["message"]


# ----------------------------------------------------------------------
# In a conversation
# ----------------------------------------------------------------------
async def test_a_chat_books_a_call_once_you_allow_it_and_it_runs_later(services, llm, clock):
    cid = services.conversations.create("web", title="Planning", packs=["web", "deferred_calls"]).id
    llm.replies = [calls(("deferred_book", {"tool": "remind", "arguments": REMIND, "at": SOON})), "Booked for Monday."]
    reply = ""
    async for event in services.agent.turn(ChatRequest(message="Remind me on Monday, but do it by yourself", client="web", conversation=cid)):
        if event["type"] == "approval":
            assert "Run the tool remind" in event["approval"]["summary"]
            await services.approvals.decide(event["approval"]["id"], "allow_once", owner(services))
        if event["type"] == "done":
            reply = event["reply"]
    assert reply == "Booked for Monday."
    [booked] = services.deferred.listing()
    assert (booked.tool, booked.conversation_id, booked.status) == ("remind", cid, "pending")
    assert ("deferred_book", "done") in [(row["tool"], row["outcome"]) for row in services.audit.recent()]
    llm.replies = []  # no model turn is needed at the due time
    seen = len(llm.seen)
    clock.now = datetime(2026, 11, 2, 8, 1, tzinfo=UTC)
    await fire(services)
    assert len(llm.seen) == seen
    assert [reminder.text for reminder in services.reminders.upcoming()] == ["Call Anna"]
