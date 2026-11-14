"""The scripts of a skill run in the sandbox: `ws_run` with `skill` mounts the skill's files read-only at /skill, for that run alone.
The gate classes are those of `ws_run` (`run`, `run_network`); the target `skill:<name>` is what a mode can name."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

import pytest

from clara.app import build_services, extend, open_database
from clara.gate import Action, Situation
from clara.settings import load_settings
from clara.skills import read_skill
from clara.tools.base import ToolContext, TurnState
from clara.workspace.sandbox import RunResult, podman_args, skill_mount
from clara.workspace.store import WorkspaceError
from modeset import default, line, make, own

CHECK_SH = """set -u
echo "sibling: $(cat /skill/notes/data.txt)"
echo "skill.md: $(head -c 3 /skill/SKILL.md)"
(echo changed > /skill/notes/data.txt) 2>&1 || echo WRITE-REFUSED
(touch /skill/new) 2>&1 || echo CREATE-REFUSED
echo made > /workspace/made-by-script
(getent hosts example.com && echo NET) || echo NONET
ls /skill/scripts
stat -c '%a %u' /skill /skill/scripts /skill/scripts/check.sh
"""
TOOL_PY = "print('python reads', open('/skill/notes/data.txt').read().strip())\n"


def add(services, name="check", extra="", projects=(), files=None):
    """A skill with these other files (path -> text), through the zip the owner would send."""
    text = f"---\nname: {name}\ndescription: Check things\n{extra}---\nRun the checks.\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", text)
        for path, data in (files or {}).items():
            archive.writestr(path, data)
    parsed = read_skill(zip_base64=base64.b64encode(buffer.getvalue()).decode())
    services.skills.add(parsed, scope="projects" if projects else "global", projects=list(projects), replace=True)


def with_files(services, name="check", **kwargs):
    add(services, name, files={"scripts/check.sh": CHECK_SH, "tool.py": TOOL_PY, "notes/data.txt": "hello\n"}, **kwargs)


def context(services, project=None, conversation="", tainted=False):
    return ToolContext(services, TurnState("t1", conversation, "web", mode="interactive", timezone="Europe/Paris", project_id=project, tainted=tainted))


def ran(services, **fields):
    """The sandbox as a stand-in: it records how it was called."""
    seen: list[dict] = []

    async def run(command, workdir, timeout, network, **kwargs):
        seen.append({"command": command, "network": network, **kwargs})
        return RunResult(1, 0, "the output", 10, False, 0.1, "runs/x.log", "")

    services.sandbox.run = run
    return seen


async def call(services, arguments, ctx=None):
    return await services.toolbox.call("ws_run", ctx or context(services), arguments, services.toolbox.names)


# ----------------------------------------------------------------------
# The folder written for a run
# ----------------------------------------------------------------------
@pytest.fixture
def temp(tmp_path, monkeypatch):
    """Where the folders of a run are made, so that a test can see what is left."""
    folder = tmp_path / "temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def left(folder):
    return sorted(os.listdir(folder))


FILES = [("SKILL.md", b"---\nname: x\n---\nhi\n"), ("scripts/check.sh", b"echo hi\n"), ("a/b/c.txt", b"deep"), ("logo.png", b"\x89PNG\x00")]


def test_the_folder_holds_every_file_as_it_was_stored_and_nothing_is_executable(temp):
    before = os.umask(0o077)  # a server started with a tight umask: the modes are set, not inherited
    try:
        with skill_mount(FILES) as root:
            assert Path(root).parent == temp and Path(root).name.startswith("clara-skill-")
            for path, data in FILES:
                assert Path(root, path).read_bytes() == data
            tree = [Path(folder, item) for folder, folders, names in os.walk(root) for item in (*folders, *names)] + [Path(root)]
            assert all(not path.is_symlink() for path in tree)
            assert {stat.S_IMODE(path.stat().st_mode) for path in tree if path.is_file()} == {0o600}
            assert {stat.S_IMODE(path.stat().st_mode) for path in tree if path.is_dir()} == {0o700}
            assert all(path.stat().st_uid == os.getuid() for path in tree)
            assert sorted(os.listdir(root)) == ["SKILL.md", "a", "logo.png", "scripts"]
        assert os.listdir(temp) == []
    finally:
        os.umask(before)


@pytest.mark.parametrize("path", ["../x", "a/../../x", "/abs/escaped", "a//b", "./a", "a/", "", "a\\b", "a\x00b", "a/./b", "x/.."])
def test_a_damaged_stored_path_is_refused_and_leaves_nothing(temp, path):
    """The parser refused all of these; a stored path is still data from a database that a bug could have damaged."""
    with pytest.raises(WorkspaceError, match="cannot be mounted"), skill_mount([("SKILL.md", b"x"), (path, b"bad")]):
        pass
    assert os.listdir(temp) == [] and not Path("/abs/escaped").exists()  # not even a `x` beside the folder of the run


def test_an_absolute_path_is_not_written_where_it_says(temp, tmp_path):
    with pytest.raises(WorkspaceError, match="cannot be mounted"), skill_mount([("SKILL.md", b"x"), (str(tmp_path / "escaped"), b"bad")]):
        pass
    assert not (tmp_path / "escaped").exists() and os.listdir(temp) == []


def test_the_same_path_twice_or_a_file_that_is_a_folder_is_refused_and_leaves_nothing(temp):
    for files in ([("a", b"1"), ("a", b"2")], [("a", b"1"), ("a/b", b"2")], [("a/b", b"1"), ("a", b"2")]):
        with pytest.raises(WorkspaceError, match="could not be put in place"), skill_mount(files):
            pass
        assert os.listdir(temp) == []


# ----------------------------------------------------------------------
# What podman is given, and when the folder goes
# ----------------------------------------------------------------------
def test_podman_gets_one_more_volume_for_a_skill_and_nothing_else_changes(services):
    plain = podman_args(services.settings, "img", "/w", "sub", False, 120, "run-1")
    mounted = podman_args(services.settings, "img", "/w", "sub", False, 120, "run-1", "/tmp/clara-skill-x")
    extra = [arg for arg in mounted if arg not in plain]
    assert extra == ["--volume=/tmp/clara-skill-x:/skill:ro,noexec,nosuid,nodev"]
    assert [arg for arg in mounted if arg != extra[0]] == plain
    assert mounted.index(extra[0]) == mounted.index("--volume=/w:/workspace:rw") + 1
    assert not any("/skill" in arg for arg in plain)
    assert "--network=pasta" in podman_args(services.settings, "img", "/w", "", True, 5, "run-2", "/s")  # the network is its own choice


class Ends:
    """A run in which podman is a stand-in: it records the folder it was given, and ends the way a test says."""

    def __init__(self, services, monkeypatch):
        self.sandbox = services.sandbox
        self.sandbox.ready = True
        self.dir = ""
        self.during = False
        self.started = asyncio.Event()
        self.how = "done"
        monkeypatch.setattr(self.sandbox, "_spawn", self.spawn)

    async def spawn(self, args, hard_limit, name):
        volume = next((arg for arg in args if ":/skill:" in arg), None)
        if volume is None:  # a run with no skill
            return 0, b"ok", 2, False
        self.dir = volume.removeprefix("--volume=").split(":")[0]
        self.during = Path(self.dir, "scripts/check.sh").read_bytes() == b"echo hi\n"
        self.started.set()
        if self.how == "hang":
            await asyncio.Event().wait()
        if self.how == "crash":
            raise FileNotFoundError("podman")
        return {"done": (0, b"ok", 2, False), "failed": (3, b"no", 2, False), "timeout": (None, b"", 0, True)}[self.how]

    async def run(self):
        return await self.sandbox.run(
            "bash /skill/scripts/check.sh", "", 5, False, conversation_id=None, turn_id="t", skill="check",
            skill_files=[("SKILL.md", b"x"), ("scripts/check.sh", b"echo hi\n")])


@pytest.mark.parametrize("how, exit_code, timed_out", [("done", 0, False), ("failed", 3, False), ("timeout", None, True)])
async def test_the_folder_is_there_during_the_run_and_gone_after_it_ends(services, monkeypatch, temp, how, exit_code, timed_out):
    ends = Ends(services, monkeypatch)
    ends.how = how
    result = await ends.run()
    assert ends.during and (result.exit_code, result.timed_out) == (exit_code, timed_out)
    assert not os.path.exists(ends.dir) and os.listdir(temp) == []


async def test_the_folder_is_gone_when_the_run_is_cancelled(services, monkeypatch, temp):
    ends = Ends(services, monkeypatch)
    ends.how = "hang"
    task = asyncio.create_task(ends.run())
    await asyncio.wait_for(ends.started.wait(), 5)
    assert os.path.isdir(ends.dir)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not os.path.exists(ends.dir) and os.listdir(temp) == []


async def test_the_folder_is_gone_when_the_run_crashes(services, monkeypatch, temp):
    ends = Ends(services, monkeypatch)
    ends.how = "crash"
    with pytest.raises(FileNotFoundError):
        await ends.run()
    assert not os.path.exists(ends.dir) and os.listdir(temp) == []


async def test_a_run_whose_files_cannot_be_put_in_place_does_not_start_and_leaves_no_row(services, monkeypatch, temp):
    ends = Ends(services, monkeypatch)
    with pytest.raises(WorkspaceError, match="cannot be mounted"):
        await ends.sandbox.run("x", "", 5, False, conversation_id=None, turn_id="t", skill="check", skill_files=[("../x", b"bad")])
    assert not ends.started.is_set() and services.sandbox.runs() == [] and os.listdir(temp) == []


async def test_the_log_says_which_skill_and_a_run_without_one_has_no_such_line(services, monkeypatch, temp):
    ends = Ends(services, monkeypatch)
    result = await ends.run()
    header = services.workspace.read_text(result.log_path)
    assert header.startswith("$ bash /skill/scripts/check.sh\n# workdir: /workspace/  network: off  timeout: 5s\n# skill: check  (")
    assert "mounted at /skill, read-only" in header.splitlines()[2] and header.splitlines()[3].startswith("# exit: 0")
    plain = await services.sandbox.run("echo", "", 5, False, conversation_id=None, turn_id="t")
    assert "# skill" not in services.workspace.read_text(plain.log_path)


# ----------------------------------------------------------------------
# The tool: which skills, and what the gate sees
# ----------------------------------------------------------------------
async def test_a_run_gets_the_files_of_the_skill_as_stored_SKILL_md_included(services):
    with_files(services)
    seen = ran(services)
    result = await call(services, {"command": "bash /skill/scripts/check.sh", "skill": "check"})
    assert result.ran and "Run #1: exit code 0" in result.text and "the output" in result.text
    [run] = seen
    assert run["skill"] == "check" and run["network"] is False
    files = dict(run["skill_files"])
    assert sorted(files) == ["SKILL.md", "notes/data.txt", "scripts/check.sh", "tool.py"]
    assert files["scripts/check.sh"] == CHECK_SH.encode() and files["SKILL.md"].startswith(b"---\nname: check\n")


async def test_in_a_turn_the_tool_offers_skill_and_the_run_gets_the_files(services, llm):
    from clara.agent.turn import ChatRequest
    from fakes import calls

    with_files(services)
    services.sandbox.ready = True  # `ws_run` is offered only when the sandbox works; the stand-in below is what runs
    seen = ran(services)
    llm.replies = [calls(("ws_run", {"command": "bash /skill/scripts/check.sh", "skill": "check"})), "Checked."]
    answer = await services.agent.answer(ChatRequest(message="run the checks", client="cli", wait_for_approvals=False))
    assert answer == "Checked." and [run["skill"] for run in seen] == ["check"]
    tool = next(tool["function"] for tool in llm.seen[0]["tools"] if tool["function"]["name"] == "ws_run")
    assert tool["parameters"]["properties"]["skill"]["type"] == "string" and tool["parameters"]["required"] == ["command"]
    assert "/skill" in tool["description"] and "interpreter" in tool["description"]


async def test_a_run_without_a_skill_is_called_as_it_always_was(services):
    seen = ran(services)
    assert (await call(services, {"command": "echo hi", "skill": ""})).ran
    assert (await call(services, {"command": "echo hi"})).ran
    assert all("skill" not in run and "skill_files" not in run for run in seen) and len(seen) == 2


@pytest.mark.parametrize("name", ["nothing", "../x", "CHECK", "/check", "check/../check"])
async def test_a_name_that_is_not_a_skill_she_may_run_is_a_plain_error_and_no_card_and_no_run(services, name):
    with_files(services)
    seen = ran(services)
    answer = await call(services, {"command": "bash /skill/scripts/check.sh", "skill": name})
    assert answer.text.startswith("Error: No such skill:") and not answer.ran and answer.approval is None and seen == []


async def test_a_skill_for_another_project_or_for_the_person_alone_is_no_skill_here(services):
    project = services.projects.create("Thesis").id
    other = services.projects.create("Other").id
    with_files(services, "mine", projects=[project])
    with_files(services, "alone", extra="disable-model-invocation: true\n")
    with_files(services, "everywhere")
    seen = ran(services)
    for ctx, name, good in [(context(services, project), "mine", True), (context(services, other), "mine", False), (context(services), "mine", False),
                            (context(services, project), "alone", False), (context(services, project), "everywhere", True)]:
        answer = await call(services, {"command": "ls /skill", "skill": name}, ctx)
        assert answer.ran is good, (name, answer.text)
        if not good:
            assert answer.text == f"Error: No such skill: {name!r}." and answer.approval is None  # the same words for all of them
    assert [run["skill"] for run in seen] == ["mine", "everywhere"]


async def test_the_action_names_the_skill_as_a_target_and_keeps_the_class(services):
    with_files(services)
    ctx = context(services)
    plain = services.toolbox.action("ws_run", ctx, {"command": "bash /skill/scripts/check.sh", "workdir": "sub", "skill": "check"})
    assert plain.klass == "run" and plain.targets == ("ws:sub", "skill:check") and plain.level == "write"
    assert plain.summary == "Run: bash /skill/scripts/check.sh, with the files of the skill check mounted at /skill"
    net = services.toolbox.action("ws_run", ctx, {"command": "curl x", "network": True, "skill": "check"})
    assert net.klass == "run_network" and net.targets == ("net", "ws:", "skill:check")
    assert net.summary == "Run with network: curl x, with the files of the skill check mounted at /skill"
    none = services.toolbox.action("ws_run", ctx, {"command": "echo"})
    assert none.targets == ("ws:",) and none.summary == "Run: echo" and none.klass == "run"
    assert services.toolbox.action("ws_run", ctx, {"command": "curl x", "network": True}).targets == ("net", "ws:")
    assert {"run", "run_network"} == set(services.toolbox.get("ws_run").classes) and "skill" in services.toolbox.get("ws_run").parameters
    with pytest.raises(ValueError, match="No such skill"):
        services.toolbox.action("ws_run", ctx, {"command": "echo", "skill": "nothing"})


async def test_the_audit_and_the_card_name_the_skill_and_the_network_asks_as_it_always_did(services):
    with_files(services)
    seen = ran(services)
    default(services, line("ask", klass="run_network"))  # the built-in default mode refuses the network: this one asks
    conversation = services.conversations.create("web", title="t").id
    ctx = context(services, conversation=conversation)
    assert (await call(services, {"command": "bash /skill/scripts/check.sh", "skill": "check"}, ctx)).ran  # the default mode approves `run`
    [row] = services.audit.recent(1)
    assert row["tool"] == "ws_run" and row["target"] == "ws: skill:check" and row["outcome"] == "ran"
    asked = await call(services, {"command": "curl x", "network": True, "skill": "check"}, ctx)  # the network is the card of `run_network`
    assert asked.approval is not None and asked.approval.action_class == "run_network" and len(seen) == 1
    assert asked.approval.summary == "Run with network: curl x, with the files of the skill check mounted at /skill"
    assert "skill:check" in asked.approval.target and "net" in asked.approval.target


async def test_a_skill_never_gets_the_network_by_itself(services):
    """`network` is the call's own flag: nothing in the skill (its text, its frontmatter) turns it on."""
    with_files(services, extra="network: true\nallowed-tools: Bash\n")
    seen = ran(services)
    assert (await call(services, {"command": "curl x", "skill": "check"})).ran
    assert seen[0]["network"] is False


