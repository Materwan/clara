"""Auto levels: a ladder from « no tool » to « everything », per-tool thresholds, and the level of a conversation."""

from __future__ import annotations

import pytest

from clara.agent.turn import ChatRequest
from clara.console import COMMANDS
from clara.gate import CLASSES, Action, Decision, LevelError, Situation
from clara.tools.base import Tool, ToolContext, TurnState, fixed
from fakes import calls


def action(tool="some_tool", klass="ws_write", level="write", *targets, secrets=()):
    return Action(tool, level, klass, targets or ("ws:a.txt",), "test", tuple(secrets))


def at(level: int, mode: str = "interactive", **kwargs) -> Situation:
    return Situation(mode, level=level, **kwargs)


def context(services, conversation="", *, mode="interactive", tainted=False, client="web") -> ToolContext:
    return ToolContext(
        services, TurnState("t1", conversation, client, mode=mode, timezone="Europe/Paris", tainted=tainted)
    )


# ----------------------------------------------------------------------
# The ladder and the table
# ----------------------------------------------------------------------
def test_there_are_six_levels_at_first_and_the_first_and_last_are_fixed(services):
    levels = services.levels
    assert [(level.position, level.name) for level in levels.ladder()] == [
        (0, "No tool"),
        (1, "Trusted read"),
        (2, "Any read"),
        (3, "Files and vault"),
        (4, "Mail"),
        (5, "Everything"),
    ]
    assert levels.top == 5
    for position in (0, 5):
        with pytest.raises(LevelError):
            levels.rename(position, "Other")
        with pytest.raises(LevelError):
            levels.remove(position)


def test_level_zero_refuses_every_tool_and_the_last_level_runs_every_tool(services):
    for klass in CLASSES:
        assert services.levels.effect("t", klass, 0) == "refuse", klass
        assert services.levels.effect("t", klass, 5) == "allow", klass


def test_the_default_thresholds_of_a_class_follow_the_ladder_you_agreed(services):
    effect = services.levels.effect
    # trusted read: reads that do not leave anything behind
    assert [effect("t", "read", n) for n in range(6)] == ["refuse", "allow", "allow", "allow", "allow", "allow"]
    # any read: the ones that bring untrusted content in
    assert [effect("t", "mail_read", n) for n in range(6)] == ["refuse", "refuse", "allow", "allow", "allow", "allow"]
    assert effect("t", "fetch_other", 1) == "refuse" and effect("t", "fetch_other", 2) == "allow"
    # files and vault: asks one level below, runs from level 3
    assert [effect("t", "ws_destroy", n) for n in range(6)] == ["refuse", "refuse", "ask", "allow", "allow", "allow"]
    assert effect("t", "vault_destroy", 2) == "ask" and effect("t", "vault_destroy", 3) == "allow"
    # mail: runs from level 4, and the mail send asks at 4 and runs at 5
    assert [effect("t", "mail_trash", n) for n in range(6)] == ["refuse", "refuse", "refuse", "ask", "allow", "allow"]
    assert [effect("t", "mail_send", n) for n in range(6)] == ["refuse", "refuse", "refuse", "refuse", "ask", "allow"]
    assert effect("t", "github_write", 4) == "ask" and effect("t", "github_write", 5) == "allow"


def test_every_tool_names_its_action_classes(everything):
    assert not [name for name in everything.toolbox.names if not everything.toolbox.get(name).classes]
    for name in everything.toolbox.names:
        assert set(everything.toolbox.get(name).classes) <= set(CLASSES), name


@pytest.fixture
def everything(environ, tmp_path, llm):
    from clara.app import build_services, extend, open_database
    from clara.settings import load_settings

    (tmp_path / "vault").mkdir()
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(tmp_path / "vault")}, use_file=False)
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    extend(s)
    yield s
    s.traffic.close()
    db.close()


