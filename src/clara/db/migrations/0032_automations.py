"""Schema version 32: one kind of automation. A schedule (at a time) and a hook (when something happens) become one
**automation**: one table, one list of runs, one trigger written as a tree of AND and OR (automations/tree.py).

This migration is Python because the conversion needs real code (a filter's operators become escaped regular expressions, a
schedule's repeat becomes a cron expression). It runs in the one transaction that also sets the version, after the automatic
backup: if anything here raises, nothing it did stays.

What becomes of each old row
----------------------------
* **Ids.** A schedule keeps its id; a hook gets `offset + its id`, where `offset` is the highest schedule id, so the two never
  collide. The same for runs (`offset` is then the highest schedule run id). The table `legacy_ids` keeps every (kind, old id,
  new id): an old audit line that names "hook 5" can be read through it.
* **A schedule** becomes an automation whose trigger is a `once` node (no repeat), or `cron` node(s): daily `M H * * *`, weekly
  `M H * * <days>`, monthly `M H D * *`; on the 29th, 30th and 31st the shorter months get `L` (the last day), as the schedule
  did. The point it had reached is kept (`reached_at` is the instant before its `next_at`), so a run that was due while
  the server is off is found again from the cron expression, and `missed` becomes `catch_up`. Its overlap, packs, model,
  per-run conversation, attempts, retention and mode are kept; a run cut by a stop is run again (`rerun_interrupted`), as a
  schedule did.
* **A hook** becomes an automation whose trigger is one `event` node with its filter converted: `contains x` is `x`, `starts_with
  x` is `^x`, `ends_with x` is `x$`, `eq x` is `^x$`, `not_contains x` is `x` with « must not match », every value escaped
  (`re2.escape`) and several values joined with `|`, never case-sensitive (the old comparison folded case), and one condition
  for each operator of a field, all of which must hold, as before. Its tools, catch-up, pace and limits are kept; its runs
  have a conversation each (`per_run`), queue behind one another (`overlap` queue), and are not repeated after a stop that
  they had begun. A hook has no time zone: it follows the server's (`''`).
* **Where the result goes** is gone: `deliver` `discord` and `both` tick « Show in history » (`in_history`); the four values
  all become the short notification to every client.
* **Runs** keep their rows (a schedule run's `due_at`, a hook run's events and its newest event's time as `due_at`), their
  conversation and their status. **Events** (`trigger_events`) are not touched: their ids are in the runs.
* **The memory of an automation** (`facts.scope`, `forgotten.scope`: `schedule:3`, `hook:2`) and **its Discord forum**
  (`discord_places.key`, `discord_threads.place_key`) are renamed `automation:<new id>`. The rule that deletes an
  automation's memory with it is made again for the new table.
* The old tables are kept as plain copies, `legacy_schedules`, `legacy_schedule_runs`, `legacy_triggers`,
  `legacy_trigger_runs`: nothing reads them. A later migration may drop them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import re2

OLD = ("schedules", "schedule_runs", "triggers", "trigger_runs")
FACTS = ("facts", "forgotten")


def _create(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE automations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT NOT NULL,
            enabled           INTEGER NOT NULL DEFAULT 1,
            tree              TEXT NOT NULL,   -- JSON: the trigger, a tree of AND and OR (automations/tree.py)
            kinds             TEXT NOT NULL DEFAULT ',',   -- the kinds of event it waits for: ',mail.received,github.push,'
            timezone          TEXT NOT NULL DEFAULT '' CHECK (timezone NOT LIKE '+%' AND timezone NOT LIKE '-%'),  -- '': the server's
            action            TEXT NOT NULL DEFAULT 'run' CHECK (action IN ('run', 'notify')),
            prompt            TEXT NOT NULL DEFAULT '',
            documents         TEXT NOT NULL DEFAULT '[]',
            packs             TEXT NOT NULL DEFAULT '[]',
            tools             TEXT NOT NULL DEFAULT '[]',
            conversation_id   TEXT REFERENCES conversations (id) ON DELETE SET NULL,
            project_id        INTEGER REFERENCES projects (id) ON DELETE SET NULL,
            model             TEXT NOT NULL DEFAULT '',
            local_only        INTEGER NOT NULL DEFAULT 0,
            per_run           INTEGER NOT NULL DEFAULT 0,
            catch_up          TEXT NOT NULL DEFAULT 'skip' CHECK (catch_up IN ('all', 'latest', 'digest', 'skip')),
            overlap           TEXT NOT NULL DEFAULT 'skip' CHECK (overlap IN ('skip', 'replace', 'queue')),
            max_per_hour      INTEGER NOT NULL DEFAULT 20,
            max_age_hours     INTEGER NOT NULL DEFAULT 0,
            include_self      INTEGER NOT NULL DEFAULT 0,
            rerun_interrupted INTEGER NOT NULL DEFAULT 0,
            attempts          INTEGER NOT NULL DEFAULT 3,
            announce          INTEGER NOT NULL DEFAULT 1,
            in_history        INTEGER NOT NULL DEFAULT 0,
            mode              TEXT REFERENCES modes (name) ON DELETE SET NULL,
            keep_days         INTEGER,
            keep_hours        INTEGER,
            keep_last         INTEGER,
            reached_at        TEXT,   -- the instant up to which its cron and date nodes have been looked at
            next_at           TEXT,   -- the next instant of those nodes (what the loop sleeps until)
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )"""
    )
    connection.execute("CREATE INDEX automations_next ON automations (enabled, next_at)")
    connection.execute("CREATE INDEX automations_conversation ON automations (conversation_id)")
    connection.execute(
        """CREATE TABLE automation_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id   INTEGER NOT NULL REFERENCES automations (id) ON DELETE CASCADE,
            due_at          TEXT NOT NULL,             -- an instant's moment; an event's time
            event_ids       TEXT NOT NULL DEFAULT '[]',   -- JSON list of trigger_events ids (empty: an instant started it)
            status          TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting', 'ok', 'closed', 'failed',
                                                            'missed', 'skipped', 'interrupted', 'expired')),
            note            TEXT NOT NULL DEFAULT '',  -- why it was skipped or expired
            summary         TEXT NOT NULL DEFAULT '',
            waiting         INTEGER NOT NULL DEFAULT 0,  -- approval requests the run left waiting
            late_seconds    INTEGER NOT NULL DEFAULT 0,
            queued_at       TEXT NOT NULL,
            started_at      TEXT,
            finished_at     TEXT,
            waiting_since   TEXT,
            reminded_at     TEXT,
            conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL
        )"""
    )
    connection.execute("CREATE INDEX automation_runs_automation ON automation_runs (automation_id, id)")
    connection.execute("CREATE INDEX automation_runs_status ON automation_runs (status)")
    connection.execute("CREATE INDEX automation_runs_conversation ON automation_runs (conversation_id)")
    connection.execute(
        "CREATE TABLE legacy_ids (kind TEXT NOT NULL, old_id INTEGER NOT NULL, new_id INTEGER NOT NULL,"
        " PRIMARY KEY (kind, old_id))"
    )
    # the memory goes with what it belongs to, whichever way the automation is deleted (the API, a deleted Discord forum)
    connection.execute(
        """CREATE TRIGGER facts_of_deleted_automation AFTER DELETE ON automations BEGIN
            DELETE FROM facts WHERE scope = 'automation:' || old.id;
            DELETE FROM forgotten WHERE scope = 'automation:' || old.id;
        END"""
    )


