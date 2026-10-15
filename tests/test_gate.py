"""Gate 3: the rule gate. Specificity, ties, the floor, the defaults, the raises, approvals, the audit log."""

from __future__ import annotations

import asyncio

import pytest

from clara.agent.turn import ChatRequest
from clara.gate import Action, RuleError, Situation
from clara.tools.base import Tool, fixed
from conftest import parse_sse
from fakes import calls

CHAT = Situation("interactive")
ALONE = Situation("unattended")


def action(tool="ws_delete", level="destructive", klass="ws_destroy", *targets, secrets=()):
    return Action(tool, level, klass, targets or ("ws:scratch/a.txt",), "test", tuple(secrets))


def test_defaults_follow_the_class(services):
    gate = services.gate
    assert gate.decide(action("ws_read", "read", "read"), CHAT).effect == "allow"
    assert gate.decide(action("ws_write", "write", "ws_write"), CHAT).effect == "allow"
    assert gate.decide(action(), CHAT).effect == "ask"
    assert gate.decide(action("ws_run", "write", "run"), CHAT).effect == "allow"
    assert gate.decide(action("ws_run", "write", "run"), ALONE).effect == "ask"
    assert gate.decide(action("web_fetch", "read", "fetch_other", "domain:x.org"), CHAT).effect == "ask"
    assert gate.decide(action("web_fetch", "read", "fetch_other", "domain:x.org"), ALONE).effect == "refuse"


def test_the_most_specific_rule_wins_and_ties_go_to_the_stricter(services):
    rules, gate = services.rules, services.gate
    rules.add("allow", tool="ws_delete")
    assert gate.decide(action(), CHAT).effect == "allow"
    rules.add("refuse", tool="ws_delete", target="scratch/**")
    assert gate.decide(action(), CHAT).effect == "refuse"  # two fields beat one
    assert gate.decide(action("ws_delete", "destructive", "ws_destroy", "ws:notes/a.md"), CHAT).effect == "allow"
    rules.add("allow", tool="ws_delete", target="scratch/*.txt")
    assert gate.decide(action(), CHAT).effect == "allow"  # a longer literal target is more specific
    rules.add("ask", tool="ws_delete", target="scratch/*.txt")
    assert gate.decide(action(), CHAT).effect == "ask"  # same specificity: the stricter wins


def test_rule_fields_situation_and_level(services):
    rules, gate = services.rules, services.gate
    rules.add("allow", tool="ws_run", situation="unattended")
    assert gate.decide(action("ws_run", "write", "run"), ALONE).effect == "allow"
    rules.add("refuse", level="destructive", situation="tainted")
    assert gate.decide(action(), Situation("interactive", tainted=True)).effect == "refuse"
    assert gate.decide(action(), CHAT).effect == "ask"
    with pytest.raises(RuleError):
        rules.add("allow")  # matches everything: refused


def test_a_move_needs_every_target_for_allow_but_one_for_refuse(services):
    rules, gate = services.rules, services.gate
    move = action("ws_move", "write", "ws_write", "ws:scratch/a", "ws:notes/b")
    rules.add("ask", tool="ws_move", target="scratch/**")
    assert gate.decide(move, CHAT).rule_id is None  # notes/b is not under scratch/: the rule does not match
    rules.add("refuse", tool="ws_move", target="notes/**")
    assert gate.decide(move, CHAT).effect == "refuse"


def test_the_floor_cannot_be_overridden(services):
    services.rules.add("allow", tool="*")
    assert services.gate.decide(action("ws_read", "read", "read", "ws:.git/config"), CHAT).floor
    assert services.gate.decide(action("ws_read", "read", "read", "ws:a/../b"), CHAT).effect == "refuse"


