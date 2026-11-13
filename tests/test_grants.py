"""Approve all next: a button that says « allow once » for the next requests of one tool in one conversation, for 30 minutes."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from clara.app import create_app
from clara.gate import Action, Decision, Grants, Situation, engine
from clara.tools.base import Tool, ToolContext, TurnState
from conftest import parse_sse
from fakes import calls
from modeset import default, line, make, own


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def asking(services):
    """In the default mode of these tests the workspace changes ask: that is what the button is for."""
    default(services, line("ask", klass="ws_write"), line("ask", klass="ws_destroy"))


@pytest.fixture
def clock(services) -> Clock:
    fake = Clock()
    services.grants = services.toolbox.grants = Grants(services.runtime, services.audit, clock=fake)
    return fake


@pytest.fixture
def web(services):
    return services.tokens.create("browser", "web")


@pytest.fixture
def conversation(services) -> str:
    return services.conversations.create("web").id


@pytest.fixture
def probe(services) -> list[dict]:
    """A tool whose class is `ws_destroy` (it asks in a chat), recording what ran."""
    ran: list[dict] = []
    action = Action("", "destructive", "ws_destroy", ("ws:p.txt",), "probe")
    services.toolbox.add(Tool("probe", "test tool", lambda context, **arguments: ran.append(arguments) or "done", lambda c, a: action))
    return ran


def context(services, conversation, *, client="web", mode="interactive", tainted=False) -> ToolContext:
    state = TurnState("t1", conversation, client, mode=mode, timezone="Europe/Paris", tainted=tainted)
    return ToolContext(services, state)


async def call(services, conversation, tool="probe", **kwargs):
    return await services.toolbox.call(tool, context(services, conversation, **kwargs), {}, services.toolbox.names)


async def press(services, conversation, web, tool="probe", **kwargs):
    """What the button does: the first call asks, then the request is allowed and the grant starts."""
    asked = await call(services, conversation, tool, **kwargs)
    assert asked.approval is not None
    services.grants.arm(asked.approval, web[0])
    await services.approvals.decide(asked.approval.id, "allow_once", web[0])
    return asked.approval


async def test_the_next_calls_of_the_tool_run_without_a_request(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    assert probe == [{}]  # the request that was pressed on
    for _ in range(3):
        result = await call(services, conversation)
        assert result.ran and result.approval is None and result.grant
    assert len(probe) == 4
    assert [item.status for item in services.approvals.list()] == ["done"]  # no other request was made
    rows = services.audit.recent()
    auto = [row for row in rows if "Approve all next" in row["detail"] and row["outcome"] == "ran"]
    assert len(auto) == 3 and "call 3" in auto[0]["detail"] and "browser" in auto[0]["detail"]
    assert any(row["outcome"] == "grant" for row in rows)


async def test_nothing_else_is_covered(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    other = services.conversations.create("web").id
    assert (await call(services, other)).approval is not None  # another conversation
    assert (await call(services, conversation, client="cli")).approval is not None  # not a turn from the web
    assert (await call(services, conversation, client="discord")).approval is not None
    assert (await call(services, conversation, mode="unattended")).approval is not None
    mail = Action("mail_send", "destructive", "mail_send", ("mail:to:a@b.c",), "send")
    services.toolbox.add(Tool("mail_probe", "test tool", lambda context, **a: "sent", lambda c, a: mail))
    default(services, line("ask", klass="mail_send"))
    assert (await call(services, conversation, "mail_probe")).approval is not None  # another tool
    assert len(probe) == 1


async def test_it_covers_any_tool_the_button_is_pressed_on_whatever_its_class(services, conversation, web, clock):
    """It was limited to workspace and vault changes and the Trash: a mail send, an automation, anything that asks is now covered,
    the one tool at a time."""
    ran: list[str] = []
    for name, klass in (("mail_probe", "mail_send"), ("auto_probe", "automation"), ("net_probe", "run_network")):
        action = Action("", "destructive", klass, ("net",), name)
        services.toolbox.add(Tool(name, "t", lambda context, _n=name, **a: ran.append(_n) or "ok", lambda c, a, _x=action: _x))
    default(services, line("ask", klass="mail_send"), line("ask", klass="automation"), line("ask", klass="run_network"))
    asked = await press(services, conversation, web, "mail_probe")
    assert asked.grantable
    assert (await call(services, conversation, "mail_probe")).ran
    assert (await call(services, conversation, "auto_probe")).approval is not None  # one tool: the one it was pressed on
    assert (await call(services, conversation, "net_probe")).approval is not None
    assert ran == ["mail_probe", "mail_probe"]


async def test_the_secret_check_still_asks(services, conversation, web, probe, clock, monkeypatch):
    """The grant answers the mode's ask; what the gate raised on purpose (a secret leaving) keeps asking."""
    await press(services, conversation, web)
    monkeypatch.setattr(engine, "OUTSIDE", engine.OUTSIDE | {"ws_destroy"})
    secret = Action("", "destructive", "ws_destroy", ("ws:p.txt",), "probe", ("private key",))
    services.toolbox._tools["probe"] = Tool("probe", "test tool", lambda context, **a: "done", lambda c, a: secret)
    assert (await call(services, conversation)).approval is not None


