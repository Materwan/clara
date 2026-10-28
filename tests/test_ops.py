"""Gate 6: Funnel blocks start, releases are checked, a broken update rolls back by itself, backups restore."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from clara.app import recover
from clara.cli.ops import main as ops_main
from clara.db import backup_database
from clara.gate import Action, Decision, Situation
from clara.ops.backups import verify
from clara.ops.discord_bot import DiscordBot, bot_environment
from clara.ops.tailscale import FunnelOn, Output, Tailscale, check_funnel_now, funnel_ports
from clara.ops.updater import UpdateError, Updater, check_signature, check_sums, safe_extract
from clara.settings import load_settings
from clara.timeutil import iso, utcnow
from clara.tools.base import ToolContext, TurnState

SERVE_STATUS = {"Web": {"box.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}},
                "AllowFunnel": {"box.ts.net:443": True}}


def test_funnel_is_read_from_serve_status():
    assert funnel_ports(SERVE_STATUS) == {443}
    assert funnel_ports({"AllowFunnel": {"box.ts.net:8443": False}}) == set()


def test_funnel_blocks_start(tmp_path):
    fake = tmp_path / "tailscale"
    fake.write_text(f"#!/bin/sh\necho '{json.dumps(SERVE_STATUS)}'\n")
    fake.chmod(0o755)
    with pytest.raises(FunnelOn):
        check_funnel_now(str(fake), 443)
    check_funnel_now(str(fake), 8443)  # another port: fine
    check_funnel_now(str(tmp_path / "missing"), 443)  # no tailscale: reported later, not here


async def test_funnel_found_at_runtime_is_turned_off_and_alerted():
    calls: list[list[str]] = []
    alerts: list[str] = []

    async def runner(args, timeout):
        calls.append(args[1:])
        if args[1:] == ["status", "--json"]:
            return Output(0, json.dumps({"BackendState": "Running", "Self": {"DNSName": "box.ts.net."}}))
        if args[1:] == ["serve", "status", "--json"]:
            return Output(0, json.dumps(SERVE_STATUS))
        return Output(0, "")

    tailscale = Tailscale(443, "http://127.0.0.1:8765", runner=runner, alert=lambda key, title, text: alerts.append(key))
    await tailscale.once()
    assert ["funnel", "--https=443", "off"] in calls
    assert "funnel" in alerts and tailscale.url == "https://box.ts.net"


def test_release_sums_and_signatures(tmp_path):
    (tmp_path / "a.tar.gz").write_bytes(b"release")
    import hashlib

    sums = f"{hashlib.sha256(b'release').hexdigest()}  a.tar.gz\n"
    check_sums(sums, tmp_path, ["a.tar.gz"])
    (tmp_path / "a.tar.gz").write_bytes(b"tampered")
    with pytest.raises(UpdateError):
        check_sums(sums, tmp_path, ["a.tar.gz"])
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    signature = base64.b64encode(key.sign(sums.encode()))
    check_signature(sums.encode(), signature, public)
    with pytest.raises(UpdateError):
        check_signature((sums + "x").encode(), signature, public)


def test_release_archives_cannot_escape(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(UpdateError):
        safe_extract(archive, tmp_path / "out")
    linked = tmp_path / "link.tar.gz"
    with tarfile.open(linked, "w:gz") as tar:
        info = tarfile.TarInfo("evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(UpdateError):
        safe_extract(linked, tmp_path / "out2")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_a_new_release_venv_still_runs_after_the_folder_is_renamed(tmp_path):
    """The updater builds the venv in `.<tag>.partial` and renames the folder: a plain venv writes the old folder in
    the first line of its scripts, and neither the preflight nor the launcher could start the release."""
    partial = tmp_path / ".v1.0.0.partial"
    partial.mkdir()
    (partial / "requirements.lock").write_text("")
    dist = "clara-1.0.0.dist-info"
    with zipfile.ZipFile(partial / "clara-1.0.0-py3-none-any.whl", "w") as wheel:  # one console script, no dependency
        wheel.writestr("clara_probe.py", "def main():\n    print('probe ran')\n")
        wheel.writestr(f"{dist}/METADATA", "Metadata-Version: 2.1\nName: clara\nVersion: 1.0.0\n")
        wheel.writestr(f"{dist}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(f"{dist}/entry_points.txt", "[console_scripts]\nprobe = clara_probe:main\n")
        wheel.writestr(f"{dist}/RECORD", "")
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    asyncio.run(Updater(SimpleNamespace())._venv(partial, {"python": python}))
    partial.rename(tmp_path / "v1.0.0")
    run = subprocess.run([str(tmp_path / "v1.0.0" / ".venv" / "bin" / "probe")], capture_output=True, text=True)
    assert (run.returncode, run.stdout.strip()) == (0, "probe ran"), run.stderr


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


GOOD = textwrap.dedent('''
    import json, os, sys, threading, http.server, sqlite3
    from pathlib import Path
    data = Path(os.environ["CLARA_DATA_DIR"]); run = data / "run"; port = int(os.environ["CLARA_PORT"])
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"status": "ok"}')
        def log_message(self, *a): pass
    server = http.server.HTTPServer(("127.0.0.1", port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (run / "boot").write_text(os.environ["CLARA_BOOT_ID"])
    log = data / "starts.log"; log.write_text(log.read_text() + os.environ["CLARA_RELEASE"] + "\\n" if log.exists() else os.environ["CLARA_RELEASE"] + "\\n")
    if (run / "update-result.json").exists():
        sys.exit(0)  # the rollback brought us back: the rehearsal is over
    plan = {"from": os.environ["OLD"], "to": os.environ["NEW"], "tag": "v9.9.9", "backup": os.environ["BACKUP"],
            "schema_before": 1, "sandbox_image_before": "old-image"}
    (run / "update.json").write_text(json.dumps(plan))
    sys.exit(75)
''')
BAD = textwrap.dedent('''
    import os, sqlite3, sys
    from pathlib import Path
    db = sqlite3.connect(Path(os.environ["CLARA_DATA_DIR"]) / "clara.db")
    db.execute("PRAGMA user_version = 2"); db.execute("CREATE TABLE IF NOT EXISTS only_new (x)"); db.commit()
    sys.exit(1)  # migrated, then broke
''')


def release(root: Path, name: str, body: str) -> Path:
    folder = root / name / ".venv" / "bin"
    folder.mkdir(parents=True)
    script = folder / "clara-server"
    script.write_text(f"#!{sys.executable}\n{body}")
    script.chmod(0o755)
    return root / name


def test_a_broken_update_rolls_back_and_restores_the_database(tmp_path):
    releases = tmp_path / "opt" / "releases"
    old = release(releases, "v1.0.0", GOOD)
    new = release(releases, "v9.9.9", BAD)
    (tmp_path / "opt" / "current").symlink_to(old)
    data = tmp_path / "data"
    (data / "run").mkdir(parents=True)
    database = data / "clara.db"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version = 1")
        db.execute("CREATE TABLE facts (text)")
        db.execute("INSERT INTO facts VALUES ('kept')")
    backup = data / "pre-update.db"
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    env = {"PATH": os.environ["PATH"], "CLARA_DATA_DIR": str(data), "CLARA_RELEASES_DIR": str(releases),
           "CLARA_PORT": str(free_port()), "CLARA_TIMEZONE": "Europe/Paris", "CLARA_ENV_FILE": str(tmp_path / "none"),
           "OLD": str(old), "NEW": str(new), "BACKUP": str(backup)}
    done = subprocess.run([sys.executable, "-m", "clara.ops.launcher"], env={**env, "PYTHONPATH": os.pathsep.join(sys.path)},
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert (data / "starts.log").read_text().split() == ["v1.0.0", "v1.0.0"]  # old, (new failed), old again
    assert (tmp_path / "opt" / "current").resolve() == old
    result = json.loads((data / "run" / "update-result.json").read_text())
    assert result["result"] == "rolled_back" and result["tag"] == "v9.9.9"
    assert result["sandbox_image_before"] == "old-image"  # the server that comes back puts it in place again
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT text FROM facts").fetchall() == [("kept",)]
        assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'only_new'").fetchall()


@pytest.mark.parametrize(("launched", "says_stopping"), [("1", False), ("", True)])
async def test_only_a_server_systemd_supervises_says_it_is_stopping(services, monkeypatch, launched, says_stopping):
    """STOPPING=1 from a server run by the launcher makes the whole unit "deactivating" (it may only be handing over to
    the next release) and systemd kills it at TimeoutStopSec: it happened after every update."""
    from clara import app as app_module

    sent: list[str] = []
    monkeypatch.setattr(app_module, "notify", sent.append)
    monkeypatch.setenv("CLARA_LAUNCHED", launched)
    app = app_module.create_app(services)
    async with app.router.lifespan_context(app):
        pass
    assert "READY=1" in sent and ("STOPPING=1" in sent) is says_stopping


def test_a_slow_loop_is_not_a_stalled_loop(services):
    """Seen on a real machine: the 10-minute and hourly loops looked stalled after two minutes, so the watchdog was not
    fed and systemd killed the server about 4.5 minutes after every start."""
    from clara.ops.upkeep import LOOP_STALL, Upkeep

    upkeep = Upkeep(services)
    now = time.monotonic()
    upkeep.periods = {"approvals": 10.0, "minute": 60.0, "incognito": 600.0, "hourly": 3600.0}
    upkeep.passes = {"approvals": now - 5, "minute": now - 40, "incognito": now - 300, "hourly": now - 300}
    assert upkeep.stalled() == []
    upkeep.passes["minute"] = now - 60 - LOOP_STALL - 1  # a loop that really stopped turning
    upkeep.passes["hourly"] = now - 3600 - LOOP_STALL - 1
    assert upkeep.stalled() == ["minute", "hourly"]


def test_the_scripts_the_workflows_run_are_executable():
    """CI and the release workflow call these scripts directly: one committed as 100644 fails with "Permission denied"
    (from Windows: git update-index --chmod=+x <file>)."""
    root = Path(__file__).resolve().parent.parent
    try:
        listing = subprocess.run(["git", "ls-files", "-s", "--", "*.sh"], cwd=root, capture_output=True, text=True,
                                 check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    modes = {line.split("\t")[1]: line.split()[0] for line in listing.splitlines()}
    workflows = "".join(path.read_text(encoding="utf-8") for path in (root / ".github" / "workflows").glob("*.yml"))
    called = set(re.findall(r"(?:scripts|deploy)/[\w.-]+\.sh", workflows))
    assert called and called <= set(modes)
    assert {name: mode for name, mode in modes.items() if mode != "100755"} == {}


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_the_installer_and_the_release_scripts_are_valid_shell():
    """The suite cannot run the installer (root, systemd), but a typo in it should not wait for a machine to show."""
    root = Path(__file__).resolve().parent.parent
    for script in ("deploy/install.sh", "scripts/build-release.sh", "scripts/build-web.sh"):
        done = subprocess.run(["bash", "-n", str(root / script)], capture_output=True, text=True)
        assert done.returncode == 0, f"{script}: {done.stderr}"


def test_a_rolled_back_update_puts_the_old_sandbox_image_back(services):
    """The updater sets the new release's image before the restart; the old release must not keep running on it."""
    from clara.ops.updater import Updater

    run = services.settings.run_dir
    run.mkdir(parents=True, exist_ok=True)
    services.runtime.set("sandbox_image", "ghcr.io/me/clara-sandbox@sha256:failed")
    (run / "update-result.json").write_text(json.dumps({"result": "rolled_back", "tag": "v9.9.9", "reason": "no /health",
                                                        "sandbox_image_before": "ghcr.io/me/clara-sandbox@sha256:old"}))
    Updater(services).report()
    assert services.runtime.get("sandbox_image") == "ghcr.io/me/clara-sandbox@sha256:old"
    assert "v9.9.9" in services.alerts.active["update"]