# ----------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------
async def test_a_mode_line_for_one_skill_asks_for_that_skills_runs_only(services):
    with_files(services, "review-pr")
    with_files(services, "weekly")
    make(services, "Careful", line("ask", klass="run", target="skill:review-pr"))
    conversation = services.conversations.create("web", title="t").id
    own(services, conversation, "Careful")
    seen = ran(services)
    ctx = context(services, conversation=conversation)
    asked = await call(services, {"command": "bash /skill/scripts/check.sh", "skill": "review-pr", "workdir": ""}, ctx)
    assert asked.approval is not None and asked.approval.action_class == "run" and "mode Careful asks" in asked.approval.reason and seen == []
    for arguments in ({"command": "echo", "skill": "weekly"}, {"command": "echo"}):  # another skill, and no skill: the default mode's `run`
        assert (await call(services, arguments, ctx)).ran
    assert len(seen) == 2


async def test_a_line_for_a_skill_ignores_the_folder_a_run_works_in(services):
    with_files(services, "review-pr")
    make(services, "Careful", line("ask", target="skill:review-*"))  # no class: any call that names a skill
    conversation = services.conversations.create("web", title="t").id
    own(services, conversation, "Careful")
    ran(services)
    asked = await call(services, {"command": "ls", "skill": "review-pr", "workdir": "sub"}, context(services, conversation=conversation))
    assert asked.approval is not None and "mode Careful asks" in asked.approval.reason


