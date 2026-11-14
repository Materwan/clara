"""Clara runs a skill through a tool: `skill_run(name, arguments)`, the two action classes it has (`skill_run`, `skill_pack`), the list of
skills in her context, and where the instructions of a skill she ran are kept (a user message after her tool call)."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

from clara.agent.turn import ChatRequest, Instructions, _asks
from clara.app import create_app
from clara.gate import CLASSES, Action, Situation
from clara.gate.actions import NOT_GRANTABLE, RAISED_WHEN_TAINTED, normal_target
from clara.gate.approvals import SUMMARY_FULL
from clara.gate.modes import DEFAULT, builtin_default, load_default
from clara.memory.conversations import TurnRow
from clara.packs.deferred import BOOKED_NEVER
from clara.packs.registry import PACKS
from clara.skills import RESERVED, read_skill
from clara.skills.store import MAX_LISTED, SUMMARY_LINE
from clara.skills.tools import MAX_ARGUMENTS
from clara.tools.base import ToolContext, TurnState
from fakes import calls
from modeset import line, make, own


def add(services, name="review-pr", description="Review a pull request", body="Review $ARGUMENTS carefully.", extra="", projects=()):
    text = f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n"
    services.skills.add(read_skill(skill_md=text), scope="projects" if projects else "global", projects=list(projects), replace=True)


def script(llm, *replies):
    """Replies for the model, each one also recording what it was given: the messages as they were then, and the tools offered."""
    seen: list[dict] = []

    def wrap(reply):
        def step(messages, tools):
            seen.append({"messages": [dict(m) for m in messages], "tools": [t["function"]["name"] for t in tools or []]})
            return reply

        return step

    llm.replies = [wrap(reply) for reply in replies]
    return seen


def owner_of(services):
    if not hasattr(services, "owner"):
        services.owner = services.tokens.create("skill-owner", "cli")[0]
    return services.owner


async def chat(services, message="go", *, answer=None, **kwargs):
    """A turn, to its end. `answer`: what to say to the first approval request it shows (allow_once, refuse_once)."""
    tester = owner_of(services)
    kwargs.setdefault("title", "t")  # a titled conversation: no job of the model's is made after the turn
    kwargs.setdefault("client", "web")
    events = []
    async with contextlib.aclosing(services.agent.turn(ChatRequest(message=message, **kwargs))) as stream:
        async for event in stream:
            events.append(event)
            if event["type"] == "approval" and answer:
                await services.approvals.decide(event["approval"]["id"], answer, tester)
    return events


def done(events) -> dict:
    return next(event for event in events if event["type"] == "done")


def approvals(events) -> list[dict]:
    return [event["approval"] for event in events if event["type"] == "approval"]


def rows(services, conversation):
    return services.conversations.read_back(conversation, calls=True)


LOADED = "Skill review-pr is loaded: its instructions follow."


# ----------------------------------------------------------------------
# Running a skill
# ----------------------------------------------------------------------
async def test_clara_runs_a_skill_and_its_instructions_reach_the_next_model_call_as_a_message(services, llm):
    add(services, body="Review $1 and note: $ARGUMENTS")
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "12 urgent"})), "Reviewed.")
    events = await chat(services, "please review pull request 12, it is urgent")
    final = done(events)
    assert final["reply"] == "Reviewed." and final["tainted"] is False and not approvals(events)
    assert "skill_run" in seen[0]["tools"]
    after = seen[1]["messages"]
    assert [m["role"] for m in after[-3:]] == ["assistant", "tool", "user"]  # the instructions follow the tool results
    assert after[-2]["tool_name"] == "skill_run" and after[-2]["content"] == LOADED
    assert after[-1]["content"] == "Review 12 and note: 12 urgent"
    stored = rows(services, final["conversation"])
    assert [(m["role"], m.get("shown", "")) for m in stored] == [
        ("user", ""), ("assistant", ""), ("tool", ""), ("user", "Clara ran /review-pr 12 urgent"), ("assistant", ""),
    ]
    assert stored[3]["content"] == "Review 12 and note: 12 urgent" and stored[3]["client"] == ""
    assert services.agent.starts_tainted(final["conversation"]) is False  # the skill's text is the owner's words
    # the next turn replays it as a message, in place, with the tool result short
    seen = script(llm, "More.")
    await chat(services, "and then?", conversation=final["conversation"])
    replay = seen[0]["messages"]
    index = next(i for i, m in enumerate(replay) if m["content"] == "Review 12 and note: 12 urgent")
    assert replay[index]["role"] == "user" and replay[index - 1]["role"] == "tool" and replay[index - 1]["content"] == LOADED


async def test_a_skill_that_never_says_arguments_gets_them_on_a_last_line(services, llm):
    add(services, body="Do the review.")
    seen = script(llm, calls(("skill_run", {"name": "/review-pr", "arguments": "12"})), "ok")
    await chat(services)
    assert seen[1]["messages"][-1]["content"] == "Do the review.\n\nArguments: 12"
    seen = script(llm, calls(("skill_run", {"name": "review-pr"})), "ok")  # the arguments are optional
    await chat(services)
    assert seen[1]["messages"][-1]["content"] == "Do the review."


async def test_a_skill_that_does_not_exist_is_an_error_and_no_request(services, llm):
    add(services)
    seen = script(llm, calls(("skill_run", {"name": "nothing"})), "ok")
    events = await chat(services)
    assert not approvals(events)
    assert seen[1]["messages"][-1]["role"] == "tool" and "No skill named 'nothing' here." in seen[1]["messages"][-1]["content"]
    for name in ("tools", "skills"):  # a reserved word is never a skill, even if a row holds it
        services.db.execute(
            "INSERT INTO skills (name, description, skill_md, body, scope, size, created_at) VALUES (?, 'x', 'x', 'EXPANDED', 'global', 1, 'x')",
            (name,),
        )
        assert name in RESERVED
        seen = script(llm, calls(("skill_run", {"name": name})), "ok")
        await chat(services)
        assert "No skill named" in seen[1]["messages"][-1]["content"]


async def test_the_arguments_are_capped_so_that_a_request_shows_them_whole(services, llm):
    add(services, extra="packs: day_activity\n")
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "x" * (MAX_ARGUMENTS + 1)})), "ok")
    events = await chat(services)
    assert not approvals(events) and "at most 1,500" in seen[1]["messages"][-1]["content"]
    # the longest arguments, the most packs: the summary of the request is not cut
    add(services, name="big", description="d", extra="packs: [day_activity, day_conversations, files_created, deferred_calls, automations]\n")
    arguments = "é" * MAX_ARGUMENTS
    script(llm, calls(("skill_run", {"name": "big", "arguments": arguments})), "ok")
    events = await chat(services, answer="refuse_once")
    [card] = approvals(events)
    assert card["class"] == "skill_pack" and len(card["summary"]) < SUMMARY_FULL["skill_pack"] and card["summary"].endswith(arguments)


# ----------------------------------------------------------------------
# Where it is offered
# ----------------------------------------------------------------------
async def test_it_is_offered_only_in_a_chat_with_a_skill_she_may_run(services, llm):
    request = ChatRequest(message="x", client="web", title="t")
    assert "skill_run" in services.agent.plan(request).hidden  # no skill at all
    add(services)
    assert "skill_run" not in services.agent.plan(request).hidden
    unattended = ChatRequest(message="x", client="server", mode="unattended")
    assert "skill_run" in services.agent.plan(unattended).hidden
    assert "skill_run" in services.agent.plan(ChatRequest(message="x", client="web", no_tools=True)).hidden
    assert "skill_run" in services.agent.plan(ChatRequest(message="x", client="web", ephemeral=True)).hidden
    # a run nobody watches: no tool, no list in its context, and a call of it is not run whatever the model asks
    seen = script(llm, calls(("skill_run", {"name": "review-pr"})), "ok")
    events = await chat(services, mode="unattended", client="server")
    assert all("skill_run" not in call["tools"] for call in seen)
    assert "Skills you may run" not in seen[0]["messages"][0]["content"]
    assert seen[1]["messages"][-1]["content"] == "Unknown tool: skill_run." and done(events)["conversation"]


async def test_the_tool_is_in_no_pack_and_a_mode_that_refuses_its_class_is_the_off_switch(services, llm):
    assert "skill_run" in services.toolbox.names
    assert not [pack.id for pack in PACKS if "skill_run" in pack.tools]
    assert services.toolbox.get("skill_run").classes == ("skill_run", "skill_pack")
    add(services)
    make(services, "No skills", line("refuse", klass="skill_run"), line("refuse", klass="skill_pack"))
    conversation = services.conversations.create("web", title="t")
    own(services, conversation.id, "No skills")
    seen = script(llm, calls(("skill_run", {"name": "review-pr"})), "ok")
    events = await chat(services, conversation=conversation.id)
    assert not approvals(events) and seen[1]["messages"][-1]["content"].startswith("Refused: mode No skills refuses skill_run")
    assert not [m for m in rows(services, conversation.id) if m.get("shown")]  # nothing was put in the conversation


async def test_a_skill_for_the_person_alone_is_not_listed_and_is_refused_without_a_request(services, llm):
    add(services, name="mine", description="Only for me", extra="disable-model-invocation: true\n")
    add(services, name="shared", description="For both")
    seen = script(llm, calls(("skill_run", {"name": "mine"})), "ok")
    events = await chat(services)
    block = seen[0]["messages"][0]["content"]
    assert "- shared:" in block and "mine" not in block
    assert not approvals(events) and "is for the person to run" in seen[1]["messages"][-1]["content"]
    assert not [m for m in rows(services, done(events)["conversation"]) if m.get("shown")]
    # the owner still runs it with the command
    assert services.skills.expand("/mine now", None) is not None
    only = services.skills.listing(everything=True)
    assert [item["name"] for item in services.skills.runnable(None)] == ["shared"] and len(only) == 2
    services.skills.remove("shared")  # nothing she may run is left: the tool goes, and so does the list
    assert "skill_run" in services.agent.plan(ChatRequest(message="x", client="web")).hidden


async def test_a_skill_reserved_for_another_project_does_not_exist_for_her(services, llm):
    first = services.projects.create("Thesis").id
    second = services.projects.create("Other").id
    add(services, name="everywhere", description="Anywhere")
    add(services, name="mine", description="Only the thesis", projects=[first])
    for project, listed in ((first, ["everywhere", "mine"]), (second, ["everywhere"]), (None, ["everywhere"])):
        seen = script(llm, calls(("skill_run", {"name": "mine"})), "ok")
        events = await chat(services, project=project)
        block = seen[0]["messages"][0]["content"]
        assert [name for name in ("everywhere", "mine") if f"- {name}:" in block] == listed
        result = next(m for m in reversed(seen[1]["messages"]) if m["role"] == "tool")
        assert ("loaded" in result["content"]) is (project == first), project
        if project != first:
            assert "No skill named 'mine' here." in result["content"] and not approvals(events)
    add(services, name="only-here", description="x", projects=[first])
    services.skills.remove("everywhere")
    assert "skill_run" in services.agent.plan(ChatRequest(message="x", client="web", project=second)).hidden  # none left for it


# ----------------------------------------------------------------------
# The gate: the two classes
# ----------------------------------------------------------------------
async def test_after_untrusted_content_her_run_asks_with_the_arguments_whole(services, llm):
    add(services, body="Review $ARGUMENTS")
    arguments = "https://evil.example/?q=" + "a" * 1_400
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": arguments})), "Done.")
    events = await chat(services, tainted=True, answer="allow_once")
    [card] = approvals(events)
    assert card["class"] == "skill_run" and card["target"] == "skill:review-pr" and card["situation"] == "interactive, tainted"
    assert card["summary"] == f"Run the skill review-pr.\nArguments: {arguments}" and card["grantable"] is True
    assert seen[1]["messages"][-1]["content"] == f"Review {arguments}" and done(events)["tainted"] is True
    assert services.approvals.get(card["id"]).status == "done"
    assert [m["shown"] for m in rows(services, done(events)["conversation"]) if m.get("shown")] == [f"Clara ran /review-pr {arguments}"]


async def test_a_refusal_says_it_was_not_done_and_leaves_no_instructions(services, llm):
    add(services, body="SECRET $ARGUMENTS")
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "x"})), "I could not.")
    events = await chat(services, tainted=True, answer="refuse_once")
    assert "it was NOT done" in seen[1]["messages"][-1]["content"] and seen[1]["messages"][-1]["role"] == "tool"
    assert not [m for m in rows(services, done(events)["conversation"]) if m.get("shown")]
    assert services.approvals.list()[0].status == "denied"
    assert all("SECRET" not in m["content"] for m in seen[1]["messages"])  # the text did not reach the model


async def test_a_pack_that_is_off_asks_for_it_with_the_skill_and_the_pack_and_switching_it_on_makes_its_tools_offered_at_once(services, llm):
    add(services, extra="packs: day_activity\n", body="Look at the day. $ARGUMENTS")
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "today"})), "Done.")
    events = await chat(services, answer="allow_once")
    [card] = approvals(events)
    assert card["class"] == "skill_pack" and card["level"] == "write" and card["target"] == "skill:review-pr"
    assert card["summary"].startswith("Run the skill review-pr and switch on the pack Day activity for this conversation")
    assert card["summary"].endswith("Arguments: today") and card["grantable"] is False
    assert "day_activity" not in seen[0]["tools"] and "day_activity" in seen[1]["tools"]  # offered in the same turn
    assert "Now on for this conversation: Day activity" in seen[1]["messages"][-2]["content"]
    assert seen[1]["messages"][-1]["content"] == "Look at the day. today"
    conversation = services.conversations.require(done(events)["conversation"])
    assert "day_activity" in conversation.active_packs()
    # a refusal does not switch it on
    seen = script(llm, calls(("skill_run", {"name": "review-pr"})), "no")
    events = await chat(services, answer="refuse_once")
    assert "day_activity" not in seen[1]["tools"]
    assert "day_activity" not in services.conversations.require(done(events)["conversation"]).active_packs()
    # a pack that is on already: nothing to ask
    on = services.conversations.create("web", title="t")
    services.conversations.update(on.id, packs=["web", "day_activity"])
    script(llm, calls(("skill_run", {"name": "review-pr"})), "ok")
    assert not approvals(await chat(services, conversation=on.id))


async def test_a_pack_that_is_off_and_cannot_be_used_asks_nothing_and_the_result_says_why(services, llm):
    add(services, extra="packs: mail\n")
    seen = script(llm, calls(("skill_run", {"name": "review-pr"})), "ok")
    events = await chat(services)
    assert not approvals(events)
    result = seen[1]["messages"][-2]["content"]
    assert result.startswith(LOADED) and "Mail" in result and "No mailbox is connected" in result
    assert seen[1]["messages"][-1]["content"].startswith("Review")  # the skill ran all the same
    assert "mail_search" not in seen[1]["tools"]


async def test_skill_pack_is_never_covered_by_a_grant_pressed_on_a_skill_run_card(services, llm):
    add(services, name="plain")
    add(services, name="needs-pack", extra="packs: day_activity\n")
    web = services.tokens.create("browser", "web")
    conversation = services.conversations.create("web", title="t").id

    def ctx():
        return ToolContext(services, TurnState("t1", conversation, "web", mode="interactive", timezone="Europe/Paris", tainted=True))

    async def call(name):
        return await services.toolbox.call("skill_run", ctx(), {"name": name}, services.toolbox.names)

    first = await call("plain")  # tainted: asks
    assert first.approval is not None and first.approval.action_class == "skill_run"
    services.grants.arm(first.approval, web[0])
    await services.approvals.decide(first.approval.id, "allow_once", web[0])
    again = await call("plain")
    assert again.ran and again.grant  # Approve all next covers the next run of the same class
    pack = await call("needs-pack")
    assert pack.approval is not None and pack.approval.action_class == "skill_pack" and not pack.ran and not pack.grant
    assert services.grants.cover(Action("skill_run", "write", "skill_pack", (), ""), Situation("interactive"), conversation, "web") is None


def test_the_classes_are_wired():
    assert CLASSES["skill_run"][:2] == ("trusted_read", "trusted_read") and CLASSES["skill_pack"][:2] == ("any_read", "all")
    assert {"skill_run", "skill_pack"} <= RAISED_WHEN_TAINTED and "skill_pack" in NOT_GRANTABLE and "skill_run" not in NOT_GRANTABLE
    assert {"skill_run", "skill_pack"} <= set(SUMMARY_FULL)
    assert normal_target("skill:review-pr") == "skill:review-pr"  # not read as a workspace path
    assert "skill_run" in BOOKED_NEVER  # its instructions are a message of a conversation, and nobody is there when a booked call runs


# ----------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------
def test_the_default_mode_gives_the_classes_these_verdicts():
    lines = {each.klass: each for each in builtin_default().lines}
    assert (lines["skill_run"].verdict, lines["skill_run"].tainted) == ("approve", "ask")
    assert (lines["skill_pack"].verdict, lines["skill_pack"].tainted) == ("ask", "ask")


def test_a_default_mode_file_written_before_the_classes_existed_still_loads_and_the_built_ins_apply(tmp_path):
    path = tmp_path / "default-mode.toml"
    path.write_text('[[verdict]]\nclass = "read"\nverdict = "approve"\n', encoding="utf-8")
    mode, note, from_file = load_default(path, {"remember"})
    assert from_file and "skill_run" in note and "skill_pack" in note
    run = Action("skill_run", "read", "skill_run", ("skill:review-pr",), "x")
    pack = Action("skill_run", "write", "skill_pack", ("skill:review-pr",), "x")
    assert mode.best(run, False).verdict == "approve" and mode.best(run, True).when(True) == "ask"
    assert mode.best(pack, False).verdict == "ask" and mode.best(pack, True).when(True) == "ask"
    assert mode.name == DEFAULT


async def test_a_mode_may_ask_or_refuse_a_class_or_name_one_skill(services, llm, client):
    add(services, name="review-pr")
    add(services, name="weekly-report")
    # per skill: ask for one, approve the rest
    make(services, "Careful", line("ask", klass="skill_run", target="skill:review-pr"))
    # through the modes page's route, which accepts the `skill:` target
    answer = await client.put("/v1/modes/Careful/verdicts", json={"class": "skill_run", "target": "skill:weekly-*", "verdict": "refuse"})
    assert answer.status_code == 200, answer.text
    conversation = services.conversations.create("web", title="t").id
    own(services, conversation, "Careful")
    seen = script(llm, calls(("skill_run", {"name": "weekly-report"})), "ok")
    assert not approvals(await chat(services, conversation=conversation))
    assert seen[1]["messages"][-1]["content"].startswith("Refused: mode Careful refuses skill_run")
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "7"})), "ok")
    [card] = approvals(await chat(services, conversation=conversation, answer="allow_once"))
    assert card["class"] == "skill_run" and card["reason"].startswith("mode Careful asks for")
    assert seen[1]["messages"][-1]["content"] == "Review 7 carefully."
    # the test box names the same decision
    tested = await client.post("/v1/modes/test", json={"tool": "skill_run", "arguments": {"name": "weekly-report"}, "mode": "Careful"})
    assert tested.status_code == 200 and tested.json()["effect"] == "refuse"


# ----------------------------------------------------------------------
# The list in her context
# ----------------------------------------------------------------------
async def test_the_list_is_sorted_the_same_from_one_turn_to_the_next_and_the_rest_of_the_prompt_does_not_change(services, llm):
    seen = script(llm, "plain")
    await chat(services)
    without = seen[0]["messages"][0]["content"]
    assert "Skills you may run" not in without
    add(services, name="zeta", description="Last " + "z" * 300, extra="argument-hint: '[id]'\n")
    add(services, name="alpha", description='"  First\\nline  "')
    seen = script(llm, "one", "two")
    first = await chat(services)
    await chat(services, conversation=done(first)["conversation"])
    one, two = seen[0]["messages"][0]["content"], seen[1]["messages"][0]["content"]
    assert one == two  # byte for byte
    block = one[one.index("## Skills you may run") :].split("\n\n")[0]
    names = [each.split(":")[0] for each in block.splitlines() if each.startswith("- ")]
    assert names == ["- alpha", "- zeta [id]"]
    assert "- alpha: First line" in block and "more skills are not listed" not in block
    entry = next(each for each in block.splitlines() if each.startswith("- zeta"))
    assert entry.endswith("…") and len(entry.split(": ", 1)[1]) == 160  # the description is cut to 160 characters
    assert "the person's words" in block and "never invent a name" in block
    assert without.split("## Current context")[0] == one.split("## Current context")[0]  # the persona is the same text
    assert one.replace(block, "").count("Skills you may run") == 0


async def test_the_list_is_capped_and_says_so_and_a_skill_not_listed_still_runs_by_name(services, llm):
    for number in range(30):
        add(services, name=f"skill-{number:02d}", description="d" * 1_000, extra="argument-hint: '[something to say]'\n")
    seen = script(llm, calls(("skill_run", {"name": "skill-29"})), "ok")
    events = await chat(services)
    block = seen[0]["messages"][0]["content"]
    section = block[block.index("## Skills you may run") :].split("\n\n")[0]
    listed = [each for each in section.splitlines() if each.startswith("- ")]
    assert len(section) <= MAX_LISTED and 10 < len(listed) < 30 and listed[0].startswith("- skill-00 [something to say]: ddd")
    assert f"({30 - len(listed)} more skills are not listed here" in section and "by its exact name" in section
    assert "- skill-29" not in section and "loaded" in seen[1]["messages"][-2]["content"]
    assert [m["shown"] for m in rows(services, done(events)["conversation"]) if m.get("shown")] == ["Clara ran /skill-29"]


# ----------------------------------------------------------------------
# Where the instructions are kept
# ----------------------------------------------------------------------
async def test_the_instructions_stay_after_more_than_five_later_tool_results(services, llm):
    add(services, body="REMEMBER THIS $ARGUMENTS")
    script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "now"})), "ok")
    first = await chat(services)
    conversation = done(first)["conversation"]
    for number in range(7):  # seven later turns, each with a tool result
        services.conversations.add_turn(
            conversation, f"question {number}",
            [TurnRow("assistant", "", [{"function": {"name": "recall_facts", "arguments": {}}}]), TurnRow("tool", f"result {number}", tool_name="recall_facts"), TurnRow("assistant", "ok")],
        )
    seen = script(llm, "ok")
    await chat(services, conversation=conversation)
    prompt = seen[0]["messages"]
    results = [m["content"] for m in prompt if m["role"] == "tool"]
    assert LOADED not in results and sum(1 for text in results if text.startswith("result")) == 5  # the old results were cut...
    assert any(m["role"] == "user" and m["content"] == "REMEMBER THIS now" for m in prompt)  # ...the instructions were not


async def test_a_prompt_that_is_cut_never_takes_the_instructions_for_the_question():
    question = {"role": "user", "content": "q"}
    kept = Instructions({"role": "user", "content": "instructions"})
    assert _asks(question) and not _asks(kept) and not _asks({"role": "tool", "content": "x"})
    from clara.agent.turn import Agent

    messages = [{"role": "system", "content": "s"}, question, {"role": "assistant", "content": ""}, {"role": "tool", "content": "t"}, kept]
    assert Agent._drop_oldest_turn(messages) is False and len(messages) == 5  # one turn: nothing to drop
    older = [{"role": "system", "content": "s"}, {"role": "user", "content": "old"}, {"role": "assistant", "content": "a"}, *messages[1:]]
    assert Agent._drop_oldest_turn(older) is True and older[1] is question and older[-1] is kept


async def test_the_row_is_no_turn_and_no_question(services, llm):
    add(services)
    script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "1"})), "ok")
    first = await chat(services)
    conversation = done(first)["conversation"]
    [skill] = [m for m in services.conversations.messages_after(conversation) if m.shown.startswith("Clara ran")]
    assert services.conversations.turns_after(conversation) == 1  # one question, not two
    assert services.conversations.question(conversation, skill.id) is None  # it cannot be asked again or edited
    assert len(services.mirror.stored_turns(conversation)) == 1  # Discord shows no question for it
    assert [m.id for m in services.conversations.history(conversation, 1)][0] < skill.id  # the turn is kept whole
    listed = services.conversations.listing()
    assert listed[0]["preview"] == "go"


async def test_the_addresses_of_a_skill_she_ran_are_not_yours(services, llm):
    add(services, body="Read https://example.org/guide first. $ARGUMENTS")
    script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "https://other.example/x"})), "ok")
    first = await chat(services)
    request = ChatRequest(message="go on", client="web", conversation=done(first)["conversation"])
    plan = services.agent.plan(request)
    script(llm, "ok")
    async for _ in services.agent.turn(request, plan):
        pass
    assert not plan.state.trusted_urls


# ----------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------
async def test_the_summary_names_a_skill_clara_ran_and_the_summariser_is_not_given_its_text(services, llm):
    add(services, body="SECRET INSTRUCTIONS $ARGUMENTS")
    add(services, name="weekly-report", description="Weekly", body="Weekly.")
    script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "12"})), "Reviewed.")
    first = await chat(services, "review it please")
    conversation = done(first)["conversation"]
    services.conversations.add_turn(conversation, "/weekly-report", [], shown="/weekly-report")  # one the owner typed
    for number in range(4):
        services.conversations.add_turn(conversation, f"more {number}", [])
    llm.replies = ["They reviewed a pull request."]
    await services.agent.compact(conversation)
    summary = services.conversations.get(conversation).summary
    assert summary == f"They reviewed a pull request.\n\n{SUMMARY_LINE}review-pr, weekly-report"
    transcript = llm.seen[-1]["messages"][-1]["content"]
    assert "Clara: [ran the skill /review-pr 12]" in transcript and "SECRET INSTRUCTIONS" not in transcript
    assert "Person: Clara ran" not in transcript  # it was not the person
    assert LOADED[:20] in transcript  # her call and its short result are there


# ----------------------------------------------------------------------
# An answer that no turn holds
# ----------------------------------------------------------------------
async def test_a_request_answered_with_no_turn_holding_it_still_leaves_the_instructions_for_the_next_turn(services, llm):
    add(services, body="Review $ARGUMENTS and SAY SO " + "very " * 100)  # longer than the 300 characters a note carries
    tester = owner_of(services)
    services.approvals.followup_delay = 0.01
    seen = script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "12"})), "Reviewed, as asked.")
    request = ChatRequest(message="review 12", client="web", title="t", tainted=True)
    async with contextlib.aclosing(services.agent.turn(request)) as stream:
        async for event in stream:
            if event["type"] == "turn":
                conversation = event["conversation"]
            if event["type"] == "approval":
                approval_id = event["approval"]["id"]
                break  # the client goes away: nothing holds the request any more
    await services.approvals.decide(approval_id, "allow_once", tester)  # run by `decide`, not by a turn
    approval = services.approvals.get(approval_id)
    assert approval.status == "done" and approval.result == LOADED
    for _ in range(200):  # the short follow-up turn that tells her how it ended
        if len(seen) == 2:
            break
        await asyncio.sleep(0.02)
    assert len(seen) == 2, "the follow-up turn did not run"
    prompt = seen[1]["messages"]
    assert "skill_run" not in seen[1]["tools"]  # a run nobody watches
    assert prompt[-1]["role"] == "user" and "[Approval update]" in prompt[-1]["content"] and "approved and done" in prompt[-1]["content"]
    assert prompt[-2]["role"] == "user" and prompt[-2]["content"].startswith("Review 12 and SAY SO very")  # the whole text, in the conversation
    assert len(prompt[-2]["content"]) > 300
    stored = [m for m in rows(services, conversation) if m.get("shown")]
    assert [m["shown"] for m in stored] == ["Clara ran /review-pr 12"]
    # and a later turn of the owner's sees it too
    seen = script(llm, "ok")
    await chat(services, conversation=conversation)
    assert any(m["role"] == "user" and m["content"].startswith("Review 12 and SAY SO") for m in seen[0]["messages"])


async def test_a_request_the_turn_waited_for_is_run_by_the_turn_once(services, llm):
    add(services)
    script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "5"})), "ok")
    events = await chat(services, tainted=True, answer="allow_once")
    stored = [m for m in rows(services, done(events)["conversation"]) if m.get("shown")]
    assert len(stored) == 1  # not once by `decide` and once by the turn
    assert services.approvals.list()[0].status == "done"


# ----------------------------------------------------------------------
# The model has no tool to add, change or remove a skill
# ----------------------------------------------------------------------
def test_no_tool_adds_changes_or_removes_a_skill(services):
    assert [name for name in services.toolbox.names if "skill" in name] == ["skill_run"]
    for tool in map(services.toolbox.get, services.toolbox.names):
        assert not {"skill_md", "zip_base64", "replace"} & set(tool.parameters), tool.name
    assert set(services.toolbox.get("skill_run").parameters) == {"name", "arguments"}


@pytest.fixture
async def http(services, token):
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test", headers={"Authorization": f"Bearer {token}"}) as client:
        yield client


async def test_the_messages_route_gives_the_row_its_shown(services, llm, http):
    add(services)
    script(llm, calls(("skill_run", {"name": "review-pr", "arguments": "9"})), "ok")
    first = await chat(services)
    shown = (await http.get(f"/v1/conversations/{done(first)['conversation']}/messages")).json()["messages"]
    ran = [m for m in shown if m.get("shown", "").startswith("Clara ran /")]
    assert [m["shown"] for m in ran] == ["Clara ran /review-pr 9"] and ran[0]["role"] == "user" and ran[0]["content"] == "Review 9 carefully."


async def test_a_skill_that_needs_the_vault_opens_it_in_the_same_turn(environ, tmp_path, llm):
    """The vault is a pack stored as `vault_open`: switching it on puts its block in the prompt and offers its tools from the next round."""
    from clara.app import build_services, extend, open_database
    from clara.settings import load_settings

    (tmp_path / "vault").mkdir()
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(tmp_path / "vault"), "CLARA_VAULT_PUSH": "false"}, use_file=False)
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    extend(s)
    s.runtime.set("default_model", "local:fake")
    s.runtime.set("reader", False)
    try:
        add(s, extra="packs: second_brain\n")
        seen = script(llm, calls(("skill_run", {"name": "review-pr"})), "ok")
        events = await chat(s, answer="allow_once")
        [card] = approvals(events)
        assert card["class"] == "skill_pack" and "Second brain" in card["summary"]
        assert "vault_search" not in seen[0]["tools"] and "vault_search" in seen[1]["tools"]
        assert seen[0]["messages"][0]["content"] != seen[1]["messages"][0]["content"] and "vault" in seen[1]["messages"][0]["content"].lower()
        assert s.conversations.require(done(events)["conversation"]).vault_open
    finally:
        s.traffic.close()
        db.close()