def test_a_successful_update_is_reported_by_the_server_that_is_already_running(services):
    """The launcher writes the success once the new release has answered /health, which is after its start."""
    from clara.ops.upkeep import Upkeep

    run = services.settings.run_dir
    run.mkdir(parents=True, exist_ok=True)
    (run / "update-result.json").write_text(json.dumps({"result": "ok", "tag": "v9.9.9"}))
    Upkeep(services).every_minute()
    assert not (run / "update-result.json").exists()
    assert [event.title for event in services.events.recent(5, ["update"])] == ["Updated to v9.9.9"]


async def test_github_says_when_its_rate_limit_is_used_up(monkeypatch):
    from types import SimpleNamespace

    from clara.ops import updater as updater_module

    def github(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"})

    real = httpx.AsyncClient
    monkeypatch.setattr(updater_module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(github), **kw))
    s = SimpleNamespace(settings=SimpleNamespace(release_repo="me/clara"), secrets=SimpleNamespace(get=lambda name: None))
    with pytest.raises(UpdateError, match=r"rate limit is used up \(it resets at 00:00 UTC\).*release_token"):
        await updater_module.Updater(s).releases()  # type: ignore[arg-type]


async def test_backups_are_private_verified_and_kept(services):
    from clara.ops.backups import Backups

    backups = Backups(services)
    message = await backups.run(manual=True)
    assert message.startswith("Backup written")
    path = Path(backups.listing()[0]["path"])
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    result = verify(path)
    assert result["integrity"] == "ok" and result["schema"] >= 1 and "facts" in result["rows"]