async def test_a_mode_may_refuse_a_skills_scripts_the_network_ones_too(services):
    with_files(services, "review-pr")
    make(services, "Strict", line("refuse", klass="run", target="skill:review-pr"), line("refuse", klass="run_network", target="skill:review-pr"))
    conversation = services.conversations.create("web", title="t").id
    own(services, conversation, "Strict")
    seen = ran(services)
    ctx = context(services, conversation=conversation)
    for arguments in ({"command": "echo", "skill": "review-pr"}, {"command": "curl x", "network": True, "skill": "review-pr"}):
        answer = await call(services, arguments, ctx)
        assert answer.text.startswith("Refused: mode Strict refuses") and not answer.ran and answer.approval is None
    assert seen == []


async def test_a_line_that_approves_a_skill_never_approves_its_network_runs(services):
    """The network is a target (`net`) that no line about a skill covers: the class `run_network` still decides, and it asks."""
    with_files(services, "review-pr")
    make(services, "Easy", line("approve", target="skill:review-pr"), line("approve", klass="run", target="skill:review-pr"),
         line("ask", klass="run_network"))
    conversation = services.conversations.create("web", title="t").id
    own(services, conversation, "Easy")
    seen = ran(services)
    ctx = context(services, conversation=conversation)
    assert (await call(services, {"command": "echo", "skill": "review-pr"}, ctx)).ran
    net = await call(services, {"command": "curl x", "network": True, "skill": "review-pr"}, ctx)
    assert net.approval is not None and net.approval.action_class == "run_network" and len(seen) == 1