def test_a_tool_can_have_thresholds_of_its_own_and_goes_back(services):
    levels = services.levels
    levels.set_tool("some_tool", 2, 4)
    assert [levels.effect("some_tool", "ws_write", n) for n in range(6)] == [
        "refuse",
        "refuse",
        "ask",
        "ask",
        "allow",
        "allow",
    ]
    assert levels.effect("other_tool", "ws_write", 3) == "allow"  # the others follow their class
    levels.reset_tool("some_tool")
    assert levels.effect("some_tool", "ws_write", 3) == "allow"
    for bad in ((0, 3), (3, 2), (1, 6), (6, 6)):
        with pytest.raises(LevelError):
            levels.set_tool("some_tool", *bad)


# ----------------------------------------------------------------------
# Adding and removing a level
# ----------------------------------------------------------------------
def test_a_level_added_acts_like_the_level_below_until_you_edit_it(services):
    levels = services.levels
    before = {n: levels.effect("t", "mail_draft", n) for n in range(6)}  # ask at 3, run from 4
    added = levels.add("Careful", before=4)
    assert (added.position, levels.top) == (4, 6)
    assert [level.name for level in levels.ladder()][3:] == ["Files and vault", "Careful", "Mail", "Everything"]
    assert levels.effect("t", "mail_draft", 4) == "ask"  # the new level: like level 3 was
    assert levels.effect("t", "mail_draft", 5) == "allow" and levels.effect("t", "mail_draft", 3) == "ask"
    assert levels.effect("t", "mail_draft", 2) == before[2]
    assert levels.effect("t", "mail_send", 6) == "allow" and levels.effect("t", "mail_send", 5) == "ask"


def test_adding_a_level_moves_what_was_set_to_the_levels_above_it(services):
    levels = services.levels
    mine = services.conversations.create("web", project_id=None)
    services.conversations.update(mine.id, auto_level=4)
    below = services.conversations.create("web")
    services.conversations.update(below.id, auto_level=2)
    services.runtime.set("auto_level_default", 5)
    levels.set_tool("some_tool", 2, 4)
    levels.add("Careful", before=3)
    assert services.conversations.get(mine.id).auto_level == 5  # still « Mail »
    assert services.conversations.get(below.id).auto_level == 2
    assert services.runtime.get("auto_level_default") == 6  # still the last level
    assert levels.overrides()["some_tool"] == (2, 5)


def test_a_level_cannot_be_added_before_the_first_or_past_the_limit(services):
    levels = services.levels
    for bad in (0, 6):
        with pytest.raises(LevelError):
            levels.add("Nope", before=bad)
    with pytest.raises(LevelError):
        levels.add("trusted READ")  # names differ
    for number in range(4):
        levels.add(f"Extra {number}")
    assert levels.top == 9
    with pytest.raises(LevelError):
        levels.add("One too many")


def test_removing_a_level_never_makes_anything_more_permissive(services):
    levels = services.levels
    chat = services.conversations.create("web")
    services.conversations.update(chat.id, auto_level=4)
    other = services.conversations.create("web")
    services.conversations.update(other.id, auto_level=5)
    services.runtime.set("auto_level_unattended", 4)
    levels.set_tool("some_tool", 3, 4)
    levels.remove(4)  # « Mail »
    assert [level.name for level in levels.ladder()] == [
        "No tool",
        "Trusted read",
        "Any read",
        "Files and vault",
        "Everything",
    ]
    assert services.conversations.get(chat.id).auto_level == 3  # below, not above
    assert services.conversations.get(other.id).auto_level == 4  # still the last level
    assert services.runtime.get("auto_level_unattended") == 3
    assert levels.overrides()["some_tool"] == (3, 4)  # it needed « Mail »: now it needs the next level, « Everything »
    assert levels.effect("some_tool", "ws_write", 3) == "ask" and levels.effect("some_tool", "ws_write", 4) == "allow"
    # the classes that were tied to « Mail » follow the next built-in level up
    assert levels.default_thresholds("mail_trash") == (3, 4)
    assert levels.effect("t", "mail_trash", 3) == "ask"


def test_renaming_keeps_the_thresholds(services):
    services.levels.rename(3, "Workbench")
    assert services.levels.name(3) == "Workbench"
    assert services.levels.effect("t", "ws_write", 3) == "allow"
    with pytest.raises(LevelError):
        services.levels.rename(2, "workbench")