async def test_where_the_mode_refuses_or_approves_there_is_nothing_for_the_grant_to_answer(services, conversation, web, probe, clock):
    make(services, "Locked", line("refuse", "probe"))
    make(services, "Open", line("approve", "probe"))
    await press(services, conversation, web)
    own(services, conversation, "Locked")
    result = await call(services, conversation)
    assert not result.ran and result.approval is None and "Refused" in result.text and "mode Locked refuses probe" in result.text
    own(services, conversation, "Open")
    assert (await call(services, conversation)).ran and not (await call(services, conversation)).grant  # approved by the mode itself


async def test_in_a_tainted_turn_it_answers_only_a_tool_whose_tainted_verdict_is_ask(services, conversation, web, probe, clock):
    make(services, "Both ask", line("ask", "probe"))
    make(services, "Ask then refuse", line("ask", "probe", tainted="refuse"))
    make(services, "Approve then ask", line("approve", "probe", tainted="ask"))
    await press(services, conversation, web)  # the default mode's ask, pressed in a normal turn
    own(services, conversation, "Both ask")
    assert (await call(services, conversation, tainted=True)).ran  # tainted verdict: ask, so the grant answers it
    own(services, conversation, "Ask then refuse")
    refused = await call(services, conversation, tainted=True)
    assert not refused.ran and refused.approval is None and "Refused" in refused.text  # tainted verdict: refuse, nothing to answer
    assert (await call(services, conversation)).ran  # the normal verdict is ask: covered
    own(services, conversation, "Approve then ask")
    assert (await call(services, conversation, tainted=True)).ran and (await call(services, conversation, tainted=True)).grant
    assert (await call(services, conversation)).ran and not (await call(services, conversation)).grant  # approved: no grant used


