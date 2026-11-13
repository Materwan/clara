"""Schema versions.

Migrations are numbered files in `migrations/`, in one sequence: `0001_initial.sql`, `0002_…`, and, when a change needs
real code (a conversion that SQL cannot say), `NNNN_name.py` defining `migrate(connection)`. A Python migration runs in
the same transaction as a SQL one, after the same backup, and sets the version the same way: if it raises, nothing it did
stays. The version a database is at is kept in `PRAGMA user_version`. Opening a database:

* newer than this code knows: refused (`SchemaTooNew`), so an old release can never write into a newer schema;
* older: a backup is taken first (SQLite's online backup API), then each missing migration runs in its own
  transaction, which also sets the new version (a failure leaves the database at the version before it);
* empty: every migration runs, no backup needed.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from ..timeutil import utcnow
from .connection import connect

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.(sql|py)$")


class SchemaTooNew(RuntimeError):
    """The database was written by a newer Clara."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str | None = None  # a SQL migration: its script
    code: Callable[[sqlite3.Connection], None] | None = None  # a Python migration: its `migrate(connection)`


def _python(item: Traversable) -> Callable[[sqlite3.Connection], None]:
    if not isinstance(item, Path):
        raise RuntimeError(f"migration {item.name} is not a plain file: a Python migration cannot be loaded from it")
    spec = importlib.util.spec_from_file_location(f"clara_migration_{item.stem}", item)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"migration {item.name} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = getattr(module, "migrate", None)
    if not callable(code):
        raise RuntimeError(f"migration {item.name} defines no migrate(connection)")
    return code


def migrations(source: Traversable | None = None) -> list[Migration]:
    """Every migration of the package (or of the folder `source`), in order. The numbers must be 1, 2, 3… with no gap, SQL
    and Python files together."""
    found: list[Migration] = []
    for item in (source or resources.files("clara.db.migrations")).iterdir():
        match = _NAME.match(item.name)
        if match is None:
            continue
        version = int(match.group(1))
        if match.group(2) == "sql":
            found.append(Migration(version, item.name, sql=item.read_text(encoding="utf-8")))
        else:
            found.append(Migration(version, item.name, code=_python(item)))
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


def _apply(connection: sqlite3.Connection, migration: Migration) -> None:
    """One migration, in one transaction with the new version: all of it or none of it."""
    if migration.code is None:
        # executescript commits whatever is open first; the script itself is one transaction
        connection.executescript(
            f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;"
        )
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        migration.code(connection)
        connection.execute(f"PRAGMA user_version = {migration.version}")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def migrate(path: Path, backup_dir: Path, source: Traversable | None = None) -> tuple[int, int, Path | None]:
    """Bring the database at `path` to the latest schema. Returns (version before, version after, backup made).
    `source` is a folder of migrations other than the package's (tests)."""
    found = migrations(source) if source is not None else migrations()
    latest = len(found)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = connect(path)
    try:
        before = schema_version(connection)
        if before > latest:
            raise SchemaTooNew(
                f"{path} is at schema version {before}, but this Clara only knows up to {latest}: "
                "run a newer release, or restore a backup made before the upgrade."
            )
        if before == latest:
            return before, before, None
        backup = None
        if before > 0:
            stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
            backup = backup_database(connection, backup_dir / f"pre-migration-v{before}-{stamp}.db")
        for migration in found[before:]:
            _apply(connection, migration)
        return before, schema_version(connection), backup
    finally:
        connection.close()