# ----------------------------------------------------------------------
# The level of a conversation
# ----------------------------------------------------------------------
def test_a_conversation_follows_its_automation_then_its_project_then_the_default(services):
    levels = services.levels
    project = services.projects.create("Mail sorting")
    chat = services.conversations.create("web", project_id=project.id)
    assert levels.where(chat.id, "interactive") == (3, "default")
    assert levels.where(chat.id, "unattended") == (3, "default")
    services.runtime.set("auto_level_unattended", 1)
    assert levels.where(chat.id, "unattended") == (1, "default")
    levels.set_stored("projects", project.id, 4)
    assert levels.where(chat.id, "interactive") == (4, "project")  # it follows the project live
    levels.set_stored("projects", project.id, 2)
    assert levels.where(chat.id, "interactive") == (2, "project")
    services.conversations.update(chat.id, auto_level=5)
    assert levels.where(chat.id, "interactive") == (5, "own")
    services.conversations.update(chat.id, auto_level=None)
    assert levels.where(chat.id, "interactive") == (2, "project")
    levels.set_stored("projects", project.id, None)
    assert levels.where(chat.id, "interactive") == (3, "default")


def test_a_run_takes_the_level_of_its_schedule_or_its_hook(services):
    levels = services.levels
    schedule = services.schedules.create(name="Weekly", prompt="Summarise.", at="2099-01-05T09:00")
    conversation = services.conversations.create("server", kind="schedule")
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (conversation.id, schedule.id))
    assert levels.where(conversation.id, "unattended") == (3, "default")
    levels.set_stored("schedules", schedule.id, 1)
    assert levels.where(conversation.id, "unattended") == (1, "automation")
    services.conversations.update(conversation.id, auto_level=2)
    assert levels.where(conversation.id, "unattended") == (2, "own")
    with pytest.raises(LevelError):
        levels.set_stored("schedules", 999, 1)
    with pytest.raises(LevelError):
        levels.set_stored("schedules", schedule.id, 9)  # past the last level


# ----------------------------------------------------------------------
# What the gate does with it
# ----------------------------------------------------------------------
def test_a_refusal_of_the_level_is_final_whatever_a_rule_says(services):
    services.rules.add("allow", tool="some_tool")
    send = action("some_tool", "mail_send", "destructive", "mail:to:a@b.c")
    refused = services.gate.decide(send, at(3))
    assert refused.effect == "refuse" and refused.by_level and not refused.floor
    assert "level 3 (Files and vault)" in refused.reason and "asks from level 4" in refused.reason
    assert services.gate.decide(send, at(4)).effect == "allow"  # where the table only asks, the rule decides
    assert services.gate.decide(send, at(0)).effect == "refuse"
    assert services.gate.decide(action("read_tool", "read", "read"), at(0)).by_level


def test_a_rule_that_asks_or_refuses_still_does_at_any_level(services):
    services.rules.add("ask", tool="some_tool")
    assert services.gate.decide(action(), at(5)).effect == "ask"
    services.rules.add("refuse", tool="some_tool", target="a.txt")
    assert services.gate.decide(action(), at(5)).effect == "refuse"


def test_the_last_level_of_a_chat_suspends_the_raises_but_a_run_nobody_watches_never_does(services):
    gate = services.gate
    push = action("github_write", "github_write", "write", "github:me/r")
    assert gate.decide(push, at(4, tainted=True)).effect == "ask"  # tainted: asks anyway
    assert gate.decide(push, at(5)).effect == "allow"
    assert gate.decide(push, at(5, tainted=True)).effect == "allow"  # the owner chose it: level 5 suspends the raise
    leaky = action("github_write", "github_write", "write", "github:me/r", secrets=["GitHub token"])
    assert gate.decide(leaky, at(5)).effect == "allow"
    assert gate.decide(leaky, at(4)).effect == "ask"
    # a hook starts tainted: not even its last level lifts that
    assert gate.decide(push, at(5, "unattended", tainted=True)).effect == "ask"
    assert gate.decide(leaky, at(5, "unattended")).effect == "ask"