async def test_nothing_ends_it_but_the_time_the_stop_button_and_a_restart(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    for _ in range(60):  # no limit of calls
        assert (await call(services, conversation)).ran
    clock.now += 29 * 60  # no silence of the page to end it
    assert (await call(services, conversation)).ran
    assert services.grants.describe(conversation)[0]["seconds_left"] == 60
    clock.now += 61
    assert (await call(services, conversation)).approval is not None  # 30 minutes
    ended = services.grants.state(conversation)["ended"]
    assert ended and ended[0]["reason"] == "30 minutes passed" and ended[0]["calls"] == 61
    assert services.grants.state(conversation)["ended"] == []  # told once


async def test_the_minutes_are_your_setting_and_the_calls_are_not(services, conversation, web, probe, clock):
    services.runtime.set("approve_all_minutes", 5)
    await press(services, conversation, web)
    clock.now += 4 * 60
    assert (await call(services, conversation)).ran
    clock.now += 61
    assert (await call(services, conversation)).approval is not None
    assert services.grants.state(conversation)["ended"][-1]["reason"] == "5 minutes passed"
    with pytest.raises(KeyError):
        services.runtime.set("approve_all_calls", 5)


async def test_stop_and_pressing_again(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    assert services.grants.stop(conversation) == 1
    assert (await call(services, conversation)).approval is not None
    assert any(row["outcome"] == "grant_end" for row in services.audit.recent())


def test_the_minutes_must_be_a_whole_number_in_range(services):
    for bad in (0, True, "30", 1441):
        with pytest.raises(ValueError):
            services.runtime.set("approve_all_minutes", bad)
    assert services.runtime.get("approve_all_minutes") == 30 and services.runtime.set("approve_all_minutes", 60) == 60


async def test_a_trash_in_a_tainted_turn_is_covered_when_the_mode_asks_for_it_then(services, conversation, web, clock):
    ran: list[dict] = []
    action = Action("", "destructive", "mail_trash", ("mail:gmail",), "trash")
    services.toolbox.add(Tool("trash_probe", "test tool", lambda context, **arguments: ran.append(arguments) or "done", lambda c, a: action))
    tainted = context(services, conversation, tainted=True)  # the turn read mail: the default mode asks for a trash then too
    asked = await services.toolbox.call("trash_probe", tainted, {}, services.toolbox.names)
    assert asked.approval is not None and asked.approval.grantable
    services.grants.arm(asked.approval, web[0])
    await services.approvals.decide(asked.approval.id, "allow_once", web[0])
    for _ in range(2):
        result = await services.toolbox.call("trash_probe", tainted, {}, services.toolbox.names)
        assert result.ran and result.grant
    assert len(ran) == 3


async def test_only_the_web_can_press_and_only_in_a_conversation_of_a_chat(services, conversation, probe):
    asked = await call(services, conversation)
    cli = services.tokens.create("terminal", "cli")[0]
    with pytest.raises(Exception, match="Only the web site"):
        services.grants.arm(asked.approval, cli)
    mail = Action("mail_send", "destructive", "mail_send", ("mail:to:a@b.c",), "send")
    sent = services.approvals.create(mail, Situation("interactive"), context(services, conversation), {}, Decision("ask", "test"))
    assert sent.grantable and asked.approval.grantable  # any request of a chat
    run = services.approvals.create(mail, Situation("unattended"), context(services, conversation), {}, Decision("ask", "test"))
    assert not run.grantable
    with pytest.raises(Exception, match="not an unattended run"):
        services.grants.arm(run, services.tokens.create("other", "web")[0])


@pytest.fixture
async def browser(services, web):
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {web[1]}"}) as http:
        yield http


async def test_the_routes(services, conversation, web, probe, clock, browser):
    asked = await call(services, conversation)
    response = await browser.post(f"/v1/approvals/{asked.approval.id}/grant")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approval"]["status"] == "done" and "beat" not in body
    assert body["grants"][0]["tool"] == "probe" and "max_calls" not in body["grants"][0]
    assert (await call(services, conversation)).ran
    state = (await browser.get(f"/v1/conversations/{conversation}/grants")).json()
    assert state["grants"][0]["calls"] == 1 and state["ended"] == []
    assert (await browser.post(f"/v1/approvals/{asked.approval.id}/grant")).status_code == 409  # answered already
    assert (await browser.delete(f"/v1/conversations/{conversation}/grants")).json() == {"stopped": 1}
    assert (await call(services, conversation)).approval is not None
    assert (await browser.put(f"/v1/conversations/{conversation}/grants", json={"tab": "x" * 8})).status_code == 405  # no beat


async def test_a_token_that_may_not_answer_cannot_press(services, conversation, probe, clock):
    quiet = services.tokens.create("watcher", "web", "none")
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {quiet[1]}"}) as http:
        asked = await call(services, conversation)
        assert (await http.post(f"/v1/approvals/{asked.approval.id}/grant")).status_code == 403
    assert services.grants.describe(conversation) == []


async def test_a_whole_chat_with_the_button(services, llm, browser):
    """A model rewrites a file three times: one card, pressed once, and every call is still printed."""
    await services.workspace.write("a.txt", "0", overwrite=False, message="test")
    rewrite = lambda text: calls(("ws_write", {"path": "a.txt", "content": text, "overwrite": True}))  # noqa: E731
    llm.replies = [rewrite("1"), rewrite("2"), rewrite("3"), "Done."]

    async def pressing() -> None:
        for _ in range(200):
            pending = services.approvals.list("pending")
            if pending:
                answer = await browser.post(f"/v1/approvals/{pending[0].id}/grant")
                assert answer.status_code == 200, answer.text
                return
            await asyncio.sleep(0.02)

    task = asyncio.create_task(pressing())
    response = await browser.post("/v1/chat/stream", json={"message": "rewrite a.txt three times"})
    await task
    events = parse_sse(response.text)
    assert sum(event["type"] == "approval" for event in events) == 1
    tools = [event for event in events if event["type"] == "tool"]
    assert len(tools) == 3 and "grant" not in tools[0] and tools[1]["grant"] == tools[2]["grant"]
    assert events[-1]["reply"] == "Done."
    assert services.workspace.read_text("a.txt").strip() == "3"
