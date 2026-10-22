"""Hooks: when something happens outside, do something. The pieces, and what keeps each safe through a stop:

* An **event** (events.py) is stored once. `ingest` writes the events, the runs they plan and the place the source had reached
  (`also`) in ONE transaction: a crash before it loses nothing (the source looks again from its old place), a crash after it
  repeats nothing (the events are stored, the runs are planned, the place has moved).
* A **trigger** is yours: an event kind, a filter, and an action: `run` (a turn of Clara's with a prompt and only the tools
  you allow) or `notify` (a text, no model). The model has no tool to make or change one.
* A **run** waits in `trigger_runs` until its trigger may start it: one run of a trigger at a time, at most `max_per_hour` an
  hour, at most MAX_PARALLEL triggers at once. So a long outage that brought a thousand mails costs a thousand runs only as
  fast as you allowed, oldest first; `max_age_hours` lets the old ones expire instead.
* **Catch-up.** An event found more than STALE_AFTER after it happened came from a stop or an outage. The trigger's `catch_up`
  says what the backlog becomes: `all` (a run each), `latest` (a run for the newest, the rest recorded as skipped), `digest`
  (one run for the whole backlog) or `skip` (all recorded, none run).
* A run cut by a stop that had done nothing yet starts again; one that had begun is `interrupted` and is not repeated (Clara
  cannot know what it already did) unless the trigger has `rerun_interrupted`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..db import Database
from ..events import Events
from ..timekeeping.dueloop import DueLoop
from ..timekeeping.schedules import Stopping
from ..timeutil import iso, now_iso, parse_iso, utcnow
from . import matching
from .events import KINDS, NOTIFY, SourceEvent, StoredEvent
from .message import fill

log = logging.getLogger(__name__)

MAX_TRIGGERS = 50
MAX_NAME = 100
MAX_PROMPT = 20_000
MAX_TEMPLATE = 500
MAX_QUEUED = 500  # runs waiting per trigger; more are recorded as skipped, with an alert
MAX_PARALLEL = 2  # triggers running at once (the model has its own slots too)
STALE_AFTER = timedelta(minutes=15)
EVENTS_KEPT_DAYS = 30
MAX_ATTEMPTS = 3
RETRY_WAITS = (60, 300)
MAX_SUMMARY = 700
CATCH_UPS = ("all", "latest", "digest", "skip")
ACTIONS = ("run", "notify")


class TriggerError(ValueError):
    pass


@dataclass(frozen=True)
class Trigger:
    id: int
    name: str
    enabled: bool
    on: str
    filter: dict
    action: str
    prompt: str
    tools: tuple[str, ...]
    project_id: int | None
    model: str
    local_only: bool
    catch_up: str
    max_per_hour: int
    max_age_hours: int
    include_self: bool
    rerun_interrupted: bool
    attempts: int
    announce: bool
    in_history: bool  # the conversations of its runs are listed in the web site's history
    created_at: str
    updated_at: str

    @property
    def effective_tools(self) -> tuple[str, ...]:
        """What a run may use: your list, else the kind's read tools and `notify`. A notification needs none."""
        if self.action != "run":
            return ()
        return self.tools or (*KINDS[self.on].read_tools, NOTIFY)


def _trigger(row) -> Trigger:
    return Trigger(
        row["id"],
        row["name"],
        bool(row["enabled"]),
        row["event_kind"],
        json.loads(row["filter"]),
        row["action"],
        row["prompt"],
        tuple(json.loads(row["tools"])),
        row["project_id"],
        row["model"],
        bool(row["local_only"]),
        row["catch_up"],
        row["max_per_hour"],
        row["max_age_hours"],
        bool(row["include_self"]),
        bool(row["rerun_interrupted"]),
        row["attempts"],
        bool(row["announce"]),
        bool(row["in_history"]),
        row["created_at"],
        row["updated_at"],
    )


def _stored(row) -> StoredEvent:
    return StoredEvent(
        source=row["source"],
        kind=row["kind"],
        key=row["key"],
        occurred_at=row["occurred_at"],
        actor=row["actor"],
        self_caused=bool(row["self_caused"]),
        title=row["title"],
        data=json.loads(row["data"]),
        id=row["id"],
        received_at=row["received_at"],
    )


