"""Schema version 32: schedules and hooks become automations, and everything that pointed at them still does."""

from __future__ import annotations

import json
import sqlite3

import pytest

from clara.automations import matching, tree
from clara.automations.events import KINDS
from clara.db import Database
from clara.db.connection import connect
from clara.db.migrate import SchemaTooNew, migrate, migrations, schema_version
from clara.events import Events
from clara.memory.conversations import Conversations

PARIS = "Europe/Paris"
NOW = "2026-11-13T10:00:00+00:00"


def old_database(path, until: int = 31):
    connection = connect(path)
    for migration in migrations()[:until]:
        connection.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;")
    return connection


def schedule(connection, id_: int, name: str, local_at: str, repeat: str = "daily", **fields) -> None:
    values = {
        "id": id_, "name": name, "prompt": f"Do {name}.", "local_at": local_at, "timezone": PARIS, "repeat": repeat,
        "next_at": "2026-11-14T08:59:00+00:00", "created_at": NOW, "updated_at": NOW, **fields,
    }
    connection.execute(f"INSERT INTO schedules ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})", list(values.values()))


def hook(connection, id_: int, name: str, kind: str, filter_: dict, **fields) -> None:
    values = {
        "id": id_, "name": name, "event_kind": kind, "filter": json.dumps(filter_), "action": "run", "prompt": f"Do {name}.",
        "created_at": NOW, "updated_at": NOW, **fields,
    }
    connection.execute(f"INSERT INTO triggers ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})", list(values.values()))


def conversation(connection, id_: str, kind: str = "chat") -> None:
    connection.execute("INSERT INTO conversations (id, client, kind, created_at, updated_at) VALUES (?, 'server', ?, ?, ?)", (id_, kind, NOW, NOW))


def fact(connection, text: str, scope: str) -> None:
    connection.execute("INSERT INTO facts (text, text_key, source, created_at, scope) VALUES (?, ?, 'model', ?, ?)", (text, text.lower(), NOW, scope))


