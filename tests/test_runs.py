"""Runs: an answer that goes on when its client leaves, can be followed again from its start, and can be stopped."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from clara.agent.runs import AlreadyRunning
from clara.agent.turn import ChatRequest
from clara.llm.types import LlmChunk, ToolCall
from clara.tools.base import Tool, fixed
from conftest import parse_sse


def _hold(services) -> asyncio.Event:
    """A tool that waits until the test lets it go: an answer is held half way, whatever the speed of the machine."""
    let_go = asyncio.Event()

    async def hold(context) -> str:
        await let_go.wait()
        return "released"

    services.toolbox.add(Tool("hold", "h", hold, fixed("read", "read", "ws:x", "wait"), {}, ()))
    return let_go


FIRST_ROUND = [LlmChunk(text="Checking. "), LlmChunk(text="One moment. "), LlmChunk(tool_calls=[ToolCall("hold", {})])]


def _start(services, message="hi", **fields):
    request = ChatRequest(message=message, client="web", **fields)
    plan = services.agent.plan(request)
    return services.agent.runs.start(request, plan, lambda error: {"type": "error", "message": str(error)})


async def _until(check, tries: int = 200) -> None:
    for _ in range(tries):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never came true")


async def _seen(run, kind: str) -> list[dict]:
    """Follow a run until an event of that kind, then leave."""
    seen = []
    async with contextlib.aclosing(run.follow()) as events:
        async for event in events:
            seen.append(event)
            if event["type"] == kind:
                break
    return seen


async def _all(run) -> list[dict]:
    return [event async for event in run.follow()]


async def test_a_run_goes_on_when_its_follower_leaves(services, llm):
    let_go = _hold(services)
    llm.replies = [FIRST_ROUND, "All done."]
    run = _start(services)
    await _seen(run, "tool_start")  # the page is closed while the tool works
    await _until(lambda: run.followers == 0)
    assert not run.task.done() and services.agent.runs.of(run.conversation) is run
    let_go.set()
    await run.task
    stored = services.conversations.read_back(run.conversation)
    assert stored[-1]["content"] == "All done." and not any("interrupted" in m["content"] for m in stored)
    assert not services.agent.runs.active
    await services.agent.drain_background()


async def test_whoever_comes_back_is_given_everything_so_far_then_the_rest(services, llm):
    let_go = _hold(services)
    llm.replies = [FIRST_ROUND, "All done."]
    run = _start(services)
    await _seen(run, "tool_start")
    replay = await _seen(run, "tool_start")  # a second visit, while the answer is still held
    assert [e["type"] for e in replay][0] == "turn"
    said = [e for e in replay if e["type"] == "token"]
    assert [e["text"] for e in said] == ["Checking. One moment. "]  # the pieces of text are kept as one
    reader = asyncio.create_task(_all(run))
    await _until(lambda: run.followers == 1)
    let_go.set()
    events = await reader
    assert [e["type"] for e in events[: len(replay)]] == [e["type"] for e in replay]
    assert events[-1]["type"] == "done" and "All done." in events[-1]["reply"]
    assert "Checking. One moment." in "".join(e["text"] for e in events if e["type"] == "token")
    await services.agent.drain_background()


async def test_stopping_a_run_keeps_what_was_said_as_interrupted(services, llm):
    _hold(services)
    llm.replies = [FIRST_ROUND, "never said"]
    run = _start(services)
    follower = asyncio.create_task(_all(run))
    await _until(lambda: any(e["type"] == "tool_start" for e in run.events()))
    await services.agent.runs.stop(run, "you stopped it")
    events = await follower
    assert events[-1]["type"] != "done"  # the follower's stream just ends
    stored = services.conversations.read_back(run.conversation, calls=True)
    assert "Checking. One moment." in stored[1]["content"]
    assert "[This answer was interrupted: you stopped it.]" in stored[-1]["content"]
    assert any(m["role"] == "tool" and "not run" in m["content"] for m in stored)
    assert not services.agent.runs.active
    await services.agent.drain_background()


async def test_one_run_at_a_time_in_a_conversation(services, llm):
    _hold(services)
    llm.replies = [FIRST_ROUND]
    run = _start(services)
    with pytest.raises(AlreadyRunning):
        _start(services, "and another", conversation=run.conversation)
    await _until(lambda: any(e["type"] == "tool_start" for e in run.events()))
    await services.agent.runs.cancel_all("the server stopped")  # what the server does as it stops
    stored = services.conversations.read_back(run.conversation)
    assert "[This answer was interrupted: the server stopped.]" in stored[-1]["content"]
    await services.agent.drain_background()


async def test_a_failure_is_the_last_event_of_a_run(services, llm):
    services.agent.accepting = False  # the turn refuses to begin
    run = _start(services)
    events = await _all(run)
    assert [e["type"] for e in events] == ["error"] and "stopping" in events[0]["message"]
    assert not services.agent.runs.active


async def test_a_run_counts_as_busy_for_the_graceful_stop(services, llm):
    _hold(services)
    llm.replies = [FIRST_ROUND]
    run = _start(services)
    assert services.agent.busy
    await services.agent.runs.stop(run, "you stopped it")
    await services.agent.drain_background()
    assert not services.agent.busy


async def test_the_api_keeps_an_answer_going_and_it_can_be_followed(client, services, llm):
    let_go = _hold(services)
    llm.replies = [FIRST_ROUND, "All done."]
    made = (await client.post("/v1/conversations", json={})).json()["id"]
    asking = asyncio.create_task(
        client.post("/v1/chat/stream", json={"message": "look it up", "conversation": made, "keep_running": True})
    )
    await _until(lambda: services.agent.runs.of(made) is not None)
    refused = await client.post("/v1/chat/stream", json={"message": "twice", "conversation": made, "keep_running": True})
    assert refused.status_code == 409
    following = asyncio.create_task(client.get(f"/v1/conversations/{made}/run"))
    await _until(lambda: services.agent.runs.of(made).followers == 2)
    let_go.set()
    asked, followed = await asking, await following
    events = parse_sse(followed.text)
    assert events[0] == {"type": "resume", "message": "look it up", "attachments": []}
    assert events[-1]["type"] == parse_sse(asked.text)[-1]["type"] == "done"
    assert (await client.get(f"/v1/conversations/{made}/run")).status_code == 404
    assert (await client.delete(f"/v1/conversations/{made}/run")).status_code == 404
    await services.agent.drain_background()


async def test_the_stop_route_ends_the_answer_and_keeps_what_was_said(client, services, llm):
    _hold(services)
    llm.replies = [FIRST_ROUND]
    made = (await client.post("/v1/conversations", json={})).json()["id"]
    asking = asyncio.create_task(
        client.post("/v1/chat/stream", json={"message": "look it up", "conversation": made, "keep_running": True})
    )
    await _until(lambda: services.agent.runs.of(made) is not None)
    assert (await client.delete(f"/v1/conversations/{made}/run")).json() == {"ok": True}
    events = parse_sse((await asking).text)
    assert "done" not in [e["type"] for e in events]
    assert "[This answer was interrupted: you stopped it.]" in services.conversations.read_back(made)[-1]["content"]
    await services.agent.drain_background()


async def test_a_kept_answer_cannot_carry_client_tools(client):
    body = {"message": "hi", "keep_running": True, "tools": [{"type": "function", "function": {"name": "x"}}]}
    assert (await client.post("/v1/chat/stream", json=body)).status_code == 422
