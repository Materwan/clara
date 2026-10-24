"""`clara-launcher`: what systemd starts. It runs the release the `current` link points to, and is the one who switches
releases, checks the new one and rolls back.

    exit 0         the server stopped: the launcher stops too
    exit 76        restart the same release
    exit 75        an update is ready (`run/update.json`): switch `current` to it, start it, and wait up to 60 s for
                   /health and the boot id the new process writes (`run/boot`). If it does not come up (or dies), stop
                   it, switch `current` back, restore the database from the pre-update backup if the schema moved,
                   start the old release, and leave `run/update-result.json` for it to raise an alert.
    anything else  the launcher exits with it (systemd's Restart=on-failure takes over)

SIGTERM and SIGINT are passed to the server (a graceful, bounded drain); the launcher then exits with the server.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from ..settings import SettingsError, load_settings
from .lifecycle import EXIT_RESTART, EXIT_UPDATE
from .updater import PLAN, RESULT

log = logging.getLogger("clara.launcher")

HEALTH_SECONDS = 60.0
STOP_SECONDS = 30.0


def switch(link: Path, target: Path) -> None:
    """Point `link` at `target` atomically."""
    temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex[:8]}")
    os.symlink(target, temporary)
    os.replace(temporary, link)


def healthy(port: int, boot_file: Path, boot: str, child: subprocess.Popen, limit: float = HEALTH_SECONDS) -> str:
    """'' when the new process answers /health and wrote its boot id within `limit` seconds, else why not."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return f"the new release exited at once (code {child.returncode})"
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:  # local address
                ok = json.loads(response.read() or b"{}").get("status") == "ok"
        except (OSError, ValueError):
            ok = False
        if ok and boot_file.exists() and boot_file.read_text(encoding="utf-8").strip() == boot:
            return ""
        time.sleep(1)
    return f"no answer from /health with the new boot id within {limit:g} s"


def stop(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.send_signal(signal.SIGTERM)
    try:
        child.wait(STOP_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def restore(database: Path, backup: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    target = sqlite3.connect(str(database))
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def schema_of(database: Path) -> int:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        settings = load_settings()
    except SettingsError as error:
        print(error, file=sys.stderr)
        return 2
    link = settings.releases_dir.parent / "current"
    run_dir = settings.run_dir
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    child: subprocess.Popen | None = None
    stopping = False

    def forward(signum, frame) -> None:
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    checking: dict | None = None
    while True:
        release = link.resolve() if link.exists() else Path(sys.prefix).parent
        server = release / ".venv" / "bin" / "clara-server"
        if not server.exists():
            server = Path(sys.prefix) / "bin" / "clara-server"
        boot = uuid.uuid4().hex
        env = {**os.environ, "CLARA_BOOT_ID": boot, "CLARA_RELEASE": release.name, "CLARA_LAUNCHED": "1"}
        (run_dir / "boot").unlink(missing_ok=True)
        log.info("starting %s (boot %s)", release.name, boot[:8])
        child = subprocess.Popen([str(server)], env=env)
        if checking is not None:
            problem = healthy(settings.port, run_dir / "boot", boot, child)
            plan, checking = checking, None
            if problem:
                log.error("the new release %s did not come up (%s): rolling back", plan["tag"], problem)
                stop(child)
                switch(link, Path(plan["from"]))
                if schema_of(settings.database) > int(plan["schema_before"]):
                    restore(settings.database, Path(plan["backup"]))
                (run_dir / RESULT).write_text(
                    json.dumps({"result": "rolled_back", "tag": plan["tag"], "reason": problem})
                )
                continue
            (run_dir / RESULT).write_text(json.dumps({"result": "ok", "tag": plan["tag"]}))
            log.info("%s is up", plan["tag"])
        code = child.wait()
        if stopping:
            return code
        if code == EXIT_UPDATE and (run_dir / PLAN).exists():
            checking = json.loads((run_dir / PLAN).read_text(encoding="utf-8"))
            (run_dir / PLAN).unlink()
            switch(link, Path(checking["to"]))
            continue
        if code == EXIT_RESTART:
            continue
        return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
