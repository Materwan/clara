"""Modes (the auto levels, the rules and the tainted-turn rule they replaced): the default-mode file, the verdict lines, the order
in which a call is decided, who may change a mode, and what a conversation does with its mode. The red-team cases of the levels
(a refusal is final, a tainted turn never gets more, the secret check always asks, a script or the bot cannot change what runs
without asking, a run nobody watches never waits) are kept here, in their mode form."""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from clara.agent.turn import ChatRequest
from clara.app import StartError, build_services, create_app, extend, open_database, static_tools
from clara.app import main as server_main
from clara.console import COMMANDS
from clara.gate import CLASSES, Action, Line, ModeError, Situation
from clara.gate.legacy import import_legacy
from clara.gate.modes import DEFAULT, builtin_default, parse_default, to_toml
from clara.settings import load_settings
from clara.tools.base import Tool, ToolContext, TurnState, fixed
from fakes import calls
from modeset import line, make, only, own

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def action(tool="some_tool", klass="ws_write", level="write", *targets, secrets=()):
    return Action(tool, level, klass, targets or ("ws:a.txt",), "test", tuple(secrets))


def at(mode_name: str | None = None, mode: str = "interactive", **kwargs) -> Situation:
    return Situation(mode, mode_name=mode_name, **kwargs)


def context(services, conversation="", *, mode="interactive", tainted=False, client="web") -> ToolContext:
    return ToolContext(
        services, TurnState("t1", conversation, client, mode=mode, timezone="Europe/Paris", tainted=tainted)
    )


def decide(services, act, situation, **kwargs):
    return services.gate.decide(act, situation, **kwargs)


@pytest.fixture
def everything(environ, tmp_path, llm):
    (tmp_path / "vault").mkdir()
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(tmp_path / "vault")}, use_file=False)
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    extend(s)
    yield s
    s.traffic.close()
    db.close()


@pytest.fixture
def probe(services) -> list[str]:
    ran: list[str] = []
    services.toolbox.add(
        Tool("probe", "p", lambda context, **a: ran.append("x") or "done", fixed("mail_draft", "write", "mail:gmail", "d"))
    )
    return ran


async def client_as(services, name: str, kind: str) -> httpx.AsyncClient:
    token = services.tokens.create(name, kind)[1]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)),
        base_url="http://clara.test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ----------------------------------------------------------------------
# The lines
# ----------------------------------------------------------------------
def test_the_built_in_default_mode_is_what_the_default_level_did(services):
    gate = services.gate
    normal = {
        "read": "allow", "memory": "allow", "ws_write": "allow", "ws_destroy": "allow", "run": "allow",
        "run_network": "refuse", "github_read": "allow", "github_write": "refuse", "mail_read": "allow",
        "mail_draft": "ask", "mail_send": "refuse", "mail_label": "ask", "mail_trash": "ask", "mail_mark": "ask",
        "mail_label_delete": "refuse", "vault_write": "allow", "vault_destroy": "allow", "web_search": "allow",
        "fetch_trusted": "allow", "fetch_other": "allow", "discord": "refuse", "automation": "refuse",
        "deferred": "ask", "folder_sensitive": "ask", "school_save": "allow",
    }
    for klass, effect in normal.items():
        assert decide(services, action("t", klass, "write"), at()).effect == effect, klass
    # what the tainted-turn rule raised asks at least when the conversation is tainted; the rest is the same
    tainted = {"vault_write": "ask", "vault_destroy": "ask", "fetch_other": "ask", "deferred": "ask",
               "github_write": "refuse", "mail_send": "refuse", "ws_write": "allow", "mail_trash": "ask", "read": "allow"}
    for klass, effect in tainted.items():
        assert gate.decide(action("t", klass, "write"), at(tainted=True)).effect == effect, klass
    assert {ln.klass for ln in builtin_default().lines} == set(CLASSES)


def test_a_line_is_checked(services):
    with pytest.raises(ModeError, match="more permissive"):
        line("ask", "t", tainted="approve")
    with pytest.raises(ModeError, match="at least one"):
        line("ask")
    with pytest.raises(ModeError, match="No action class"):
        line("ask", klass="nothing")
    with pytest.raises(ModeError, match="verdict is one of"):
        line("allow", "t")
    assert line("ask", "t").tainted == "ask" and line("approve", "t", tainted="refuse").tainted == "refuse"


def test_the_most_specific_line_wins_and_ties_go_to_the_stricter(services):
    delete = action("ws_delete", "ws_destroy", "destructive", "ws:scratch/a.txt")
    only(services, line("approve", klass="ws_destroy"), line("ask", tool="ws_delete"))
    assert decide(services, delete, at()).effect == "ask"  # a tool name beats a class alone
    only(services, line("approve", klass="ws_destroy"), line("ask", "ws_delete"), line("refuse", "ws_delete", "ws_destroy"))
    assert decide(services, delete, at()).effect == "refuse"  # two fields beat one
    only(services, line("refuse", "ws_delete", "ws_destroy"), line("approve", "ws_delete", "ws_destroy", "scratch/**"))
    assert decide(services, delete, at()).effect == "allow"  # three fields beat two
    assert decide(services, action("ws_delete", "ws_destroy", "destructive", "ws:notes/a.md"), at()).effect == "refuse"
    only(services, line("approve", "ws_delete", "ws_destroy", "scratch/**"), line("ask", "ws_delete", "ws_destroy", "scratch/*.txt"))
    assert decide(services, delete, at()).effect == "ask"  # a longer literal target is more specific
    only(services, line("refuse", "ws_*", "ws_destroy"), line("ask", "ws_delete", "ws_destroy"))
    assert decide(services, delete, at()).effect == "ask"  # a longer literal tool
    only(services, line("approve", "ws_delete", "ws_destroy"), line("ask", "ws_*", "ws_destroy"))
    assert decide(services, delete, at()).effect == "allow"
    only(services, line("approve", klass="ws_destroy", tainted="ask"), line("ask", klass="ws_destroy", target="scratch/**"))
    assert decide(services, delete, at()).effect == "ask"
    assert decide(services, delete, at(tainted=True)).effect == "ask"  # on a tie in fields, the target is the more specific
    same = Line("approve", "ws_delete", "ws_destroy")
    assert same.specificity(False) < Line("ask", "ws_delete", "ws_destroy").specificity(False)  # a tie: the stricter


def test_a_move_needs_every_target_for_approve_but_one_for_refuse(services):
    move = lambda *targets: action("ws_move", "ws_write", "write", *targets)  # noqa: E731
    only(services, line("ask", klass="ws_write"), line("approve", "ws_move", "ws_write", "notes/**"))
    assert decide(services, move("ws:notes/a.md", "ws:notes/b.md"), at()).effect == "allow"
    assert decide(services, move("ws:notes/a.md", "ws:other/b.md"), at()).effect == "ask"  # one target outside: no approval
    only(services, line("approve", klass="ws_write"), line("refuse", "ws_move", "ws_write", "secret/**"))
    assert decide(services, move("ws:notes/a.md", "ws:secret/b.md"), at()).effect == "refuse"  # one target is enough
    assert decide(services, move("ws:notes/a.md", "ws:notes/b.md"), at()).effect == "allow"