def test_tainted_turns_and_secrets_raise_outside_writes(services):
    gate = services.gate
    services.rules.add("allow", tool="github_write")
    write = action("github_write", "write", "github_write", "github:me/repo")
    assert gate.decide(write, CHAT).effect == "allow"
    assert gate.decide(write, Situation("interactive", tainted=True)).effect == "ask"
    services.runtime.set("tainted_rule", False)
    assert gate.decide(write, Situation("interactive", tainted=True)).effect == "allow"
    leaky = action("github_write", "write", "github_write", "github:me/repo", secrets=["GitHub token"])
    assert gate.decide(leaky, CHAT).effect == "ask"


def test_the_model_has_no_tool_to_approve_or_edit_rules(services):
    names = services.toolbox.names
    assert not any("rule" in name or "approv" in name or "gate" in name for name in names)


async def test_an_unattended_ask_never_runs_and_never_blocks(services, llm):
    await services.workspace.write("doomed.txt", "x", overwrite=False, message="test")
    llm.replies = [calls(("ws_delete", {"path": "doomed.txt"})), "I asked for permission."]
    reply = await services.agent.answer(ChatRequest(message="clean up", client="server", mode="unattended",
                                                    kind="schedule"))
    assert reply == "I asked for permission."
    assert services.workspace.exists("doomed.txt")
    pending = services.approvals.list("pending")
    assert len(pending) == 1 and pending[0].tool == "ws_delete"
    assert services.events.recent(5, ["approval"])  # you were told at once
    outcomes = [row["outcome"] for row in services.audit.recent()]
    assert "asked" in outcomes and "ran" not in outcomes
    services.db.execute("UPDATE approvals SET expires_at = '2020-01-01T00:00:00+00:00'")
    services.approvals.sweep()
    assert services.approvals.get(pending[0].id).status == "expired"
    assert services.workspace.exists("doomed.txt")


async def test_a_chat_waits_for_the_answer_and_allow_always_writes_a_rule(client, services, llm, token):
    await services.workspace.write("old.txt", "x", overwrite=False, message="test")
    llm.replies = [calls(("ws_delete", {"path": "old.txt"})), "Deleted it."]
    tester = services.tokens.find("tester")

    async def answer() -> None:
        for _ in range(100):
            pending = services.approvals.list("pending")
            if pending:
                await services.approvals.decide(pending[0].id, "allow_always", tester)
                return
            await asyncio.sleep(0.02)

    answering = asyncio.create_task(answer())
    response = await client.post("/v1/chat/stream", json={"message": "delete old.txt"})
    await answering
    events = parse_sse(response.text)
    assert any(event["type"] == "approval" for event in events)
    assert events[-1]["reply"] == "Deleted it."
    assert not services.workspace.exists("old.txt")
    rule = services.rules.list()[0]
    assert (rule.effect, rule.tool, rule.target, rule.origin) == ("allow", "ws_delete", "ws:old.txt", "approval")
    approval = services.approvals.list()[0]
    assert approval.status == "done" and approval.told


async def test_a_chat_that_gets_no_answer_does_not_run_it(client, services, llm):
    await services.workspace.write("keep.txt", "x", overwrite=False, message="test")
    llm.replies = [calls(("ws_delete", {"path": "keep.txt"})), "Waiting for you."]
    response = await client.post("/v1/chat/stream", json={"message": "delete keep.txt"})  # the wait is 2 s in tests
    events = parse_sse(response.text)
    tool = next(event for event in events if event["type"] == "tool")
    assert "NOT done" in tool["result"]
    assert services.workspace.exists("keep.txt")
    assert services.approvals.list("pending")


async def test_refuse_always_writes_a_refusal(services):
    tester = services.tokens.create("someone", "web")[0]
    await services.workspace.write("x.txt", "x", overwrite=False, message="t")
    from clara.tools.base import ToolContext, TurnState

    context = ToolContext(services, TurnState("t1", "", "web", mode="unattended", timezone="Europe/Paris"))
    result = await services.toolbox.call("ws_delete", context, {"path": "x.txt"}, services.toolbox.names)
    assert result.approval is not None
    settled = await services.approvals.decide(result.approval.id, "refuse_always", tester)
    assert settled.status == "denied"
    again = await services.toolbox.call("ws_delete", context, {"path": "x.txt"}, services.toolbox.names)
    assert again.text.startswith("Refused") and services.workspace.exists("x.txt")


