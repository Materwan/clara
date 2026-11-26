"""A message the person writes while Clara's answer waits (for a sub agent's report, an approval, the answers to `ask_questions`) is read
at the wait point, answered, and the same wait goes on (DECISIONS.md, 2026-11-26, « Clara answers while she waits »)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from clara.app import create_app
from clara.gate import Action, Situation
from clara.gate.grants import Grant, GrantError
from clara.tools.base import Tool, ToolContext, TurnState, fixed
from fakes import Hang, calls
from test_panel import QUESTIONS
from test_runs import _hold, _start, _until

TASK = {"task": "Find the opening hours of the library.", "title": "Library hours"}


@pytest.fixture
def cid(services) -> str:
    return services.conversations.create("web", title="Parent").id


@pytest.fixture
async def web(services):
    token = services.tokens.create("page", "web")[1]
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://clara.test", headers={"Authorization": f"Bearer {token}"}
    ) as http:
        yield http


@pytest.fixture
def sent(services) -> list[int]:
    """A tool that asks in the default mode (a Drive write) and counts the times it ran."""
    ran: list[int] = []

    async def send(context) -> str:
        ran.append(1)
        return "sent"

    services.toolbox.add(Tool("probe_drive", "d", send, fixed("drive_write", "write", "drive:f1", "create a Drive file"), {}, ()))
    return ran


def script(llm, parent, sub=lambda messages, tools: "Report."):
    """Who is asking decides the reply: a sub agent (its prompt says it is one), a title job, or the parent."""

    def next_reply(ref):
        def reply(messages, tools):
            system = messages[0]["content"] if messages[0]["role"] == "system" else ""
            if system.startswith("Write a title"):
                return "A title"
            return (sub if "You are a sub agent" in system else parent)(messages, tools)

        return reply

    llm.next_reply = next_reply


def wrote(message: dict) -> bool:
    """Is this the prompt of an answer to a message sent during a wait? (the note it carries says so)"""
    return message["role"] == "user" and "wrote this while you wait" in message["content"]


def has_tool_result(messages: list[dict], name: str | None = None) -> bool:
    return any(m["role"] == "tool" and (name is None or m.get("tool_name") == name) for m in messages)


def sub_hangs(messages, tools):
    """A sub agent that waits in the `hold` tool, then reports."""
    return "The library opens at nine." if has_tool_result(messages) else calls(("hold", {}))


def said(run) -> str:
    return "".join(e["text"] for e in run.events() if e["type"] == "token")


def kinds(run) -> list[str]:
    return [e["type"] for e in run.events()]


def url(cid: str) -> str:
    return f"/v1/conversations/{cid}/run/messages"


async def waiting_for_a_sub_agent(services, llm, cid, parent=None):
    """A parent that delegates, and a sub agent held in a tool: the parent waits for its report."""
    let_go = _hold(services)

    def default(messages, tools):
        last = messages[-1]
        if wrote(last):
            return "I am waiting for the library sub agent."
        return "Done." if has_tool_result(messages) else calls(("delegate", TASK))

    script(llm, parent or default, sub_hangs)
    run = _start(services, "check the library", conversation=cid)
    await _until(lambda: run.state.waits == 1 and services.subagents.running(cid) == 1)
    return run, let_go


async def test_a_message_during_a_sub_agents_wait_is_read_answered_and_the_same_wait_goes_on(services, llm, cid, web):
    seen: list[list[dict]] = []

    def parent(messages, tools):
        if wrote(messages[-1]):
            seen.append(messages)
            return "I am waiting for the library sub agent."
        return "Done." if has_tool_result(messages) else calls(("delegate", TASK))

    run, let_go = await waiting_for_a_sub_agent(services, llm, cid, parent)
    names = {tool["function"]["name"] for tool in llm.seen[0]["tools"]}
    assert "delegate" in names and "stop" not in names  # `stop` is for the answer to a message, nowhere else
    sent = await web.post(url(cid), json={"message": "where are you?", "ref": "m1"})
    assert sent.status_code == 200 and sent.json() == {"ok": True}
    await _until(lambda: "I am waiting for the library sub agent." in said(run))
    await asyncio.sleep(0.05)  # the answer ends
    assert not run.task.done() and services.subagents.running(cid) == 1  # the wait goes on: the sub agent was not touched
    prompt = seen[0]
    assert "where are you?" in prompt[-1]["content"] and "Sub agents working now" in prompt[-1]["content"]
    assert any(m["role"] == "tool" and "has not ended" in m["content"] for m in prompt)  # the call that waits stands in
    let_go.set()
    await run.task
    events = run.events()
    order = [e["type"] for e in events if e["type"] in ("tool_start", "wait", "interjection", "tool", "done")]
    assert order == ["tool_start", "wait", "interjection", "wait", "tool", "done"]
    assert [e["on"] for e in events if e["type"] == "wait"] == [True, False]
    assert next(e for e in events if e["type"] == "interjection") == {
        "type": "interjection", "ref": "m1", "message": "where are you?"}
    assert "The library opens at nine." in next(e["result"] for e in events if e["type"] == "tool")  # the report still arrived
    assert events[-1]["type"] == "done" and events[-1]["reply"] == "Done."  # the answer to the message is not the reply
    stored = services.conversations.read_back(cid, calls=True)
    assert [(m["role"], m["content"][:10]) for m in stored if m["role"] != "tool"] == [
        ("user", "check the "), ("assistant", ""), ("user", "where are "),
        ("assistant", "I am waiti"), ("assistant", "Done.")]
    assert [m["role"] for m in stored] == ["user", "assistant", "tool", "user", "assistant", "assistant"]
    assert services.conversations.get(cid) and not services.agent.runs.active
    await services.agent.drain_background()


async def test_a_message_during_an_approval_wait_does_not_approve_the_card_and_the_card_is_asked_again(
        services, llm, cid, web, sent):
    object.__setattr__(services.settings, "approval_wait", 10)

    def parent(messages, tools):
        if wrote(messages[-1]):
            return "Your request is still waiting for you."
        return "Done." if has_tool_result(messages) else calls(("probe_drive", {}))

    script(llm, parent)
    run = _start(services, "make it", conversation=cid)
    await _until(lambda: run.state.waits == 1)
    (card,) = services.approvals.waiting(cid)
    posted = await web.post(url(cid), json={"message": "yes, go ahead"})
    assert posted.status_code == 200
    await _until(lambda: "Your request is still waiting" in said(run) and kinds(run)[-1] == "approval")
    assert services.approvals.get(card.id).status == "pending" and sent == []  # a message is not an answer
    cards = [e["approval"] for e in run.events() if e["type"] == "approval"]
    assert [c["id"] for c in cards] == [card.id, card.id]  # asked again, after her answer
    assert not run.task.done()
    owner = services.tokens.create("owner", "web")[0]
    await services.approvals.decide(card.id, "allow_once", owner)
    await run.task
    assert sent == [1] and run.events()[-1]["reply"] == "Done."
    await services.agent.drain_background()


async def test_a_message_during_ask_questions_does_not_answer_them_and_they_are_asked_again(services, llm, cid, web):
    def parent(messages, tools):
        if wrote(messages[-1]):
            return "Your questions are still open."
        return "Postgres then." if has_tool_result(messages) else calls(("ask_questions", {"questions": QUESTIONS}))

    script(llm, parent)
    run = _start(services, "build it", conversation=cid)
    await _until(lambda: run.state.waits == 1)
    (ask,) = services.questions.waiting(cid)
    assert (await web.post(url(cid), json={"message": "Postgres"})).status_code == 200  # it looks like an answer
    await _until(lambda: "Your questions are still open." in said(run) and kinds(run)[-1] == "questions")
    assert services.questions.waiting(cid) == [ask] and not ask.future.done()
    assert [e["ask"]["id"] for e in run.events() if e["type"] == "questions"] == [ask.id, ask.id]
    services.questions.reply(ask.id, cid, [{"options": [1], "other": ""}, {"options": [0], "other": ""}])
    await run.task
    assert "Answer: Postgres" in next(e["result"] for e in run.events() if e["type"] == "tool")
    assert run.events()[-1]["reply"] == "Postgres then."
    await services.agent.drain_background()


async def test_only_the_web_page_may_write_during_a_wait(services, llm, cid, web, client):
    run, let_go = await waiting_for_a_sub_agent(services, llm, cid)
    assert (await client.post(url(cid), json={"message": "hello"})).status_code == 409  # a cli token
    bot = services.tokens.create("bot", "discord")[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {bot}", "X-Clara-Client": "web"}) as other:
        assert (await other.post(url(cid), json={"message": "hello"})).status_code == 409  # whatever label it claims
    again = await web.post("/v1/chat/stream", json={"message": "hello", "conversation": cid, "keep_running": True})
    assert again.status_code == 409  # the stream route keeps its 409 for everyone, the page included
    assert run.state.inbox == [] and "interjection" not in kinds(run)
    let_go.set()
    await run.task
    await services.agent.drain_background()


async def test_a_busy_conversation_that_is_not_in_a_wait_still_gets_409(services, llm, cid, web):
    let_go = _hold(services)
    llm.replies = [calls(("hold", {})), "Done."]  # a tool that runs: no wait
    run = _start(services, "work", conversation=cid)
    await _until(lambda: "tool_start" in kinds(run))
    assert run.state.waits == 0
    assert (await web.post(url(cid), json={"message": "hello"})).status_code == 409
    assert (await web.post("/v1/chat/stream", json={"message": "hello", "conversation": cid, "keep_running": True})).status_code == 409
    other = services.conversations.create("web").id
    assert (await web.post(url(other), json={"message": "hello"})).status_code == 404  # nothing is answering there
    let_go.set()
    await run.task
    assert (await web.post(url(cid), json={"message": "hello"})).status_code == 404  # and nothing is, once it ended
    await services.agent.drain_background()


async def test_stop_asks_a_card_and_approving_it_stops_the_sub_agent(services, llm, cid, web):
    def parent(messages, tools):
        last = messages[-1]
        if wrote(last):
            assert [tool["function"]["name"] for tool in tools] == ["stop"]  # offered to this answer only
            (sub,) = services.subagents.working(cid)
            assert sub[0] in last["content"]
            return calls(("stop", {"sub_agent": sub[0]}))
        if last["role"] == "tool" and last.get("tool_name") == "stop":
            return "Stopped it."
        return "Done." if has_tool_result(messages) else calls(("delegate", TASK))

    run, _ = await waiting_for_a_sub_agent(services, llm, cid, parent)
    (sub,) = [row["id"] for row in services.db.all("SELECT id FROM conversations WHERE parent_id = ?", (cid,))]
    await _until(lambda: services.agent.runs.of(sub) is not None)
    assert (await web.post(url(cid), json={"message": "stop the library sub agent"})).status_code == 200
    await _until(lambda: services.approvals.waiting(cid))  # asked, and nothing is stopped yet
    (card,) = services.approvals.waiting(cid)
    assert card.tool == "stop" and card.action_class == "subagent_stop" and sub in card.summary
    assert services.agent.runs.of(sub) is not None and not card.describe()["grantable"]
    owner = services.tokens.create("owner", "web")[0]
    with pytest.raises(GrantError):
        services.grants.arm(card, owner)  # « Approve all next » never covers it
    await services.approvals.decide(card.id, "allow_once", owner)
    await run.task
    events = run.events()
    assert [e["name"] for e in events if e["type"] == "tool"] == ["stop", "delegate"]  # her answer ends before the wait does
    delegate = next(e["result"] for e in events if e["type"] == "tool" and e["name"] == "delegate")
    assert "could not finish its task" in delegate and "stopped" in delegate  # the parent is told, as for the page's Stop
    assert "was stopped" in next(e["result"] for e in events if e["type"] == "tool" and e["name"] == "stop")
    row = services.db.one("SELECT state FROM sub_agents WHERE conversation_id = ?", (sub,))
    assert row["state"] == "failed" and not services.subagents.busy(sub) and services.agent.runs.of(sub) is None
    assert "[This answer was interrupted: you asked Clara to stop it.]" in services.conversations.read_back(sub)[-1]["content"]
    assert events[-1]["reply"] == "Done." and "Stopped it." in said(run)
    await services.agent.drain_background()


async def test_a_refused_stop_leaves_the_sub_agent_working(services, llm, cid, web):
    def parent(messages, tools):
        last = messages[-1]
        if wrote(last):
            return calls(("stop", {"sub_agent": services.subagents.working(cid)[0][0]}))
        if last["role"] == "tool" and last.get("tool_name") == "stop":
            assert "refused" in last["content"]
            return "I did not stop it."
        return "Done." if has_tool_result(messages) else calls(("delegate", TASK))

    run, let_go = await waiting_for_a_sub_agent(services, llm, cid, parent)
    await web.post(url(cid), json={"message": "stop it"})
    await _until(lambda: services.approvals.waiting(cid))
    (card,) = services.approvals.waiting(cid)
    await services.approvals.decide(card.id, "refuse_once", services.tokens.create("owner", "web")[0])
    await _until(lambda: "I did not stop it." in said(run))
    await asyncio.sleep(0.05)
    assert services.subagents.running(cid) == 1 and not run.task.done()
    let_go.set()
    await run.task
    (sub,) = [row["id"] for row in services.db.all("SELECT id FROM conversations WHERE parent_id = ?", (cid,))]
    assert services.db.one("SELECT state FROM sub_agents WHERE conversation_id = ?", (sub,))["state"] == "done"
    await services.agent.drain_background()


async def test_approve_all_next_never_covers_stop(services, cid):
    grant = Grant(1, cid, "stop", "subagent_stop", "write", "owner", services.grants.clock())
    services.grants._active[grant.key] = grant  # as if a button had been pressed for the tool
    action = Action("stop", "write", "subagent_stop", ("subagents",), "Stop a sub agent")
    assert services.grants.cover(action, Situation("interactive"), cid, "web") is None
    assert grant.calls == 0
    ask = services.gate.decide(action, Situation("interactive"))
    assert ask.effect == "ask"  # the default mode asks, in a normal turn and in one that read outside content
    assert services.gate.decide(action, Situation("interactive", tainted=True)).effect == "ask"


async def test_stop_refuses_what_is_not_a_working_sub_agent_of_this_conversation(services, cid):
    context = ToolContext(services, TurnState("t1", cid, "web", timezone="UTC"))
    for given in ("", "c" + "0" * 16, cid):
        outcome = await services.toolbox.call("stop", context, {"sub_agent": given}, services.toolbox.names)
        assert outcome.text.startswith("Error: No sub agent") and outcome.approval is None
    other = services.conversations.create("web", title="S", parent_id=cid).id  # a sub agent that is not working
    outcome = await services.toolbox.call("stop", context, {"sub_agent": other}, services.toolbox.names)
    assert "is not working" in outcome.text and outcome.approval is None


async def test_a_message_nobody_read_when_the_answer_is_stopped_is_stored(services, llm, cid, web):
    def parent(messages, tools):
        if wrote(messages[-1]):
            return Hang(30)  # the answer to the first message takes its time: the second is not read
        return "Done." if has_tool_result(messages) else calls(("delegate", TASK))

    run, _ = await waiting_for_a_sub_agent(services, llm, cid, parent)
    await web.post(url(cid), json={"message": "first"})
    await _until(lambda: "interjection" in kinds(run))
    assert (await web.post(url(cid), json={"message": "second"})).status_code == 200  # taken: the wait is still on
    assert run.state.inbox == [("", "second")]
    await services.agent.runs.stop(run, "you stopped it")
    stored = services.conversations.read_back(cid, calls=True)
    assert [(m["role"], m["content"][:10]) for m in stored] == [
        ("user", "check the "), ("assistant", ""), ("tool", "[not run: "),
        ("user", "first"), ("assistant", "[This answ"), ("user", "second")]
    assert not services.subagents.running(cid) and not services.agent.runs.active
    await services.agent.drain_background()


async def test_a_wait_the_answer_cannot_be_given_in_keeps_the_message_and_the_wait(services, llm, cid, web):
    def parent(messages, tools):
        if wrote(messages[-1]):
            raise RuntimeError("the model is down")
        return "Done." if has_tool_result(messages) else calls(("delegate", TASK))

    run, let_go = await waiting_for_a_sub_agent(services, llm, cid, parent)
    await web.post(url(cid), json={"message": "hello?"})
    await _until(lambda: any(e["type"] == "warning" for e in run.events()))
    assert "could not answer your message" in next(e["message"] for e in run.events() if e["type"] == "warning")
    let_go.set()
    await run.task
    assert [m["content"] for m in services.conversations.read_back(cid) if m["role"] == "user"] == ["check the library", "hello?"]
    assert run.events()[-1]["reply"] == "Done."
    await services.agent.drain_background()


async def test_an_empty_message_is_refused(services, llm, cid, web):
    run, let_go = await waiting_for_a_sub_agent(services, llm, cid)
    assert (await web.post(url(cid), json={"message": "   "})).status_code == 422
    assert run.state.inbox == []
    let_go.set()
    await run.task
    await asyncio.sleep(0)
    await services.agent.drain_background()
