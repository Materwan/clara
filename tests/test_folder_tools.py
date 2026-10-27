"""Folders on the person's computer (DECISIONS.md, « Folders on your computer »): the paths, the gate classes, which chats
are offered the tools, the budget, the cards, and a turn that walks a folder the browser answers."""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
import pytest

from clara.agent.turn import ChatRequest
from clara.app import create_app
from clara.gate import Action, Decision, Situation
from clara.gate.actions import CLASSES, FOR_A_CONVERSATION, NOT_PUBLISHED
from clara.gate.approvals import ApprovalError
from clara.tools.base import ToolContext, ToolError, TurnState
from clara.tools.folder_tools import TOOL_FOLDERS, FolderGrant, FolderRequest, clean, hidden
from fakes import calls

GRANT = "grant-0001-aaaa"
OTHER = "grant-0002-bbbb"
TOOLS = frozenset(TOOL_FOLDERS)


def page(text: str = "", clear: list[str] | None = None) -> str:
    """What the browser answers for a call that worked."""
    return json.dumps({"text": text, "clear": clear or []})


def failed(message: str) -> str:
    return json.dumps({"error": message})


@pytest.fixture
def conversation(services) -> str:
    return services.conversations.create("web").id


def context(services, conversation: str, *, grant: str | None = GRANT, mode: str = "interactive") -> ToolContext:
    state = TurnState("t-folder", conversation, "web", mode=mode, timezone="Europe/Paris")
    if grant is not None:
        state.folder = FolderGrant(grant, "Documents")
    return ToolContext(services, state)


# ----------------------------------------------------------------------
# Paths and hidden names
# ----------------------------------------------------------------------
def test_a_path_stays_inside_the_folder():
    assert clean("") == ""
    assert clean(" docs ") == "docs"
    assert clean("a\\b/./c/") == "a/b/c"
    for bad in ["../x", "a/../../b", "/etc/passwd", "C:/Users", "a\x00b"]:
        with pytest.raises(ToolError):
            clean(bad)
    with pytest.raises(ToolError):
        clean(3)


def test_a_dot_name_is_hidden_wherever_it_is():
    assert hidden(".env")
    assert hidden("projects/.git/config")
    assert not hidden("projects/notes.md")


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------
def test_the_folder_tools_are_described_by_their_class(services, conversation):
    tools = services.toolbox
    ctx = context(services, conversation)
    listing = tools.action("folder_list", ctx, {"path": "docs"})
    assert (listing.klass, listing.target) == ("folder_read", "folder:docs")
    assert tools.action("folder_find", ctx, {"pattern": "*.pdf"}).klass == "folder_read"
    assert tools.action("folder_find", ctx, {"pattern": "*.pdf", "include_hidden": True}).klass == "folder_sensitive"
    assert tools.action("folder_read", ctx, {"path": ".env"}).klass == "folder_sensitive"
    # a file no listing has named yet asks too: the safe side
    assert tools.action("folder_read", ctx, {"path": "docs/a.txt"}).klass == "folder_sensitive"
    services.folders.remember_clear(conversation, ["docs/a.txt", "docs/.env"])
    assert tools.action("folder_read", ctx, {"path": "docs/a.txt"}).klass == "folder_read"
    assert tools.action("folder_read", ctx, {"path": "docs/.env"}).klass == "folder_sensitive"


def test_a_search_that_starts_inside_a_hidden_folder_asks_like_one_that_includes_hidden_files(services, conversation):
    tools = services.toolbox
    ctx = context(services, conversation)
    for name in ("folder_grep", "folder_find"):
        assert tools.action(name, ctx, {"pattern": "x", "path": ".ssh"}).klass == "folder_sensitive"
        assert tools.action(name, ctx, {"pattern": "x", "path": "app/.git/hooks"}).klass == "folder_sensitive"
        assert tools.action(name, ctx, {"pattern": "x", "path": "app/src"}).klass == "folder_read"
        assert tools.action(name, ctx, {"pattern": "x"}).klass == "folder_read"
    listing = tools.action("folder_list", ctx, {"path": ".ssh"})
    assert listing.klass == "folder_read"  # names only, marked [hidden] by the page: a read of content is what asks


