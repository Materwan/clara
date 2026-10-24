"""Approve all next: a button that says « allow once » for the next requests of one tool, while the page is there."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from clara.app import create_app
from clara.gate import Action, Grants, Situation, engine
from clara.gate.grants import SILENCE
from clara.tools.base import Tool, ToolContext, TurnState
from conftest import parse_sse
from fakes import calls

TAB = "tab-aaaaaaaa"
OTHER_TAB = "tab-bbbbbbbb"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


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


async def call(services, conversation, **kwargs):
    return await services.toolbox.call("probe", context(services, conversation, **kwargs), {}, services.toolbox.names)


async def press(services, conversation, web, tab=TAB):
    """What the button does: the first call asks, then the request is allowed and the grant starts."""
    asked = await call(services, conversation)
    assert asked.approval is not None
    services.grants.arm(asked.approval, tab, web[0])
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
    assert len(auto) == 3 and "call 3 of 50" in auto[0]["detail"] and "browser" in auto[0]["detail"]
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
    asked = await services.toolbox.call("mail_probe", context(services, conversation), {}, services.toolbox.names)
    assert asked.approval is not None  # another tool, another class
    assert len(probe) == 1


async def test_a_raise_still_asks(services, conversation, web, probe, clock, monkeypatch):
    """The grant answers the plain default ask; what the gate raised on purpose keeps asking."""
    await press(services, conversation, web)
    monkeypatch.setattr(engine, "RAISED_WHEN_TAINTED", engine.RAISED_WHEN_TAINTED | {"ws_destroy"})
    assert (await call(services, conversation, tainted=True)).approval is not None
    services.runtime.set("tainted_rule", False)  # your own switch: the rule is off, so there is no raise
    assert (await call(services, conversation, tainted=True)).ran
    monkeypatch.setattr(engine, "OUTSIDE", engine.OUTSIDE | {"ws_destroy"})
    secret = Action("", "destructive", "ws_destroy", ("ws:p.txt",), "probe", ("private key",))
    services.toolbox._tools["probe"] = Tool("probe", "test tool", lambda context, **a: "done", lambda c, a: secret)
    assert (await call(services, conversation)).approval is not None


async def test_a_rule_that_refuses_and_the_floor_beat_the_grant(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    services.rules.add("refuse", tool="probe")
    result = await call(services, conversation)
    assert not result.ran and result.approval is None and "Refused" in result.text


async def test_it_ends_when_the_page_stops_beating(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    clock.now += SILENCE - 1
    assert services.grants.beat(conversation, TAB)["grants"]  # a beat keeps it
    clock.now += SILENCE - 1
    assert (await call(services, conversation)).ran
    clock.now += SILENCE + 1
    assert (await call(services, conversation)).approval is not None
    ended = services.grants.beat(conversation, TAB)["ended"]
    assert ended and ended[0]["reason"] == "the page stopped beating" and ended[0]["calls"] == 1
    assert services.grants.beat(conversation, TAB)["ended"] == []  # told once


async def test_another_page_cannot_keep_it_alive(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    for _ in range(3):
        clock.now += SILENCE / 2
        services.grants.beat(conversation, OTHER_TAB)
    assert (await call(services, conversation)).approval is not None


async def test_the_limits_are_your_settings(services, conversation, web, probe, clock):
    services.runtime.set("approve_all_calls", 2)
    services.runtime.set("approve_all_minutes", 5)
    await press(services, conversation, web)
    assert (await call(services, conversation)).ran and (await call(services, conversation)).ran
    assert (await call(services, conversation)).approval is not None  # the third call of the grant
    assert services.grants.beat(conversation, TAB)["ended"][0]["reason"] == "2 calls were made"

    await press(services, conversation, web)
    for _ in range(4):  # four minutes of beats
        clock.now += 60
        services.grants.beat(conversation, TAB)
    assert (await call(services, conversation)).ran
    clock.now += 61
    assert services.grants.beat(conversation, TAB)["ended"][-1]["reason"] == "5 minutes passed"
    assert (await call(services, conversation)).approval is not None


async def test_stop_and_pressing_again(services, conversation, web, probe, clock):
    await press(services, conversation, web)
    assert services.grants.stop(conversation) == 1
    assert (await call(services, conversation)).approval is not None
    assert any(row["outcome"] == "grant_end" for row in services.audit.recent())


def test_the_limits_must_be_whole_numbers_in_range(services):
    for key, bad in (("approve_all_calls", 0), ("approve_all_calls", True), ("approve_all_calls", "50"),
                     ("approve_all_calls", 1001), ("approve_all_minutes", 0), ("approve_all_minutes", 1441)):
        with pytest.raises(ValueError):
            services.runtime.set(key, bad)
    assert services.runtime.get("approve_all_calls") == 50 and services.runtime.get("approve_all_minutes") == 30
    assert services.runtime.set("approve_all_calls", 100) == 100


async def test_approve_all_next_covers_a_mail_trash_even_in_a_tainted_turn(services, conversation, web, clock):
    ran: list[dict] = []
    action = Action("", "destructive", "mail_trash", ("mail:gmail",), "trash")
    services.toolbox.add(Tool("trash_probe", "test tool", lambda context, **arguments: ran.append(arguments) or "done", lambda c, a: action))
    tainted = context(services, conversation, tainted=True)  # the turn read mail: a trash is still covered
    asked = await services.toolbox.call("trash_probe", tainted, {}, services.toolbox.names)
    assert asked.approval is not None and asked.approval.grantable
    services.grants.arm(asked.approval, TAB, web[0])
    await services.approvals.decide(asked.approval.id, "allow_once", web[0])
    for _ in range(2):
        result = await services.toolbox.call("trash_probe", tainted, {}, services.toolbox.names)
        assert result.ran and result.grant
    assert len(ran) == 3


async def test_only_the_web_can_press_and_only_some_changes_can_be_granted(services, conversation, probe):
    asked = await call(services, conversation)
    cli = services.tokens.create("terminal", "cli")[0]
    with pytest.raises(Exception, match="Only the web site"):
        services.grants.arm(asked.approval, TAB, cli)
    mail = Action("mail_send", "destructive", "mail_send", ("mail:to:a@b.c",), "send")
    sent = services.approvals.create(mail, Situation("interactive"), context(services, conversation), {}, services.gate.decide(mail, Situation("interactive")))
    assert not sent.grantable and asked.approval.grantable
    with pytest.raises(Exception, match="workspace and vault changes and mail trashing only"):
        services.grants.arm(sent, TAB, services.tokens.create("other", "web")[0])


@pytest.fixture
async def browser(services, web):
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {web[1]}"}) as http:
        yield http


async def test_the_routes(services, conversation, web, probe, clock, browser):
    asked = await call(services, conversation)
    assert (await browser.post(f"/v1/approvals/{asked.approval.id}/grant", json={"tab": "short"})).status_code == 422
    response = await browser.post(f"/v1/approvals/{asked.approval.id}/grant", json={"tab": TAB})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approval"]["status"] == "done" and body["beat"] == 10
    assert body["grants"][0]["tool"] == "probe" and body["grants"][0]["max_calls"] == 50
    assert (await call(services, conversation)).ran
    beat = (await browser.put(f"/v1/conversations/{conversation}/grants", json={"tab": TAB})).json()
    assert beat["grants"][0]["calls"] == 1 and beat["ended"] == []
    again = await browser.post(f"/v1/approvals/{asked.approval.id}/grant", json={"tab": TAB})
    assert again.status_code == 409  # answered already
    assert (await browser.delete(f"/v1/conversations/{conversation}/grants")).json() == {"stopped": 1}
    assert (await call(services, conversation)).approval is not None


async def test_a_token_that_may_not_answer_cannot_press(services, conversation, probe, clock):
    quiet = services.tokens.create("watcher", "web", "none")
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {quiet[1]}"}) as http:
        asked = await call(services, conversation)
        assert (await http.post(f"/v1/approvals/{asked.approval.id}/grant", json={"tab": TAB})).status_code == 403
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
                answer = await browser.post(f"/v1/approvals/{pending[0].id}/grant", json={"tab": TAB})
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