def test_a_tainted_turn_below_the_last_level_still_asks_for_what_the_table_runs(services):
    """Red team: untrusted content was read, at level 3 the vault runs without asking, but a vault write is raised."""
    note = action("vault_edit", "vault_write", "write", "vault:a")
    assert services.gate.decide(note, at(3)).effect == "allow"
    tainted = services.gate.decide(note, at(3, tainted=True))
    assert tainted.effect == "ask" and tainted.raised == ("this turn read untrusted content",)
    # and what is outside the level does not ask, it is refused
    assert services.gate.decide(action("github_write", "github_write", "write", "github:r"), at(3, tainted=True)).effect == "refuse"
    services.runtime.set("tainted_rule", False)
    assert services.gate.decide(note, at(3, tainted=True)).effect == "allow"


def test_the_computer_folder_is_never_read_by_a_run_nobody_watches(services):
    for klass in ("folder_read", "folder_sensitive"):
        decision = services.gate.decide(action("folder_read", klass, "read", "folder:x"), at(5, "unattended"))
        assert decision.effect == "refuse" and decision.floor


def test_an_answered_request_is_not_judged_by_the_level_again(services):
    send = action("some_tool", "mail_send", "destructive", "mail:to:a@b.c")
    assert services.gate.decide(send, at(1)).effect == "refuse"
    assert services.gate.decide(send, at(1), approved=True).effect == "allow"
    services.rules.add("refuse", tool="some_tool")  # a rule that refuses still stops it
    assert services.gate.decide(send, at(1), approved=True).effect == "refuse"


def test_the_rule_tester_explains_the_level(services):
    explained = services.gate.explain(action("some_tool", "mail_trash", "destructive"), at(3))
    assert explained["effect"] == "ask"
    assert explained["level"] == {"position": 3, "name": "Files and vault", "ask_from": 3, "approve_from": 4}


# ----------------------------------------------------------------------
# What a turn does with it
# ----------------------------------------------------------------------
@pytest.fixture
def probe(services) -> list[str]:
    ran: list[str] = []
    services.toolbox.add(
        Tool("probe", "p", lambda context, **a: ran.append("x") or "done", fixed("mail_draft", "write", "mail:gmail", "d"))
    )
    return ran


async def test_the_level_is_read_at_each_call_so_lowering_it_stops_the_next_tool(services, probe):
    chat = services.conversations.create("web")
    services.conversations.update(chat.id, auto_level=4)
    ctx = context(services, chat.id)
    assert (await services.toolbox.call("probe", ctx, {}, services.toolbox.names)).ran
    services.conversations.update(chat.id, auto_level=3)  # lowered in the middle of the turn
    asked = await services.toolbox.call("probe", ctx, {}, services.toolbox.names)
    assert asked.approval is not None and "level 3 (Files and vault) asks" in asked.approval.reason
    services.conversations.update(chat.id, auto_level=1)
    refused = await services.toolbox.call("probe", ctx, {}, services.toolbox.names)
    assert refused.text.startswith("Refused") and "raise the auto level" in refused.text and "level 1" in refused.text
    assert probe == ["x"]


async def test_what_the_level_lets_through_is_in_the_audit_log_with_the_level(services, probe):
    chat = services.conversations.create("web")
    services.conversations.update(chat.id, auto_level=4)
    await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    row = next(r for r in services.audit.recent() if r["tool"] == "probe")
    assert row["outcome"] == "ran" and "level 4 (Mail)" in row["detail"]


async def test_an_approved_request_runs_whatever_the_level_became(services, probe):
    chat = services.conversations.create("web")
    services.conversations.update(chat.id, auto_level=3)
    asked = await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    services.conversations.update(chat.id, auto_level=0)  # lowered while the card waited
    tester = services.tokens.create("someone", "web")[0]
    settled = await services.approvals.decide(asked.approval.id, "allow_once", tester)
    assert settled.status == "done" and probe == ["x"]  # you answered that very call