def test_the_folder_classes_and_their_defaults(services):
    assert CLASSES["folder_read"][:2] == ("allow", "refuse")
    assert CLASSES["folder_sensitive"][:2] == ("ask", "refuse")
    assert "folder_sensitive" in FOR_A_CONVERSATION and "folder_sensitive" in NOT_PUBLISHED
    chat = Situation("interactive")
    unattended = Situation("unattended")
    listing = Action("folder_list", "read", "folder_read", ("folder:docs",), "List docs")
    secret = Action("folder_read", "read", "folder_sensitive", ("folder:.env",), "Read .env")
    assert services.gate.decide(listing, chat).effect == "allow"
    assert services.gate.decide(listing, unattended).effect == "refuse"
    assert services.gate.decide(secret, chat).effect == "ask"
    assert services.gate.decide(secret, unattended).effect == "refuse"


# ----------------------------------------------------------------------
# Who is offered the tools
# ----------------------------------------------------------------------
def test_the_tools_are_offered_only_in_a_web_chat_with_a_folder_open(services):
    def hidden_in(request: ChatRequest) -> frozenset[str]:
        return frozenset(services.agent.plan(request).hidden) & TOOLS

    folder = FolderRequest(GRANT, "Documents")
    assert hidden_in(ChatRequest(message="hi", client="web")) == TOOLS
    assert hidden_in(ChatRequest(message="hi", client="web", folder=folder)) == frozenset()
    assert hidden_in(ChatRequest(message="hi", client="cli", folder=folder)) == TOOLS
    assert hidden_in(ChatRequest(message="hi", client="web", mode="unattended", folder=folder)) == TOOLS


# ----------------------------------------------------------------------
# The budget and the grants
# ----------------------------------------------------------------------
def test_the_budget_belongs_to_a_grant_and_a_new_grant_starts_again(services, conversation):
    folders = services.folders
    services.runtime.set("folder_budget", 1000)
    folders.begin(conversation, FolderRequest(GRANT, "Documents"))
    assert folders.budget(conversation, GRANT) == (1000, 0)
    assert folders.charge(conversation, GRANT, 800) == 800
    assert folders.charge(conversation, GRANT, 800) == 200
    assert folders.charge(conversation, GRANT, 1) == 0
    folders.begin(conversation, FolderRequest(GRANT, "Documents", budget=5000))  # the same grant: raised, what was read kept
    assert folders.budget(conversation, GRANT) == (5000, 1000)
    folders.begin(conversation, FolderRequest(OTHER, "Photos"))  # a new grant: a new budget
    assert folders.budget(conversation, OTHER) == (1000, 0)
    assert folders.budget(conversation, GRANT) is None


def test_a_budget_is_capped_and_the_name_is_cleaned(services, conversation):
    grant = services.folders.begin(conversation, FolderRequest(GRANT, "Doc\numents", budget=10**9))
    assert grant.name == "Doc uments"
    assert services.folders.budget(conversation, GRANT) == (16_000_000, 0)


# ----------------------------------------------------------------------
# Through the toolbox: sent to the page, settled, budgeted, tainting
# ----------------------------------------------------------------------
async def test_a_listing_is_sent_to_the_page_and_its_answer_is_settled(services, conversation):
    services.folders.begin(conversation, FolderRequest(GRANT, "Documents"))
    ctx = context(services, conversation)
    outcome = await services.toolbox.call("folder_list", ctx, {"path": ""}, TOOLS)
    assert outcome.dispatch is not None and outcome.text == ""
    text, ok = services.toolbox.settle(outcome.dispatch, page("notes.txt\nsub/", clear=["notes.txt", ".env"]))
    assert ok and text == "notes.txt\nsub/"
    assert services.folders.is_clear(conversation, "notes.txt")
    assert not services.folders.is_clear(conversation, ".env")  # a hidden name is never clear
    assert ctx.turn.tainted  # what a folder says is untrusted content


async def test_a_call_without_a_folder_open_is_not_sent(services, conversation):
    ctx = context(services, conversation, grant=None)
    outcome = await services.toolbox.call("folder_list", ctx, {"path": ""}, TOOLS)
    assert outcome.dispatch is None
    assert "no folder is open" in outcome.text