# ----------------------------------------------------------------------
# The default-mode file
# ----------------------------------------------------------------------
def test_the_shipped_default_mode_file_is_the_built_in_copy():
    text = (ROOT / "deploy" / "default-mode.toml").read_text(encoding="utf-8")
    assert text == to_toml(builtin_default()), "regenerate deploy/default-mode.toml from gate/modes.py (to_toml(builtin_default()))"
    assert parse_default(text, "default-mode.toml", {"ws_write"}).lines == builtin_default().lines


def start(environ, tmp_path, llm, text: str | None):
    path = tmp_path / "mode-under-test.toml"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    settings = load_settings({**environ, "CLARA_DEFAULT_MODE_FILE": str(path)}, use_file=False)
    db = open_database(settings)
    try:
        return build_services(settings, db, provider_factory=llm.factory), db
    except Exception:
        db.close()
        raise


def test_the_server_reads_the_file_at_start(environ, tmp_path, llm):
    text = (
        'packs = ["mail"]\n[[verdict]]\nclass = "ws_write"\nverdict = "ask"\ntainted = "refuse"\n'
        '[[verdict]]\ntool = "mail_send"\ntarget = "mail:to:*@school.fr"\nverdict = "approve"\n'
    )
    s, db = start(environ, tmp_path, llm, text)
    try:
        assert s.modes.default.packs == ("mail",)
        assert decide(s, action("ws_write", "ws_write"), at()).effect == "ask"
        assert decide(s, action("ws_write", "ws_write"), at(tainted=True)).effect == "refuse"
        assert decide(s, action("t", "read", "read"), at()).effect == "allow"  # a class the file leaves out: the built-in line
        send = action("mail_send", "mail_send", "destructive", "mail:to:a@school.fr")
        assert decide(s, send, at()).effect == "allow"
        assert decide(s, action("mail_send", "mail_send", "destructive", "mail:to:a@evil.org"), at()).effect == "refuse"
        assert "default_mode_file" not in s.alerts.active
    finally:
        s.traffic.close()
        db.close()


def test_a_missing_file_means_the_built_in_copy_and_an_alert(environ, tmp_path, llm):
    s, db = start(environ, tmp_path, llm, None)
    try:
        assert s.modes.default.lines == builtin_default().lines
        assert "mode-under-test.toml does not exist" in s.alerts.active["default_mode_file"]
        assert decide(s, action("t", "mail_trash", "destructive"), at()).effect == "ask"
    finally:
        s.traffic.close()
        db.close()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("[[verdict]\nclass = 1", "not valid TOML"),
        ('colour = "red"', "unknown field colour"),
        ('[[verdict]]\nclass = "read"\nverdict = "approve"\nnote = "x"', "Unknown field: note"),
        ('[[verdict]]\nclass = "read"\nverdict = "allow"', "verdict is one of"),
        ('[[verdict]]\nclass = "read"\nverdict = "ask"\ntainted = "approve"', "more permissive"),
        ('[[verdict]]\nclass = "nothing"\nverdict = "ask"', "No action class nothing"),
        ('[[verdict]]\ntool = "no_such_tool"\nverdict = "ask"', "No tool no_such_tool"),
        ('[[verdict]]\ntool = "nothing_*"\nverdict = "ask"', "No tool matches nothing_*"),
        ('[[verdict]]\nverdict = "ask"', "at least one"),
        ('packs = ["nothing"]', "No such tool pack"),
        ('[[verdict]]\nclass = "read"\nverdict = "ask"\n[[verdict]]\nclass = "read"\nverdict = "refuse"', "same tool, class and target"),
    ],
)
def test_an_invalid_file_stops_the_server_and_says_where(environ, tmp_path, llm, text, reason):
    with pytest.raises(StartError) as stopped:
        start(environ, tmp_path, llm, text)
    assert "mode-under-test.toml" in str(stopped.value) and reason in str(stopped.value)


def test_the_message_names_the_line_of_the_wrong_entry(environ, tmp_path, llm):
    text = '# my file\n[[verdict]]\nclass = "read"\nverdict = "ask"\n\n[[verdict]]\nclass = "memory"\nverdict = "nope"\n'
    with pytest.raises(StartError, match=r"line 6"):
        start(environ, tmp_path, llm, text)