async def test_a_new_conversation_can_be_started_at_a_level_and_a_project_sets_the_rest(services, llm, client):
    llm.replies = ["Hello."]
    response = await client.post("/v1/chat", json={"message": "hi", "auto_level": 1})
    assert response.status_code == 200, response.text
    chat = services.conversations.listing()[0]
    assert chat["auto_level"] == 1
    assert (await client.post("/v1/chat", json={"message": "hi", "auto_level": 9})).status_code == 422


async def test_a_hook_or_a_schedule_run_keeps_its_own_level_for_the_tools_it_calls(services, llm):
    services.runtime.set("auto_level_unattended", 2)
    llm.replies = [calls(("ws_write", {"path": "a.txt", "content": "x"})), "Asked."]
    await services.agent.answer(ChatRequest(message="go", client="server", mode="unattended", kind="schedule"))
    pending = services.approvals.list("pending")
    assert [a.tool for a in pending] == ["ws_write"]  # level 2 asks for a file write; nobody was there: it is held
    assert "level 2 (Any read) asks" in pending[0].reason


async def test_the_decision_of_a_card_names_the_level_that_asked(services, probe):
    chat = services.conversations.create("web")
    services.conversations.update(chat.id, auto_level=3)
    asked = await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    assert asked.approval.reason.startswith("level 3 (Files and vault) asks for probe (it runs from level 4 (Mail))")
    assert isinstance(services.gate.decide(action(), at(3)), Decision)


# ----------------------------------------------------------------------
# The API
# ----------------------------------------------------------------------
async def test_the_page_reads_the_ladder_the_defaults_and_every_tool(client, services):
    body = (await client.get("/v1/levels")).json()
    assert [level["name"] for level in body["levels"]][0] == "No tool" and body["top"] == 5
    assert body["defaults"] == {"chat": 3, "unattended": 3}
    tool = next(t for t in body["tools"] if t["name"] == "ws_delete")
    assert (tool["classes"], tool["ask_from"], tool["approve_from"], tool["changed"]) == (["ws_destroy"], 2, 3, False)
    assert {row["class"] for row in body["classes"]} == set(CLASSES)


async def test_the_ladder_is_edited_from_the_page(client, services):
    added = await client.post("/v1/levels", json={"name": "Careful", "before": 4})
    assert added.status_code == 201 and added.json()["top"] == 6
    assert (await client.patch("/v1/levels/4", json={"name": "Very careful"})).json()["levels"][4]["name"] == "Very careful"
    assert (await client.patch("/v1/levels/0", json={"name": "Nothing"})).status_code == 422
    assert (await client.delete("/v1/levels/6")).status_code == 422  # the last one is fixed
    assert (await client.delete("/v1/levels/4")).json()["top"] == 5
    assert any(row["tool"] == "auto_level" and row["outcome"] == "level" for row in services.audit.recent())


async def test_a_tools_thresholds_are_set_and_reset_and_a_default_is_not_remembered(client, services):
    body = (await client.put("/v1/levels/tools/ws_delete", json={"ask_from": 1, "approve_from": 5})).json()
    tool = next(t for t in body["tools"] if t["name"] == "ws_delete")
    assert (tool["ask_from"], tool["approve_from"], tool["changed"]) == (1, 5, True)
    assert services.gate.decide(action("ws_delete", "ws_destroy", "destructive"), at(4)).effect == "ask"
    back = (await client.put("/v1/levels/tools/ws_delete", json={"ask_from": 2, "approve_from": 3})).json()
    assert next(t for t in back["tools"] if t["name"] == "ws_delete")["changed"] is False
    assert services.levels.overrides() == {}
    assert (await client.put("/v1/levels/tools/ws_delete", json={"ask_from": 3, "approve_from": 2})).status_code == 422
    assert (await client.put("/v1/levels/tools/nothing", json={"ask_from": 1, "approve_from": 2})).status_code == 404
    await client.put("/v1/levels/tools/ws_delete", json={"ask_from": 1, "approve_from": 4})
    assert (await client.delete("/v1/levels/tools/ws_delete")).status_code == 200 and services.levels.overrides() == {}


