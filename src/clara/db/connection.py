"""One connection to the database, shared by the whole server and guarded by a lock.

Every operation takes a few milliseconds, so async code calls it directly. Writes that must stand or fall together go
through `transaction()`, which takes SQLite's write lock at once (BEGIN IMMEDIATE) so that two of them cannot
deadlock each other into `database is locked`.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

Row = sqlite3.Row

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA secure_delete = ON",  # deleted text is overwritten, not left in free pages
    "PRAGMA busy_timeout = 5000",
)


def connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        connection.execute(pragma)
    return connection


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._connection = connect(self.path)
        self._lock = threading.RLock()
        self._depth = 0  # nested transaction() calls join the outer one

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def execute(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._connection.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self._connection.executemany(sql, rows)

    def all(self, sql: str, params: Sequence[Any] | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, params).fetchone()

    def value(self, sql: str, params: Sequence[Any] | dict = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        return default if row is None or row[0] is None else row[0]

    def insert(self, sql: str, params: Sequence[Any] | dict = ()) -> int:
        with self._lock:
            cursor = self._connection.execute(sql, params)
            return int(cursor.lastrowid or 0)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction; nested calls join the outermost one."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self._connection
                finally:
                    self._depth -= 1
                return
            self._connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._connection
            except BaseException:
                self._depth = 0
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._depth = 0
                self._connection.execute("COMMIT")