async def test_reads_count_against_the_budget_and_stop_when_it_is_spent(services, conversation):
    services.runtime.set("folder_budget", 10)
    services.folders.begin(conversation, FolderRequest(GRANT, "Documents"))
    services.folders.remember_clear(conversation, ["notes.txt"])
    ctx = context(services, conversation)
    first = await services.toolbox.call("folder_read", ctx, {"path": "notes.txt"}, TOOLS)
    text, ok = services.toolbox.settle(first.dispatch, page("0123456789ABCDEF"))
    assert ok and text.startswith("0123456789")
    assert "budget ends here" in text
    assert services.folders.budget(conversation, GRANT) == (10, 10)
    again = await services.toolbox.call("folder_read", ctx, {"path": "notes.txt"}, TOOLS)
    assert again.dispatch is None and "budget is spent" in again.text


async def test_a_search_counts_what_it_returns_and_a_listing_does_not(services, conversation):
    services.runtime.set("folder_budget", 1000)
    services.folders.begin(conversation, FolderRequest(GRANT, "Documents"))
    ctx = context(services, conversation)
    outcome = await services.toolbox.call("folder_grep", ctx, {"pattern": "todo"}, TOOLS)
    text, ok = services.toolbox.settle(outcome.dispatch, page("notes.txt:2: TODO: call bob"))
    assert ok
    assert services.folders.budget(conversation, GRANT) == (1000, len(text.encode("utf-8")))
    services.folders.begin(conversation, FolderRequest(GRANT, "Documents", budget=1))  # the same grant, now spent
    searched = await services.toolbox.call("folder_grep", ctx, {"pattern": "todo"}, TOOLS)
    assert searched.dispatch is None and "budget is spent" in searched.text
    listed = await services.toolbox.call("folder_list", ctx, {"path": ""}, TOOLS)
    assert listed.dispatch is not None  # a listing is not file content: it is still allowed


async def test_a_page_error_is_text_for_the_model_and_not_tainting(services, conversation):
    services.folders.begin(conversation, FolderRequest(GRANT, "Documents"))
    ctx = context(services, conversation)
    outcome = await services.toolbox.call("folder_find", ctx, {"pattern": "*.pdf"}, TOOLS)
    text, ok = services.toolbox.settle(outcome.dispatch, failed("the folder is not there any more"))
    assert not ok and text == "Error: the folder is not there any more"
    assert not ctx.turn.tainted


async def test_a_hidden_file_asks_and_nothing_is_published_about_it(services, conversation, monkeypatch):
    services.folders.begin(conversation, FolderRequest(GRANT, "Documents"))
    published: list[tuple] = []
    monkeypatch.setattr(services.events, "publish", lambda *args, **kwargs: published.append(args))
    ctx = context(services, conversation)
    asked = await services.toolbox.call("folder_read", ctx, {"path": ".env"}, TOOLS)
    assert asked.approval is not None and asked.dispatch is None
    assert all(args[0] != "approval" for args in published)  # its summary names a file: it stays in the chat
    services.allowances.add(conversation, "folder_read", "folder:.env", asked.approval.id)
    again = await services.toolbox.call("folder_read", ctx, {"path": ".env"}, TOOLS)
    assert again.approval is None and again.dispatch is not None  # allowed for this conversation: no second card


async def test_an_allowance_is_only_for_folder_reads_of_a_conversation(services, conversation):
    ctx = context(services, conversation)
    action = Action("probe", "destructive", "ws_destroy", ("ws:p.txt",), "probe")
    approval = services.approvals.create(action, Situation("interactive"), ctx, {}, Decision("ask", "test"))
    token = services.tokens.create("browser", "web")[0]
    with pytest.raises(ApprovalError):
        await services.approvals.decide(approval.id, "allow_conversation", token)


async def test_always_is_not_an_answer_for_a_request_that_lasts_one_conversation(services, conversation):
    """A rule for `folder:.env` would apply to the `.env` of every folder opened later."""
    ctx = context(services, conversation)
    action = Action("folder_read", "read", "folder_sensitive", ("folder:.env",), "Read .env")
    approval = services.approvals.create(action, Situation("interactive"), ctx, {"path": ".env"}, Decision("ask", "test"))
    token = services.tokens.create("browser", "web")[0]
    with pytest.raises(ApprovalError, match="cannot be allowed for good"):
        await services.approvals.decide(approval.id, "allow_always", token)
    assert services.approvals.get(approval.id).status == "pending"  # still there to answer
    assert not services.rules.list()
    await services.approvals.decide(approval.id, "allow_conversation", token)
    assert services.allowances.covers(conversation, "folder_read", "folder:.env")