# ----------------------------------------------------------------------
# A refusal stands: a line about the skill never loosens what the rest of the mode refuses for the same run
# ----------------------------------------------------------------------
# `p/**` is a short pattern on purpose: a line about the skill (`skill:review-pr`) is the more specific one, so without the guard it wins
def careful(services, *lines, name="M", tainted=False):
    make(services, name, *lines)
    conversation = services.conversations.create("web", title="t").id
    own(services, conversation, name)
    return context(services, conversation=conversation, tainted=tainted)


@pytest.mark.parametrize("on_skill", ["approve", "ask"])
async def test_a_refusal_on_the_folder_stands_whatever_a_line_about_the_skill_says(services, on_skill):
    with_files(services, "review-pr")
    with_files(services, "weekly")
    ctx = careful(services, line("refuse", klass="run", target="p/**"), line(on_skill, klass="run", target="skill:review-pr"))
    seen = ran(services)
    for arguments in ({"skill": "review-pr"}, {"skill": "weekly"}, {}):  # the skill the line names, another one, and none
        answer = await call(services, {"command": "ls", "workdir": "p/sub", **arguments}, ctx)
        assert answer.text.startswith("Refused: mode M refuses") and "p/**" in answer.text and not answer.ran and answer.approval is None
    assert seen == []
    elsewhere = await call(services, {"command": "ls", "workdir": "other", "skill": "review-pr"}, ctx)  # the skill line governs the rest
    if on_skill == "approve":
        assert elsewhere.ran and len(seen) == 1
    else:
        assert elsewhere.approval is not None and "mode M asks" in elsewhere.approval.reason and seen == []
    assert (await call(services, {"command": "ls", "workdir": "other", "skill": "weekly"}, ctx)).ran  # no line for it: the default mode's `run`