def summary_of(reply: str) -> str:
    text = " ".join((reply.strip().split("\n\n", 1)[0]).split())
    if not text:
        return "Clara finished, without writing anything."
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 1] + "…"


Runner = Callable[[Trigger, int, list[StoredEvent]], Awaitable[str]]  # (trigger, run id, its events) -> Clara's answer

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "filter": None,
    "action": "run",
    "prompt": "",
    "tools": None,
    "model": "",
    "local_only": False,
    "catch_up": "all",
    "max_per_hour": 20,
    "max_age_hours": 0,
    "include_self": False,
    "rerun_interrupted": False,
    "attempts": MAX_ATTEMPTS,
    "announce": True,
    "in_history": False,
}


class Triggers(DueLoop):
    name = "triggers"

    def __init__(
        self,
        db: Database,
        events: Events,
        projects: Any,
        tool_names: Callable[[], frozenset[str]],
        clock: Callable[[], datetime] = utcnow,
    ):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.projects = projects
        self._tool_names = tool_names
        self.runner: Runner | None = None
        self.alert: Callable[[Trigger, str], None] | None = None
        self._running: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Writing and reading triggers
    # ------------------------------------------------------------------
    @property
    def firing(self) -> bool:
        return bool(self._running)

    def wants(self, kind: str) -> bool:
        """Is any enabled trigger waiting for this kind of event? (A source need not look if none is.)"""
        return self.db.one("SELECT 1 FROM triggers WHERE enabled = 1 AND event_kind = ?", (kind,)) is not None

    def waiting_since(self, kind: str) -> str | None:
        """Since when a trigger waits for this kind of event: the earliest of the enabled ones, as last made or changed (turning
        one on is a change). None if none does."""
        row = self.db.one("SELECT MIN(updated_at) AS at FROM triggers WHERE enabled = 1 AND event_kind = ?", (kind,))
        return row["at"]

    def find(self, trigger_id: int) -> Trigger | None:
        row = self.db.one("SELECT * FROM triggers WHERE id = ?", (trigger_id,))
        return _trigger(row) if row else None

    def get(self, trigger_id: int) -> Trigger:
        found = self.find(trigger_id)
        if found is None:
            raise TriggerError(f"No hook {trigger_id}.")
        return found

    def by_name(self, name: str) -> Trigger | None:
        row = self.db.one("SELECT * FROM triggers WHERE name = ?", (name,))
        return _trigger(row) if row else None

    def listing(self) -> list[Trigger]:
        return [_trigger(row) for row in self.db.all("SELECT * FROM triggers ORDER BY id")]

    def _normal(self, values: dict) -> dict:
        """Every field checked and put in the form it is stored in."""
        name = " ".join(str(values["name"] or "").split())
        if not name or len(name) > MAX_NAME:
            raise TriggerError(f"A hook needs a name of 1 to {MAX_NAME} characters.")
        kind = KINDS.get(values["on"])
        if kind is None:
            raise TriggerError(f"{values['on']!r} is not a kind of event ({', '.join(KINDS)}).")
        action = values["action"]
        if action not in ACTIONS:
            raise TriggerError(f"action must be one of {', '.join(ACTIONS)}.")
        prompt = str(values["prompt"] or "").strip()
        if action == "run" and not prompt:
            raise TriggerError("A hook that runs Clara needs a prompt: what to do when the event comes.")
        if len(prompt) > (MAX_PROMPT if action == "run" else MAX_TEMPLATE):
            raise TriggerError(f"The {'prompt' if action == 'run' else 'text'} is too long.")
        try:
            filter_ = matching.check(kind, values["filter"])
        except matching.FilterError as error:
            raise TriggerError(str(error)) from None
        tools = self._tools(values["tools"], action)
        if values["catch_up"] not in CATCH_UPS:
            raise TriggerError(f"catch_up must be one of {', '.join(CATCH_UPS)}.")
        for field, low, high in (("max_per_hour", 1, 600), ("max_age_hours", 0, 8760), ("attempts", 1, MAX_ATTEMPTS)):
            if (
                not isinstance(values[field], int)
                or isinstance(values[field], bool)
                or not low <= values[field] <= high
            ):
                raise TriggerError(f"{field} must be a whole number from {low} to {high}.")
        model = str(values["model"] or "").strip()
        if len(model) > 200 or (model and ":" not in model):
            raise TriggerError("A model is written provider:model.")
        return {
            "name": name,
            "event_kind": kind.kind,
            "filter": json.dumps(filter_),
            "action": action,
            "prompt": prompt,
            "tools": json.dumps(tools),
            "model": model,
            "catch_up": values["catch_up"],
            "max_per_hour": values["max_per_hour"],
            "max_age_hours": values["max_age_hours"],
            "attempts": values["attempts"],
            **{
                field: int(bool(values[field]))
                for field in ("enabled", "local_only", "include_self", "rerun_interrupted", "announce", "in_history")
            },
        }

    def _tools(self, raw: Any, action: str) -> list[str]:
        if raw in (None, ""):
            return []
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise TriggerError("tools is a list of tool names or globs (mail_*).")
        found = list(dict.fromkeys(item.strip() for item in raw if item.strip()))
        if found and action != "run":
            raise TriggerError("A notification uses no tools.")
        if len(found) > 40:
            raise TriggerError("At most 40 tools.")
        known = self._tool_names()
        for item in found:
            if len(item) > 100 or not any(fnmatch.fnmatchcase(name, item) for name in known):
                raise TriggerError(f"{item!r} matches no tool of Clara's.")
        return found

    def _project(self, project: Any) -> int | None:
        if project in (None, "none"):
            return None
        if not isinstance(project, int) or isinstance(project, bool) or self.projects.get(project) is None:
            raise TriggerError(f"No project {project}.")
        return project

    def create(self, name: str, on: str, *, project: Any = "none", **fields: Any) -> Trigger:
        unknown = set(fields) - set(DEFAULTS)
        if unknown:
            raise TriggerError(f"Unknown fields: {', '.join(sorted(unknown))}.")
        if int(self.db.value("SELECT COUNT(*) FROM triggers", (), 0)) >= MAX_TRIGGERS:
            raise TriggerError(f"At most {MAX_TRIGGERS} hooks.")
        values = self._normal(
            {**DEFAULTS, **{k: v for k, v in fields.items() if v is not None}, "name": name, "on": on}
        )
        if self.by_name(values["name"]) is not None:
            raise TriggerError(f"A hook named {values['name']!r} exists already.")
        values["project_id"] = self._project(project)
        now = now_iso()
        columns = list(values)
        trigger_id = self.db.insert(
            f"INSERT INTO triggers ({', '.join(columns)}, created_at, updated_at) VALUES ({', '.join('?' * len(columns))}, ?, ?)",
            (*values.values(), now, now),
        )
        return self.get(trigger_id)

    def update(self, trigger_id: int, **fields: Any) -> Trigger:
        current = self.get(trigger_id)
        project = fields.pop("project", "keep")
        if "on" in fields and fields["on"] != current.on:
            raise TriggerError("The kind of event cannot change: make another hook.")
        unknown = set(fields) - set(DEFAULTS) - {"name", "on"}
        if unknown:
            raise TriggerError(f"Unknown fields: {', '.join(sorted(unknown))}.")
        merged = {
            "name": current.name,
            "on": current.on,
            "enabled": current.enabled,
            "filter": current.filter,
            "action": current.action,
            "prompt": current.prompt,
            "tools": list(current.tools),
            "model": current.model,
            "local_only": current.local_only,
            "catch_up": current.catch_up,
            "max_per_hour": current.max_per_hour,
            "max_age_hours": current.max_age_hours,
            "include_self": current.include_self,
            "rerun_interrupted": current.rerun_interrupted,
            "attempts": current.attempts,
            "announce": current.announce,
            "in_history": current.in_history,
        }
        merged.update({key: value for key, value in fields.items() if value is not None or key == "filter"})
        values = self._normal(merged)
        other = self.by_name(values["name"])
        if other is not None and other.id != trigger_id:
            raise TriggerError(f"A hook named {values['name']!r} exists already.")
        values["project_id"] = current.project_id if project == "keep" else self._project(project)
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.db.transaction():
            self.db.execute(
                f"UPDATE triggers SET {assignments}, updated_at = ? WHERE id = ?",
                (*values.values(), now_iso(), trigger_id),
            )
            if (
                current.enabled and not values["enabled"]
            ):  # turned off: what waited does not wait for its turn to come back
                self._skip_queued(trigger_id, "The hook was turned off.")
        self.wake()
        return self.get(trigger_id)

    def delete(self, trigger_id: int) -> bool:
        task = self._running.get(trigger_id)
        if task is not None:
            task.cancel()
        return self.db.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,)).rowcount > 0

    def _skip_queued(self, trigger_id: int, note: str) -> None:
        self.db.execute(
            "UPDATE trigger_runs SET status = 'skipped', note = ?, finished_at = ? WHERE trigger_id = ? AND status = 'queued'",
            (note, now_iso(), trigger_id),
        )

    def describe(self, trigger: Trigger) -> dict:
        last = self.db.one(
            "SELECT * FROM trigger_runs WHERE trigger_id = ? AND status NOT IN ('queued', 'skipped') ORDER BY id DESC LIMIT 1",
            (trigger.id,),
        )
        return {
            "id": trigger.id,
            "name": trigger.name,
            "enabled": trigger.enabled,
            "on": trigger.on,
            "filter": trigger.filter,
            "action": trigger.action,
            "prompt": trigger.prompt,
            "tools": list(trigger.tools),
            "effective_tools": list(trigger.effective_tools),
            "project": trigger.project_id,
            "model": trigger.model,
            "local_only": trigger.local_only,
            "catch_up": trigger.catch_up,
            "max_per_hour": trigger.max_per_hour,
            "max_age_hours": trigger.max_age_hours,
            "include_self": trigger.include_self,
            "rerun_interrupted": trigger.rerun_interrupted,
            "attempts": trigger.attempts,
            "announce": trigger.announce,
            "in_history": trigger.in_history,
            "created_at": trigger.created_at,
            "updated_at": trigger.updated_at,
            "queued": int(
                self.db.value(
                    "SELECT COUNT(*) FROM trigger_runs WHERE trigger_id = ? AND status = 'queued'", (trigger.id,), 0
                )
            ),
            "running": trigger.id in self._running,
            "runs": int(
                self.db.value(
                    "SELECT COUNT(*) FROM trigger_runs WHERE trigger_id = ? AND status NOT IN ('queued', 'skipped')",
                    (trigger.id,),
                    0,
                )
            ),
            "last": self._run_view(last) if last else None,
        }

    # ------------------------------------------------------------------
    # Events in
    # ------------------------------------------------------------------
    def wanted_by(self, trigger: Trigger, event: SourceEvent) -> bool:
        if event.kind != trigger.on or (event.self_caused and not trigger.include_self):
            return False
        return matching.matches(trigger.filter, event.data)

    def ingest(self, events: list[SourceEvent], also: Callable[[], None] | None = None) -> int:
        """Store the events that are new, plan the runs of the triggers that want them, and call `also` (a source moving its
        place forward), all in one transaction. Returns the runs planned."""
        now = self._clock()
        planned, overflowed = 0, []
        with self.db.transaction():
            stored: list[StoredEvent] = []
            for event in events:
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO trigger_events (source, kind, key, actor, self_caused, title, data, occurred_at, received_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.source,
                        event.kind,
                        event.key,
                        event.actor,
                        int(event.self_caused),
                        event.title[:300],
                        json.dumps(event.data, ensure_ascii=False),
                        event.occurred_at,
                        iso(now),
                    ),
                )
                if cursor.rowcount:
                    stored.append(
                        StoredEvent(**{**event.__dict__, "id": int(cursor.lastrowid or 0), "received_at": iso(now)})
                    )
            for trigger in self.listing():
                mine = [event for event in stored if trigger.enabled and self.wanted_by(trigger, event)]
                if mine:
                    count, overflow = self._plan(trigger, mine, now)
                    planned += count
                    if overflow:
                        overflowed.append(trigger)
            if also is not None:
                also()
        for trigger in overflowed:
            if self.alert is not None:
                self.alert(
                    trigger,
                    f"More than {MAX_QUEUED} runs of “{trigger.name}” are waiting: the newest events were recorded but not run.",
                )
        if planned:
            self.wake()
        return planned

    def _insert_run(self, trigger_id: int, events: list[StoredEvent], status: str, note: str = "") -> None:
        now = now_iso()
        self.db.execute(
            "INSERT INTO trigger_runs (trigger_id, event_ids, status, note, queued_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                trigger_id,
                json.dumps([event.id for event in events]),
                status,
                note,
                now,
                now if status != "queued" else None,
            ),
        )

    def _plan(self, trigger: Trigger, events: list[StoredEvent], now: datetime) -> tuple[int, bool]:
        """The runs these events make for this trigger. Returns (runs queued, whether the queue was full)."""
        events = sorted(events, key=lambda event: (parse_iso(event.occurred_at), event.id))
        stale_ids = {event.id for event in events if now - parse_iso(event.occurred_at) > STALE_AFTER}
        stale = [event for event in events if event.id in stale_ids]
        fresh = [event for event in events if event.id not in stale_ids]
        groups: list[list[StoredEvent]] = []
        skipped: list[tuple[list[StoredEvent], str]] = []
        if stale:
            if trigger.catch_up == "all":
                groups += [[event] for event in stale]
            elif trigger.catch_up == "digest":
                groups.append(stale)
            elif trigger.catch_up == "latest":
                groups.append(stale[-1:])
                skipped += [
                    ([event], "Found late (after a stop): only the newest of the backlog runs.") for event in stale[:-1]
                ]
            else:
                skipped += [([event], "Found late (after a stop): this hook skips a backlog.") for event in stale]
        groups += [[event] for event in fresh]
        room = MAX_QUEUED - int(
            self.db.value(
                "SELECT COUNT(*) FROM trigger_runs WHERE trigger_id = ? AND status = 'queued'", (trigger.id,), 0
            )
        )
        queued, overflow = 0, False
        for group in groups:
            if room <= 0:
                overflow = True
                skipped.append((group, f"More than {MAX_QUEUED} runs were waiting: this one was not run."))
                continue
            self._insert_run(trigger.id, group, "queued")
            queued += 1
            room -= 1
        for group, note in skipped:
            self._insert_run(trigger.id, group, "skipped", note)
        return queued, overflow

    # ------------------------------------------------------------------
    # Working off the queue
    # ------------------------------------------------------------------
    def _started_since(self, trigger_id: int, since: datetime) -> list[str]:
        return [
            row[0]
            for row in self.db.all(
                "SELECT started_at FROM trigger_runs WHERE trigger_id = ? AND started_at > ? ORDER BY started_at",
                (trigger_id, iso(since)),
            )
        ]

    def _expire(self, now: datetime) -> None:
        for trigger in self.listing():
            if not trigger.max_age_hours:
                continue
            limit = now - timedelta(hours=trigger.max_age_hours)
            for row in self.db.all(
                "SELECT * FROM trigger_runs WHERE trigger_id = ? AND status = 'queued' ORDER BY id", (trigger.id,)
            ):
                events = self._events_of(row)
                if events and parse_iso(events[-1].occurred_at) < limit:  # even its newest event is too old
                    self.db.execute(
                        "UPDATE trigger_runs SET status = 'expired', note = ?, finished_at = ? WHERE id = ?",
                        (f"Older than {trigger.max_age_hours} hours when its turn came.", iso(now), row["id"]),
                    )

    def fire_due(self) -> int:
        if self.stopping:
            return 0
        now = self._clock()
        self._expire(now)
        started = 0
        seen: set[int] = set()
        for row in self.db.all(
            "SELECT r.* FROM trigger_runs r JOIN triggers t ON t.id = r.trigger_id"
            " WHERE r.status = 'queued' AND t.enabled = 1 ORDER BY r.id"
        ):
            trigger_id = row["trigger_id"]
            if trigger_id in seen or trigger_id in self._running:
                continue
            seen.add(trigger_id)
            if len(self._running) >= MAX_PARALLEL:
                break
            trigger = self.get(trigger_id)
            if len(self._started_since(trigger_id, now - timedelta(hours=1))) >= trigger.max_per_hour:
                continue
            self._start(trigger, row["id"])
            started += 1
        return started

    def next_due(self) -> datetime | None:
        if len(self._running) >= MAX_PARALLEL:
            return None  # nothing can start until a run ends, and its end wakes the loop (else the loop would spin)
        now = self._clock()
        soonest: datetime | None = None
        for row in self.db.all(
            "SELECT r.trigger_id FROM trigger_runs r JOIN triggers t ON t.id = r.trigger_id"
            " WHERE r.status = 'queued' AND t.enabled = 1 GROUP BY r.trigger_id"
        ):
            if row["trigger_id"] in self._running:
                continue  # its end wakes the loop
            trigger = self.get(row["trigger_id"])
            recent = self._started_since(trigger.id, now - timedelta(hours=1))
            if len(recent) < trigger.max_per_hour:
                return now
            free = parse_iso(recent[len(recent) - trigger.max_per_hour]) + timedelta(hours=1)
            soonest = free if soonest is None else min(soonest, free)
        return soonest

    def _start(self, trigger: Trigger, run_id: int) -> None:
        self.db.execute(
            "UPDATE trigger_runs SET status = 'running', started_at = ? WHERE id = ?", (iso(self._clock()), run_id)
        )
        task = asyncio.create_task(self._execute(trigger, run_id))
        self._running[trigger.id] = task
        task.add_done_callback(lambda _: (self._running.pop(trigger.id, None), self.wake()))

    def _events_of(self, row) -> list[StoredEvent]:
        ids = json.loads(row["event_ids"])
        if not ids:
            return []
        found = self.db.all(
            f"SELECT * FROM trigger_events WHERE id IN ({', '.join('?' * len(ids))}) ORDER BY occurred_at, id", ids
        )
        return [_stored(event) for event in found]

    async def pause_before_retry(self, attempt: int) -> None:
        if self.stopping:
            raise Stopping()
        await asyncio.sleep(RETRY_WAITS[attempt - 1])
        if self.stopping:
            raise Stopping()

    def did_something(self, conversation_id: str | None) -> bool:
        """Has the run's conversation anything Clara wrote or did? (Only the question: a run that stopped before that is
        started afresh.)"""
        if not conversation_id:
            return False
        return (
            self.db.one(
                "SELECT 1 FROM messages WHERE conversation_id = ? AND role != 'user' LIMIT 1", (conversation_id,)
            )
            is not None
        )

    async def _execute(self, trigger: Trigger, run_id: int) -> None:
        row = self.db.one("SELECT * FROM trigger_runs WHERE id = ?", (run_id,))
        events = self._events_of(row) if row else []
        status, summary = "ok", ""
        try:
            if not events:
                raise TriggerError("The events of this run are gone.")
            if trigger.action == "notify":
                summary = self._notify(trigger, events)
            else:
                if self.runner is None:
                    raise TriggerError("Clara cannot run it now.")
                summary = summary_of(await self.runner(trigger, run_id, events))
        except Stopping:
            self._cut(trigger, run_id)
            return
        except Exception as error:
            log.warning("hook %s: the run failed (%s: %s)", trigger.id, type(error).__name__, error)
            status, summary = "failed", f"Clara could not run it: {error or type(error).__name__}"
        self.db.execute(
            "UPDATE trigger_runs SET status = ?, finished_at = ?, summary = ? WHERE id = ?",
            (status, now_iso(), summary, run_id),
        )
        self._tell(trigger, run_id, status, summary)

    def _cut(self, trigger: Trigger, run_id: int) -> None:
        """A run the stop cut: started afresh if it had done nothing, else interrupted (or started afresh if you said so)."""
        row = self.db.one("SELECT conversation_id FROM trigger_runs WHERE id = ?", (run_id,))
        began = self.did_something(row["conversation_id"] if row else None)
        if not began or trigger.rerun_interrupted:
            self.db.execute(
                "UPDATE trigger_runs SET status = 'queued', started_at = NULL, conversation_id = NULL WHERE id = ?",
                (run_id,),
            )
        else:
            self.db.execute(
                "UPDATE trigger_runs SET status = 'interrupted', finished_at = ?, note = ? WHERE id = ?",
                (
                    now_iso(),
                    "The server stopped while it ran; it may have done part of its work, so it is not run again.",
                    run_id,
                ),
            )

    def recover(self) -> int:
        """At start: runs left `running` by a crash are put right as a stop would have."""
        rows = self.db.all("SELECT r.id, r.trigger_id FROM trigger_runs r WHERE r.status = 'running'")
        for row in rows:
            trigger = self.find(row["trigger_id"])
            if trigger is not None:
                self._cut(trigger, row["id"])
        return len(rows)

    def _notify(self, trigger: Trigger, events: list[StoredEvent]) -> str:
        template = trigger.prompt or KINDS[trigger.on].template
        if len(events) == 1:
            title, text = trigger.name, fill(template, {"title": events[0].title, **events[0].data})
        else:
            title = f"{trigger.name}: {len(events)} events"
            text = "\n".join(fill(template, {"title": event.title, **event.data}) for event in events[:10])
            text += f"\n…and {len(events) - 10} more." if len(events) > 10 else ""
        self.events.publish(
            "notification", title, text, source="triggers", targets=("web",) if trigger.local_only else None
        )
        return summary_of(text)

    def _tell(self, trigger: Trigger, run_id: int, status: str, summary: str) -> None:
        if trigger.action == "notify" or (status == "ok" and not trigger.announce):
            return
        row = self.db.one("SELECT conversation_id FROM trigger_runs WHERE id = ?", (run_id,))
        self.events.publish(
            "schedule",
            f"Hook: {trigger.name}",
            summary,
            payload={"trigger": trigger.id, "run": run_id, "status": status},
            source="triggers",
            conversation_id=row["conversation_id"] if row else None,
            targets=("web",) if trigger.local_only else None,
        )

    async def wait_running(self) -> None:
        await asyncio.gather(*self._running.values(), return_exceptions=True)

    # ------------------------------------------------------------------
    # Looking
    # ------------------------------------------------------------------
    def event(self, event_id: int) -> StoredEvent | None:
        row = self.db.one("SELECT * FROM trigger_events WHERE id = ?", (event_id,))
        return _stored(row) if row else None

    def recent_events(self, limit: int = 20, kind: str = "") -> list[StoredEvent]:
        if kind:
            rows = self.db.all("SELECT * FROM trigger_events WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit))
        else:
            rows = self.db.all("SELECT * FROM trigger_events ORDER BY id DESC LIMIT ?", (limit,))
        return [_stored(row) for row in rows]

    def _run_view(self, row) -> dict:
        events = self._events_of(row)
        return {
            "id": row["id"],
            "trigger": row["trigger_id"],
            "status": row["status"],
            "note": row["note"],
            "summary": row["summary"],
            "queued_at": row["queued_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "conversation": row["conversation_id"],
            "events": [{"id": event.id, "title": event.title, "at": event.occurred_at} for event in events[:10]],
            "event_count": len(json.loads(row["event_ids"])),
        }

    def runs(self, trigger_id: int, limit: int = 20) -> list[dict]:
        return [
            self._run_view(row)
            for row in self.db.all(
                "SELECT * FROM trigger_runs WHERE trigger_id = ? ORDER BY id DESC LIMIT ?", (trigger_id, limit)
            )
        ]

    def preview(self, trigger: Trigger, event: StoredEvent | SourceEvent) -> dict:
        """What would happen for this event, without doing it."""
        from .message import message_of

        wanted = self.wanted_by(trigger, event)
        reason = ""
        if event.kind != trigger.on:
            reason = f"It is a {event.kind} event; the hook waits for {trigger.on}."
        elif event.self_caused and not trigger.include_self:
            reason = "The event was caused by Clara's own account, and the hook does not take those (include_self)."
        elif not wanted:
            reason = "The filter does not match."
        shown = event if isinstance(event, StoredEvent) else StoredEvent(**{**event.__dict__, "id": 0})
        message = (
            fill(trigger.prompt or KINDS[trigger.on].template, {"title": shown.title, **shown.data})
            if trigger.action == "notify"
            else message_of(trigger.name, trigger.prompt, [shown])
        )
        return {
            "matches": wanted,
            "why_not": reason,
            "action": trigger.action,
            "message": message,
            "tools": list(trigger.effective_tools),
            "tainted": trigger.action == "run",
        }

    def prune(self, retention_days: int) -> None:
        """Old events (30 days) not wanted by a run still waiting, and old runs (the audit retention)."""
        active: set[int] = set()
        for row in self.db.all("SELECT event_ids FROM trigger_runs WHERE status IN ('queued', 'running')"):
            active.update(json.loads(row["event_ids"]))
        cutoff = iso(self._clock() - timedelta(days=EVENTS_KEPT_DAYS))
        old = [
            row[0]
            for row in self.db.all("SELECT id FROM trigger_events WHERE received_at < ?", (cutoff,))
            if row[0] not in active
        ]
        for start in range(0, len(old), 500):
            batch = old[start : start + 500]
            self.db.execute(f"DELETE FROM trigger_events WHERE id IN ({', '.join('?' * len(batch))})", batch)
        self.db.execute(
            "DELETE FROM trigger_runs WHERE finished_at IS NOT NULL AND finished_at < ?",
            (iso(self._clock() - timedelta(days=retention_days)),),
        )