# ----------------------------------------------------------------------
# Reading the old rows
# ----------------------------------------------------------------------
def _cron(minute: int, hour: int, day: str = "*", months: str = "*", weekdays: str = "*") -> dict:
    return {"type": "cron", "expr": f"{minute} {hour} {day} {months} {weekdays}", "as": "instant"}


UNREADABLE = "2000-01-01T00:00"  # a date long past: a schedule whose time cannot be read becomes one that never fires, and is off


def schedule_tree(row: sqlite3.Row) -> dict:
    """The trigger a schedule was, as a tree."""
    try:
        local = datetime.fromisoformat(row["local_at"])
    except ValueError:
        return {"type": "or", "of": [{"type": "once", "at": UNREADABLE}]}
    if not row["repeat"]:
        return {"type": "or", "of": [{"type": "once", "at": local.isoformat(timespec="minutes")}]}
    if row["repeat"] == "daily":
        nodes = [_cron(local.minute, local.hour)]
    elif row["repeat"] == "weekly":
        days = [int(day) for day in row["weekdays"].split(",") if day] or [local.weekday()]
        nodes = [_cron(local.minute, local.hour, weekdays=",".join(str((day + 1) % 7) for day in sorted(set(days))))]
    else:  # monthly: the day it began on, and on a month too short for it the last day
        day = local.day
        if day <= 28:
            nodes = [_cron(local.minute, local.hour, day=str(day))]
        elif day == 29:
            nodes = [_cron(local.minute, local.hour, "29", "1,3-12"), _cron(local.minute, local.hour, "L", "2")]
        elif day == 30:
            nodes = [_cron(local.minute, local.hour, "30", "1,3-12"), _cron(local.minute, local.hour, "L", "2")]
        else:
            nodes = [
                _cron(local.minute, local.hour, "31", "1,3,5,7,8,10,12"),
                _cron(local.minute, local.hour, "L", "2,4,6,9,11"),
            ]
    return {"type": "or", "of": nodes}


