"""Scheduled prompts: a prompt (with documents and tool packs) Clara runs by herself at a set time, once or as a routine.

* Once, daily, on some weekdays, or monthly (the last day of a shorter month), at the wall-clock time of the first
  run in its IANA zone, so summer time is followed.
* A run is a turn in a conversation: the schedule's own, shared by its runs, or (`per_run`) a new one each time. The run
  is **unattended**: an action the rules say to ask about is stored and pushed to you, and the run goes on without it.
  The schedule's **packs** (tools.py in packs/) say which special tools it has, and can have a model of its own.
* A run can **ask you something and wait** (`ask_person`): it then has the status `waiting`, your answer arrives in the
  run's conversation like in any chat (with the same tools), and `finish_run` ends it. A waiting run is reminded once
  (CLARA_WAITING_REMIND_AT) and closed from what it has when `overlap` says so.
* Each run is a row of `schedule_runs`: when it was due, when it ran, how it ended (ok, closed, failed, missed,
  skipped, interrupted), a summary and how many requests it left waiting. You get a `schedule` event (the Discord bot
  posts it in #notification); a run that waited ends with an `ask` event instead.
* **Missed** (the server was off at the due time, more than an hour ago): `missed` is `skip` (recorded, not run),
  `latest` (the latest missed run takes place now, the others are recorded) or `all` (they take place one after the
  other). **Overlap** (a run comes due while the previous one still runs or waits): `overlap` is `skip` (recorded, not
  run), `replace` (a waiting run is closed from what it has, then the new one starts; a running one is waited for) or
  `queue` (the new one waits until the old one ends).
* **Retry** (`attempts`, 3 by default): a run the model could not answer (busy, unreachable: 429, 5xx, a timeout) is
  tried again after 1 and 5 minutes. Only when it had done nothing yet: a run that already used a tool or wrote text is
  not run again, so its actions are not repeated.
* The next run is set before a run starts, so nothing runs twice. A run cut by a stop is run again at the next start. A
  run more than CLARA_LATE_ALERT_SECONDS late raises an alert (not a catch-up).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from ..db import Database
from ..events import Events, delivery
from ..memory.retention import KEEP_FIELDS, Own, RetentionError, check_own
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
MAX_BACKLOG = 31  # missed runs looked at, and queued runs kept, per schedule
REMIND_AFTER = timedelta(hours=3)
MISSED = ("skip", "latest", "all")
OVERLAPS = ("skip", "replace", "queue")
MAX_ATTEMPTS = 3
RETRY_WAITS = (60, 300)  # seconds before the 2nd and the 3rd attempt
ASK_TEXT = 2_000

INSTRUCTIONS = (
    "This is a scheduled task: the person asked you to do it at this time and is most likely not looking. Do the work "
    "with your tools. Nobody can answer a question now, so ask none: take the most reasonable reading. If an action "
    "waits for the person's permission, carry on with the rest and say what is waiting. Your final answer is also sent "
    "to the person as a notification: begin it with a plain summary of what you did and found, in at most three "
    "sentences, without heading or list; details may follow after a blank line."
)
INSTRUCTIONS_ASKING = (
    "This is a scheduled task: the person asked you to do it at this time. Do the work with your tools. When you need "
    "something from the person, ask it once with ask_person (one message), then end your turn: the run waits, and the "
    "answer arrives in this conversation, where you go on. End the run with finish_run. Apart from that question, ask "
    "nothing: take the most reasonable reading. If an action waits for the person's permission, carry on with the rest."
)
INSTRUCTIONS_TALKING = (
    "This is a scheduled task: the person asked you to do it at this time and will read your answer on Discord. Do the "
    "work with your tools. If an action waits for the person's permission, carry on with the rest and say what is "
    "waiting. Your final answer is sent to the person on Discord as a message they can answer, and this conversation "
    "goes on there with their reply: write it to them, what you did and found, and when something calls for a decision "
    "or an action of yours (a reminder, a task), end with one short proposal or question."
)
CLOSING = (
    "[No answer received] The person did not answer, and this task is due again. Finish it now with what you have: do "
    "what the task says to do when nobody answers (mark what is not confirmed as such, change nothing that needs a "
    "confirmation), then call finish_run with a summary."
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
    packs: tuple[str, ...] = ("web",)
    per_run: bool = False
    missed: str = "skip"
    overlap: str = "skip"
    model: str = ""
    in_history: bool = False  # its conversations are listed in the web site's history
    attempts: int = MAX_ATTEMPTS  # tries of a run when the model is busy or unreachable (1: no retry)
    deliver: str = "notice"  # where a run's result goes (events/delivery.py)
    auto_level: int | None = None  # its auto level (gate/levels.py); None: its project's, else the unattended default
    keep_days: int | None = (
        None  # its own limits on the conversations its runs make (memory/retention.py); None: follow
    )
    keep_hours: int | None = None  # the global setting (days and hours are both set, or both None)
    keep_last: int | None = None

    @property
    def asks(self) -> bool:
        return "ask_person" in self.packs


@dataclass(frozen=True)
class Run:
    """The run being made: its row, and the moment it was due."""

    id: int
    due: datetime


def _schedule(row) -> Schedule:
    return Schedule(
        row["id"],
        row["name"],
        row["prompt"],
        tuple(json.loads(row["documents"])),
        row["conversation_id"],
        row["project_id"],
        row["repeat"],
        tuple(int(d) for d in row["weekdays"].split(",") if d),
        datetime.fromisoformat(row["local_at"]),
        row["timezone"],
        parse_iso(row["next_at"]) if row["next_at"] else None,
        bool(row["enabled"]),
        bool(row["local_only"]),
        row["created_at"],
        tuple(json.loads(row["packs"] or "[]")),
        bool(row["per_run"]),
        row["missed"],
        row["overlap"],
        row["model"],
        bool(row["in_history"]),
        row["attempts"],
        row["deliver"],
        row["auto_level"],
        row["keep_days"],
        row["keep_hours"],
        row["keep_last"],
    )


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


def instructions_of(schedule: Schedule) -> str:
    if schedule.asks:
        return INSTRUCTIONS_ASKING
    return INSTRUCTIONS_TALKING if schedule.deliver in ("discord", "both") else INSTRUCTIONS


def summary_of(reply: str) -> str:
    text = " ".join((reply.strip().split("\n\n", 1)[0]).split())
    if not text:
        return "Clara finished, without writing anything."
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 1] + "…"


Runner = Callable[[Schedule, int, Run], Awaitable[str]]  # (the schedule, the run number, the run) -> Clara's answer
Closer = Callable[[Schedule, Any], Awaitable[None]]  # (the schedule, the waiting run's row): a turn that finishes it
Waiting = Callable[[str], int]  # requests waiting in a conversation


class Schedules(DueLoop):
    name = "schedules"

    def __init__(self, db: Database, events: Events, home_zone: str, clock: Callable[[], datetime] = utcnow):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.home_zone = home_zone
        self.runner: Runner | None = None
        self.closer: Closer | None = None
        self.waiting: Waiting = lambda conversation_id: 0
        self.late: Callable[[str, float], None] | None = None
        self.late_after = 300.0
        self.remind_at = time(9, 0)
        self.check_packs: Callable[[list[str]], list[str]] = lambda ids: list(ids)
        self._running: dict[int, asyncio.Task] = {}
        self._closing: dict[int, asyncio.Task] = {}

    def local_now(self, zone_name: str) -> datetime:
        """The clock's moment in a zone (the services' own, so tests and the loop agree)."""
        return self._clock().astimezone(zone(zone_name))

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

    def _packs(self, packs: list[str] | None) -> str:
        try:
            return json.dumps(self.check_packs(list(packs or [])))
        except ValueError as error:
            raise ScheduleError(str(error)) from None

    @staticmethod
    def _policy(value: str, allowed: tuple[str, ...], what: str) -> str:
        if value not in allowed:
            raise ScheduleError(f"{what} must be one of: {', '.join(allowed)}.")
        return value

    @staticmethod
    def _deliver(value: Any, local_only: bool) -> str:
        try:
            return delivery.check(value, local_only=local_only)
        except ValueError as error:
            raise ScheduleError(str(error)) from None

    @staticmethod
    def _keep(days: Any, hours: Any, last: Any) -> Own:
        try:
            return check_own(days, hours, last)
        except RetentionError as error:
            raise ScheduleError(str(error)) from None

    @staticmethod
    def _attempts(value: int) -> int:
        if not isinstance(value, int) or not 1 <= value <= MAX_ATTEMPTS:
            raise ScheduleError(f"attempts must be a whole number from 1 to {MAX_ATTEMPTS} (1: no retry).")
        return value

    def _timing(
        self, at: str, timezone: str | None, repeat: str, weekdays: list[int]
    ) -> tuple[datetime, str, str, tuple[int, ...]]:
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

    def first_run(
        self, at: str, timezone: str | None = None, repeat: str = "", weekdays: list[int] | None = None
    ) -> datetime:
        """When a schedule made with these would first run; ScheduleError when `create` would not accept them."""
        local, zone_name, repeat, days = self._timing(at, timezone, repeat, weekdays or [])
        return self._first(local, zone_name, repeat, days)

    def create(
        self,
        name: str,
        prompt: str,
        at: str,
        *,
        documents: list[dict] | None = None,
        timezone: str | None = None,
        repeat: str = "",
        weekdays: list[int] | None = None,
        project_id: int | None = None,
        local_only: bool = False,
        conversation_id: str | None = None,
        packs: list[str] | None = None,
        per_run: bool = False,
        missed: str = "skip",
        overlap: str = "skip",
        model: str = "",
        attempts: int = MAX_ATTEMPTS,
        deliver: str = "notice",
        keep_days: int | None = None,
        keep_hours: int | None = None,
        keep_last: int | None = None,
    ) -> Schedule:
        if int(self.db.value("SELECT COUNT(*) FROM schedules", (), 0)) >= MAX_SCHEDULES:
            raise ScheduleError(f"At most {MAX_SCHEDULES} scheduled tasks: delete some first.")
        docs = self._documents(documents)
        local, zone_name, repeat, days = self._timing(at, timezone, repeat, weekdays or [])
        keep = self._keep(keep_days, keep_hours, keep_last)
        now = now_iso()
        schedule_id = self.db.insert(
            "INSERT INTO schedules (name, prompt, documents, conversation_id, project_id, repeat, weekdays, local_at,"
            " timezone, next_at, local_only, packs, per_run, missed, overlap, model, attempts, deliver, in_history,"
            " keep_days, keep_hours, keep_last, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._name(name),
                self._prompt(prompt, docs),
                json.dumps(docs, ensure_ascii=False),
                conversation_id,
                project_id,
                repeat,
                ",".join(map(str, days)),
                local.isoformat(timespec="minutes"),
                zone_name,
                iso(self._first(local, zone_name, repeat, days)),
                int(local_only),
                self._packs(packs),
                int(per_run),
                self._policy(missed, MISSED, "missed"),
                self._policy(overlap, OVERLAPS, "overlap"),
                model.strip(),
                self._attempts(attempts),
                self._deliver(deliver, local_only),
                int(delivery.forum(deliver)),
                keep.days,
                keep.hours,
                keep.last,
                now,
                now,
            ),
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
            docs = (
                list(old.documents)
                if fields.get("documents") is None
                else self._documents(fields["documents"], old.documents)
            )
            columns["documents"] = json.dumps(docs, ensure_ascii=False)
            columns["prompt"] = self._prompt(fields["prompt"] if "prompt" in fields else old.prompt, docs)
        if "project_id" in fields:
            columns["project_id"] = fields["project_id"]
        if "local_only" in fields:
            columns["local_only"] = int(bool(fields["local_only"]))
        if "packs" in fields:
            columns["packs"] = self._packs(fields["packs"])
        if "per_run" in fields:
            columns["per_run"] = int(bool(fields["per_run"]))
        if "missed" in fields:
            columns["missed"] = self._policy(fields["missed"], MISSED, "missed")
        if "overlap" in fields:
            columns["overlap"] = self._policy(fields["overlap"], OVERLAPS, "overlap")
        if "model" in fields:
            columns["model"] = str(fields["model"] or "").strip()
        if "attempts" in fields:
            columns["attempts"] = self._attempts(fields["attempts"])
        if "in_history" in fields:
            columns["in_history"] = int(bool(fields["in_history"]))
        if any(key in fields for key in KEEP_FIELDS):
            keep = self._keep(
                fields.get("keep_days", old.keep_days),
                fields.get("keep_hours", old.keep_hours),
                fields.get("keep_last", old.keep_last),
            )
            columns.update(keep_days=keep.days, keep_hours=keep.hours, keep_last=keep.last)
        if "deliver" in fields or "local_only" in fields:
            columns["deliver"] = self._deliver(
                fields.get("deliver", old.deliver), bool(fields.get("local_only", old.local_only))
            )
            if delivery.forum(columns["deliver"]) and not delivery.forum(old.deliver) and "in_history" not in fields:
                columns["in_history"] = 1  # its runs live in a forum thread, made from "Show in history"
        enabled = bool(fields.get("enabled", old.enabled))
        if {"at", "timezone", "repeat", "weekdays"} & fields.keys() or enabled != old.enabled:
            at = fields.get("at") or old.local_at.isoformat(timespec="minutes")
            local, zone_name, repeat, days = self._timing(
                at,
                fields.get("timezone") or old.timezone,
                fields.get("repeat", old.repeat),
                fields["weekdays"] if "weekdays" in fields else list(old.weekdays),
            )
            columns.update(
                local_at=local.isoformat(timespec="minutes"),
                timezone=zone_name,
                repeat=repeat,
                weekdays=",".join(map(str, days)),
                enabled=int(enabled),
                next_at=iso(self._first(local, zone_name, repeat, days)) if enabled else None,
            )
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
        rows = self.db.all(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?", (schedule_id, limit)
        )
        return [dict(row) for row in rows]

    def waiting_run(self, schedule_id: int):
        return self.db.one(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? AND status = 'waiting' ORDER BY id DESC LIMIT 1",
            (schedule_id,),
        )

    def describe(self, schedule: Schedule) -> dict:
        last = self.db.one(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? AND status != 'queued' ORDER BY id DESC LIMIT 1",
            (schedule.id,),
        )
        waiting = self.waiting_run(schedule.id)
        tz = zone(schedule.timezone)
        return {
            "id": schedule.id,
            "name": schedule.name,
            "prompt": schedule.prompt,
            "documents": [{"name": d["name"], "chars": len(d["text"])} for d in schedule.documents],
            "conversation": schedule.conversation_id,
            "project": schedule.project_id,
            "repeat": schedule.repeat,
            "weekdays": list(schedule.weekdays),
            "local_at": schedule.local_at.isoformat(timespec="minutes"),
            "timezone": schedule.timezone,
            "next_at": iso(schedule.next_at) if schedule.next_at else None,
            "next_local": local_text(schedule.next_at, tz) if schedule.next_at else None,
            "enabled": schedule.enabled,
            "local_only": schedule.local_only,
            "running": schedule.id in self._running,
            "packs": list(schedule.packs),
            "per_run": schedule.per_run,
            "missed": schedule.missed,
            "overlap": schedule.overlap,
            "model": schedule.model,
            "in_history": schedule.in_history,
            "attempts": schedule.attempts,
            "deliver": schedule.deliver,
            "auto_level": schedule.auto_level,
            "keep_days": schedule.keep_days,
            "keep_hours": schedule.keep_hours,
            "keep_last": schedule.keep_last,
            "waiting_run": {
                "id": waiting["id"],
                "conversation": waiting["conversation_id"],
                "since": waiting["waiting_since"],
            }
            if waiting
            else None,
            "queued": int(
                self.db.value(
                    "SELECT COUNT(*) FROM schedule_runs WHERE schedule_id = ? AND status = 'queued'", (schedule.id,), 0
                )
            ),
            "runs": int(
                self.db.value(
                    "SELECT COUNT(*) FROM schedule_runs WHERE schedule_id = ?"
                    " AND status NOT IN ('missed', 'queued', 'skipped')",
                    (schedule.id,),
                    0,
                )
            ),
            "last": dict(last) if last else None,
        }

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    @property
    def firing(self) -> bool:
        return bool(self._running) or bool(self._closing)

    def busy(self, schedule_id: int) -> bool:
        """A run of this schedule is going, being closed, or waiting for an answer."""
        return schedule_id in self._running or schedule_id in self._closing or self.waiting_run(schedule_id) is not None

    def run_now(self, schedule_id: int) -> Schedule:
        schedule = self.get(schedule_id)
        if schedule.id in self._running:
            raise ScheduleError("It is running already.")
        if self.waiting_run(schedule.id) is not None or schedule.id in self._closing:
            raise ScheduleError("It is waiting for your answer: answer it, or close it first.")
        if self.stopping:
            raise ScheduleError("The server is stopping.")
        self._start(schedule, self._clock())
        return schedule

    def _start(self, schedule: Schedule, due: datetime, run_id: int | None = None, late: float | None = None) -> None:
        late = max(0, int((self._clock() - due).total_seconds())) if late is None else int(late)
        if run_id is None:
            run_id = self.db.insert(
                "INSERT INTO schedule_runs (schedule_id, due_at, started_at, status, late_seconds) VALUES (?, ?, ?, 'running', ?)",
                (schedule.id, iso(due), now_iso(), late),
            )
        else:
            self.db.execute(
                "UPDATE schedule_runs SET status = 'running', started_at = ?, late_seconds = ? WHERE id = ?",
                (now_iso(), late, run_id),
            )
        task = asyncio.create_task(self._execute(schedule, run_id, due))
        self._running[schedule.id] = task
        task.add_done_callback(lambda _: (self._running.pop(schedule.id, None), self.wake()))

    def _queued(self) -> list:
        return self.db.all(
            "SELECT r.* FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id"
            " WHERE r.status = 'queued' AND s.enabled = 1 ORDER BY r.due_at, r.id"
        )

    def _remind_instant(self, since: datetime, tz) -> datetime:
        """The next CLARA_WAITING_REMIND_AT after three hours of waiting."""
        after = (since + REMIND_AFTER).astimezone(tz)
        candidate = datetime.combine(after.date(), self.remind_at)
        if resolve_local(candidate, tz) <= since + REMIND_AFTER:
            candidate += timedelta(days=1)
        return resolve_local(candidate, tz)

    def next_due(self) -> datetime | None:
        value = self.db.value("SELECT MIN(next_at) FROM schedules WHERE enabled = 1 AND next_at IS NOT NULL")
        soon = [parse_iso(value)] if value else []
        for run in self._queued():
            if not self.busy(run["schedule_id"]):
                soon.append(self._clock())
        for run in self.db.all(
            "SELECT r.*, s.timezone FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id"
            " WHERE r.status = 'waiting' AND r.reminded_at IS NULL AND r.waiting_since IS NOT NULL"
        ):
            soon.append(self._remind_instant(parse_iso(run["waiting_since"]), zone(run["timezone"])))
        return min(soon) if soon else None

    def _occurrences(self, schedule: Schedule, now: datetime) -> list[datetime]:
        """Every moment this schedule was due up to now, oldest first (the one in next_at, then the repeats)."""
        assert schedule.next_at is not None
        found = [schedule.next_at]
        if schedule.repeat:
            tz = zone(schedule.timezone)
            while len(found) < MAX_BACKLOG:
                following = next_local(schedule.local_at, schedule.repeat, found[-1], tz, schedule.weekdays)
                if following > now:
                    break
                found.append(following)
        return found

    def fire_due(self) -> int:
        now = self._clock()
        started = 0
        for row in self.db.all(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_at IS NOT NULL AND next_at <= ? ORDER BY next_at",
            (iso(now),),
        ):
            schedule = _schedule(row)
            dues = self._occurrences(schedule, now)
            following = (
                next_local(schedule.local_at, schedule.repeat, now, zone(schedule.timezone), schedule.weekdays)
                if schedule.repeat
                else None
            )
            self.db.execute(
                "UPDATE schedules SET next_at = ? WHERE id = ?", (iso(following) if following else None, schedule.id)
            )
            old = [due for due in dues if (now - due).total_seconds() > LATE_SKIP_SECONDS]
            fresh = [due for due in dues if due not in old]
            if schedule.missed == "all":
                skipped, caught_up = [], old
            elif schedule.missed == "latest" and not fresh:
                skipped, caught_up = old[:-1], old[-1:]
            else:
                skipped, caught_up = old, []
            for due in skipped:
                self._record(
                    schedule, due, "missed", f"The server was not running at {iso(due)}: this run was skipped."
                )
            for due in caught_up:  # a backlog waits its turn, whatever `overlap` says
                started += self._admit(schedule, due, backlog=True)
            for due in fresh:
                late = (now - due).total_seconds()
                if late > self.late_after and self.late is not None:
                    self.late(f"schedule “{schedule.name}”", late)
                started += self._admit(schedule, due)
        for run in self._queued():  # runs that waited for their schedule to be free
            schedule = self.find(run["schedule_id"])
            if schedule is not None and not self.busy(schedule.id) and not self.stopping:
                self._start(schedule, parse_iso(run["due_at"]), run["id"])
                started += 1
        self._remind(now)
        return started

    def _admit(self, schedule: Schedule, due: datetime, backlog: bool = False) -> int:
        """A run that is due: it starts, or the schedule's overlap policy says what happens. Returns runs started."""
        if not self.busy(schedule.id) and not self._queued_for(schedule.id):
            self._start(schedule, due)
            return 1
        if schedule.overlap == "skip" and not backlog:
            self._record(schedule, due, "skipped", "The previous run was still going or waiting: this one was skipped.")
        else:
            if (
                int(
                    self.db.value(
                        "SELECT COUNT(*) FROM schedule_runs WHERE schedule_id = ? AND status = 'queued'",
                        (schedule.id,),
                        0,
                    )
                )
                >= MAX_BACKLOG
            ):
                self._record(schedule, due, "skipped", "Too many runs were waiting: this one was skipped.")
                return 0
            self.db.execute(
                "INSERT INTO schedule_runs (schedule_id, due_at, status) VALUES (?, ?, 'queued')",
                (schedule.id, iso(due)),
            )
            if schedule.overlap == "replace" and not backlog:
                self.close_waiting(schedule.id)
        return 0

    def _queued_for(self, schedule_id: int) -> bool:
        return (
            self.db.one("SELECT 1 FROM schedule_runs WHERE schedule_id = ? AND status = 'queued'", (schedule_id,))
            is not None
        )

    # ------------------------------------------------------------------
    # Waiting for an answer
    # ------------------------------------------------------------------
    def mark_waiting(self, run_id: int, question: str) -> None:
        """The run asked the person something: it waits (its turn goes on to its end, then the run stays `waiting`)."""
        self.db.execute(
            "UPDATE schedule_runs SET status = 'waiting', waiting_since = COALESCE(waiting_since, ?), summary = ?"
            " WHERE id = ? AND status IN ('running', 'waiting')",
            (iso(self._clock()), summary_of(question), run_id),
        )

    def finish_run(self, run_id: int, summary: str, closed: bool = False) -> None:
        """The run is over (its tool `finish_run`, or the closing turn). One that waited ends with an `ask` event, which
        also takes the Discord thread away; the others end like any run, with a `schedule` event."""
        row = self.db.one("SELECT * FROM schedule_runs WHERE id = ?", (run_id,))
        if row is None or row["status"] not in ("running", "waiting"):
            return
        status = "closed" if closed else "ok"
        text = summary_of(summary) if summary.strip() else "Finished."
        self.db.execute(
            "UPDATE schedule_runs SET status = ?, finished_at = ?, summary = ? WHERE id = ?",
            (status, now_iso(), text, run_id),
        )
        schedule = self.find(row["schedule_id"])
        if schedule is not None:
            if row["waiting_since"]:
                self.events.publish(
                    "ask",
                    schedule.name,
                    text,
                    payload={
                        "conversation": row["conversation_id"],
                        "stage": "closed" if closed else "finished",
                        "run": run_id,
                        **delivery.forum_mark(schedule.deliver),
                    },
                    targets=("web",) if schedule.local_only else ("web", "discord"),
                    source="schedules",
                    conversation_id=row["conversation_id"],
                )
            else:
                self._tell(schedule, status, text, 0, conversation=row["conversation_id"])
        self.wake()

    def close_waiting(self, schedule_id: int) -> bool:
        """Close the waiting run of a schedule from what it has (a turn of Clara's), in the background. False when there
        is none, or it is being closed."""
        run = self.waiting_run(schedule_id)
        schedule = self.find(schedule_id)
        if run is None or schedule is None or schedule_id in self._closing or self.closer is None:
            return False
        task = asyncio.create_task(self._close(schedule, run))
        self._closing[schedule_id] = task
        task.add_done_callback(lambda _: (self._closing.pop(schedule_id, None), self.wake()))
        return True

    async def _close(self, schedule: Schedule, run) -> None:
        try:
            assert self.closer is not None
            await self.closer(schedule, run)
        except Stopping:
            return
        except Exception as error:
            log.warning(
                "schedule %s: closing run %s failed (%s: %s)", schedule.id, run["id"], type(error).__name__, error
            )
        current = self.db.one("SELECT * FROM schedule_runs WHERE id = ?", (run["id"],))
        if current is not None and current["status"] == "waiting":  # Clara did not finish it: it is closed all the same
            self.finish_run(run["id"], "Closed without an answer.", closed=True)
        self.wake()

    def _remind(self, now: datetime) -> None:
        for run in self.db.all(
            "SELECT r.*, s.timezone, s.name, s.local_only, s.deliver FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id"
            " WHERE r.status = 'waiting' AND r.reminded_at IS NULL AND r.waiting_since IS NOT NULL"
        ):
            if self._remind_instant(parse_iso(run["waiting_since"]), zone(run["timezone"])) > now:
                continue
            self.db.execute("UPDATE schedule_runs SET reminded_at = ? WHERE id = ?", (iso(now), run["id"]))
            self.events.publish(
                "ask",
                run["name"],
                "Still waiting for your answer: answer in the conversation to finish.",
                payload={
                    "conversation": run["conversation_id"],
                    "stage": "reminder",
                    "run": run["id"],
                    **delivery.forum_mark(run["deliver"]),
                },
                targets=("web",) if run["local_only"] else ("web", "discord"),
                source="schedules",
                conversation_id=run["conversation_id"],
            )

    # ------------------------------------------------------------------
    # A run
    # ------------------------------------------------------------------
    async def pause_before_retry(self, attempt: int) -> None:
        """Wait before the attempt after `attempt`. A server that stops meanwhile ends the run: it runs again at the next
        start, as a run cut by a stop does."""
        if self.stopping:
            raise Stopping()
        await asyncio.sleep(RETRY_WAITS[attempt - 1])
        if self.stopping:
            raise Stopping()

    async def _execute(self, schedule: Schedule, run_id: int, due: datetime) -> None:
        number = int(
            self.db.value(
                "SELECT COUNT(*) FROM schedule_runs WHERE schedule_id = ?"
                " AND status NOT IN ('queued', 'skipped', 'missed')",
                (schedule.id,),
                1,
            )
        )
        reply = ""
        try:
            if self.runner is None:
                raise ScheduleError("Clara cannot run it now.")
            reply = await self.runner(schedule, number, Run(run_id, due))
            status, summary = "ok", summary_of(reply)
        except Stopping:
            self.db.execute(
                "UPDATE schedule_runs SET status = 'interrupted', finished_at = ? WHERE id = ?", (now_iso(), run_id)
            )
            if schedule.repeat or self.find(schedule.id):
                self.db.execute(
                    "UPDATE schedules SET next_at = ? WHERE id = ? AND (next_at IS NULL OR next_at > ?)",
                    (iso(due), schedule.id, iso(due)),
                )
            return
        except Exception as error:
            log.warning("schedule %s: the run failed (%s: %s)", schedule.id, type(error).__name__, error)
            status, summary = "failed", f"Clara could not run it: {error or type(error).__name__}"
        current = self.db.one("SELECT status, conversation_id FROM schedule_runs WHERE id = ?", (run_id,))
        if current is None or current["status"] in ("waiting", "ok", "closed"):
            self.wake()  # it asked and waits, or finish_run ended it: nothing more to record
            return
        schedule = self.find(schedule.id) or schedule
        waiting = self.waiting(schedule.conversation_id) if schedule.conversation_id else 0
        self.db.execute(
            "UPDATE schedule_runs SET status = ?, finished_at = ?, summary = ?, waiting = ? WHERE id = ?",
            (status, now_iso(), summary, waiting, run_id),
        )
        self._tell(schedule, status, summary, waiting, reply, current["conversation_id"])
        self.wake()

    def _record(self, schedule: Schedule, due: datetime, status: str, summary: str) -> None:
        self.db.execute(
            "INSERT INTO schedule_runs (schedule_id, due_at, finished_at, status, summary) VALUES (?, ?, ?, ?, ?)",
            (schedule.id, iso(due), now_iso(), status, summary),
        )
        self._tell(schedule, status, summary, 0)

    def _tell(
        self,
        schedule: Schedule,
        status: str,
        summary: str,
        waiting: int,
        reply: str = "",
        conversation: str | None = None,
    ) -> None:
        """How a run ended, the way the schedule's `deliver` says: the short notification, and for Discord the whole answer."""
        conversation = conversation or schedule.conversation_id
        note = f"\n{waiting} request{'s' if waiting > 1 else ''} wait for your permission." if waiting else ""
        ok = status == "ok"
        if delivery.says(schedule.deliver, ok):
            delivery.say(
                self.events,
                f"Scheduled task: {schedule.name}",
                (reply.strip() or summary) + (f"\n{note}" if note else ""),
                conversation=conversation,
                source="schedules",
                payload={"schedule": schedule.id, "forum": True},
            )
        if delivery.notifies(schedule.deliver, ok):
            self.events.publish(
                "schedule",
                f"Scheduled task: {schedule.name}",
                summary + note,
                payload={"schedule": schedule.id, "status": status, "waiting": waiting},
                source="schedules",
                conversation_id=conversation,
                targets=delivery.OTHER_CLIENTS if ok and schedule.deliver in ("web", "both") else None,
            )

    def recover(self) -> int:
        """At start: runs left `running` by a crash are marked interrupted and their schedule is due again now."""
        rows = self.db.all("SELECT * FROM schedule_runs WHERE status = 'running'")
        for row in rows:
            self.db.execute(
                "UPDATE schedule_runs SET status = 'interrupted', finished_at = ? WHERE id = ?", (now_iso(), row["id"])
            )
            self.db.execute(
                "UPDATE schedules SET next_at = ? WHERE id = ? AND enabled = 1 AND (next_at IS NULL OR next_at > ?)",
                (row["due_at"], row["schedule_id"], row["due_at"]),
            )
        return len(rows)

    async def wait_running(self) -> None:
        await asyncio.gather(*self._running.values(), *self._closing.values(), return_exceptions=True)