def test_the_check_command_reports_the_file(environ, tmp_path, monkeypatch, capsys, caplog):
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLARA_ENV_FILE", str(tmp_path / "none.env"))
    assert server_main(["--check"]) == 0 and "default mode are fine" in capsys.readouterr().out
    (tmp_path / "default-mode.toml").write_text('[[verdict]]\nclass = "read"\nverdict = "maybe"\n', encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert server_main(["--check"]) == 3
    assert "default-mode.toml" in caplog.text and "line 1" in caplog.text
    (tmp_path / "default-mode.toml").unlink()
    assert server_main(["--check"]) == 0 and "Warning:" in capsys.readouterr().out  # missing is not a failure


def test_every_tool_a_line_can_name_is_known_without_a_running_server(everything):
    assert everything.toolbox.names <= {tool.name for tool in static_tools()}  # the vault's too, with or without a vault


def test_the_default_mode_cannot_be_changed_by_a_request(services):
    with pytest.raises(ModeError, match="file the server reads"):
        services.modes.update("default", packs=["mail"])
    with pytest.raises(ModeError, match="cannot be deleted"):
        services.modes.delete("default", {})
    with pytest.raises(ModeError, match="fixed"):
        services.modes.create("Default")


# ----------------------------------------------------------------------
# What the gate does with a mode
# ----------------------------------------------------------------------
def test_a_mode_names_what_it_decides_and_the_default_mode_decides_the_rest(services):
    make(services, "Mail", line("approve", klass="mail_send"), line("approve", "mail_draft"))
    send = action("mail_send", "mail_send", "destructive", "mail:to:a@b.c")
    assert decide(services, send, at()).effect == "refuse"  # the default mode
    assert decide(services, send, at("Mail")).effect == "allow"
    assert decide(services, action("t", "ws_write", "write"), at("Mail")).effect == "allow"  # not named: the default mode's
    assert decide(services, action("t", "mail_trash", "destructive"), at("Mail")).effect == "ask"
    assert "mode Mail approves" in decide(services, send, at("Mail")).reason


def test_a_mode_that_refuses_every_class_is_the_old_level_zero_and_one_that_approves_them_all_the_last_level(services):
    """The levels 0 and 5 have no mode of their own: a mode is as strict or as open as its lines (a default mode file with
    `refuse` for every class is level 0). What it cannot change is the floor, the local-only rule and the secret check."""
    make(services, "Nothing", *(line("refuse", klass=klass) for klass in CLASSES))
    make(services, "All", *(line("approve", klass=klass) for klass in CLASSES))
    for klass in CLASSES:
        assert decide(services, action("t", klass, "write"), at("Nothing")).effect == "refuse", klass
        assert decide(services, action("t", klass, "write"), at("All", tainted=True)).effect == "allow", klass
    leaky = action("github_write", "github_write", "write", "github:me/r", secrets=["GitHub token"])
    assert decide(services, leaky, at("All")).effect == "ask"


def test_a_refusal_of_the_mode_is_final(services):
    make(services, "Locked", line("refuse", klass="mail_send"), line("approve", "mail_draft"))
    send = action("some_tool", "mail_send", "destructive", "mail:to:a@b.c")
    refused = decide(services, send, at("Locked"))
    assert refused.effect == "refuse" and refused.by_mode and not refused.floor
    assert "mode Locked refuses some_tool" in refused.reason
    assert decide(services, send, at("Locked"), approved=True).effect == "refuse"  # an answered card does not undo it
    assert decide(services, send, at("Locked", tainted=True)).effect == "refuse"
    leaky = action("some_tool", "mail_send", "destructive", "mail:to:a@b.c", secrets=["GitHub token"])
    assert decide(services, leaky, at("Locked")).effect == "refuse"  # the secret check only makes things stricter


def test_a_tainted_turn_gets_the_tainted_verdict_which_is_never_more_permissive(services):
    """Red team: untrusted content was read. The vault runs without asking, but a vault write asks once tainted; a mode may
    approve it for one tool, but never approve in a tainted turn what it asks in a normal one."""
    note = action("vault_edit", "vault_write", "write", "vault:a")
    assert decide(services, note, at()).effect == "allow"
    tainted = decide(services, note, at(tainted=True))
    assert tainted.effect == "ask" and "read untrusted content" in tainted.reason
    make(services, "Careless", line("approve", "vault_edit", tainted="approve"))
    assert decide(services, note, at("Careless", tainted=True)).effect == "allow"  # the owner chose it, tool by tool
    assert decide(services, action("other", "vault_write", "write", "vault:a"), at("Careless", tainted=True)).effect == "ask"
    with pytest.raises(ModeError):
        services.modes.set_line("Careless", Line("ask", "vault_edit", tainted="approve"))
    assert decide(services, action("github_write", "github_write", "write", "github:r"), at(tainted=True)).effect == "refuse"


def test_the_secret_check_always_asks_in_every_mode_and_nothing_suspends_it(services):
    make(services, "Everything", line("approve", klass="github_write", tainted="approve"), line("approve", klass="fetch_other"))
    leaky = action("github_write", "github_write", "write", "github:me/r", secrets=["GitHub token"])
    for mode in ("interactive", "unattended"):
        for tainted in (False, True):
            decision = decide(services, leaky, at("Everything", mode, tainted=tainted))
            assert decision.effect == "ask" and "looks like a secret" in decision.raised[0], (mode, tainted)
    quiet = action("github_write", "github_write", "write", "github:me/r")
    assert decide(services, quiet, at("Everything")).effect == "allow"
    assert decide(services, quiet, at("Everything", tainted=True)).effect == "allow"
    assert decide(services, leaky, at("Everything"), approved=True).effect == "allow"  # you answered that very call
    # no mode lowers it: a search query that repeats private text is the same check
    assert decide(services, action("web_search", "web_search", "read", "search", secrets=["private text"]), at()).effect == "ask"


def test_the_computer_folder_is_never_read_by_a_run_nobody_watches(services):
    make(services, "Open", line("approve", klass="folder_read"), line("approve", klass="folder_sensitive"))
    for klass in ("folder_read", "folder_sensitive"):
        decision = decide(services, action("folder_read", klass, "read", "folder:x"), at("Open", "unattended"))
        assert decision.effect == "refuse" and decision.floor


def test_the_floor_and_the_local_only_rule_beat_every_mode(services):
    make(services, "Open", line("approve", klass="ws_write"), line("approve", klass="mail_send"))
    assert decide(services, action("ws_write", "ws_write", "write", "ws:.git/config"), at("Open")).floor
    assert decide(services, action("ws_write", "ws_write", "write", "ws:../x"), at("Open")).floor
    send = action("mail_send", "mail_send", "destructive", "mail:to:a@b.c")
    assert decide(services, send, at("Open", local_only=True)).effect == "refuse"
    assert decide(services, send, at("Open", local_only=True), approved=True).effect == "refuse"


def test_an_answered_request_is_not_judged_again_unless_the_mode_refuses(services):
    make(services, "Ask", line("ask", klass="mail_send"))
    make(services, "Locked", line("refuse", klass="mail_send"))
    send = action("some_tool", "mail_send", "destructive", "mail:to:a@b.c")
    assert decide(services, send, at("Ask")).effect == "ask"
    assert decide(services, send, at("Ask"), approved=True).effect == "allow"
    assert decide(services, send, at("Locked"), approved=True).effect == "refuse"  # a refusal is final


def test_a_hook_runs_with_its_tainted_verdicts_from_its_first_word(services):
    push = action("github_write", "github_write", "write", "github:me/r")
    make(services, "Hooks", line("approve", klass="github_write", tainted="ask"))
    assert decide(services, push, at("Hooks", "unattended", tainted=True)).effect == "ask"
    assert decide(services, push, at("Hooks", "unattended")).effect == "allow"


def test_the_tester_says_which_line_decided_and_the_verdicts(services):
    make(services, "Mail", line("approve", klass="mail_trash", tainted="ask"))
    explained = services.gate.explain(action("some_tool", "mail_trash", "destructive"), at("Mail"))
    assert explained["effect"] == "allow" and explained["mode"] == "Mail" and explained["from_default"] is False
    assert explained["line"]["class"] == "mail_trash" and explained["verdicts"] == {"normal": "approve", "tainted": "ask"}
    not_named = services.gate.explain(action("some_tool", "ws_write", "write"), at("Mail"))
    assert not_named["mode"] == DEFAULT and not_named["from_default"] is True


# ----------------------------------------------------------------------
# The mode of a conversation
# ----------------------------------------------------------------------
def test_a_conversation_follows_its_automation_then_its_project_then_the_default(services):
    modes = services.modes
    make(services, "A")
    make(services, "B")
    project = services.projects.create("Mail sorting")
    chat = services.conversations.create("web", project_id=project.id)
    assert modes.where(chat.id) == (DEFAULT, "default")
    modes.set_stored("projects", project.id, "A")
    assert modes.where(chat.id) == ("A", "project")  # it follows the project live
    own(services, chat.id, "B")
    assert modes.where(chat.id) == ("B", "own")
    assert modes.where(chat.id, own=False) == ("A", "project")
    own(services, chat.id, None)
    modes.set_stored("projects", project.id, None)
    assert modes.where(chat.id) == (DEFAULT, "default")
    assert modes.where("") == (DEFAULT, "default") and modes.where("c0000000000000000") == (DEFAULT, "default")
    with pytest.raises(ModeError):
        modes.set_stored("projects", project.id, "Nothing")
    with pytest.raises(ModeError, match="follow"):
        modes.set_stored("projects", project.id, "default")  # the default mode is what follows: it is not stored


def test_a_run_takes_the_mode_of_its_schedule_or_its_hook(services):
    modes = services.modes
    make(services, "Runs")
    make(services, "Own")
    schedule = services.schedules.create(name="Weekly", prompt="Summarise.", at="2099-01-05T09:00")
    conversation = services.conversations.create("server", kind="schedule")
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (conversation.id, schedule.id))
    assert modes.where(conversation.id) == (DEFAULT, "default")
    modes.set_stored("schedules", schedule.id, "Runs")
    assert modes.where(conversation.id) == ("Runs", "automation")
    own(services, conversation.id, "Own")
    assert modes.where(conversation.id) == ("Own", "own")
    with pytest.raises(ModeError):
        modes.set_stored("schedules", 999, "Runs")
    trigger = services.triggers.create(name="H", on="mail.received", prompt="p")
    modes.set_stored("triggers", trigger.id, "Runs")
    assert services.triggers.find(trigger.id).mode == "Runs" and services.schedules.get(schedule.id).mode == "Runs"