async def test_the_defaults_and_the_level_of_a_conversation_a_project_a_schedule_and_a_hook(client, services):
    assert (await client.put("/v1/levels/defaults", json={"chat": 2, "unattended": 1})).json()["defaults"] == {
        "chat": 2,
        "unattended": 1,
    }
    assert (await client.put("/v1/levels/defaults", json={"chat": 7})).status_code == 422
    chat = services.conversations.create("web")
    assert (await client.get(f"/v1/conversations/{chat.id}/level")).json() == {
        "own": None,
        "level": 2,
        "name": "Any read",
        "source": "default",
        "top": 5,
    }
    set_ = await client.put(f"/v1/conversations/{chat.id}/level", json={"level": 4})
    assert set_.json()["level"] == 4 and set_.json()["source"] == "own"
    assert (await client.put(f"/v1/conversations/{chat.id}/level", json={"level": 6})).status_code == 422
    assert (await client.put(f"/v1/conversations/{chat.id}/level", json={"level": None})).json()["source"] == "default"
    assert (await client.get("/v1/conversations/c0000000000000000/level")).status_code == 404
    project = services.projects.create("P")
    assert (await client.put(f"/v1/projects/{project.id}/level", json={"level": 1})).json() == {"level": 1, "name": "Trusted read"}
    assert (await client.get(f"/v1/projects/{project.id}")).json()["auto_level"] == 1
    assert (await client.put("/v1/projects/999/level", json={"level": 1})).status_code == 404
    schedule = services.schedules.create(name="W", prompt="p", at="2099-01-05T09:00")
    assert (await client.put(f"/v1/schedules/{schedule.id}/level", json={"level": 4})).status_code == 200
    assert (await client.get(f"/v1/schedules/{schedule.id}")).json()["auto_level"] == 4
    trigger = services.triggers.create(name="H", on="mail.received", prompt="p")
    assert (await client.put(f"/v1/triggers/{trigger.id}/level", json={"level": 2})).status_code == 200
    assert (await client.get(f"/v1/triggers/{trigger.id}")).json()["auto_level"] == 2


async def test_the_model_cannot_change_a_level(services):
    names = services.toolbox.names
    assert not any("level" in name for name in names)
    from clara.packs import automations

    assert not any("level" in field for field in automations.BOTH)


async def test_a_token_that_may_not_answer_everything_cannot_raise_a_level(services):
    """A level lets calls run without a card: it is an answer given in advance. A script never may, and the Discord bot
    (scope `safe`, which cannot approve a destructive action) may only lower."""
    import httpx

    from clara.app import create_app

    chat = services.conversations.create("web")
    services.conversations.update(chat.id, auto_level=3)
    project = services.projects.create("P")

    async def as_(name, client):
        token = services.tokens.create(name, client)[1]
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {token}"})

    async with await as_("bot", "discord") as bot:
        assert (await bot.put(f"/v1/conversations/{chat.id}/level", json={"level": 4})).status_code == 403
        assert (await bot.put(f"/v1/conversations/{chat.id}/level", json={"level": 3})).status_code == 200  # no more
        assert (await bot.put(f"/v1/conversations/{chat.id}/level", json={"level": 1})).status_code == 200  # lower
        assert (await bot.put(f"/v1/conversations/{chat.id}/level", json={"level": None})).status_code == 403  # follow: 3, a raise
        services.conversations.update(chat.id, auto_level=3)
        assert (await bot.put(f"/v1/conversations/{chat.id}/level", json={"level": None})).status_code == 200  # follow: still 3
        services.levels.set_stored("projects", project.id, 5)
        services.conversations.update(chat.id, project_id=project.id, auto_level=1)
        # following would be level 5, which is above level 1: that is a raise too
        assert (await bot.put(f"/v1/conversations/{chat.id}/level", json={"level": None})).status_code == 403
        assert (await bot.post("/v1/conversations", json={"auto_level": 5})).status_code == 403
        assert (await bot.post("/v1/conversations", json={"auto_level": 2})).status_code == 201
        for call in (bot.post("/v1/levels", json={"name": "X"}), bot.put("/v1/levels/defaults", json={"chat": 5}),
                     bot.put("/v1/levels/tools/ws_delete", json={"ask_from": 1, "approve_from": 1}),
                     bot.put(f"/v1/projects/{project.id}/level", json={"level": 5})):
            assert (await call).status_code == 403
        assert (await bot.get("/v1/levels")).status_code == 200  # reading is free
    async with await as_("script", "script") as script:
        assert (await script.put(f"/v1/conversations/{chat.id}/level", json={"level": 0})).status_code == 403
        assert (await script.post("/v1/chat", json={"message": "hi", "auto_level": 1})).status_code == 403
        assert services.conversations.get(chat.id).auto_level == 1


