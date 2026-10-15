"""Gate 3: the workspace. Path escapes, links, .git, a commit per change, undo, limits, and the sandbox's flags."""

from __future__ import annotations

import os
import shutil

import pytest

from clara.agent.turn import ChatRequest
from clara.app import build_services, extend, open_database
from clara.settings import load_settings
from clara.workspace.paths import PathError, clean, resolve
from clara.workspace.sandbox import podman_args
from clara.workspace.store import WorkspaceError
from conftest import parse_sse
from fakes import calls


@pytest.mark.parametrize("bad", ["/etc/passwd", "../data/clara.db", "a/../../x", ".git/config", "notes/.git/x", "C:/x"])
def test_paths_cannot_leave_the_workspace_or_reach_git(bad):
    with pytest.raises(PathError):
        clean(bad)


def test_links_are_never_followed(services, tmp_path):
    root = services.workspace.root
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, root / "link.txt")
    os.symlink(tmp_path, root / "folder")
    with pytest.raises(PathError):
        resolve(root, "link.txt")
    with pytest.raises(PathError):
        resolve(root, "folder/secret.txt")
    with pytest.raises((PathError, WorkspaceError)):
        services.workspace.read_text("link.txt")


async def test_every_change_is_a_commit_and_can_be_undone(services):
    ws = services.workspace
    created = await ws.write("notes/plan.md", "# Plan\n", overwrite=False, message="ws_write: notes/plan.md")
    assert created.action == "created" and created.commit
    edited, count = await ws.edit("notes/plan.md", "# Plan", "# The plan", replace_all=False, message="ws_edit: plan")
    assert count == 1 and ws.read_text("notes/plan.md") == "# The plan\n"
    history = await ws.log("notes/plan.md")
    assert [entry["message"] for entry in history] == ["ws_edit: plan", "ws_write: notes/plan.md"]
    await ws.undo(edited.commit)
    assert ws.read_text("notes/plan.md") == "# Plan\n"
    with pytest.raises(WorkspaceError):
        await ws.write("notes/plan.md", "again", overwrite=False, message="x")


async def test_limits(services):
    ws = services.workspace
    ws.max_file_bytes = 10
    with pytest.raises(WorkspaceError):
        await ws.write("big.txt", "x" * 11, overwrite=False, message="x")


async def test_writing_a_note_shows_a_card_and_overwriting_asks(client, services, llm):
    llm.replies = [calls(("ws_write", {"path": "notes/list.md", "content": "- eggs\n"})), "Written."]
    events = parse_sse((await client.post("/v1/chat/stream", json={"message": "make a list"})).text)
    assert {"type": "file", "path": "notes/list.md", "action": "created", "size": 7}.items() <= \
        next(e for e in events if e["type"] == "file").items()
    assert events[-1]["files"][0]["path"] == "notes/list.md"
    llm.replies = [calls(("ws_write", {"path": "notes/list.md", "content": "gone", "overwrite": True})), "Hm."]
    await services.agent.answer(ChatRequest(message="replace it", client="cli", wait_for_approvals=False))
    assert services.workspace.read_text("notes/list.md") == "- eggs\n"
    assert services.approvals.list("pending")[0].level == "destructive"


async def test_ws_run_is_not_offered_without_a_working_sandbox(services, llm):
    assert not services.sandbox.ready
    await services.agent.answer(ChatRequest(message="hi", client="cli"))
    offered = {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}
    assert "ws_run" not in offered and "ws_read" in offered


def test_the_container_is_locked_down(services):
    args = podman_args(services.settings, "img", "/w", "sub", False, 120, "run-1")
    joined = " ".join(args)
    for flag in ("--rm", "--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
                 "--userns=keep-id", "--pids-limit=256", "--memory=1g", "--cpus=2", "--timeout=120",
                 "--http-proxy=false", "--tmpfs=/workspace/.git:ro", "--volume=/w:/workspace:rw"):
        assert flag in joined
    assert "--env-host" not in joined and "--privileged" not in joined
    assert "--network=pasta" in " ".join(podman_args(services.settings, "img", "/w", "", True, 5, "run-2"))


@pytest.mark.skipif(shutil.which("podman") is None, reason="needs rootless podman (integration)")
async def test_a_script_sees_no_network_no_git_and_no_host_files(environ, llm):
    """The real sandbox (the image localhost/clara-sandbox:latest). CI sets CLARA_REQUIRE_SANDBOX=1: no silent skip."""
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
        result = await s.sandbox.run(
            "ls -a /workspace/.git | wc -l; cat /etc/hostname >/dev/null; env | sort; "
            "(getent hosts example.com && echo NET) || echo NONET; touch made-here", "", 60, False,
            conversation_id=None, turn_id="t")
        assert "NONET" in result.output and "CLARA" not in result.output
        assert s.workspace.exists("made-here") and result.commit
    finally:
        s.traffic.close()
        db.close()