async def test_the_mode_is_read_at_each_call_so_changing_it_stops_the_next_tool(services, probe):
    make(services, "Runs", line("approve", "probe"))
    make(services, "Careful", line("ask", "probe"))
    make(services, "Locked", line("refuse", "probe"))
    chat = services.conversations.create("web")
    own(services, chat.id, "Runs")
    ctx = context(services, chat.id)
    assert (await services.toolbox.call("probe", ctx, {}, services.toolbox.names)).ran
    own(services, chat.id, "Careful")  # changed in the middle of the turn
    asked = await services.toolbox.call("probe", ctx, {}, services.toolbox.names)
    assert asked.approval is not None and "mode Careful asks for probe" in asked.approval.reason
    own(services, chat.id, "Locked")
    refused = await services.toolbox.call("probe", ctx, {}, services.toolbox.names)
    assert refused.text.startswith("Refused") and "change the mode of this conversation" in refused.text
    assert "mode Locked refuses probe" in refused.text and probe == ["x"]


async def test_what_a_mode_lets_through_is_in_the_audit_log_with_the_mode(services, probe):
    make(services, "Runs", line("approve", "probe"))
    chat = services.conversations.create("web")
    own(services, chat.id, "Runs")
    await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    row = next(r for r in services.audit.recent() if r["tool"] == "probe")
    assert row["outcome"] == "ran" and "mode Runs approves probe" in row["detail"] and row["rule_id"] is None


async def test_an_approved_request_runs_whatever_the_mode_became_unless_it_refuses(services, probe):
    make(services, "Careful", line("ask", "probe"))
    make(services, "Runs", line("approve", "probe"))
    make(services, "Locked", line("refuse", "probe"))
    chat = services.conversations.create("web")
    own(services, chat.id, "Careful")
    first = await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    own(services, chat.id, "Runs")  # changed while the card waited
    tester = services.tokens.create("someone", "web")[0]
    assert (await services.approvals.decide(first.approval.id, "allow_once", tester)).status == "done" and probe == ["x"]
    own(services, chat.id, "Careful")
    second = await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    own(services, chat.id, "Locked")
    settled = await services.approvals.decide(second.approval.id, "allow_once", tester)
    assert settled.status == "failed" and "mode Locked refuses probe" in settled.result and probe == ["x"]


async def test_the_card_names_the_mode_that_asked(services, probe):
    chat = services.conversations.create("web")
    asked = await services.toolbox.call("probe", context(services, chat.id), {}, services.toolbox.names)
    assert asked.approval.reason.startswith("mode default asks for probe: class mail_draft")


async def test_a_run_nobody_watches_holds_what_its_mode_asks(services, llm):
    make(services, "Careful", line("ask", klass="ws_write"))
    schedule = services.schedules.create(name="W", prompt="p", at="2099-01-05T09:00")
    services.modes.set_stored("schedules", schedule.id, "Careful")
    made = services.conversations.create("server", kind="schedule")
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (made.id, schedule.id))
    llm.replies = [calls(("ws_write", {"path": "a.txt", "content": "x"})), "Asked."]
    await services.agent.answer(
        ChatRequest(message="go", client="server", conversation=made.id, mode="unattended", kind="schedule")
    )
    pending = services.approvals.list("pending")
    assert [a.tool for a in pending] == ["ws_write"]  # nobody was there: it is held, the run went on
    assert "mode Careful asks for ws_write" in pending[0].reason


async def test_a_followup_keeps_the_mode_of_its_conversation(services, llm):
    make(services, "Locked", line("refuse", klass="ws_write"))
    make(services, "Runs", line("approve", klass="ws_write"))
    chat = services.conversations.create("web")
    run = services.conversations.create("server", kind="schedule")
    own(services, chat.id, "Locked")
    own(services, run.id, "Runs")
    for conversation in (chat, run):
        llm.replies = [calls(("ws_write", {"path": f"{conversation.id}.txt", "content": "x"})), "Done."]
        await services.agent.followup(conversation.id, "[Approval update] The person answered.")
    written = [row for row in services.audit.recent() if row["tool"] == "ws_write"]
    assert [row["outcome"] for row in written if row["conversation_id"] == chat.id] == ["refused"]
    assert [row["outcome"] for row in written if row["conversation_id"] == run.id] == ["ran"]


async def test_a_new_conversation_starts_in_its_mode_and_a_project_sets_the_rest(services, llm, client):
    make(services, "Careful")
    llm.replies = ["Hello."]
    response = await client.post("/v1/chat", json={"message": "hi", "mode": "Careful"})
    assert response.status_code == 200, response.text
    assert services.conversations.listing()[0]["mode"] == "Careful"
    assert (await client.post("/v1/chat", json={"message": "hi", "mode": "Nothing"})).status_code == 404
    assert (await client.post("/v1/chat", json={"message": "hi", "mode": "default"})).status_code == 422


# ----------------------------------------------------------------------
# Packs
# ----------------------------------------------------------------------
async def test_the_packs_of_a_mode_are_added_to_the_ones_on_and_a_change_keeps_the_others(services, client):
    make(services, "Mail", packs=["mail"])
    make(services, "Files", packs=["files_created"])
    chat = services.conversations.create("web", packs=["web", "qcm"])
    set_ = await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "Mail"})
    assert set_.status_code == 200 and set_.json()["packs"] == ["mail"]
    assert services.conversations.get(chat.id).active_packs() == ["web", "qcm", "mail"]
    await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "Files"})
    assert services.conversations.get(chat.id).active_packs() == ["web", "qcm", "mail", "files_created"]  # nothing taken away
    await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": None})
    assert services.conversations.get(chat.id).active_packs() == ["web", "qcm", "mail", "files_created"]


async def test_a_conversation_that_follows_a_project_mode_gets_its_packs_at_its_next_turn(services, llm):
    make(services, "Mail", packs=["mail", "day_activity"])
    project = services.projects.create("P")
    services.modes.set_stored("projects", project.id, "Mail")
    chat = services.conversations.create("web", project_id=project.id)
    assert "mail" not in chat.active_packs()
    llm.replies = ["Hello."]
    await services.agent.answer(ChatRequest(message="hi", client="web", conversation=chat.id))
    packs = services.conversations.get(chat.id).active_packs()
    assert {"mail", "day_activity", "web"} <= set(packs)