@pytest.fixture
def old(tmp_path):
    """A database at version 31 with realistic schedules (ids 1, 2, 4, 5, 6) and hooks (ids 1, 2, 5), their runs, their memory
    and their Discord forums."""
    path = tmp_path / "data" / "clara.db"
    path.parent.mkdir()
    connection = old_database(path)
    for cid in ("c-note", "c-shared", "c-ask", "c-hook-ok"):
        conversation(connection, cid, "schedule" if cid == "c-shared" else "chat")
    connection.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES (1, 'Home', ?, ?)", (NOW, NOW))
    schedule(connection, 1, "Daily note", "2026-10-18T23:59", packs='["second_brain", "ask_person"]', per_run=1, missed="latest",
             overlap="replace", in_history=1, deliver="both", keep_days=2, keep_hours=0, keep_last=5, attempts=2, model="local:fake")
    schedule(connection, 2, "Weekly", "2026-11-02T08:00", "weekly", weekdays="0,4", deliver="web", project_id=1)
    schedule(connection, 4, "Month end", "2026-01-31T09:00", "monthly", deliver="discord", conversation_id="c-shared")
    schedule(connection, 5, "Once", "2026-12-01T09:00", "", next_at="2026-12-01T08:00:00+00:00", local_only=1)
    schedule(connection, 6, "Paused", "2026-11-02T08:00", enabled=0, next_at=None)
    hook(connection, 1, "Invoices", "mail.received", {
        "subject": {"contains": ["invoice", "facture"], "not_contains": ["newsletter"]},
        "from": {"starts_with": "billing@"}, "has_attachment": {"eq": True}},
        tools='["mail_read"]', catch_up="latest", max_per_hour=6, max_age_hours=24, deliver="discord", project_id=1, include_self=1,
        rerun_interrupted=1, announce=0, keep_last=3)
    hook(connection, 2, "Daily note", "github.push", {"branch": {"eq": "main"}, "message": {"ends_with": "fix (a+b)."}},
         action="notify", prompt="{repo}", deliver="web", enabled=0)
    hook(connection, 5, "Everything", "github.pull_request", {})
    for key, kind, at in (("k1", "mail.received", "2026-11-13T09:00:00+00:00"), ("k2", "github.push", "2026-11-13T09:30:00+00:00"),
                          ("k3", "github.push", "2026-11-13T09:45:00+00:00")):
        connection.execute("INSERT INTO trigger_events (source, kind, key, occurred_at, received_at, title) VALUES ('x', ?, ?, ?, ?, ?)", (kind, key, at, at, key))
    connection.execute("INSERT INTO schedule_runs (id, schedule_id, due_at, started_at, finished_at, status, summary, late_seconds, conversation_id)"
                       " VALUES (1, 1, '2026-11-12T22:59:00+00:00', '2026-11-12T22:59:05+00:00', '2026-11-12T23:00:00+00:00', 'ok', 'Done.', 5, 'c-note')")
    connection.execute("INSERT INTO schedule_runs (id, schedule_id, due_at, started_at, status, summary, waiting_since, reminded_at, conversation_id)"
                       " VALUES (2, 1, '2026-11-13T22:59:00+00:00', '2026-11-13T22:59:05+00:00', 'waiting', 'Question?', '2026-11-13T23:00:00+00:00', NULL, 'c-ask')")
    connection.execute("INSERT INTO trigger_runs (id, trigger_id, event_ids, status, summary, queued_at, started_at, finished_at, conversation_id)"
                       " VALUES (1, 1, '[1]', 'ok', 'Handled.', '2026-11-13T09:00:01+00:00', '2026-11-13T09:00:02+00:00', '2026-11-13T09:01:00+00:00', 'c-hook-ok')")
    connection.execute("INSERT INTO trigger_runs (id, trigger_id, event_ids, status, queued_at) VALUES (2, 2, '[2, 3]', 'queued', '2026-11-13T09:46:00+00:00')")
    connection.execute("INSERT INTO trigger_runs (id, trigger_id, event_ids, status, note, queued_at, finished_at) VALUES (3, 5, '[2]', 'skipped', 'Found late', '2026-11-13T09:46:00+00:00', '2026-11-13T09:46:00+00:00')")
    for scope, text in (("schedule:1", "Likes short notes"), ("hook:2", "Ignore bot pushes"), ("hook:5", "Everything is noise"),
                        ("schedule:4", "Month end on the 31st"), ("project:1", "A project fact"), ("", "A global fact")):
        fact(connection, text, scope)
    connection.execute("INSERT INTO forgotten (scope, text_key, forgotten_at) VALUES ('hook:2', 'old note', ?)", (NOW,))
    for key in ("project:1", "schedule:1", "schedule:4", "hook:2", "main"):
        connection.execute("INSERT INTO discord_places (key, category_id, forum_id, updated_at) VALUES (?, 'cat', 'forum', ?)", (key, NOW))
    connection.execute("INSERT INTO discord_threads (conversation_id, place_key, thread_id) VALUES ('c-note', 'schedule:1', 't1')")
    connection.execute("INSERT INTO discord_threads (conversation_id, place_key, thread_id) VALUES ('c-hook-ok', 'hook:1', 't2')")
    connection.close()
    return path


def row(connection, sql: str, *params):
    found = connection.execute(sql, params).fetchone()
    return None if found is None else found[0]


