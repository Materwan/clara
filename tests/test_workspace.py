"""Gate 3: the workspace. Path escapes, links, .git, a commit per change, undo, limits, and the sandbox's flags."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading

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


def _plant_fsmonitor(root, tmp_path):
    """A repository inside the workspace whose config makes git run a script that touches a marker file."""
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    nested = root / "sub"
    nested.mkdir()
    identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    subprocess.run(["git", "-C", str(nested), *identity, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    with open(nested / ".git" / "config", "a", encoding="utf-8") as config:
        config.write(f"[core]\n\tfsmonitor = {hook}\n")
    (nested / "a.txt").write_text("x", encoding="utf-8")
    return marker


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
async def test_a_planted_repository_cannot_make_the_hosts_git_run_a_command(services, tmp_path):
    """CODE_REVIEW S1: a script can plant `sub/.git` whose `core.fsmonitor` is a command; git must not run it."""
    ws = services.workspace
    marker = _plant_fsmonitor(ws.root, tmp_path)

    await ws.commit("after a script run")  # git add -A, git status
    assert not marker.exists()

    (ws.root / "b.txt").write_text("y", encoding="utf-8")
    ws.recover()  # the synchronous helper: git status, git add -A
    ws.git_sync("status", "--porcelain")
    assert not marker.exists()


def _finishes(function, *args, seconds: float = 5.0):
    """What `function` returns or raises; the test fails, instead of hanging, if it is still blocked after `seconds`."""
    box: dict = {}

    def run() -> None:
        try:
            box["result"] = function(*args)
        except BaseException as error:  # reported to the test below
            box["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"{function.__name__} is blocked"
    return box


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
async def test_a_named_pipe_left_by_a_script_is_refused_and_never_waited_for(services):
    """Audit (boundaries, high): opening a pipe nobody writes to blocked the event loop on ws_read and ws_search."""
    ws = services.workspace
    os.mkfifo(ws.root / "pipe")
    (ws.root / "notes" / "a.md").write_text("findable\n", encoding="utf-8")

    assert isinstance(_finishes(ws.read_bytes, "pipe")["error"], PathError)
    assert isinstance(_finishes(ws.read_text, "pipe")["error"], PathError)
    assert _finishes(ws.search, "findable")["result"] == ["notes/a.md:1: findable"]  # the pipe is skipped

    async def overwrite() -> None:
        await ws.write("pipe", "x", overwrite=True, message="x")

    with pytest.raises(PathError):
        await asyncio.wait_for(overwrite(), 5)


def test_a_file_bigger_than_the_limit_for_a_file_is_not_read_whole(services):
    """Audit (boundaries, high): a script can write files past the limit, and ws_read took them whole into memory."""
    ws = services.workspace
    ws.max_file_bytes = 10
    (ws.root / "notes" / "ok.txt").write_bytes(b"x" * 10)
    (ws.root / "notes" / "big.txt").write_bytes(b"x" * 11)
    assert ws.read_bytes("notes/ok.txt") == b"x" * 10
    with pytest.raises(WorkspaceError, match="bigger than"):
        ws.read_bytes("notes/big.txt")
    with pytest.raises(WorkspaceError, match="bigger than"):
        ws.read_text("notes/big.txt")


async def test_an_edit_leaves_the_bytes_it_does_not_touch_as_they_were(services):
    """Audit (data integrity, high): ws_edit decoded with errors="replace" and wrote UTF-8 back, so every byte that was
    not UTF-8 in the file (here a Latin-1 é) became U+FFFD, far from the passage that was edited."""
    ws = services.workspace
    (ws.root / "notes" / "old.txt").write_bytes(b"caf\xe9 = coffee\nprice: 3\n")
    change, count = await ws.edit("notes/old.txt", "price: 3", "price: 4", replace_all=False, message="ws_edit: price")
    assert count == 1 and (ws.root / "notes" / "old.txt").read_bytes() == b"caf\xe9 = coffee\nprice: 4\n"
    await ws.edit("notes/old.txt", "coffee", "caf\u00e9 au lait", replace_all=False, message="ws_edit: coffee")
    assert (ws.root / "notes" / "old.txt").read_bytes() == b"caf\xe9 = caf\xc3\xa9 au lait\nprice: 4\n"
    with pytest.raises(WorkspaceError, match="plain text"):
        await ws.edit("notes/old.txt", "price", "pri\udc80ce", replace_all=False, message="x")
    (ws.root / "notes" / "blob.bin").write_bytes(b"\x00\x01abc")
    with pytest.raises(WorkspaceError, match="not a text file"):
        await ws.edit("notes/blob.bin", "abc", "xyz", replace_all=False, message="x")


async def test_the_web_editor_is_not_offered_a_file_it_would_damage(client, services):
    (services.workspace.root / "notes" / "latin.txt").write_bytes(b"caf\xe9\n")
    (services.workspace.root / "notes" / "utf8.txt").write_text("café\n", encoding="utf-8")
    latin = (await client.get("/v1/workspace/file", params={"path": "notes/latin.txt"})).json()
    assert latin["binary"] is True and "content" not in latin
    utf8 = (await client.get("/v1/workspace/file", params={"path": "notes/utf8.txt"})).json()
    assert utf8["binary"] is False and utf8["content"] == "café\n"
    download = await client.get("/v1/workspace/file", params={"path": "notes/latin.txt", "raw": "true"})
    assert download.content == b"caf\xe9\n"  # the bytes themselves are still there to download


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


def test_the_tmpfs_over_dot_git_is_not_filled_from_the_host_repository(services):
    """Podman copies the host's `.git` into a `--tmpfs` unless told not to: a fresh `git init` is more than the 64k
    and every run failed ("crun: write: No space left on device"); and the repository was inside the container."""
    (mount,) = [a for a in podman_args(services.settings, "img", "/w", "", False, 5, "run-3") if "/workspace/.git" in a]
    assert mount.startswith("--tmpfs=/workspace/.git:") and "notmpcopyup" in mount.split(":", 1)[1].split(",")


@pytest.mark.parametrize("code, out, ok", [
    (0, "ISOLATED\nGIT-HIDDEN", True), (0, "NETWORK\nGIT-HIDDEN", False), (0, "ISOLATED\nGIT-VISIBLE", False),
    (126, "Error: OCI runtime error: crun: write: No space left on device", False)])
async def test_the_readiness_probe_starts_the_image_as_a_run_does(services, monkeypatch, code, out, ok):
    """Over a `.git` bigger than its tmpfs, with the real options (keep-id included: the first start of an image with it
    can copy the whole image), and with time to do that: a probe that was lighter said "ready" while every run failed."""
    seen = {}

    async def fake_exec(*args, timeout=60.0):
        volume = next(a for a in args if a.startswith("--volume=")).removeprefix("--volume=").split(":")[0]
        seen.update(args=args, timeout=timeout, root=volume, canary=os.path.getsize(f"{volume}/.git/canary"))
        return code, out

    monkeypatch.setattr(services.sandbox, "_exec", fake_exec)
    assert (await services.sandbox.probe("img"))[0] is ok
    assert {"--userns=keep-id", "--read-only", "--network=none"} <= set(seen["args"])
    assert any("notmpcopyup" in a for a in seen["args"])
    assert seen["canary"] > 64 * 1024 and seen["timeout"] >= 600
    assert not os.path.exists(seen["root"])  # the throw-away workspace is gone


async def test_a_probe_that_times_out_removes_its_container(services, monkeypatch):
    calls = []

    async def fake_exec(*args, timeout=60.0):
        calls.append(args)
        return (124, "timed out") if "run" in args else (0, "")

    monkeypatch.setattr(services.sandbox, "_exec", fake_exec)
    assert (await services.sandbox.probe("img")) == (False, "timed out")
    assert calls[-1][1:3] == ("rm", "--force") and calls[-1][3].startswith("clara-probe-")


async def test_podman_is_started_from_the_root_folder(services, tmp_path, monkeypatch):
    """`sudo -u clara clara doctor` from a home directory clara cannot enter: Podman fails with "cannot chdir"."""
    monkeypatch.chdir(tmp_path)
    assert await services.sandbox._exec(shutil.which("pwd") or "/bin/pwd") == (0, "/")


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