async def test_a_run_nobody_watches_gets_the_packs_of_its_modes_at_its_turn(services, llm):
    make(services, "Runs", packs=["day_activity"])
    schedule = services.schedules.create(name="W", prompt="p", at="2099-01-05T09:00")
    services.modes.set_stored("schedules", schedule.id, "Runs")
    made = services.conversations.create("server", kind="schedule", packs=["web"])
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (made.id, schedule.id))
    llm.replies = ["Done."]
    await services.agent.answer(ChatRequest(message="go", client="server", conversation=made.id, mode="unattended", kind="schedule"))
    assert services.conversations.get(made.id).active_packs() == ["web", "day_activity"]


def test_a_setting_row_left_by_the_levels_is_not_read_and_breaks_nothing(services):
    services.db.execute("INSERT INTO settings (key, value, updated_at) VALUES ('tainted_rule', 'false', 'x'), ('auto_level_default', '5', 'x')")
    assert "tainted_rule" not in {row["key"] for row in services.runtime.describe()}
    with pytest.raises(KeyError):
        services.runtime.get("auto_level_default")
    assert decide(services, action("vault_edit", "vault_write", "write", "vault:a"), at(tainted=True)).effect == "ask"


# ----------------------------------------------------------------------
# Making, changing and deleting a mode
# ----------------------------------------------------------------------
def test_a_mode_is_made_changed_and_deleted_with_the_numbers_of_what_uses_it(services):
    modes = services.modes
    made = modes.create("Mail work", ["mail"], [line("approve", "mail_read"), line("ask", klass="mail_send", target="mail:to:*")])
    assert [m for m in modes.names()] == ["Mail work"] and made.packs == ("mail",)
    with pytest.raises(ModeError, match="already"):
        modes.create("mail WORK")
    for bad in ("", "a/b", "x" * 31, "what?"):
        with pytest.raises(ModeError):
            modes.create(bad)
    with pytest.raises(ModeError, match="No tool nothing"):
        modes.create("Bad", [], [line("ask", "nothing")])
    with pytest.raises(ModeError, match="same tool"):
        modes.create("Bad", [], [line("ask", "mail_read"), line("refuse", "mail_read")])
    assert modes.set_line("Mail work", line("refuse", "mail_read")).lines[0].verdict == "refuse"
    assert len(modes.remove_line("Mail work", "mail_read").lines) == 1
    with pytest.raises(ModeError, match="no such line"):
        modes.remove_line("Mail work", "mail_read")
    chat = services.conversations.create("web")
    run = services.conversations.create("server", kind="schedule")
    project = services.projects.create("P")
    schedule = services.schedules.create(name="W", prompt="p", at="2099-01-05T09:00")
    trigger = services.triggers.create(name="H", on="mail.received", prompt="p")
    own(services, chat.id, "Mail work")
    own(services, run.id, "Mail work")
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (run.id, schedule.id))
    modes.set_stored("projects", project.id, "Mail work")
    modes.set_stored("schedules", schedule.id, "Mail work")
    modes.set_stored("triggers", trigger.id, "Mail work")
    uses = {"conversations": 1, "automation_conversations": 1, "automations": 2, "projects": 1}
    assert modes.uses("Mail work") == uses
    from clara.gate.modes import NeedsConfirmation

    with pytest.raises(NeedsConfirmation) as asked:
        modes.delete("Mail work", {})
    assert asked.value.uses == uses and "1 conversation(s), 1 automation conversation(s), 2 automation(s)" in str(asked.value)
    with pytest.raises(NeedsConfirmation):
        modes.delete("Mail work", {**uses, "automations": 1})  # the numbers changed since you read them
    assert modes.delete("Mail work", uses) == uses
    assert modes.names() == [] and services.conversations.get(chat.id).mode is None
    assert modes.where(run.id) == (DEFAULT, "default") and services.triggers.find(trigger.id).mode is None
    assert services.schedules.get(schedule.id).mode is None and services.projects.get(project.id).mode is None


async def test_the_page_reads_the_modes_and_everything_it_needs_to_edit_one(client, services):
    make(services, "Mail work", line("approve", "mail_read"), packs=["mail"])
    body = (await client.get("/v1/modes")).json()
    assert body["default"]["default"] is True and body["default"]["file"].endswith("default-mode.toml") and body["default"]["from_file"]
    assert [m["name"] for m in body["modes"]] == ["Mail work"] and body["modes"][0]["uses"]["conversations"] == 0
    assert {row["class"] for row in body["classes"]} == set(CLASSES)
    tool = next(t for t in body["tools"] if t["name"] == "mail_trash")
    assert tool["classes"] == ["mail_trash"] and tool["pack"] == "mail"
    assert {p["id"] for p in body["packs"]} >= {"mail", "github", "web"}
    one = (await client.get("/v1/modes/mail work")).json()
    assert one["verdicts"] == [{"tool": "mail_read", "class": "", "target": "", "verdict": "approve", "tainted": "approve"}]
    row = next(r for r in one["effective"] if r["tool"] == "mail_trash")
    assert (row["verdict"], row["from"], row["line"]["class"]) == ("ask", "default", "mail_trash")
    assert next(r for r in one["effective"] if r["tool"] == "mail_read")["from"] == "mode"
    assert (await client.get("/v1/modes/Nothing")).status_code == 404
    default_view = (await client.get("/v1/modes/default")).json()
    assert default_view["name"] == DEFAULT and default_view["effective"]


async def test_a_mode_is_made_changed_and_deleted_from_the_page(client, services):
    made = await client.post(
        "/v1/modes", json={"name": "Mail work", "packs": ["mail"], "verdicts": [{"tool": "mail_read", "verdict": "approve"}]}
    )
    assert made.status_code == 201 and made.json()["packs"] == ["mail"]
    assert (await client.post("/v1/modes", json={"name": "Mail work"})).status_code == 422
    assert (await client.post("/v1/modes", json={"name": "default"})).status_code == 422
    assert (await client.post("/v1/modes", json={"name": "X", "packs": ["nothing"]})).status_code == 422
    assert (await client.post("/v1/modes", json={"name": "X", "verdicts": [{"class": "mail_send", "verdict": "ask", "tainted": "approve"}]})).status_code == 422
    put = await client.put(
        "/v1/modes/Mail work/verdicts", json={"class": "mail_send", "target": "mail:to:*@school.fr", "verdict": "approve", "tainted": "ask"}
    )
    assert put.status_code == 200 and len(put.json()["verdicts"]) == 2
    assert (await client.put("/v1/modes/Mail work/verdicts", json={"tool": "nothing", "verdict": "ask"})).status_code == 422
    assert (await client.put("/v1/modes/Nothing/verdicts", json={"tool": "mail_read", "verdict": "ask"})).status_code == 404
    assert (await client.put("/v1/modes/default/verdicts", json={"tool": "mail_read", "verdict": "ask"})).status_code == 422
    removed = await client.delete("/v1/modes/Mail work/verdicts", params={"class": "mail_send", "target": "mail:to:*@school.fr"})
    assert removed.status_code == 200 and len(removed.json()["verdicts"]) == 1
    assert (await client.delete("/v1/modes/Mail work/verdicts", params={"class": "mail_send"})).status_code == 404
    changed = await client.put("/v1/modes/Mail work", json={"packs": [], "verdicts": []})
    assert changed.json()["packs"] == [] and changed.json()["verdicts"] == []
    assert (await client.put("/v1/modes/default", json={"packs": []})).status_code == 422
    chat = services.conversations.create("web")
    own(services, chat.id, "Mail work")
    refused = await client.delete("/v1/modes/Mail work")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "needs_confirmation"
    assert "1 conversation(s)" in refused.json()["error"]["message"]
    numbers = {"conversations": 1, "automation_conversations": 0, "automations": 0, "projects": 0}
    assert (await client.delete("/v1/modes/Mail work", params={**numbers, "conversations": 0})).status_code == 409
    gone = await client.delete("/v1/modes/Mail work", params=numbers)
    assert gone.status_code == 200 and gone.json() == {"deleted": True, "moved": numbers}
    assert services.conversations.get(chat.id).mode is None
    assert (await client.delete("/v1/modes/default", params=numbers)).status_code == 422
    assert (await client.delete("/v1/modes/Nothing", params=numbers)).status_code == 404
    assert any(row["tool"] == "mode" and row["outcome"] == "mode" for row in services.audit.recent())


