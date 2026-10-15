"""Gate 6: Funnel blocks start, releases are checked, a broken update rolls back by itself, backups restore."""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from clara.app import recover
from clara.cli.ops import main as ops_main
from clara.db import backup_database
from clara.gate import Action, Decision, Situation
from clara.ops.backups import verify
from clara.ops.tailscale import FunnelOn, Output, Tailscale, check_funnel_now, funnel_ports
from clara.ops.updater import UpdateError, check_signature, check_sums, safe_extract
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
            "schema_before": 1}
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
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT text FROM facts").fetchall() == [("kept",)]
        assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'only_new'").fetchall()


async def test_backups_are_private_verified_and_kept(services):
    from clara.ops.backups import Backups

    backups = Backups(services)
    message = await backups.run(manual=True)
    assert message.startswith("Backup written")
    path = Path(backups.listing()[0]["path"])
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    result = verify(path)
    assert result["integrity"] == "ok" and result["schema"] >= 1 and "facts" in result["rows"]


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