def _restic_that(tmp_path, services, body: str):
    """A stand-in for restic: a script that writes its PID to a file, then does `body`."""
    import dataclasses

    script = tmp_path / "restic"
    script.write_text(f"#!/bin/sh\necho $$ > {tmp_path}/pid\n{body}\n")
    script.chmod(0o755)
    services.settings = dataclasses.replace(services.settings, restic_bin=str(script), restic_repository="rest:x")
    services.secrets.set("restic_password", "pw")
    return tmp_path / "pid"


def _gone(pid_file) -> bool:
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return True
    return False


async def test_restic_is_stopped_when_it_times_out_and_the_alert_says_why(services, tmp_path, monkeypatch):
    """Audit (resource lifecycle, high): wait_for cancelled communicate() but left restic running with the lock, and
    str(TimeoutError()) is empty, so the alert was blank."""
    from clara.ops import backups as backups_module

    pid = _restic_that(tmp_path, services, "exec sleep 300")
    monkeypatch.setattr(backups_module, "RESTIC_TIMEOUT", 0.3)
    with pytest.raises(RuntimeError, match="restic backup took more than .* minutes and was stopped"):
        await backups_module.Backups(services)._restic("backup")
    assert _gone(pid)


async def test_restic_is_stopped_when_the_nightly_task_is_cancelled(services, tmp_path):
    import asyncio

    from clara.ops.backups import Backups

    pid = _restic_that(tmp_path, services, "exec sleep 300")
    task = asyncio.ensure_future(Backups(services)._restic("backup"))
    for _ in range(100):
        if pid.exists() and pid.read_text().strip():
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _gone(pid)