async def test_the_mode_of_a_conversation_a_project_a_schedule_and_a_hook(client, services):
    make(services, "Mail work", packs=["mail"])
    chat = services.conversations.create("web")
    got = (await client.get(f"/v1/conversations/{chat.id}/mode")).json()
    assert got == {"own": None, "mode": DEFAULT, "source": "default", "follows": {"mode": DEFAULT, "source": "default"}, "packs": []}
    set_ = (await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "Mail work"})).json()
    assert set_["mode"] == "Mail work" and set_["source"] == "own" and set_["own"] == "Mail work"
    assert (await client.get(f"/v1/conversations/{chat.id}")).json()["mode"] == "Mail work"
    assert (await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "Nothing"})).status_code == 404
    assert (await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "default"})).status_code == 422
    assert (await client.put(f"/v1/conversations/{chat.id}/mode", json={"mode": None})).json()["source"] == "default"
    assert (await client.get("/v1/conversations/c0000000000000000/mode")).status_code == 404
    project = services.projects.create("P")
    assert (await client.put(f"/v1/projects/{project.id}/mode", json={"mode": "Mail work"})).json() == {"mode": "Mail work"}
    assert (await client.get(f"/v1/projects/{project.id}")).json()["mode"] == "Mail work"
    assert (await client.put("/v1/projects/999/mode", json={"mode": "Mail work"})).status_code == 404
    schedule = services.schedules.create(name="W", prompt="p", at="2099-01-05T09:00")
    assert (await client.put(f"/v1/schedules/{schedule.id}/mode", json={"mode": "Mail work"})).status_code == 200
    assert (await client.get(f"/v1/schedules/{schedule.id}")).json()["mode"] == "Mail work"
    trigger = services.triggers.create(name="H", on="mail.received", prompt="p")
    assert (await client.put(f"/v1/triggers/{trigger.id}/mode", json={"mode": "Mail work"})).status_code == 200
    assert (await client.get(f"/v1/triggers/{trigger.id}")).json()["mode"] == "Mail work"
    made = await client.post("/v1/conversations", json={"mode": "Mail work"})
    assert made.status_code == 201 and made.json()["mode"] == "Mail work" and "mail" in made.json()["packs"]


async def test_the_tester_says_what_would_decide_a_call(client, services):
    make(services, "Files", line("approve", klass="ws_destroy", tainted="ask"))
    body = {"tool": "ws_delete", "arguments": {"path": "a.txt"}, "mode": "Files"}
    plain = (await client.post("/v1/modes/test", json=body)).json()
    assert plain["effect"] == "allow" and plain["mode"] == "Files" and plain["line"]["class"] == "ws_destroy"
    assert plain["action"]["class"] == "ws_destroy" and plain["verdicts"] == {"normal": "approve", "tainted": "ask"}
    assert (await client.post("/v1/modes/test", json={**body, "tainted": True})).json()["effect"] == "ask"
    assert (await client.post("/v1/modes/test", json={**body, "mode": "default"})).json()["effect"] == "allow"  # level 3 approves it
    assert (await client.post("/v1/modes/test", json={**body, "local_only": True})).json()["effect"] == "allow"  # stays on the machine
    assert (await client.post("/v1/modes/test", json={**body, "mode": "Nothing"})).status_code == 404
    assert (await client.post("/v1/modes/test", json={"tool": "nothing"})).status_code == 404


async def test_the_old_routes_are_gone(client):
    for method, path in (("GET", "/v1/levels"), ("GET", "/v1/rules"), ("PUT", "/v1/rules/tainted"), ("POST", "/v1/rules/test")):
        assert (await client.request(method, path)).status_code in (404, 405), path


# ----------------------------------------------------------------------
# Who may change a mode
# ----------------------------------------------------------------------
async def test_only_a_token_that_may_answer_everything_makes_changes_or_sets_a_mode(services):
    make(services, "Mail work")
    chat = services.conversations.create("web")
    project = services.projects.create("P")
    schedule = services.schedules.create(name="W", prompt="p", at="2099-01-05T09:00")
    trigger = services.triggers.create(name="H", on="mail.received", prompt="p")
    numbers = {"conversations": 0, "automation_conversations": 0, "automations": 0, "projects": 0}
    for kind in ("discord", "script"):  # the bot (scope safe) and a script (scope none)
        async with await client_as(services, f"as-{kind}", kind) as other:
            for call in (
                other.post("/v1/modes", json={"name": "X"}),
                other.put("/v1/modes/Mail work", json={"packs": []}),
                other.put("/v1/modes/Mail work/verdicts", json={"tool": "mail_read", "verdict": "approve"}),
                other.delete("/v1/modes/Mail work/verdicts", params={"tool": "mail_read"}),
                other.delete("/v1/modes/Mail work", params=numbers),
                other.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "Mail work"}),
                other.put(f"/v1/conversations/{chat.id}/mode", json={"mode": None}),
                other.put(f"/v1/projects/{project.id}/mode", json={"mode": "Mail work"}),
                other.put(f"/v1/schedules/{schedule.id}/mode", json={"mode": "Mail work"}),
                other.put(f"/v1/triggers/{trigger.id}/mode", json={"mode": "Mail work"}),
                other.post("/v1/conversations", json={"mode": "Mail work"}),
                other.post("/v1/chat", json={"message": "hi", "mode": "Mail work"}),
            ):
                assert (await call).status_code == 403, kind
            assert (await other.get("/v1/modes")).status_code == 200  # reading is free
            assert (await other.post("/v1/modes/test", json={"tool": "ws_delete", "arguments": {"path": "a"}})).status_code == 200
            assert (await other.post("/v1/conversations", json={})).status_code == 201  # a conversation with no mode of its own
    assert services.modes.names() == ["Mail work"] and services.conversations.get(chat.id).mode is None
    async with await client_as(services, "browser", "web") as web:
        assert (await web.put(f"/v1/conversations/{chat.id}/mode", json={"mode": "Mail work"})).status_code == 200


async def test_the_default_modes_file_is_not_a_setting_and_no_knob_stands_for_the_levels(client, services):
    for key in ("auto_level_default", "auto_level_unattended", "tainted_rule", "approve_all_calls"):
        assert (await client.put(f"/v1/admin/settings/{key}", json={"value": 5})).status_code == 404, key
    assert "default_mode" not in {row["key"] for row in services.runtime.describe()}