async def test_a_request_that_names_a_file_of_your_computer_is_shown_and_answered_by_the_web_only(services, conversation):
    ctx = context(services, conversation)
    action = Action("folder_read", "read", "folder_sensitive", ("folder:.env",), "Read .env")
    approval = services.approvals.create(action, Situation("interactive"), ctx, {"path": ".env"}, Decision("ask", "test"))
    app = create_app(services)

    def http(client: str, approvals: str | None = None) -> httpx.AsyncClient:
        token = services.tokens.create(f"t-{client}-{approvals}", client, approvals)[1]
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {token}"})

    for client, approvals in (("discord", None), ("script", "all"), ("cli", None)):
        async with http(client, approvals) as other:
            listed = (await other.get("/v1/approvals", params={"status": "pending"})).json()["approvals"]
            assert approval.id not in [item["id"] for item in listed]
            assert (await other.post(f"/v1/approvals/{approval.id}/decide", json={"answer": "allow_once"})).status_code == 404
    async with http("web") as web:
        listed = (await web.get("/v1/approvals", params={"status": "pending"})).json()["approvals"]
        assert approval.id in [item["id"] for item in listed]
        refused = await web.post(f"/v1/approvals/{approval.id}/decide", json={"answer": "allow_always"})
        assert refused.status_code == 422
        assert (await web.post(f"/v1/approvals/{approval.id}/decide", json={"answer": "allow_once"})).status_code == 200


async def test_a_remote_tool_is_never_run_on_the_server(services, conversation):
    text, ok = await services.toolbox.execute("folder_list", context(services, conversation), {"path": ""})
    assert not ok and "while the conversation is open" in text


# ----------------------------------------------------------------------
# A whole turn, with a browser that answers
# ----------------------------------------------------------------------
async def walk(services, request: ChatRequest, answers: dict, approve: str | None = None) -> list[dict]:
    """Run a turn. Each `tool_requests` is answered by `answers[name](arguments)`; each card is answered `approve` (if set)
    while the turn waits for it, as the web site would."""
    token = services.tokens.create("browser", "web")[0]
    events: list[dict] = []
    tasks: list[asyncio.Task] = []

    async def answer_card(approval_id: int) -> None:
        await asyncio.sleep(0.05)  # the turn is waiting by then
        await services.approvals.decide(approval_id, approve, token)

    async with contextlib.aclosing(services.agent.turn(request)) as stream:
        async for event in stream:
            events.append(event)
            if event["type"] == "approval" and approve:
                tasks.append(asyncio.create_task(answer_card(event["approval"]["id"])))
            if event["type"] == "tool_requests":
                for call in event["calls"]:
                    content = answers[call["name"]](call["arguments"])
                    services.agent.submit_results(event["turn"], request.token_id, {call["id"]: content})
    await asyncio.gather(*tasks)
    return events


async def test_a_turn_walks_the_folder_and_reads_a_hidden_file_once_allowed(services, llm, monkeypatch):
    published: list[tuple] = []
    monkeypatch.setattr(services.events, "publish", lambda *args, **kwargs: published.append(args))
    llm.replies = [
        calls(("folder_list", {"path": ""})),
        calls(("folder_read", {"path": "notes.txt"})),
        calls(("folder_read", {"path": ".env"})),
        "The key is in .env.",
    ]
    answers = {
        "folder_list": lambda arguments: page("notes.txt\n.env [ignored]", clear=["notes.txt"]),
        "folder_read": lambda arguments: page(f"content of {arguments['path']}"),
    }
    request = ChatRequest(
        message="walk the folder",
        client="web",
        folder=FolderRequest(GRANT, "Documents"),
        token_id=None,
    )
    events = await walk(services, request, answers, approve="allow_conversation")
    tools = [event for event in events if event["type"] == "tool"]
    assert [event["name"] for event in tools] == ["folder_list", "folder_read", "folder_read"]
    assert "content of .env" in tools[2]["result"]
    assert events[-1]["type"] == "done"
    conversation = next(event["conversation"] for event in events if event["type"] == "turn")
    assert services.allowances.covers(conversation, "folder_read", "folder:.env")
    assert services.folders.budget(conversation, GRANT)[1] > 0
    assert all(args[0] != "approval" for args in published)