async def test_the_discord_token_cannot_answer_destructive_requests(services):
    bot = services.tokens.create("bot", "discord")[0]
    assert bot.approvals == "safe"
    await services.workspace.write("y.txt", "x", overwrite=False, message="t")
    from clara.tools.base import ToolContext, TurnState

    context = ToolContext(services, TurnState("t2", "", "discord", mode="unattended", timezone="Europe/Paris"))
    result = await services.toolbox.call("ws_delete", context, {"path": "y.txt"}, services.toolbox.names)
    from clara.gate import ApprovalError

    with pytest.raises(ApprovalError):
        await services.approvals.decide(result.approval.id, "allow_once", bot)


async def test_a_tool_that_reads_the_web_taints_the_turn(services, llm):
    async def fetch(context, url: str) -> str:
        return "page text"

    async def push(context, text: str) -> str:
        return "pushed"

    services.toolbox.add(Tool("fake_fetch", "f", fetch, fixed("fetch_trusted", "read", "domain:x", "fetch"),
                              {"url": {"type": "string"}}, ("url",), taints=True))
    services.toolbox.add(Tool("fake_push", "p", push, fixed("github_write", "write", "github:me/r", "push"),
                              {"text": {"type": "string"}}, ("text",)))
    services.rules.add("allow", tool="fake_push")
    llm.replies = [calls(("fake_push", {"text": "a"})), calls(("fake_fetch", {"url": "u"})),
                   calls(("fake_push", {"text": "b"})), "done"]
    await services.agent.answer(ChatRequest(message="go", client="cli", wait_for_approvals=False))
    outcomes = [(row["tool"], row["outcome"]) for row in reversed(services.audit.recent())]
    assert outcomes == [("fake_push", "ran"), ("fake_fetch", "ran"), ("fake_push", "asked")]


def test_a_local_only_turn_sends_nothing_out(services):
    local = Situation("interactive", local_only=True)
    services.rules.add("allow", tool="web_fetch")  # no rule opens it
    leaving = ("web_search", "fetch_trusted", "fetch_other", "github_read", "github_write", "drive_read", "drive_write",
               "run_network", "vault_write", "vault_destroy")
    for klass in leaving:
        decision = services.gate.decide(action("web_fetch", "read", klass, "domain:x.org"), local)
        assert decision.effect == "refuse" and decision.floor, klass
    assert services.gate.decide(action("ws_write", "write", "ws_write"), local).effect == "allow"
    assert services.gate.decide(action("ws_run", "write", "run"), local).effect == "allow"
    assert services.gate.decide(action("web_fetch", "read", "fetch_trusted", "domain:x.org"), CHAT).effect == "allow"


def test_vault_changes_stay_local_when_the_vault_does_not_push(services):
    services.gate.local_refused = services.gate.local_refused - {"vault_write", "vault_destroy"}
    local = Situation("interactive", local_only=True)
    assert services.gate.decide(action("vault_edit", "write", "vault_write", "vault:a"), local).effect == "allow"


async def test_a_local_only_conversation_is_not_offered_outside_tools(services, llm):
    def offered() -> set[str]:
        chat = [call for call in llm.seen if call["tools"]][-1]  # not a title or summary job
        return {tool["function"]["name"] for tool in chat["tools"]}

    await services.agent.answer(ChatRequest(message="hi", client="cli"))
    assert "web_search" in offered()
    await services.agent.answer(ChatRequest(message="hi", client="cli", local_only=True))
    assert not {name for name in offered() if name.startswith(("web_", "github_", "drive_"))}
    assert "ws_read" in offered()
