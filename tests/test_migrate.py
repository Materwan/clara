"""Schema versions: SQL and Python migrations share one sequence, one transaction and one backup."""

from __future__ import annotations

import sqlite3

import pytest

from clara.db.connection import connect
from clara.db.migrate import LATEST_VERSION, SchemaTooNew, migrate, migrations, schema_version

SQL_ONE = "CREATE TABLE things (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\nINSERT INTO things (name) VALUES ('first');\n"
PY_TWO = """
def migrate(connection):
    connection.execute("ALTER TABLE things ADD COLUMN shout TEXT")
    for row in connection.execute("SELECT id, name FROM things").fetchall():
        connection.execute("UPDATE things SET shout = ? WHERE id = ?", (row["name"].upper(), row["id"]))
"""
PY_FAILS = """
def migrate(connection):
    connection.execute("INSERT INTO things (name) VALUES ('half done')")
    connection.execute("DROP TABLE things")
    raise RuntimeError("this step cannot be finished")
"""


def folder(tmp_path, **files):
    found = tmp_path / "migrations"
    found.mkdir()
    for name, text in files.items():
        (found / name).write_text(text, encoding="utf-8")
    return found


def test_the_real_sequence_counts_every_kind():
    found = migrations()
    assert LATEST_VERSION == len(found) == found[-1].version
    assert [m.version for m in found] == list(range(1, LATEST_VERSION + 1))
    assert all((m.sql is None) != (m.code is None) for m in found)  # SQL xor Python


def test_a_python_migration_runs_in_the_sequence(tmp_path):
    source = folder(tmp_path, **{"0001_things.sql": SQL_ONE, "0002_shout.py": PY_TWO})
    path = tmp_path / "data" / "clara.db"
    assert migrate(path, tmp_path / "backups", source) == (0, 2, None)  # a new database needs no backup
    connection = connect(path)
    assert schema_version(connection) == 2
    assert [tuple(row) for row in connection.execute("SELECT name, shout FROM things")] == [("first", "FIRST")]
    connection.close()
    assert migrate(path, tmp_path / "backups", source) == (2, 2, None)  # up to date: nothing to do


def test_a_python_migration_after_a_backup_on_an_existing_database(tmp_path):
    first = folder(tmp_path, **{"0001_things.sql": SQL_ONE})
    path = tmp_path / "data" / "clara.db"
    migrate(path, tmp_path / "backups", first)
    (first / "0002_shout.py").write_text(PY_TWO, encoding="utf-8")
    before, after, backup = migrate(path, tmp_path / "backups", first)
    assert (before, after) == (1, 2)
    assert backup is not None and backup.exists() and backup.name.startswith("pre-migration-v1-")
    saved = sqlite3.connect(backup)  # the backup holds the database as it was before the step
    assert [column[1] for column in saved.execute("PRAGMA table_info(things)")] == ["id", "name"]
    saved.close()


def test_a_python_migration_that_fails_rolls_back_the_whole_step(tmp_path):
    source = folder(tmp_path, **{"0001_things.sql": SQL_ONE})
    path = tmp_path / "data" / "clara.db"
    migrate(path, tmp_path / "backups", source)
    (source / "0002_fails.py").write_text(PY_FAILS, encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot be finished"):
        migrate(path, tmp_path / "backups", source)
    connection = connect(path)
    assert schema_version(connection) == 1  # still at the version before the step
    assert [row["name"] for row in connection.execute("SELECT name FROM things")] == ["first"]  # nothing half done
    assert not connection.in_transaction
    connection.close()
    (source / "0002_fails.py").write_text(PY_TWO, encoding="utf-8")  # the step can be tried again once mended
    assert migrate(path, tmp_path / "backups", source)[:2] == (1, 2)


def test_a_sql_migration_after_a_python_one_still_runs(tmp_path):
    source = folder(
        tmp_path,
        **{
            "0001_things.sql": SQL_ONE,
            "0002_shout.py": PY_TWO,
            "0003_more.sql": "INSERT INTO things (name, shout) VALUES ('later', 'LATER');",
        },
    )
    path = tmp_path / "clara.db"
    assert migrate(path, tmp_path / "backups", source)[:2] == (0, 3)
    connection = connect(path)
    assert connection.execute("SELECT COUNT(*) FROM things").fetchone()[0] == 2
    connection.close()


def test_a_gap_in_the_sequence_is_refused(tmp_path):
    source = folder(tmp_path, **{"0001_things.sql": SQL_ONE, "0003_shout.py": PY_TWO})
    with pytest.raises(RuntimeError, match="0003_shout.py is out of sequence"):
        migrations(source)
    (tmp_path / "second").mkdir()
    twice = folder(tmp_path / "second", **{"0001_a.sql": SQL_ONE})
    (twice / "0001_b.py").write_text(PY_TWO, encoding="utf-8")
    with pytest.raises(RuntimeError, match="out of sequence"):
        migrations(twice)  # two files with the same number are a gap too


def test_a_python_migration_without_migrate_is_refused(tmp_path):
    source = folder(tmp_path, **{"0001_nothing.py": "x = 1\n"})
    with pytest.raises(RuntimeError, match="defines no migrate"):
        migrations(source)


def test_a_database_newer_than_the_code_is_refused_whatever_the_kind(tmp_path):
    source = folder(tmp_path, **{"0001_things.sql": SQL_ONE, "0002_shout.py": PY_TWO})
    path = tmp_path / "clara.db"
    migrate(path, tmp_path / "backups", source)
    (source / "0002_shout.py").unlink()  # an older release only knows version 1
    with pytest.raises(SchemaTooNew, match="schema version 2"):
        migrate(path, tmp_path / "backups", source)