def test_every_schedule_and_hook_is_converted_with_the_ids_it_had_or_an_offset(old, tmp_path):
    before, after, backup = migrate(old, tmp_path / "backups")
    assert (before, after) == (31, len(migrations())) and backup is not None  # the automatic backup was taken first
    connection = connect(old)
    assert [(r["id"], r["name"]) for r in connection.execute("SELECT id, name FROM automations ORDER BY id")] == [
        (1, "Daily note"), (2, "Weekly"), (4, "Month end"), (5, "Once"), (6, "Paused"),
        (7, "Invoices"), (8, "Daily note (hook)"), (11, "Everything")]  # a schedule keeps its id; a hook gets 6 + its id
    ids = [tuple(r) for r in connection.execute("SELECT kind, old_id, new_id FROM legacy_ids WHERE kind IN ('schedule', 'hook') ORDER BY kind, old_id")]
    assert ids == [("hook", 1, 7), ("hook", 2, 8), ("hook", 5, 11), ("schedule", 1, 1), ("schedule", 2, 2), ("schedule", 4, 4),
                   ("schedule", 5, 5), ("schedule", 6, 6)]
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"schedules", "schedule_runs", "triggers", "trigger_runs"}  # gone, and kept as plain copies:
    assert {"legacy_schedules", "legacy_schedule_runs", "legacy_triggers", "legacy_trigger_runs"} <= tables
    assert [row(connection, f"SELECT COUNT(*) FROM legacy_{t}") for t in ("schedules", "schedule_runs", "triggers", "trigger_runs")] == [5, 2, 3, 3]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_a_schedule_becomes_the_cron_expression_of_its_repeat_and_keeps_its_settings(old, tmp_path):
    migrate(old, tmp_path / "backups")
    connection = connect(old)
    zone = __import__("zoneinfo").ZoneInfo(PARIS)

    def converted(number: int) -> dict:
        found = json.loads(row(connection, "SELECT tree FROM automations WHERE id = ?", number))
        return tree.normalize(found, KINDS.get, zone)  # the stored tree is one this code accepts, unchanged
    expressions = lambda number: [n.get("expr") or n.get("at") for n in converted(number)["of"]]  # noqa: E731
    assert expressions(1) == ["59 23 * * *"] and expressions(2) == ["0 8 * * 1,5"] and expressions(5) == ["2026-12-01T09:00"]
    assert expressions(4) == ["0 9 31 1,3,5,7,8,10,12 *", "0 9 L 2,4,6,9,11 *"]  # the 31st, and the last day of a shorter month
    for number in (1, 2, 4, 5, 6):
        assert json.loads(row(connection, "SELECT tree FROM automations WHERE id = ?", number)) == converted(number)
    daily = connection.execute("SELECT * FROM automations WHERE id = 1").fetchone()
    assert (daily["catch_up"], daily["overlap"], daily["per_run"], daily["rerun_interrupted"], daily["attempts"], daily["model"]) == (
        "latest", "replace", 1, 1, 2, "local:fake")
    assert json.loads(daily["packs"]) == ["second_brain", "ask_person"] and json.loads(daily["tools"]) == []
    assert (daily["keep_days"], daily["keep_hours"], daily["keep_last"], daily["timezone"]) == (2, 0, 5, PARIS)
    assert daily["in_history"] == 1 and daily["announce"] == 1 and daily["kinds"] == ","  # both: ticked "Show in history"
    assert [row(connection, "SELECT in_history FROM automations WHERE id = ?", n) for n in (1, 2, 4, 5, 6)] == [1, 0, 1, 0, 0]  # web: no; discord: yes
    paused = connection.execute("SELECT enabled, next_at FROM automations WHERE id = 6").fetchone()
    assert tuple(paused) == (0, None)
    # the point it had reached: the instant before its next one, so what was due while the server was off is found again
    assert row(connection, "SELECT reached_at FROM automations WHERE id = 1") == "2026-11-14T08:58:00+00:00"
    assert row(connection, "SELECT next_at FROM automations WHERE id = 1") == "2026-11-14T08:59:00+00:00"
    assert row(connection, "SELECT project_id FROM automations WHERE id = 2") == 1 and row(connection, "SELECT local_only FROM automations WHERE id = 5") == 1
    assert row(connection, "SELECT conversation_id FROM automations WHERE id = 4") == "c-shared"


