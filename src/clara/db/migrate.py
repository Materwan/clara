"""Schema versions.

Migrations are numbered SQL files in `migrations/` (`0001_initial.sql`, `0002_…`). The version a database is at is
kept in `PRAGMA user_version`. Opening a database:

* newer than this code knows: refused (`SchemaTooNew`), so an old release can never write into a newer schema;
* older: a backup is taken first (SQLite's online backup API), then each missing migration runs in its own
  transaction, which also sets the new version;
* empty: every migration runs, no backup needed.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ..timeutil import utcnow
from .connection import connect

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class SchemaTooNew(RuntimeError):
    """The database was written by a newer Clara."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def migrations() -> list[Migration]:
    found: list[Migration] = []
    for item in resources.files("clara.db.migrations").iterdir():
        match = _NAME.match(item.name)
        if match:
            found.append(Migration(int(match.group(1)), item.name, item.read_text(encoding="utf-8")))
    found.sort(key=lambda migration: migration.version)
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise RuntimeError(f"migration {migration.name} is out of sequence (expected {expected:04d})")
    return found


LATEST_VERSION = len(migrations())


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def backup_database(source: sqlite3.Connection | Path, destination: Path) -> Path:
    """A consistent copy of a live database through SQLite's backup API, written private (0600)."""
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + ".partial")
    temporary.unlink(missing_ok=True)
    opened = isinstance(source, Path)
    connection = connect(source) if isinstance(source, Path) else source
    target = sqlite3.connect(str(temporary))
    try:
        temporary.chmod(0o600)
        connection.backup(target)
    finally:
        target.close()
        if opened:
            connection.close()
    temporary.replace(destination)
    return destination


def migrate(path: Path, backup_dir: Path) -> tuple[int, int, Path | None]:
    """Bring the database at `path` to the latest schema. Returns (version before, version after, backup made)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = connect(path)
    try:
        before = schema_version(connection)
        if before > LATEST_VERSION:
            raise SchemaTooNew(
                f"{path} is at schema version {before}, but this Clara only knows up to {LATEST_VERSION}: "
                "run a newer release, or restore a backup made before the upgrade."
            )
        if before == LATEST_VERSION:
            return before, before, None
        backup = None
        if before > 0:
            stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
            backup = backup_database(connection, backup_dir / f"pre-migration-v{before}-{stamp}.db")
        for migration in migrations()[before:]:
            # executescript commits whatever is open first; the script itself is one transaction
            connection.executescript(
                f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;"
            )
        return before, schema_version(connection), backup
    finally:
        connection.close()