def test_the_model_cannot_change_a_mode(services):
    assert not any(part in ("mode", "level") for name in services.toolbox.names for part in name.split("_"))
    from clara.packs import automations

    fields = {*automations.BOTH, *automations.SCHEDULE_ONLY, *automations.HOOK_ONLY}
    assert not fields & {"mode", "level", "auto_level"}  # an automation she makes gets the default mode (or its project's)


# ----------------------------------------------------------------------
# The console and the terminal
# ----------------------------------------------------------------------
def run_command(services, args, token):
    return next(command.run for command in COMMANDS if command.name == "mode")(services, args, token)


def test_the_console_reads_and_sets_modes(services):
    admin = services.tokens.create("admin", "console")[0]
    assert "default (the file" in run_command(services, [], admin)
    assert run_command(services, ["new", "Mail work", "mail"], admin).startswith("Made the mode Mail work")
    assert run_command(services, ["verdict", "Mail work", "approve", "tool=mail_read"], admin) == "Mail work: tool mail_read → approve"
    assert run_command(services, ["verdict", "Mail work", "ask", "class=mail_send", "target=mail:to:*", "tainted=refuse"], admin).endswith("ask (if tainted: refuse)")
    assert run_command(services, ["verdict", "Mail work", "ask", "colour=red"], admin).startswith("Error: Unknown fields")
    assert "mail_read" in run_command(services, ["show", "Mail work"], admin) and "packs mail" in run_command(services, ["show", "Mail work"], admin)
    assert "mail" in run_command(services, ["list"], admin)
    chat = services.conversations.create("web")
    assert run_command(services, ["conversation", chat.id, "Mail work"], admin) == f"conversation {chat.id}: mode Mail work"
    assert run_command(services, ["conversation", chat.id], admin) == "Mode Mail work (from own)"
    assert "mail" in services.conversations.get(chat.id).active_packs()
    assert run_command(services, ["conversation", chat.id, "follow"], admin) == f"conversation {chat.id}: follows"
    assert run_command(services, ["conversation", chat.id, "Nothing"], admin).startswith("Error: No mode")
    assert run_command(services, ["unverdict", "Mail work", "tool=mail_read"], admin).count("→") == 1
    own(services, chat.id, "Mail work")
    asked = run_command(services, ["delete", "Mail work"], admin)
    assert "1 conversation(s)" in asked and "/mode delete Mail work 1 0 0 0" in asked
    assert run_command(services, ["delete", "Mail work", "1", "0", "0", "0"], admin).startswith("Deleted Mail work")
    assert services.conversations.get(chat.id).mode is None
    assert "approve" in run_command(services, ["test", "ws_write", '{"path": "a.txt", "content": "x"}'], admin)
    assert run_command(services, ["nonsense"], admin).startswith("Usage:")


def test_a_token_that_may_not_answer_everything_only_reads_in_the_console(services):
    make(services, "Mail work")
    bot = services.tokens.create("bot", "discord")[0]
    for args in (["new", "X"], ["delete", "Mail work"], ["verdict", "Mail work", "ask", "tool=mail_read"], ["packs", "Mail work", "mail"],
                 ["conversation", "c1", "Mail work"], ["import-legacy"]):
        assert run_command(services, args, bot).startswith("Error: this token may not change modes"), args
    assert "Mail work" in run_command(services, ["list"], bot) and services.modes.names() == ["Mail work"]


class TerminalApi:
    def __init__(self):
        self.sent: list[tuple[str, str, dict | None]] = []

    def get(self, path, **params):
        if path == "/v1/modes":
            return {"modes": [{"name": "Mail work"}, {"name": "Locked"}]}
        return {"own": "Locked", "mode": "Locked", "source": "own"}

    def put(self, path, body):
        self.sent.append(("PUT", path, body))
        return {}

    def post(self, path, body=None):
        self.sent.append(("POST", path, body))
        return {"id": "c-new"}


def test_the_terminal_shows_sets_and_starts_a_conversation_in_a_mode():
    from clara.cli.chat import mode_text

    api = TerminalApi()
    chat = SimpleNamespace(api=api, conversation=None, incognito=False, local_only=False)
    assert "Not started: it will run in the default mode" in mode_text(chat, "")
    assert "Mode Locked, from own" in mode_text(chat, "locked")  # a new conversation is made in that mode
    assert chat.conversation == "c-new" and api.sent == [
        ("POST", "/v1/conversations", {"incognito": False, "local_only": False, "mode": "Locked"})
    ]
    mode_text(chat, "follow")
    assert api.sent[-1] == ("PUT", "/v1/conversations/c-new/mode", {"mode": None})
    assert mode_text(chat, "x").startswith("A mode is Mail work, Locked, or follow")


def test_the_terminal_does_not_start_a_conversation_to_follow():
    from clara.cli.chat import mode_text

    api = TerminalApi()
    chat = SimpleNamespace(api=api, conversation=None, incognito=False, local_only=False)
    assert mode_text(chat, "follow").startswith("Not started") and api.sent == []


# ----------------------------------------------------------------------
# The migration and `import-legacy`
# ----------------------------------------------------------------------
def legacy_database(tmp_path):
    """A database at schema 30 holding what the auto levels and the rules left."""
    import importlib

    from clara.db.connection import connect

    migrate_module = importlib.import_module("clara.db.migrate")
    path = tmp_path / "old.db"
    old = connect(path)
    for migration in migrate_module.migrations()[:30]:
        old.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;")
    old.execute("INSERT INTO conversations (id, title, titled_by, client, kind, created_at, updated_at, auto_level)"
                " VALUES ('c1', '', '', 'web', 'chat', 'x', 'x', 5)")
    old.execute("INSERT INTO tool_levels (tool, klass, ask_from, approve_from) VALUES ('ws_run', '', 2, 4)")
    old.execute("INSERT INTO tool_levels (tool, klass, ask_from, approve_from) VALUES ('mail_send', 'mail_send', 1, 4)")
    old.execute("INSERT INTO tool_levels (tool, klass, ask_from, approve_from) VALUES ('gone_tool', '', 1, 2)")
    for effect, tool, target, situation, level, enabled, note in (
        ("allow", "mail_send", "mail:to:*@school.fr", "", "", 1, "my school"),
        ("refuse", "ws_delete", "scratch/**", "", "", 1, ""),
        ("allow", "github_write", "", "", "", 1, ""),
        ("allow", "ws_write", "", "unattended", "", 1, ""),
        ("allow", "", "ws:x/**", "", "", 1, ""),
        ("refuse", "ws_move", "", "", "", 0, "off"),
    ):
        old.execute("INSERT INTO rules (effect, tool, target, situation, level, origin, note, enabled, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'console', ?, ?, 'x', 'x')", (effect, tool, target, situation, level, note, enabled))
    old.execute("INSERT INTO settings (key, value, updated_at) VALUES ('auto_level_default', '3', 'x'), ('tainted_rule', 'true', 'x'),"
                " ('auto_level_unattended', '2', 'x'), ('approve_all_calls', '50', 'x'), ('default_model', '\"local:fake\"', 'x')")
    old.close()
    return path, migrate_module