async def test_restic_that_ignores_the_request_to_stop_is_killed(services, tmp_path, monkeypatch):
    from clara.ops import backups as backups_module

    pid = _restic_that(tmp_path, services, "trap '' TERM\nwhile :; do sleep 0.1; done")
    monkeypatch.setattr(backups_module, "RESTIC_TIMEOUT", 0.3)
    monkeypatch.setattr(backups_module, "RESTIC_GRACE", 0.3)
    with pytest.raises(RuntimeError, match="was stopped"):
        await backups_module.Backups(services)._restic("backup")
    assert _gone(pid)


async def test_restic_output_and_failures_are_reported_as_before(services, tmp_path):
    from clara.ops.backups import Backups

    _restic_that(tmp_path, services, "echo snapshot saved")
    assert await Backups(services)._restic("backup") == "snapshot saved"
    _restic_that(tmp_path, services, "echo 'repository is locked' >&2\nexit 1")
    with pytest.raises(RuntimeError, match="repository is locked"):
        await Backups(services)._restic("backup")


async def test_the_drain_has_a_limit(services):
    import asyncio

    from clara.ops.lifecycle import Lifecycle

    lifecycle = Lifecycle(services, drain_seconds=0.3, poll=0.01, flush=0.0)
    services.agent.stats.active = 1  # an answer that never ends
    exited: list[int] = []
    lifecycle.on_exit = exited.append
    lifecycle.stop()
    await asyncio.sleep(0.6)
    assert exited == [0] and not services.agent.accepting
    services.agent.stats.active = 0


