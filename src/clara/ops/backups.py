"""Backups.

* **Local**, every night at CLARA_BACKUP_TIME (03:30) in your zone: a consistent copy of the database through SQLite's
  backup API, written private (0600) in `backups/daily/`; 14 kept, and every Sunday's copied to `backups/weekly/`
  (8 kept).
* **Remote**, when CLARA_RESTIC_REPOSITORY is set: the newest backup and a `git bundle` of the workspace go to a restic
  repository on another machine of your tailnet (sftp: or rest:), encrypted by restic with the password in the secrets
  store (`restic_password`). The secrets key is never in it: keep it somewhere else. Run `clara backup init-remote` once.
* Any failure raises an alert. `clara restore --verify <file>` opens a backup, runs `integrity_check` and counts rows;
  a monthly reminder asks you to try it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ..db import backup_database
from ..timeutil import utcnow, zone

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

RESTIC_TIMEOUT = 3600
RESTIC_GRACE = 20  # seconds restic gets to release the repository lock after it is asked to stop


def verify(path: Path) -> dict:
    """Open a backup read-only: its integrity, its schema version and how many rows each table holds."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
                " ORDER BY name"
            )
        ]
        counts = {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    finally:
        connection.close()
    return {"file": str(path), "integrity": integrity, "schema": version, "rows": counts}


class Backups:
    def __init__(self, s: Services):
        self.s = s
        self.dir = s.settings.backups_dir
        self.last: str | None = None
        self.last_remote: str | None = None
        self.problem = ""
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def describe(self) -> dict:
        newest = self.listing()[:1]
        return {
            "last": self.last or (newest[0]["name"] if newest else None),
            "remote": self.s.settings.restic_repository,
            "last_remote": self.last_remote,
            "problem": self.problem,
        }

    def listing(self) -> list[dict]:
        found = []
        for kind in ("daily", "weekly", "manual", "pre-update", ""):
            folder = self.dir / kind if kind else self.dir
            for path in folder.glob("*.db") if folder.is_dir() else []:
                found.append(
                    {
                        "name": path.name,
                        "kind": kind or "migration",
                        "path": str(path),
                        "size_mb": round(path.stat().st_size / 1_000_000, 1),
                        "mtime": path.stat().st_mtime,
                    }
                )
        return sorted(found, key=lambda item: item["mtime"], reverse=True)

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.ensure_future(self.nightly())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    def _next_time(self) -> datetime:
        tz = zone(self.s.settings.timezone)
        hour, minute = (int(part) for part in self.s.settings.backup_time.split(":"))
        now = datetime.now(tz)
        moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return moment if moment > now else moment + timedelta(days=1)

    async def nightly(self) -> None:
        while True:
            wait = (self._next_time() - datetime.now(zone(self.s.settings.timezone))).total_seconds()
            await asyncio.sleep(max(1.0, wait))
            await self.run()

    async def run(self, manual: bool = False) -> str:
        async with self._lock:
            try:
                path = await asyncio.to_thread(self.local, "manual" if manual else "daily")
            except Exception as error:
                self.problem = f"local backup failed: {error}"
                self.s.alerts.raise_("backup", "A backup failed", self.problem)
                return self.problem
            message = f"Backup written: {path.name}."
            if self.s.settings.restic_repository:
                try:
                    await self.remote(path)
                    message += " Copied to the remote repository."
                except Exception as error:
                    self.problem = f"remote backup failed: {error}"
                    self.s.alerts.raise_("backup", "A backup failed", self.problem)
                    return message + f" {self.problem}"
            self.problem = ""
            self.s.alerts.clear("backup")
            return message

    def local(self, kind: str) -> Path:
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        folder = self.dir / kind
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = backup_database(self.s.db.connection, folder / f"clara-{stamp}.db")
        result = verify(path)
        if result["integrity"] != "ok":
            raise RuntimeError(f"the copy is not sound ({result['integrity']})")
        self.last = path.name
        if kind == "daily":
            if datetime.now(zone(self.s.settings.timezone)).weekday() == 6:
                weekly = self.dir / "weekly"
                weekly.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(path, weekly / path.name)
                os.chmod(weekly / path.name, 0o600)
                self._keep(weekly, self.s.settings.backup_keep_weekly)
            self._keep(folder, self.s.settings.backup_keep_daily)
        elif kind == "manual":
            self._keep(folder, 10)
        return path

    @staticmethod
    def _keep(folder: Path, count: int) -> None:
        files = sorted(folder.glob("clara-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[count:]:
            old.unlink(missing_ok=True)

    def _restic_env(self) -> dict[str, str]:
        password = self.s.secrets.get("restic_password")
        if not password:
            raise RuntimeError("the secret restic_password is not set")
        keep = ("PATH", "HOME", "XDG_CACHE_HOME", "SSH_AUTH_SOCK")
        env = {key: value for key, value in os.environ.items() if key in keep}
        env.update(RESTIC_REPOSITORY=self.s.settings.restic_repository, RESTIC_PASSWORD=password)
        return env

    async def _restic(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self.s.settings.restic_bin,
            *args,
            env=self._restic_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), RESTIC_TIMEOUT)
        except TimeoutError:
            await self._stop(process)
            raise RuntimeError(
                f"restic {args[0]} took more than {RESTIC_TIMEOUT // 60} minutes and was stopped"
            ) from None
        except BaseException:  # the nightly task is cancelled at shutdown
            await self._stop(process)
            raise
        text = out.decode(errors="replace").strip()
        if process.returncode != 0:
            raise RuntimeError(text.splitlines()[-1] if text else f"restic exited {process.returncode}")
        return text

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        """Restic left running would keep the repository locked, and the next backup would fail on it. It is asked to
        stop (it then removes its lock), and killed if it does not."""
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), RESTIC_GRACE)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        except BaseException:  # cancelled again while it was being stopped
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise

    async def remote(self, database: Path) -> None:
        files = [str(database)]
        bundle = self.dir / "workspace.bundle"
        if self.s.workspace is not None:
            await self.s.workspace.git("bundle", "create", str(bundle), "--all")
            os.chmod(bundle, 0o600)
            files.append(str(bundle))
        await self._restic("backup", "--tag", "clara", "--host", "clara", *files)
        await self._restic(
            "forget",
            "--tag",
            "clara",
            "--keep-daily",
            str(self.s.settings.backup_keep_daily),
            "--keep-weekly",
            str(self.s.settings.backup_keep_weekly),
            "--prune",
        )
        self.last_remote = utcnow().isoformat(timespec="seconds")

    async def init_remote(self) -> str:
        return await self._restic("init")