def test_the_migration_makes_every_level_follow_keeps_the_legacy_tables_and_takes_a_backup(tmp_path):
    from clara.db.connection import connect

    path, migrate_module = legacy_database(tmp_path)
    before, after, backup = migrate_module.migrate(path, tmp_path / "backups")
    assert (before, after) == (30, migrate_module.LATEST_VERSION) and backup is not None and backup.exists()
    new = connect(path)
    tables = {row[0] for row in new.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"modes", "mode_packs", "mode_verdicts", "legacy_auto_levels", "legacy_tool_levels", "legacy_rules", "legacy_settings"} <= tables
    assert not tables & {"auto_levels", "tool_levels", "rules"}
    assert new.execute("SELECT mode FROM conversations WHERE id = 'c1'").fetchone()[0] is None
    for table in ("conversations", "projects", "schedules", "triggers"):
        assert "auto_level" not in [row[1] for row in new.execute(f"PRAGMA table_info({table})")]
    assert [tuple(r) for r in new.execute("SELECT key FROM settings")] == [("default_model",)]
    assert {tuple(r)[0] for r in new.execute("SELECT key FROM legacy_settings")} == {
        "auto_level_default", "auto_level_unattended", "tainted_rule", "approve_all_calls"}
    assert new.execute("SELECT COUNT(*) FROM legacy_rules").fetchone()[0] == 6
    assert [tuple(r) for r in new.execute("SELECT tool, klass, ask_from, approve_from FROM legacy_tool_levels WHERE tool = 'ws_run'")] == [("ws_run", "", 2, 4)]
    with pytest.raises(sqlite3.IntegrityError):
        new.execute("INSERT INTO modes (name, created_at, updated_at) VALUES ('A', 'x', 'x'), ('a', 'x', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        new.execute("INSERT INTO mode_verdicts (mode, tool, verdict, tainted) VALUES ('nothing', 't', 'ask', 'ask')")
    new.close()


def test_import_legacy_prints_a_default_mode_made_from_the_levels_and_the_rules(tmp_path):
    from clara.db import Database

    path, migrate_module = legacy_database(tmp_path)
    migrate_module.migrate(path, tmp_path / "backups")
    db = Database(path)
    tools = {tool.name: tool.classes for tool in static_tools()}
    text = import_legacy(db, tools)
    db.close()
    mode = parse_default(text, "imported.toml", {tool for tool in tools})
    best = lambda tool, klass, *targets, tainted=False: mode.best(Action(tool, "write", klass, targets or ("ws:a",), ""), tainted)  # noqa: E731
    assert best("t", "ws_write").verdict == "approve" and best("t", "mail_trash").verdict == "ask"  # the old default level 3
    assert best("t", "vault_write").tainted == "ask"  # what the tainted-turn rule raised
    # the thresholds of a tool: ws_run asked from 2 and ran from 4 (whole tool): ask at level 3, for both of its classes
    assert best("ws_run", "run").verdict == "ask" and best("ws_run", "run_network").verdict == "ask"
    assert best("mail_send", "mail_send", "mail:to:a@b.c").verdict == "ask"  # asks from 1, runs from 4: ask at 3
    # a rule that allows opens one address; a refusal stays a refusal; what the level refused no rule opened
    assert best("mail_send", "mail_send", "mail:to:a@school.fr").verdict == "approve"
    assert best("mail_send", "mail_send", "mail:to:a@school.fr").tainted == "ask"
    assert best("ws_delete", "ws_destroy", "ws:scratch/a").verdict == "refuse" and best("ws_delete", "ws_destroy", "ws:notes/a").verdict == "approve"
    assert best("github_write", "github_write", "github:r").verdict == "refuse"
    assert "Not imported, it is for the situation unattended" in text and "Not imported, it names no tool" in text
    assert "gone_tool, which is not a tool" in text and "rule #6" not in text
    assert "The old default level was 3" in text and "Runs nobody watched were at level 2" in text


def test_clara_mode_import_legacy_prints_it_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from clara.cli.ops import main as ops_main

    path, _ = legacy_database(tmp_path)
    for name, value in {"CLARA_DATA_DIR": str(tmp_path), "CLARA_SECRET_KEY": "ab" * 32, "CLARA_TIMEZONE": "Europe/Paris",
                        "CLARA_TAILSCALE": "off", "CLARA_RUN": "off", "CLARA_CHECK_PERMISSIONS": "false",
                        "CLARA_ENV_FILE": str(tmp_path / "none.env")}.items():
        monkeypatch.setenv(name, value)
    path.rename(tmp_path / "clara.db")
    assert ops_main(["mode", "import-legacy"]) == 0
    out = capsys.readouterr()
    assert out.out.startswith("# A default mode made by `clara mode import-legacy`") and "[[verdict]]" in out.out
    assert "default-mode.toml" in out.err and not (tmp_path / "default-mode.toml").exists()


def test_import_legacy_through_the_console_is_for_a_token_that_may_answer_everything(services):
    services.db.execute("INSERT INTO legacy_rules (effect, tool, target, origin, created_at, updated_at) VALUES ('refuse', 'ws_delete', 'a/**', 'console', 'x', 'x')")
    admin = services.tokens.create("admin", "console")[0]
    text = run_command(services, ["import-legacy"], admin)
    assert "[[verdict]]" in text and 'target = "a/**"' in text


def test_the_preflight_refuses_a_release_that_would_not_start_on_the_default_mode_file(tmp_path, monkeypatch, capsys):
    from clara.cli.ops import main as ops_main
    from clara.db import migrate

    for name, value in {"CLARA_DATA_DIR": str(tmp_path), "CLARA_SECRET_KEY": "ab" * 32, "CLARA_TIMEZONE": "Europe/Paris",
                        "CLARA_TAILSCALE": "off", "CLARA_RUN": "off", "CLARA_CHECK_PERMISSIONS": "false",
                        "CLARA_ENV_FILE": str(tmp_path / "none.env")}.items():
        monkeypatch.setenv(name, value)
    copy = tmp_path / "copy" / "clara.db"
    migrate(copy, copy.parent / "backups")
    wanted = tmp_path / "mode.toml"
    monkeypatch.setenv("CLARA_DEFAULT_MODE_FILE", str(wanted))
    wanted.write_text('[[verdict]]\nclass = "nothing"\nverdict = "ask"\n', encoding="utf-8")
    assert ops_main(["preflight", "--database", str(copy)]) == 1 and "mode.toml" in capsys.readouterr().err
    wanted.write_text(to_toml(builtin_default()), encoding="utf-8")
    assert ops_main(["preflight", "--database", str(copy)]) == 0
    wanted.unlink()  # a missing file is not a reason to refuse an update: the built-in copy is used
    assert ops_main(["preflight", "--database", str(copy)]) == 0


def test_a_name_with_an_accent_is_found_whatever_its_case(services):
    made = services.modes.create("Écoute")
    assert services.modes.get("écoute") is made or services.modes.get("écoute").name == "Écoute"
    assert services.modes.require("ÉCOUTE").name == "Écoute"
    with pytest.raises(ModeError, match="already"):
        services.modes.create("écoute")