async def test_a_crash_is_put_straight_at_start(services):
    """After kill -9: an approved action that was running is not run twice, a scheduled run is due again, and the
    workspace history stays whole."""
    state = TurnState(turn_id="t1", conversation_id="", client="cli")
    action = Action("ws_delete", "destructive", "ws_destroy", ("ws:a.txt",), "Delete a.txt")
    approval = services.approvals.create(action, Situation("interactive"), ToolContext(services, state),
                                         {"path": "a.txt"}, Decision("ask", "default"))
    services.db.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (approval.id,))
    schedule = services.schedules.create("news", "Give me the news.", "2030-01-01 08:00", repeat="daily")
    due = iso(utcnow() - timedelta(minutes=5))
    services.db.execute("INSERT INTO schedule_runs (schedule_id, due_at, started_at, status) VALUES (?, ?, ?, 'running')",
                        (schedule.id, due, due))
    (services.workspace.root / "half.txt").write_text("written when the power went")

    recover(services)

    settled = services.approvals.get(approval.id)
    assert settled.status == "failed" and "may or may not have been done" in settled.result
    assert services.db.value("SELECT status FROM schedule_runs WHERE schedule_id = ?", (schedule.id,)) == "interrupted"
    assert services.db.value("SELECT next_at FROM schedules WHERE id = ?", (schedule.id,)) == due
    assert services.workspace.git_sync("status", "--porcelain").strip() == ""


def test_a_backup_restores_into_a_clean_data_folder(services, tmp_path, monkeypatch, capsys):
    """The restore drill: `clara restore --verify`, then `clara restore` on a machine that has nothing."""
    services.facts.add("Alex keeps bees.", "you")
    backup = backup_database(services.db.connection, tmp_path / "copy" / "clara-backup.db")
    clean = tmp_path / "clean"
    clean.mkdir(mode=0o700)
    for name, value in {"CLARA_DATA_DIR": str(clean), "CLARA_SECRET_KEY": "ab" * 32, "CLARA_TIMEZONE": "Europe/Paris",
                        "CLARA_TAILSCALE": "off", "CLARA_RUN": "off", "CLARA_CHECK_PERMISSIONS": "false",
                        "CLARA_ENV_FILE": str(tmp_path / "none.env")}.items():
        monkeypatch.setenv(name, value)

    assert ops_main(["restore", str(backup), "--verify"]) == 0
    assert "integrity ok" in capsys.readouterr().out
    assert not (clean / "clara.db").exists()
    assert ops_main(["restore", str(backup)]) == 0
    restored = sqlite3.connect(clean / "clara.db")
    assert restored.execute("SELECT text FROM facts").fetchall() == [("Alex keeps bees.",)]
    restored.close()
    assert oct((clean / "clara.db").stat().st_mode & 0o777) == "0o600"