def test_a_hook_becomes_an_event_node_with_escaped_expressions_that_match_what_it_matched(old, tmp_path):
    migrate(old, tmp_path / "backups")
    connection = connect(old)
    invoices = json.loads(row(connection, "SELECT tree FROM automations WHERE id = 7"))
    node = invoices["of"][0]
    assert node["kind"] == "mail.received" and node["filter"] == {
        "subject": [{"pattern": "(?:invoice|facture)", "not": False, "case_sensitive": False},
                    {"pattern": "newsletter", "not": True, "case_sensitive": False}],
        "from": {"pattern": "^billing\\@", "not": False, "case_sensitive": False}, "has_attachment": {"eq": True}}  # re2.escape keeps an @ escaped
    assert tree.normalize(invoices, KINDS.get, __import__("zoneinfo").ZoneInfo(PARIS)) == invoices
    wanted = node["filter"]
    ok = {"subject": "Your FACTURE 12", "from": "billing@acme.example", "has_attachment": 1}
    assert matching.matches(wanted, ok)
    assert not matching.matches(wanted, {**ok, "subject": "Invoice newsletter"}) and not matching.matches(wanted, {**ok, "from": "x billing@acme"})
    assert not matching.matches(wanted, {**ok, "has_attachment": 0})
    push = json.loads(row(connection, "SELECT tree FROM automations WHERE id = 8"))["of"][0]["filter"]
    assert push == {"branch": {"pattern": "^main$", "not": False, "case_sensitive": False},
                    "message": {"pattern": "fix\\ \\(a\\+b\\)\\.$", "not": False, "case_sensitive": False}}  # every character escaped
    assert matching.matches(push, {"branch": "MAIN", "message": "Please fix (a+b)."})
    assert not matching.matches(push, {"branch": "main2", "message": "fix (a+b)."}) and not matching.matches(push, {"branch": "main", "message": "fix (aab)!"})
    assert json.loads(row(connection, "SELECT tree FROM automations WHERE id = 11")) == {
        "type": "or", "of": [{"type": "event", "kind": "github.pull_request", "filter": {}}]}
    first = connection.execute("SELECT * FROM automations WHERE id = 7").fetchone()
    assert (first["catch_up"], first["max_per_hour"], first["max_age_hours"], first["include_self"], first["rerun_interrupted"],
            first["announce"], first["per_run"], first["overlap"], first["in_history"], first["timezone"], first["kinds"]) == (
        "latest", 6, 24, 1, 1, 0, 1, "queue", 1, "", ",mail.received,")  # a hook follows the server's zone, and queues
    assert json.loads(first["tools"]) == ["mail_read"] and first["keep_last"] == 3 and first["project_id"] == 1
    second = connection.execute("SELECT enabled, action, prompt, in_history FROM automations WHERE id = 8").fetchone()
    assert tuple(second) == (0, "notify", "{repo}", 0)


def test_runs_events_memory_and_forums_all_follow_their_automation(old, tmp_path):
    migrate(old, tmp_path / "backups")
    connection = connect(old)
    runs = [tuple(r) for r in connection.execute(
        "SELECT id, automation_id, status, due_at, event_ids, conversation_id, summary, waiting_since FROM automation_runs ORDER BY id")]
    assert runs == [
        (1, 1, "ok", "2026-11-12T22:59:00+00:00", "[]", "c-note", "Done.", None),
        (2, 1, "waiting", "2026-11-13T22:59:00+00:00", "[]", "c-ask", "Question?", "2026-11-13T23:00:00+00:00"),
        (3, 7, "ok", "2026-11-13T09:00:00+00:00", "[1]", "c-hook-ok", "Handled.", None),  # a hook run: id 2 + 1, due at its event's time
        (4, 8, "queued", "2026-11-13T09:45:00+00:00", "[2, 3]", None, "", None),
        (5, 11, "skipped", "2026-11-13T09:30:00+00:00", "[2]", None, "", None)]
    assert row(connection, "SELECT note FROM automation_runs WHERE id = 5") == "Found late"
    assert row(connection, "SELECT late_seconds FROM automation_runs WHERE id = 1") == 5
    assert [tuple(r) for r in connection.execute("SELECT kind, old_id, new_id FROM legacy_ids WHERE kind LIKE '%run' ORDER BY kind, old_id")] == [
        ("hook_run", 1, 3), ("hook_run", 2, 4), ("hook_run", 3, 5), ("schedule_run", 1, 1), ("schedule_run", 2, 2)]
    assert row(connection, "SELECT COUNT(*) FROM trigger_events") == 3  # the events are not touched: their ids are in the runs
    # the scoped memory
    scopes = sorted((r["scope"], r["text"]) for r in connection.execute("SELECT scope, text FROM facts"))
    assert scopes == [("", "A global fact"), ("automation:1", "Likes short notes"), ("automation:11", "Everything is noise"),
                      ("automation:4", "Month end on the 31st"), ("automation:8", "Ignore bot pushes"), ("project:1", "A project fact")]
    assert row(connection, "SELECT scope FROM forgotten") == "automation:8"
    # the Discord forums and threads
    assert sorted(r[0] for r in connection.execute("SELECT key FROM discord_places")) == ["automation:1", "automation:4", "automation:8", "main", "project:1"]
    assert dict(connection.execute("SELECT conversation_id, place_key FROM discord_threads")) == {"c-note": "automation:1", "c-hook-ok": "automation:7"}
    # the memory goes with the automation, as it did with a schedule or a hook
    connection.execute("DELETE FROM automations WHERE id = 8")
    assert row(connection, "SELECT COUNT(*) FROM facts WHERE scope = 'automation:8'") == 0 and row(connection, "SELECT COUNT(*) FROM forgotten") == 0
    assert row(connection, "SELECT COUNT(*) FROM facts") == 5 and row(connection, "SELECT COUNT(*) FROM automation_runs WHERE automation_id = 8") == 0