async def test_a_line_about_the_skill_without_a_refusal_anywhere_governs_as_it_did(services):
    with_files(services, "review-pr")
    ctx = careful(services, line("ask", klass="run", target="p/**"), line("approve", klass="run", target="skill:review-pr"))
    ran(services)
    # `ask` on the folder is not a refusal: the more specific line, about the skill, approves
    assert (await call(services, {"command": "ls", "workdir": "p/sub", "skill": "review-pr"}, ctx)).ran
    asked = await call(services, {"command": "ls", "workdir": "p/sub"}, ctx)  # a run with no skill: the folder line, as always
    assert asked.approval is not None and "mode M asks" in asked.approval.reason


async def test_a_refusal_of_the_whole_class_stands_too(services):
    with_files(services, "review-pr")
    ctx = careful(services, line("refuse", klass="run"), line("approve", klass="run", target="skill:review-pr"))
    seen = ran(services)
    answer = await call(services, {"command": "ls", "skill": "review-pr"}, ctx)
    assert answer.text.startswith("Refused: mode M refuses") and seen == []


async def test_the_refusal_is_the_one_of_the_situation_of_the_turn(services):
    """A refusal that applies to a turn that read untrusted content only (`(if tainted)`) stands in that turn, and in no other."""
    with_files(services, "review-pr")
    lines = (line("approve", klass="run", target="p/**", tainted="refuse"), line("approve", klass="run", target="skill:review-pr"))
    seen = ran(services)
    calm = careful(services, *lines)
    assert (await call(services, {"command": "ls", "workdir": "p/sub", "skill": "review-pr"}, calm)).ran
    tainted = careful(services, *lines, name="N", tainted=True)
    answer = await call(services, {"command": "ls", "workdir": "p/sub", "skill": "review-pr"}, tainted)
    assert answer.text.startswith("Refused: mode N refuses") and "the conversation read untrusted content" in answer.text and len(seen) == 1