# ----------------------------------------------------------------------
# The console
# ----------------------------------------------------------------------
def test_the_console_reads_and_sets_levels(services):
    run = next(command.run for command in COMMANDS if command.name == "level")
    token = services.tokens.create("admin", "console")[0]
    assert "3  Files and vault" in run(services, [], token) and "Default: chats 3 (Files and vault)" in run(services, ["list"], token)
    assert run(services, ["default", "unattended", "1"], token) == "unattended: level 1 (Trusted read)"
    assert run(services, ["default", "4"], token) == "chat: level 4 (Mail)"
    chat = services.conversations.create("web")
    assert run(services, ["conversation", chat.id, "2"], token) == f"conversation {chat.id}: level 2 (Any read)"
    assert run(services, ["conversation", chat.id], token) == "Level 2 (Any read) (from own)"
    assert run(services, ["conversation", chat.id, "follow"], token) == f"conversation {chat.id}: follows"
    assert run(services, ["tool", "ws_delete", "1", "4"], token) == "ws_delete: asks from level 1, runs from level 4"
    assert run(services, ["tool", "ws_delete"], token) == "ws_delete: refused below level 1, asks from 1, runs from 4"
    assert run(services, ["tool", "ws_delete", "reset"], token).endswith("default of its class.")
    assert run(services, ["add", "Careful", "4"], token) == "Added level 4 (Careful)."
    assert run(services, ["rename", "4", "Very careful"], token) == "Level 4 is now Very careful."
    assert run(services, ["remove", "4"], token) == "Removed level 4 (Very careful)."
    assert run(services, ["remove", "5"], token).startswith("Error:")
    assert run(services, ["nonsense"], token).startswith("Usage:")


# ----------------------------------------------------------------------
# The terminal chat
# ----------------------------------------------------------------------
class TerminalApi:
    def __init__(self):
        self.sent: list[tuple[str, str, dict | None]] = []

    def get(self, path, **params):
        if path == "/v1/levels":
            names = ["No tool", "Trusted read", "Any read", "Files and vault", "Mail", "Everything"]
            return {"top": 5, "defaults": {"chat": 3, "unattended": 3},
                    "levels": [{"position": n, "name": name} for n, name in enumerate(names)]}
        return {"own": 1, "level": 1, "name": "Trusted read", "source": "own", "top": 5}

    def put(self, path, body):
        self.sent.append(("PUT", path, body))
        return {}

    def post(self, path, body=None):
        self.sent.append(("POST", path, body))
        return {"id": "c-new"}


def test_the_terminal_shows_sets_and_starts_a_conversation_at_a_level():
    from types import SimpleNamespace

    from clara.cli.chat import level_text

    api = TerminalApi()
    chat = SimpleNamespace(api=api, conversation=None, incognito=False, local_only=False)
    assert "Not started: it will run at the default, level 3 (Files and vault)" in level_text(chat, "")
    assert "Level 1 (Trusted read), from own" in level_text(chat, "1")  # a new conversation is made at that level
    assert chat.conversation == "c-new" and api.sent == [("POST", "/v1/conversations",
        {"incognito": False, "local_only": False, "auto_level": 1})]
    level_text(chat, "follow")
    assert api.sent[-1] == ("PUT", "/v1/conversations/c-new/level", {"level": None})
    assert level_text(chat, "9").startswith("A level is 0 No tool") and level_text(chat, "x").startswith("A level is")
