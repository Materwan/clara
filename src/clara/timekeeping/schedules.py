"""Scheduled prompts: a prompt (with documents) Clara runs by herself at a set time, once or as a routine.

* Once, daily, on some weekdays, or monthly (the last day of a shorter month), at the wall-clock time of the first
  run in its IANA zone, so summer time is followed.
* Every run is a turn in the schedule's own conversation, so runs read one after the other. The run is
  **unattended**: an action the rules say to ask about is stored and pushed to you, and the run goes on without it.
* Each run is a row of `schedule_runs`: when it was due, when it ran, how it ended (ok, failed, missed, skipped,
  interrupted), a summary (the first paragraph of Clara's answer) and how many requests it left waiting. You get a
  `schedule` event (the Discord bot sends it as a DM).
* The next run is set before a run starts, so nothing runs twice. A run due more than an hour ago (the server was off)
  is skipped and you are told; a run due while the previous one still runs is skipped; a run cut by a stop is run
  again at the next start. A run more than CLARA_LATE_ALERT_SECONDS late raises an alert.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..db import Database
from ..events import Events
from ..timeutil import REPEATS, iso, next_local, now_iso, parse_iso, resolve_local, utcnow, zone
from .clock import MomentError, local_text, read_moment, zone_of
from .dueloop import DueLoop

log = logging.getLogger(__name__)

MAX_NAME = 100
MAX_PROMPT = 20_000
MAX_DOCUMENTS_CHARS = 150_000
MAX_SCHEDULES = 100
LATE_SKIP_SECONDS = 3600
MAX_SUMMARY = 700

INSTRUCTIONS = (
    "This is a scheduled task: the person asked you to do it at this time and is most likely not looking. Do the work "
    "with your tools. Nobody can answer a question now, so ask none: take the most reasonable reading. If an action "
    "waits for the person's permission, carry on with the rest and say what is waiting. Your final answer is also sent "
    "to the person as a notification: begin it with a plain summary of what you did and found, in at most three "
    "sentences, without heading or list; details may follow after a blank line."
)


class ScheduleError(ValueError):
    pass


class Stopping(Exception):
    """The server is stopping: the run did not take place and will at the next start."""


@dataclass(frozen=True)
class Schedule:
    id: int
    name: str
    prompt: str
    documents: tuple[dict, ...]
    conversation_id: str | None
    project_id: int | None
    repeat: str
    weekdays: tuple[int, ...]
    local_at: datetime
    timezone: str
    next_at: datetime | None
    enabled: bool
    local_only: bool
    created_at: str


def _schedule(row) -> Schedule:
    return Schedule(row["id"], row["name"], row["prompt"], tuple(json.loads(row["documents"])), row["conversation_id"],
                    row["project_id"], row["repeat"], tuple(int(d) for d in row["weekdays"].split(",") if d),
                    datetime.fromisoformat(row["local_at"]), row["timezone"],
                    parse_iso(row["next_at"]) if row["next_at"] else None, bool(row["enabled"]), bool(row["local_only"]),
                    row["created_at"])


def _document(doc: dict) -> str:
    """A document as the web chat puts it in a message."""
    longest = max([0, *(len(run) for run in re.findall(r"`+", doc["text"]))])
    fence = "`" * max(3, longest + 1)
    name = doc["name"].replace('"', "'").replace("\n", " ")
    return f'<document name="{name}" type="text">\n{fence}\n{doc["text"].rstrip()}\n{fence}\n</document>'


def message_of(schedule: Schedule) -> str:
    parts = [schedule.prompt.strip()] if schedule.prompt.strip() else []
    if schedule.documents:
        names = ", ".join(doc["name"] for doc in schedule.documents)
        parts.append(f"(Attached: {names})")
        parts += [_document(doc) for doc in schedule.documents]
    return "\n\n".join(parts)


def summary_of(reply: str) -> str:
    text = " ".join((reply.strip().split("\n\n", 1)[0]).split())
    if not text:
        return "Clara finished, without writing anything."
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 1] + "…"


Runner = Callable[[Schedule, int], Awaitable[str]]  # (the schedule, the run number) -> Clara's answer
Waiting = Callable[[str], int]  # requests waiting in a conversation


class Schedules(DueLoop):
    name = "schedules"

    def __init__(self, db: Database, events: Events, home_zone: str, clock: Callable[[], datetime] = utcnow):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.home_zone = home_zone
        self.runner: Runner | None = None
        self.waiting: Waiting = lambda conversation_id: 0
        self.late: Callable[[str, float], None] | None = None
        self.late_after = 300.0
        self._running: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    @staticmethod
    def _name(name: str) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ScheduleError("A scheduled task needs a name.")
        if len(name) > MAX_NAME:
            raise ScheduleError(f"A name is at most {MAX_NAME} characters long.")
        return name

    @staticmethod
    def _documents(given: list[dict] | None, kept: tuple[dict, ...] = ()) -> list[dict]:
        old = {doc["name"]: doc for doc in kept}
        found = []
        for item in given or []:
            name = str(item.get("name") or "").strip()[:200]
            if not name:
                raise ScheduleError("A document needs a name.")
            text = item.get("text")
            if text is None:
                if name not in old:
                    raise ScheduleError(f"The document {name} has no text.")
                text = old[name]["text"]
            found.append({"name": name, "text": str(text)})
        if sum(len(doc["text"]) for doc in found) > MAX_DOCUMENTS_CHARS:
            raise ScheduleError(f"The documents are too long: at most {MAX_DOCUMENTS_CHARS:,} characters in all.")
        return found

    @staticmethod
    def _prompt(prompt: str, documents: list[dict]) -> str:
        if len(prompt) > MAX_PROMPT:
            raise ScheduleError(f"A prompt is at most {MAX_PROMPT} characters long.")
        if not prompt.strip() and not documents:
            raise ScheduleError("A scheduled task needs a prompt.")
        return prompt

    def _timing(self, at: str, timezone: str | None, repeat: str, weekdays: list[int]) -> tuple[datetime, str, str, tuple[int, ...]]:
        repeat = (repeat or "").strip().lower()
        if repeat and repeat not in REPEATS:
            raise ScheduleError(f"repeat must be one of: {', '.join(REPEATS)} (or nothing).")
        if any(not isinstance(day, int) or not 0 <= day <= 6 for day in weekdays):
            raise ScheduleError("A weekday is a number from 0 (Monday) to 6 (Sunday).")
        try:
            zone_name, tz = zone_of(timezone, self.home_zone)
            _, local = read_moment(at, tz)
        except MomentError as error:
            raise ScheduleError(str(error)) from None
        days = tuple(sorted(set(weekdays))) or (local.weekday(),)
        return local, zone_name, repeat, days if repeat == "weekly" else ()

    def _first(self, local: datetime, zone_name: str, repeat: str, days: tuple[int, ...]) -> datetime:
        now = self._clock()
        tz = zone(zone_name)
        if not repeat:
            moment = resolve_local(local, tz)
            if moment <= now:
                raise ScheduleError("That moment is already past.")
            return moment
        return next_local(local, repeat, now - timedelta(seconds=1), tz, days)

    def create(self, name: str, prompt: str, at: str, *, documents: list[dict] | None = None, timezone: str | None = None,
               repeat: str = "", weekdays: list[int] | None = None, project_id: int | None = None,
               local_only: bool = False, conversation_id: str | None = None) -> Schedule:
        if int(self.db.value("SELECT COUNT(*) FROM schedules", (), 0)) >= MAX_SCHEDULES:
            raise ScheduleError(f"At most {MAX_SCHEDULES} scheduled tasks: delete some first.")
        docs = self._documents(documents)
        local, zone_name, repeat, days = self._timing(at, timezone, repeat, weekdays or [])
        now = now_iso()
        schedule_id = self.db.insert(
            "INSERT INTO schedules (name, prompt, documents, conversation_id, project_id, repeat, weekdays, local_at,"
            " timezone, next_at, local_only, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._name(name), self._prompt(prompt, docs), json.dumps(docs, ensure_ascii=False), conversation_id,
             project_id, repeat, ",".join(map(str, days)), local.isoformat(timespec="minutes"), zone_name,
             iso(self._first(local, zone_name, repeat, days)), int(local_only), now, now),
        )
        self.wake()
        return self.get(schedule_id)

    def find(self, schedule_id: int) -> Schedule | None:
        row = self.db.one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        return _schedule(row) if row else None

    def get(self, schedule_id: int) -> Schedule:
        schedule = self.find(schedule_id)
        if schedule is None:
            raise ScheduleError(f"No scheduled task {schedule_id}.")
        return schedule

    def listing(self) -> list[Schedule]:
        return [_schedule(row) for row in self.db.all("SELECT * FROM schedules ORDER BY id")]

    def update(self, schedule_id: int, **fields: Any) -> Schedule:
        old = self.get(schedule_id)
        columns: dict[str, Any] = {}
        if "name" in fields:
            columns["name"] = self._name(fields["name"])
        if "prompt" in fields or "documents" in fields:
            docs = list(old.documents) if fields.get("documents") is None else self._documents(fields["documents"], old.documents)
            columns["documents"] = json.dumps(docs, ensure_ascii=False)
            columns["prompt"] = self._prompt(fields["prompt"] if "prompt" in fields else old.prompt, docs)
        if "project_id" in fields:
            columns["project_id"] = fields["project_id"]
        if "local_only" in fields:
            columns["local_only"] = int(bool(fields["local_only"]))
        enabled = bool(fields.get("enabled", old.enabled))
        if {"at", "timezone", "repeat", "weekdays"} & fields.keys() or enabled != old.enabled:
            at = fields.get("at") or old.local_at.isoformat(timespec="minutes")
            local, zone_name, repeat, days = self._timing(
                at, fields.get("timezone") or old.timezone, fields.get("repeat", old.repeat),
                fields["weekdays"] if "weekdays" in fields else list(old.weekdays),
            )
            columns.update(local_at=local.isoformat(timespec="minutes"), timezone=zone_name, repeat=repeat,
                           weekdays=",".join(map(str, days)), enabled=int(enabled),
                           next_at=iso(self._first(local, zone_name, repeat, days)) if enabled else None)
        if columns:
            columns["updated_at"] = now_iso()
            assignments = ", ".join(f"{name} = ?" for name in columns)
            self.db.execute(f"UPDATE schedules SET {assignments} WHERE id = ?", (*columns.values(), schedule_id))
            self.wake()
        return self.get(schedule_id)

    def delete(self, schedule_id: int) -> bool:
        deleted = self.db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,)).rowcount > 0
        if deleted:
            self.wake()
        return deleted

    def runs(self, schedule_id: int, limit: int = 20) -> list[dict]:
        rows = self.db.all("SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?",
                           (schedule_id, limit))
        return [dict(row) for row in rows]

    def describe(self, schedule: Schedule) -> dict:
        last = self.db.one("SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT 1", (schedule.id,))
        tz = zone(schedule.timezone)
        return {
            "id": schedule.id, "name": schedule.name, "prompt": schedule.prompt,
            "documents": [{"name": d["name"], "chars": len(d["text"])} for d in schedule.documents],
            "conversation": schedule.conversation_id, "project": schedule.project_id, "repeat": schedule.repeat,
            "weekdays": list(schedule.weekdays), "local_at": schedule.local_at.isoformat(timespec="minutes"),
            "timezone": schedule.timezone, "next_at": iso(schedule.next_at) if schedule.next_at else None,
            "next_local": local_text(schedule.next_at, tz) if schedule.next_at else None,
            "enabled": schedule.enabled, "local_only": schedule.local_only, "running": schedule.id in self._running,
            "runs": int(self.db.value("SELECT COUNT(*) FROM schedule_runs WHERE schedule_id = ? AND status != 'missed'",
                                      (schedule.id,), 0)),
            "last": dict(last) if last else None,
        }

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    @property
    def firing(self) -> bool:
        return bool(self._running)

    def run_now(self, schedule_id: int) -> Schedule:
        schedule = self.get(schedule_id)
        if schedule.id in self._running:
            raise ScheduleError("It is running already.")
        if self.stopping:
            raise ScheduleError("The server is stopping.")
        self._start(schedule, self._clock())
        return schedule

    def _start(self, schedule: Schedule, due: datetime) -> None:
        run_id = self.db.insert(
            "INSERT INTO schedule_runs (schedule_id, due_at, started_at, status, late_seconds) VALUES (?, ?, ?, 'running', ?)",
            (schedule.id, iso(due), now_iso(), max(0, int((self._clock() - due).total_seconds()))),
        )
        task = asyncio.create_task(self._execute(schedule, run_id, due))
        self._running[schedule.id] = task
        task.add_done_callback(lambda _: self._running.pop(schedule.id, None))

    def next_due(self) -> datetime | None:
        value = self.db.value("SELECT MIN(next_at) FROM schedules WHERE enabled = 1 AND next_at IS NOT NULL")
        return parse_iso(value) if value else None

    def fire_due(self) -> int:
        now = self._clock()
        started = 0
        for row in self.db.all("SELECT * FROM schedules WHERE enabled = 1 AND next_at IS NOT NULL AND next_at <= ?"
                               " ORDER BY next_at", (iso(now),)):
            schedule = _schedule(row)
            assert schedule.next_at is not None
            following = (next_local(schedule.local_at, schedule.repeat, now, zone(schedule.timezone), schedule.weekdays)
                         if schedule.repeat else None)
            self.db.execute("UPDATE schedules SET next_at = ? WHERE id = ?",
                            (iso(following) if following else None, schedule.id))
            late = (now - schedule.next_at).total_seconds()
            if late > LATE_SKIP_SECONDS:
                self._record(schedule, schedule.next_at, "missed",
                             f"The server was not running at {iso(schedule.next_at)}: this run was skipped.")
            elif schedule.id in self._running:
                self._record(schedule, schedule.next_at, "skipped", "The previous run was still going: this one was skipped.")
            else:
                if late > self.late_after and self.late is not None:
                    self.late(f"schedule “{schedule.name}”", late)
                self._start(schedule, schedule.next_at)
                started += 1
        return started

    async def _execute(self, schedule: Schedule, run_id: int, due: datetime) -> None:
        number = int(self.db.value("SELECT COUNT(*) FROM schedule_runs WHERE schedule_id = ?", (schedule.id,), 1))
        try:
            if self.runner is None:
                raise ScheduleError("Clara cannot run it now.")
            status, summary = "ok", summary_of(await self.runner(schedule, number))
        except Stopping:
            self.db.execute("UPDATE schedule_runs SET status = 'interrupted', finished_at = ? WHERE id = ?",
                            (now_iso(), run_id))
            if schedule.repeat or self.find(schedule.id):
                self.db.execute("UPDATE schedules SET next_at = ? WHERE id = ? AND (next_at IS NULL OR next_at > ?)",
                                (iso(due), schedule.id, iso(due)))
            return
        except Exception as error:
            log.warning("schedule %s: the run failed (%s: %s)", schedule.id, type(error).__name__, error)
            status, summary = "failed", f"Clara could not run it: {error or type(error).__name__}"
        current = self.find(schedule.id) or schedule
        waiting = self.waiting(current.conversation_id) if current.conversation_id else 0
        self.db.execute("UPDATE schedule_runs SET status = ?, finished_at = ?, summary = ?, waiting = ? WHERE id = ?",
                        (status, now_iso(), summary, waiting, run_id))
        self._tell(current, status, summary, waiting)

    def _record(self, schedule: Schedule, due: datetime, status: str, summary: str) -> None:
        self.db.execute(
            "INSERT INTO schedule_runs (schedule_id, due_at, finished_at, status, summary) VALUES (?, ?, ?, ?, ?)",
            (schedule.id, iso(due), now_iso(), status, summary),
        )
        self._tell(schedule, status, summary, 0)

    def _tell(self, schedule: Schedule, status: str, summary: str, waiting: int) -> None:
        text = summary
        if waiting:
            text += f"\n{waiting} request{'s' if waiting > 1 else ''} wait for your permission."
        self.events.publish("schedule", f"Scheduled task: {schedule.name}", text,
                            payload={"schedule": schedule.id, "status": status, "waiting": waiting},
                            source="schedules", conversation_id=schedule.conversation_id)

    def recover(self) -> int:
        """At start: runs left `running` by a crash are marked interrupted and their schedule is due again now."""
        rows = self.db.all("SELECT * FROM schedule_runs WHERE status = 'running'")
        for row in rows:
            self.db.execute("UPDATE schedule_runs SET status = 'interrupted', finished_at = ? WHERE id = ?",
                            (now_iso(), row["id"]))
            self.db.execute("UPDATE schedules SET next_at = ? WHERE id = ? AND enabled = 1 AND (next_at IS NULL OR next_at > ?)",
                            (row["due_at"], row["schedule_id"], row["due_at"]))
        return len(rows)

    async def wait_running(self) -> None:
        await asyncio.gather(*self._running.values(), return_exceptions=True)