async def test_a_run_with_the_network_keeps_the_refusal_of_its_folder_and_of_the_network(services):
    with_files(services, "review-pr")
    ctx = careful(services, line("refuse", klass="run_network", target="p/**"), line("ask", klass="run_network", target="skill:review-pr"),
                  line("ask", klass="run_network"))
    seen = ran(services)
    answer = await call(services, {"command": "curl x", "network": True, "workdir": "p/sub", "skill": "review-pr"}, ctx)
    assert answer.text.startswith("Refused: mode M refuses") and not answer.ran and answer.approval is None and seen == []
    asked = await call(services, {"command": "curl x", "network": True, "workdir": "other", "skill": "review-pr"}, ctx)
    assert asked.approval is not None and asked.approval.action_class == "run_network"


def test_only_a_line_that_refuses_stands_not_the_fall_back_when_no_line_covers_the_run(services):
    from clara.gate.modes import DEFAULT, Mode

    services.modes.default = Mode(DEFAULT, (), (line("approve", target="skill:review-pr"),))  # a default mode with a gap
    run = Action("ws_run", "write", "run", ("ws:p/sub", "skill:review-pr"), "x")
    assert services.modes.verdict(run, Situation("interactive"))[2] == "approve"
    bare = Action("ws_run", "write", "run", ("ws:p/sub",), "x")
    assert services.modes.verdict(bare, Situation("interactive"))[2:] == ("refuse",)  # nothing decides it: nothing runs


def test_an_action_with_no_target_but_its_skill_is_decided_as_it_was(services):
    """`skill_run` has only the `skill:` target: a mode that refuses the class and approves one skill keeps that exception."""
    make(services, "N", line("refuse", klass="skill_run"), line("approve", klass="skill_run", target="skill:review-pr"))

    def effect(name):
        action = Action("skill_run", "read", "skill_run", (f"skill:{name}",), "x")
        return services.modes.verdict(action, Situation("interactive", mode_name="N"))[2]

    assert effect("review-pr") == "approve" and effect("weekly") == "refuse"