# ----------------------------------------------------------------------
# The Discord bot, started with the server
# ----------------------------------------------------------------------
def test_the_bot_gets_its_settings_and_none_of_claras(environ, tmp_path):
    env_file = tmp_path / "clara.env"
    env_file.write_text("CLARA_DISCORD_USER_ID=42\nCLARA_DISCORD_CHANNEL_GENERAL=1\nCLARA_TOKEN_FILE=/x/clara-token\n"
                        "CLARA_DISCORD_AUTOSTART=true\nCLARA_PORT=9999\n")
    settings = load_settings({**environ, "CLARA_PORT": "9000"}, env_file=env_file)
    env = bot_environment(settings, {"PATH": "/bin", "CLARA_SECRET_KEY": "ab" * 32, "CLARA_SECRET_KEY_FILE": "/k",
                                     "CLARA_DISCORD_CHANNEL_GENERAL": "7", "CLARA_PORT": "9000"})
    assert env["PATH"] == "/bin"
    assert env["CLARA_DISCORD_USER_ID"] == "42" and env["CLARA_TOKEN_FILE"] == "/x/clara-token"
    assert env["CLARA_DISCORD_CHANNEL_GENERAL"] == "7"  # the environment wins over the file, as for Clara
    assert env["CLARA_URL"] == "http://127.0.0.1:9000" and env["CLARA_TIMEZONE"] == "Europe/Paris"
    assert env["CLARA_DISCORD_STATE"] == str(settings.data_dir / "discord-state.json")
    assert "CLARA_SECRET_KEY" not in env and "CLARA_SECRET_KEY_FILE" not in env and "CLARA_PORT" not in env


def script(body: str) -> list[str]:
    return [sys.executable, "-c", textwrap.dedent(body)]


async def until(condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("it did not happen in time")


async def test_a_bot_that_crashes_is_started_again_and_stopped_with_the_server(settings, tmp_path):
    runs = tmp_path / "runs"
    bot = DiscordBot(settings, lambda *alert: None, retry=(0.01,), command=script(f"""
        import pathlib, sys, time
        runs = pathlib.Path({str(runs)!r})
        runs.open("a").write("x")
        if len(runs.read_text()) < 3:
            sys.exit(1)
        time.sleep(60)
        """))
    bot.start()
    await until(lambda: runs.exists() and len(runs.read_text()) == 3 and bot.process is not None)
    process = bot.process
    await bot.stop()
    assert process.returncode is not None and bot.process is None
    assert len(runs.read_text()) == 3  # not started a fourth time


@pytest.mark.parametrize("code, alerted", [(0, False), (2, True), (3, False)])
async def test_a_bot_that_is_stopped_or_refused_is_not_retried(settings, tmp_path, code, alerted):
    runs = tmp_path / "runs"
    alerts = []
    bot = DiscordBot(settings, lambda *alert: alerts.append(alert), retry=(0.01,), command=script(f"""
        import pathlib, sys
        pathlib.Path({str(runs)!r}).open("a").write("x")
        sys.exit({code})
        """))
    bot.start()
    await until(lambda: bot._task.done())
    await bot.stop()
    assert runs.read_text() == "x"
    assert [key for key, *_ in alerts] == (["discord"] if alerted else []) and bool(bot.problem) == alerted


async def test_a_missing_discord_library_is_an_alert_not_a_crash(settings, monkeypatch):
    alerts = []
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    bot = DiscordBot(settings, lambda *alert: alerts.append(alert))
    bot.start()
    await until(lambda: bot._task.done())
    assert alerts and alerts[0][0] == "discord" and "discord.py is not installed" in alerts[0][2]


def test_the_bot_is_only_started_when_asked(environ, tmp_path, llm):
    from clara.app import build_services, extend, open_database

    for autostart, expected in (("false", None), ("true", DiscordBot)):
        data = tmp_path / autostart
        settings = load_settings({**environ, "CLARA_DATA_DIR": str(data), "CLARA_DISCORD_AUTOSTART": autostart}, use_file=False)
        db = open_database(settings)
        s = build_services(settings, db, provider_factory=llm.factory)
        extend(s)
        assert (s.discord is None) if expected is None else isinstance(s.discord, expected)
        s.traffic.close()
        db.close()