def _alternatives(values: list) -> str:
    escaped = [re2.escape(str(value)) for value in values]
    return escaped[0] if len(escaped) == 1 else "(?:" + "|".join(escaped) + ")"


def convert_filter(old: dict) -> dict:
    """A hook's filter, `{field: {operator: [values]}}`, as conditions `{field: {pattern, not, case_sensitive}}`."""
    found: dict = {}
    for name, spec in old.items():
        if isinstance(spec.get("eq"), bool):
            found[name] = {"eq": spec["eq"]}
            continue
        conditions = []
        for operator, values in spec.items():
            text = _alternatives(values if isinstance(values, list) else [values])
            pattern = {
                "contains": text,
                "not_contains": text,
                "starts_with": f"^{text}",
                "ends_with": f"{text}$",
                "eq": f"^{text}$",
            }[operator]
            conditions.append({"pattern": pattern, "not": operator == "not_contains", "case_sensitive": False})
        found[name] = conditions[0] if len(conditions) == 1 else conditions
    return found


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ----------------------------------------------------------------------
# The migration
# ----------------------------------------------------------------------
def migrate(connection: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    schedules = connection.execute("SELECT * FROM schedules ORDER BY id").fetchall()
    schedule_runs = connection.execute("SELECT * FROM schedule_runs ORDER BY id").fetchall()
    hooks = connection.execute("SELECT * FROM triggers ORDER BY id").fetchall()
    hook_runs = connection.execute("SELECT * FROM trigger_runs ORDER BY id").fetchall()
    offset = max((row["id"] for row in schedules), default=0)
    run_offset = max((row["id"] for row in schedule_runs), default=0)

    # the old tables go first (their indexes and the rules that deleted memory with them), kept as plain copies
    for kind in ("trigger", "index"):
        for row in connection.execute(
            f"SELECT name FROM sqlite_master WHERE type = '{kind}' AND tbl_name IN ({', '.join('?' * len(OLD))})"
            " AND sql IS NOT NULL",
            OLD,
        ).fetchall():
            connection.execute(f"DROP {kind} {row['name']}")
    for table in OLD:
        connection.execute(f"CREATE TABLE legacy_{table} AS SELECT * FROM {table}")
    for table in ("schedule_runs", "schedules", "trigger_runs", "triggers"):  # the runs before what they point at
        connection.execute(f"DROP TABLE {table}")
    _create(connection)

    names: set[str] = set()
    columns = (
        "id, name, enabled, tree, kinds, timezone, action, prompt, documents, packs, tools, conversation_id, project_id,"
        " model, local_only, per_run, catch_up, overlap, max_per_hour, max_age_hours, include_self, rerun_interrupted,"
        " attempts, announce, in_history, mode, keep_days, keep_hours, keep_last, reached_at, next_at, created_at, updated_at"
    )
    insert = f"INSERT INTO automations ({columns}) VALUES ({', '.join('?' * 33)})"
    for row in schedules:
        names.add(row["name"])
        tree = schedule_tree(row)
        readable = tree["of"][0].get("at") != UNREADABLE
        enabled = int(bool(row["enabled"]) and readable)
        next_at = row["next_at"] if enabled else None
        reached = (
            (datetime.fromisoformat(next_at) - timedelta(minutes=1)).astimezone(UTC).isoformat(timespec="seconds")
            if next_at
            else now
        )
        shown = int(bool(row["in_history"]) or row["deliver"] in ("discord", "both"))
        connection.execute(
            insert,
            (
                row["id"], row["name"], enabled, json.dumps(tree), ",", row["timezone"], "run",
                row["prompt"], row["documents"], row["packs"], "[]", row["conversation_id"], row["project_id"],
                row["model"], row["local_only"], row["per_run"], row["missed"], row["overlap"], 20, 0, 0, 1,
                row["attempts"], 1, shown, row["mode"], row["keep_days"], row["keep_hours"], row["keep_last"], reached,
                next_at, row["created_at"], row["updated_at"],
            ),
        )  # fmt: skip
        connection.execute("INSERT INTO legacy_ids VALUES ('schedule', ?, ?)", (row["id"], row["id"]))
    for row in hooks:
        name = row["name"]
        if name in names:
            name = f"{name} (hook)"
        while name in names:
            name += " 2"
        names.add(name)
        try:
            tree_filter, enabled = convert_filter(json.loads(row["filter"])), row["enabled"]
        except (
            ValueError,
            KeyError,
            AttributeError,
            TypeError,
        ):  # not a filter this code reads: off, for you to look at
            tree_filter, enabled = {}, 0
        tree = {"type": "or", "of": [{"type": "event", "kind": row["event_kind"], "filter": tree_filter}]}
        new_id = offset + row["id"]
        shown = int(bool(row["in_history"]) or row["deliver"] in ("discord", "both"))
        connection.execute(
            insert,
            (
                new_id, name, enabled, json.dumps(tree), f",{row['event_kind']},", "", row["action"], row["prompt"], "[]",
                "[]", row["tools"], None, row["project_id"], row["model"], row["local_only"], 1, row["catch_up"], "queue",
                row["max_per_hour"], row["max_age_hours"], row["include_self"], row["rerun_interrupted"], row["attempts"],
                row["announce"], shown, row["mode"], row["keep_days"], row["keep_hours"], row["keep_last"], now, None,
                row["created_at"], row["updated_at"],
            ),
        )  # fmt: skip
        connection.execute("INSERT INTO legacy_ids VALUES ('hook', ?, ?)", (row["id"], new_id))

    runs = (
        "INSERT INTO automation_runs (id, automation_id, due_at, event_ids, status, note, summary, waiting, late_seconds,"
        " queued_at, started_at, finished_at, waiting_since, reminded_at, conversation_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    for row in schedule_runs:
        connection.execute(
            runs,
            (
                row["id"], row["schedule_id"], row["due_at"], "[]", row["status"], "", row["summary"], row["waiting"],
                row["late_seconds"], row["started_at"] or row["finished_at"] or row["due_at"], row["started_at"],
                row["finished_at"], row["waiting_since"], row["reminded_at"], row["conversation_id"],
            ),
        )  # fmt: skip
        connection.execute("INSERT INTO legacy_ids VALUES ('schedule_run', ?, ?)", (row["id"], row["id"]))
    for row in hook_runs:
        newest = connection.execute(
            "SELECT MAX(occurred_at) FROM trigger_events WHERE id IN (SELECT value FROM json_each(?))",
            (row["event_ids"],),
        ).fetchone()[0]
        connection.execute(
            runs,
            (
                run_offset + row["id"], offset + row["trigger_id"], newest or row["queued_at"], row["event_ids"],
                row["status"], row["note"], row["summary"], 0, 0, row["queued_at"], row["started_at"],
                row["finished_at"], None, None, row["conversation_id"],
            ),
        )  # fmt: skip
        connection.execute("INSERT INTO legacy_ids VALUES ('hook_run', ?, ?)", (row["id"], run_offset + row["id"]))

    # what pointed at a schedule or a hook by its name: the memory, and the Discord forum and its threads
    moved = [("schedule", row["id"], row["id"]) for row in schedules] + [
        ("hook", row["id"], offset + row["id"]) for row in hooks
    ]
    for kind, old, new in moved:
        for table in FACTS:
            connection.execute(f"UPDATE {table} SET scope = ? WHERE scope = ?", (f"automation:{new}", f"{kind}:{old}"))
        connection.execute("UPDATE discord_places SET key = ? WHERE key = ?", (f"automation:{new}", f"{kind}:{old}"))
        connection.execute(
            "UPDATE discord_threads SET place_key = ? WHERE place_key = ?", (f"automation:{new}", f"{kind}:{old}")
        )

    # nothing may be lost on the way
    for table, expected in (
        ("automations", len(schedules) + len(hooks)),
        ("automation_runs", len(schedule_runs) + len(hook_runs)),
    ):
        if _count(connection, table) != expected:
            raise RuntimeError(f"the conversion of the schedules and hooks lost rows of {table}")