# ----------------------------------------------------------------------
# The real sandbox (rootless Podman; CI sets CLARA_REQUIRE_SANDBOX=1: no silent skip)
# ----------------------------------------------------------------------
@pytest.fixture
async def real(environ, llm, temp):
    if shutil.which("podman") is None:
        if os.environ.get("CLARA_REQUIRE_SANDBOX") == "1":
            pytest.fail("podman is not installed")
        pytest.skip("needs rootless podman (integration)")
    settings = load_settings({**environ, "CLARA_RUN": "auto"}, use_file=False)
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    extend(s)
    try:
        await s.sandbox.check()
        if not s.sandbox.ready:
            if os.environ.get("CLARA_REQUIRE_SANDBOX") == "1":
                pytest.fail(f"sandbox not ready: {s.sandbox.problem}")
            pytest.skip(f"sandbox not ready: {s.sandbox.problem}")
        assert os.listdir(temp) == []  # the readiness probe cleaned up after itself
        yield s
    finally:
        s.traffic.close()
        db.close()


async def test_a_script_of_a_skill_runs_from_slash_skill_and_sees_its_files_and_nothing_else(real, temp):
    with_files(real)
    s = real
    files = s.skills.files_of("check")
    result = await s.sandbox.run("bash /skill/scripts/check.sh", "", 60, False, conversation_id=None, turn_id="t", skill="check", skill_files=files)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "sibling: hello" in out and "skill.md: ---" in out  # its siblings, SKILL.md among them, at the paths the skill gave
    assert "Read-only file system" in out and "WRITE-REFUSED" in out and "CREATE-REFUSED" in out  # /skill is read-only
    assert "NONET" in out and "NET" not in out.replace("NONET", "")  # no network, as for any run
    uid = os.getuid()
    assert "check.sh" in out and f"700 {uid}\n700 {uid}\n600 {uid}\n" in out  # the modes (private to the user), and the owner
    assert s.workspace.read_text("made-by-script") == "made\n" and result.commit  # the workspace is still the one writable mount
    assert "# skill: check" in s.workspace.read_text(result.log_path)
    assert left(temp) == []  # the folder of the run is gone


async def test_scripts_go_through_an_interpreter_and_the_mount_is_not_executable(real, temp):
    with_files(real)
    s = real
    files = s.skills.files_of("check")

    async def run(command):
        return await s.sandbox.run(command, "", 60, False, conversation_id=None, turn_id="t", skill="check", skill_files=files)

    python = await run("python3 /skill/tool.py")
    assert python.exit_code == 0 and "python reads hello" in python.output
    direct = await run("/skill/scripts/check.sh")  # not executable: by its mode, and the mount says noexec
    assert direct.exit_code != 0 and "Permission denied" in direct.output
    seen = await run("grep ' /skill ' /proc/self/mountinfo")
    assert seen.exit_code == 0 and all(word in seen.output for word in ("ro,", "nosuid", "nodev", "noexec"))
    none = await s.sandbox.run("ls /skill 2>&1 || echo NO-SKILL", "", 60, False, conversation_id=None, turn_id="t")
    assert "NO-SKILL" in none.output  # a run with no skill has no /skill
    assert left(temp) == []


async def test_ws_run_with_a_skill_end_to_end_on_the_real_sandbox(real, temp):
    with_files(real)
    s = real
    conversation = s.conversations.create("web", title="t").id
    answer = await call(s, {"command": "bash /skill/scripts/check.sh && python3 /skill/tool.py", "skill": "check"}, context(s, conversation=conversation))
    assert answer.ran and "exit code 0" in answer.text and "python reads hello" in answer.text and "sibling: hello" in answer.text
    [row] = s.audit.recent(1)
    assert row["tool"] == "ws_run" and "skill:check" in row["target"] and row["outcome"] == "ran"
    assert left(temp) == []
    gone = await call(s, {"command": "ls /skill", "skill": "nothing"})
    assert gone.text.startswith("Error: No such skill") and len(s.sandbox.runs()) == 1
