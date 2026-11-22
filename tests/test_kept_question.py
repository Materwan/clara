"""A question is stored when it is sent, not when its answer ends: a stop, an error or a crash before the first word leaves it,
the conversation is listed with it at once, and the prompt never carries it twice."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses

import pytest

from clara.agent.turn import ChatRequest, PromptTooLarge
from clara.app import recover
from clara.memory.conversations import ConversationError, TurnRow
from fakes import Hang, calls

STOPPED = "[This answer was interrupted: you stopped it.]"


def _start(services, message="my question", **fields):
    request = ChatRequest(message=message, client="web", **fields)
    plan = services.agent.plan(request)
    return services.agent.runs.start(request, plan, lambda error: {"type": "error", "message": str(error)})


async def _until(check, tries: int = 300) -> None:
    for _ in range(tries):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never came true")


def _path(services, conversation: str) -> list[tuple[str, str]]:
    return [(m["role"], m["content"]) for m in services.conversations.read_back(conversation)]


def _preview(services, conversation: str) -> str | None:
    found = {item["id"]: item for item in services.conversations.listing(kind="any")}
    return found[conversation]["preview"] if conversation in found else None


def _asked(seen: list[dict]) -> list[dict]:
    """The prompts of chat turns (a title or a summary is asked with no tools)."""
    return [call["messages"] for call in seen if call["tools"]]


def _times(messages: list[dict], text: str) -> int:
    return sum(text in str(message["content"]) for message in messages if message["role"] == "user")


async def test_a_new_conversation_stopped_before_the_first_word_keeps_its_message(services, llm):
    llm.replies = [Hang(60)]
    run = _start(services, "my first question")
    await _until(lambda: services.conversations.message_count(run.conversation) == 1)
    # the model is still working: the conversation is already listed, with its question
    assert _preview(services, run.conversation) == "my first question"
    assert _path(services, run.conversation) == [("user", "my first question")]
    await services.agent.runs.stop(run, "you stopped it")
    assert _path(services, run.conversation) == [("user", "my first question"), ("assistant", STOPPED)]
    assert _preview(services, run.conversation) == "my first question"
    await services.agent.drain_background()


async def test_the_next_prompt_carries_a_stopped_question_once(services, llm):
    llm.replies = [Hang(60)]
    first = _start(services, "the stopped question")
    await _until(lambda: services.conversations.message_count(first.conversation) == 1)
    await services.agent.runs.stop(first, "you stopped it")
    llm.replies = ["Fine."]
    await services.agent.answer(ChatRequest(message="and now?", client="web", conversation=first.conversation))
    prompt = _asked(llm.seen)[-1]
    assert _times(prompt, "the stopped question") == 1 and _times(prompt, "and now?") == 1
    assert any(STOPPED in str(m["content"]) for m in prompt if m["role"] == "assistant")
    await services.agent.drain_background()


async def test_the_list_route_shows_a_question_while_the_model_is_still_working(client, services, llm):
    llm.replies = [Hang(60)]
    made = (await client.post("/v1/conversations", json={})).json()["id"]
    asking = asyncio.create_task(
        client.post("/v1/chat/stream", json={"message": "look it up", "conversation": made, "keep_running": True})
    )
    await _until(lambda: services.conversations.message_count(made) == 1)
    listed = (await client.get("/v1/conversations")).json()["conversations"]
    assert [(item["id"], item["preview"]) for item in listed] == [(made, "look it up")]
    shown = (await client.get(f"/v1/conversations/{made}/messages")).json()["messages"]
    assert [(m["role"], m["content"]) for m in shown] == [("user", "look it up")]
    assert (await client.delete(f"/v1/conversations/{made}/run")).json() == {"ok": True}
    await asking
    listed = (await client.get("/v1/conversations")).json()["conversations"]
    assert [(item["id"], item["preview"]) for item in listed] == [(made, "look it up")]
    assert _path(services, made)[-1] == ("assistant", STOPPED)
    await services.agent.drain_background()


async def test_a_model_that_fails_before_the_first_word_leaves_the_question_and_the_reason(services, llm):
    llm.replies = [RuntimeError("the model broke")]
    conversation = None
    with pytest.raises(RuntimeError):
        async with contextlib.aclosing(services.agent.turn(ChatRequest(message="why", client="web"))) as events:
            async for event in events:
                conversation = event["conversation"]
    assert _path(services, conversation) == [
        ("user", "why"),
        ("assistant", "[This answer was interrupted: the model broke.]"),
    ]


async def test_a_prompt_that_does_not_fit_leaves_the_question_and_the_reason(services, llm, monkeypatch):
    monkeypatch.setattr("clara.agent.turn.estimate_prompt_tokens", lambda messages, tools=None: 10**9)
    conversation = None
    with pytest.raises(PromptTooLarge):
        async with contextlib.aclosing(services.agent.turn(ChatRequest(message="a lot", client="web"))) as events:
            async for event in events:
                conversation = event["conversation"]
    (question, note) = _path(services, conversation)
    assert question == ("user", "a lot") and note[1].startswith("[This answer was interrupted: The prompt needs about")


async def test_a_message_refused_before_the_turn_stores_nothing(services, llm):
    with pytest.raises(PromptTooLarge):  # one message above half the window: an HTTP error for the client
        services.agent.plan(ChatRequest(message="x " * 60_000, client="web"))
    assert services.conversations.listing(kind="any") == []


async def test_the_server_stopping_leaves_the_question_and_its_note(services, llm):
    llm.replies = [Hang(60)]
    run = _start(services, "still there?")
    await _until(lambda: services.conversations.message_count(run.conversation) == 1)
    await services.agent.runs.cancel_all("the server stopped")
    assert _path(services, run.conversation) == [
        ("user", "still there?"),
        ("assistant", "[This answer was interrupted: the server stopped.]"),
    ]
    await services.agent.drain_background()


async def test_a_normal_turn_stores_the_question_once_then_its_answer(services, llm):
    llm.replies = [calls(("recall_facts", {"query": "x"})), "Here it is."]
    events = [e async for e in services.agent.turn(ChatRequest(message="dig", client="web"))]
    done = events[-1]
    stored = services.conversations.read_back(done["conversation"], calls=True)
    assert [m["role"] for m in stored] == ["user", "assistant", "tool", "assistant"]
    assert stored[0]["content"] == "dig" and stored[0]["id"] == done["message_id"]
    assert stored[-1]["content"] == "Here it is."
    prompt = _asked(llm.seen)[0]
    assert _times(prompt, "dig") == 1  # sent once, from the request, and not replayed from the stored question
    assert all(_times(prompt_, "dig") == 1 for prompt_ in _asked(llm.seen))  # nor in the second round
    await services.agent.drain_background()


async def test_a_model_that_says_nothing_leaves_no_question_without_an_answer(services, llm):
    llm.replies = [""]
    events = [e async for e in services.agent.turn(ChatRequest(message="anything?", client="web"))]
    assert events[-1]["type"] == "done" and events[-1]["reply"] == ""
    assert _path(services, events[-1]["conversation"]) == [
        ("user", "anything?"),
        ("assistant", "[This answer was interrupted: the model gave no answer.]"),
    ]


async def test_asking_again_makes_a_version_and_a_failure_keeps_the_new_one(services, llm):
    llm.replies = ["First answer."]
    first = [e async for e in services.agent.turn(ChatRequest(message="the question", client="web"))][-1]
    conversation = first["conversation"]
    llm.replies = ["Second answer."]
    again = [
        e
        async for e in services.agent.turn(
            ChatRequest(message="the question", client="web", conversation=conversation, fork_of=first["message_id"])
        )
    ][-1]
    assert again["versions"] == [first["message_id"], again["message_id"]]
    assert _path(services, conversation) == [("user", "the question"), ("assistant", "Second answer.")]
    llm.replies = [RuntimeError("down")]
    with pytest.raises(RuntimeError):
        async for _ in services.agent.turn(
            ChatRequest(message="the question", client="web", conversation=conversation, fork_of=again["message_id"])
        ):
            pass
    # the failed version is the one on the path, with its note: the old one is not put back in its place
    assert _path(services, conversation) == [("user", "the question"), ("assistant", "[This answer was interrupted: down.]")]
    (question,) = [m for m in services.conversations.read_back(conversation) if m["role"] == "user"]
    assert len(question["versions"]) == 3


async def test_the_prompt_carries_the_question_once_after_the_conversation_is_summarised(services, llm):
    services.agent.settings = dataclasses.replace(services.agent.settings, history_turns=3, keep_recent_turns=1)
    conversation = services.conversations.create("web")
    for number in range(5):
        services.conversations.add_turn(
            conversation.id, f"old {number}", [TurnRow("assistant", f"reply {number}")], client="web", turn_id=f"t{number}"
        )
    llm.replies = ["Summary of the old turns.", "Answer."]
    events = [
        e
        async for e in services.agent.turn(
            ChatRequest(message="the new question", client="web", conversation=conversation.id)
        )
    ]
    assert "compacted" in [e["type"] for e in events]  # the summary was made before the model was asked
    prompt = _asked(llm.seen)[-1]
    assert _times(prompt, "the new question") == 1
    current = services.conversations.get(conversation.id)
    (question,) = [m for m in services.conversations.read_back(conversation.id) if m["content"] == "the new question"]
    assert current.summary and question["id"] > current.summary_upto  # kept after the summary, not summarised away
    assert "the new question" not in current.summary
    assert events[-1]["message_id"] == question["id"]
    await services.agent.drain_background()


async def test_the_turn_in_flight_takes_no_place_in_the_replayed_history(services, llm):
    """The turn in flight is not one of the turns of the history: the window keeps as many earlier turns as it always did."""
    services.agent.settings = dataclasses.replace(services.agent.settings, history_turns=3, compact_percent=0)
    conversation = services.conversations.create("web")
    for number in range(5):
        services.conversations.add_turn(
            conversation.id, f"old {number}", [TurnRow("assistant", f"reply {number}")], client="web", turn_id=f"t{number}"
        )
    llm.replies = ["Answer."]
    await services.agent.answer(ChatRequest(message="the new question", client="web", conversation=conversation.id))
    prompt = _asked(llm.seen)[-1]
    assert [m["content"] for m in prompt if m["role"] == "user"][:-1] == ["old 2", "old 3", "old 4"]
    await services.agent.drain_background()


def test_a_question_is_stored_alone_and_its_answer_follows_it(services):
    store = services.conversations
    conversation = store.create("web").id
    clock = lambda: services.db.value("SELECT seq FROM mirror_clock WHERE id = 1")  # noqa: E731
    before = clock()
    question = store.add_question(conversation, "q?", client="web", turn_id="t-1")
    assert clock() == before  # the Discord mirror's live feed writes the question: no sync is started for it
    assert [m.role for m in store.messages_after(conversation)] == ["user"]
    store.add_answer(conversation, question, [TurnRow("assistant", "a")], turn_id="t-1")
    assert clock() > before
    rows = store.messages_after(conversation)
    assert [(m.role, m.turn_id) for m in rows] == [("user", "t-1"), ("assistant", "t-1")]
    assert rows[1].parent_id == rows[0].id
    assert store.history(conversation, 5, without_turn="t-1") == []  # a turn in flight is left out
    with pytest.raises(ConversationError):
        store.add_answer(conversation, question + 99, [TurnRow("assistant", "lost")])
    store.delete(conversation)
    with pytest.raises(ConversationError):
        store.add_answer(conversation, question, [TurnRow("assistant", "lost")])


def test_the_sweep_at_start_closes_a_question_left_without_an_answer_once(services):
    store = services.conversations
    lost = store.create("web").id
    store.add_question(lost, "never answered", client="web", turn_id="t-lost")
    answered = store.create("web").id
    store.add_turn(answered, "fine", [TurnRow("assistant", "yes")], client="web", turn_id="t-ok")
    run = store.create("server", kind="schedule").id  # an unattended run: its recovery reads whether Clara wrote anything
    store.add_turn(run, "the run's message", [], client="server", turn_id="t-run")
    ran = store.create("web").id
    store.add_turn(ran, "/skill", [], client="web", turn_id="t-skill", shown="Clara ran /skill")
    rows_before = {c: store.message_count(c) for c in (answered, run, ran)}

    recover(services)

    assert [(m.role, m.content, m.turn_id) for m in store.messages_after(lost)] == [
        ("user", "never answered", "t-lost"),
        ("assistant", "[This answer was interrupted: the server stopped.]", "t-lost"),
    ]
    assert {c: store.message_count(c) for c in (answered, run, ran)} == rows_before
    assert store.close_unanswered() == 0 and store.message_count(lost) == 2  # nothing changes the second time
    assert _preview(services, lost) == "never answered"


async def test_an_unattended_run_that_is_cut_before_it_said_anything_still_stores_nothing(services, llm):
    llm.replies = [Hang(60)]
    request = ChatRequest(message="the run", client="server", mode="unattended", kind="schedule")
    plan = services.agent.plan(request)
    run = services.agent.runs.start(request, plan, lambda error: {"type": "error", "message": str(error)})
    await asyncio.sleep(0.1)
    assert services.conversations.message_count(run.conversation) == 0  # not stored while it runs
    await services.agent.runs.stop(run, "the server stopped")
    assert services.conversations.message_count(run.conversation) == 0  # and nothing after: its own recovery reads that
    await services.agent.drain_background()


async def test_an_unattended_run_cut_after_it_spoke_stores_both_together_as_before(services, llm):
    llm.replies = ["A long answer that will be cut somewhere in the middle of it."]
    request = ChatRequest(message="the run", client="server", mode="unattended", kind="schedule")
    async with contextlib.aclosing(services.agent.turn(request)) as events:
        async for event in events:
            if event["type"] == "turn":
                conversation = event["conversation"]
                assert services.conversations.message_count(conversation) == 0
            if event["type"] == "token":
                break
    stored = _path(services, conversation)
    assert [role for role, _ in stored] == ["user", "assistant"] and stored[0][1] == "the run"
    assert "[This answer was interrupted" in stored[1][1]
    await services.agent.drain_background()


async def test_the_compaction_keeps_the_question_in_flight_out_of_the_summary(services, llm):
    store = services.conversations
    conversation = store.create("web").id
    for number in range(3):
        store.add_turn(conversation, f"old {number}", [TurnRow("assistant", f"reply {number}")], client="web", turn_id=f"t{number}")
    question = store.add_question(conversation, "in flight", client="web", turn_id="t-new")
    llm.replies = ["Summary."]
    choice = services.router.choose("", False)
    await services.agent._compact_locked(conversation, keep=0, choice=choice, without_turn="t-new")  # what `_fit` does last
    current = store.get(conversation)
    assert current.summary == "Summary." and 0 < current.summary_upto < question
    assert [m.id for m in store.messages_after(conversation, current.summary_upto)] == [question]


async def test_the_turn_in_flight_does_not_start_a_summary_one_turn_early(services, llm):
    services.agent.settings = dataclasses.replace(services.agent.settings, history_turns=5)
    conversation = services.conversations.create("web")
    for number in range(5):
        services.conversations.add_turn(conversation.id, f"old {number}", [TurnRow("assistant", "r")], client="web", turn_id=f"t{number}")
    llm.replies = ["Answer."]
    events = [e async for e in services.agent.turn(ChatRequest(message="new", client="web", conversation=conversation.id))]
    assert "compacted" not in [e["type"] for e in events]  # five earlier turns fit the history of five turns
    await services.agent.drain_background()