async def test_the_migrated_database_works_with_the_services(old, tmp_path):
    migrate(old, tmp_path / "backups")
    db = Database(old)
    try:
        conversations = Conversations(db)
        groups = {c["id"]: c["group"] for c in conversations.listing(kind="any", limit=10)}
        assert groups["c-hook-ok"] == {"kind": "automation", "id": 7, "name": "Invoices"}  # grouped under its hook, with its new id
        assert groups["c-ask"] == {"kind": "automation", "id": 1, "name": "Daily note"}  # the conversation of a run that waits
        assert groups["c-shared"] == {"kind": "automation", "id": 4, "name": "Month end"}  # and the one a schedule shared between its runs
        assert set(conversations.memory_scopes("c-hook-ok")) == {"automation"}
        assert conversations.memory_scopes("c-hook-ok")["automation"].scope == "automation:7"
        from clara.automations import Automations

        service = Automations(db, Events(db), PARIS)
        assert [service.describe(item)["id"] for item in service.listing()] == [1, 2, 4, 5, 6, 7, 8, 11]
        waiting = next(item for item in service.listing() if item.id == 1)
        assert service.waiting_run(waiting.id)["id"] == 2 and service.busy(waiting.id)
        assert service.describe(service.get(7))["when_words"] == "a mail where subject matches “(?:invoice|facture)”, subject must not match “newsletter”, from matches “^billing\\@”, has_attachment is yes"
        assert service.wants("mail.received") and service.wants("github.pull_request") and not service.wants("github.push")  # #8 is off
    finally:
        db.close()


def test_a_failed_conversion_leaves_the_database_at_version_31_as_it_was(old, tmp_path):
    connection = connect(old)
    connection.execute("DROP TABLE discord_threads")  # something the conversion needs is missing
    connection.close()
    with pytest.raises(sqlite3.OperationalError):
        migrate(old, tmp_path / "backups")
    connection = connect(old)
    assert schema_version(connection) == 31
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"schedules", "schedule_runs", "triggers", "trigger_runs"} <= tables and "automations" not in tables
    assert row(connection, "SELECT COUNT(*) FROM schedules") == 5 and row(connection, "SELECT COUNT(*) FROM facts WHERE scope = 'hook:2'") == 1
    assert not connection.in_transaction


def test_a_schedule_whose_time_cannot_be_read_is_converted_off_and_nothing_is_lost(tmp_path):
    path = tmp_path / "clara.db"
    connection = old_database(path)
    schedule(connection, 1, "Odd", "08:00")  # not a date and time
    schedule(connection, 2, "Fine", "2026-11-02T08:00")
    connection.close()
    migrate(path, tmp_path / "backups")
    connection = connect(path)
    assert [tuple(r) for r in connection.execute("SELECT name, enabled FROM automations ORDER BY id")] == [("Odd", 0), ("Fine", 1)]


def test_an_empty_installation_and_a_second_start_change_nothing(tmp_path):
    path = tmp_path / "clara.db"
    assert migrate(path, tmp_path / "backups")[:2] == (0, len(migrations()))
    assert migrate(path, tmp_path / "backups") == (len(migrations()), len(migrations()), None)
    connection = connect(path)
    assert row(connection, "SELECT COUNT(*) FROM automations") == 0
    assert row(connection, "SELECT COUNT(*) FROM legacy_schedules") == 0 and row(connection, "SELECT COUNT(*) FROM legacy_trigger_runs") == 0
    newer = tmp_path / "newer.db"
    old_database(newer).execute(f"PRAGMA user_version = {len(migrations()) + 1}")
    with pytest.raises(SchemaTooNew):
        migrate(newer, tmp_path / "backups")
