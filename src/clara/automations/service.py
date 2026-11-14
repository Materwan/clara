"""Automations: Clara does something when a trigger holds. One kind, made of:

* a **trigger**: a tree of AND and OR over events (a mail, a push, a pull request…), instants (a cron time, a date) and
  conditions of time (a window, a cron condition) (tree.py);
* an **action**: `run` (a turn of Clara's with a prompt, its documents, and its tools) or `notify` (a text, no model);
* **limits**: the pace (`max_per_hour`), what becomes of what was missed (`catch_up`), what happens when a run comes due while the
  last is not over (`overlap`), when a waiting run expires (`max_age_hours`), the tries when the model is busy (`attempts`), one
  conversation for each run or one shared (`per_run`), how long the conversations are kept (memory/retention.py).

The pieces, and what keeps each safe through a stop:

* An **event** (events.py) is stored once. `ingest` writes the events, the runs they plan and the place the source had reached
  (`also`) in ONE transaction: a crash before it loses nothing (the source looks again from its old place), a crash after it
  repeats nothing. An **instant** is looked at by the due loop: the instants since the automation's `reached_at` are worked out
  from its cron expressions and dates, and the new `reached_at` is written in the same transaction as the runs they plan.
* A **run** waits in `automation_runs` until its automation may start it: one run of an automation at a time, at most
  `max_per_hour` an hour, at most MAX_PARALLEL runs of events at once, and MAX_PARALLEL_INSTANTS of instants beside them. So a long outage that brought a thousand mails costs a thousand
  runs only as fast as you allowed, oldest first; `max_age_hours` lets the old ones expire instead.
* An event that the tree refuses only because of the time (it came outside the window of an AND) is recorded as `skipped`, with the
  reason, and starts nothing. An event the filters refuse is not recorded (it is not this automation's).
* **Catch-up.** An event found more than STALE_AFTER after it happened, an instant more than LATE_SKIP_SECONDS after it was due, came
  from a stop or an outage. `catch_up` says what the backlog becomes: `all` (a run each), `latest` (a run for the newest; for
  instants only when none is fresh), `digest` (one run for the whole backlog of events) or `skip` (all recorded, none run).
* A run can **ask you something and wait** (`ask_person`): its status is `waiting`, your answer arrives in the run's conversation,
  and `finish_run` ends it. A waiting run is reminded once and closed from what it has when `overlap` says so.
* A run cut by a stop that had done nothing yet starts again; one that had begun is `interrupted` and is not repeated (Clara cannot
  know what it already did) unless the automation has `rerun_interrupted`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from ..db import Database
from ..events import Events, delivery
from ..memory.retention import KEEP_FIELDS, Own, RetentionError, check_own, merged_own
from ..timekeeping.clock import MomentError, local_text, zone_of
from ..timekeeping.dueloop import DueLoop
from ..timeutil import iso, now_iso, parse_iso, resolve_local, utcnow, zone
from . import tree as trees
from .events import KINDS, NOTIFY, EventKind, SourceEvent, StoredEvent
from .message import fill

log = logging.getLogger(__name__)

MAX_AUTOMATIONS = 150
MAX_NAME = 100
MAX_PROMPT = 20_000
MAX_TEMPLATE = 500
MAX_DOCUMENTS_CHARS = 150_000
MAX_QUEUED = 500  # runs waiting per automation for events; more are recorded as skipped, with an alert
MAX_BACKLOG = 31  # instants looked at in one pass, and runs of instants kept waiting
MAX_PARALLEL = 2  # runs of events at once (the model has its own slots too)
MAX_PARALLEL_INSTANTS = 1  # runs of instants (cron, date, run now) at once: a pool of their own, so an event backlog never delays one
STALE_AFTER = timedelta(minutes=15)  # an event found later than this after it happened is a backlog
LATE_SKIP_SECONDS = 3600  # an instant found later than this after it was due is a backlog
EVENTS_KEPT_DAYS = 30
MAX_ATTEMPTS = 3
RETRY_WAITS = (60, 300)  # seconds before the 2nd and the 3rd attempt
MAX_SUMMARY = 700
REMIND_AFTER = timedelta(hours=3)
CATCH_UPS = ("all", "latest", "digest", "skip")
OVERLAPS = ("skip", "replace", "queue")
ACTIONS = ("run", "notify")
SKIPPED_BUSY = "The previous run was still going or waiting: this one was skipped."


class AutomationError(ValueError):
    pass


class Stopping(Exception):
    """The server is stopping: the run did not take place and will at the next start."""


@dataclass(frozen=True)
class Automation:
    id: int
    name: str
    enabled: bool
    tree: dict
    timezone: str  # '' follows the server's (CLARA_TIMEZONE)
    action: str
    prompt: str
    documents: tuple[dict, ...]
    packs: tuple[str, ...]  # the tool packs its runs get (a schedule's way)
    tools: tuple[str, ...]  # the only tools its runs may use, names or globs (a hook's way)
    conversation_id: str | None  # the one shared by its runs (not per_run)
    project_id: int | None
    model: str
    local_only: bool
    per_run: bool
    catch_up: str
    overlap: str
    max_per_hour: int
    max_age_hours: int
    include_self: bool
    rerun_interrupted: bool
    attempts: int
    announce: bool
    in_history: (
        bool  # its runs live in a Discord forum thread, and its conversations are listed in the web site's history
    )
    mode: str | None  # its mode (gate/modes.py); None: its project's, else the default mode
    keep_days: int | None  # its own limits on the conversations of its runs (memory/retention.py); None: follow
    keep_hours: int | None  # the global setting (days and hours are both set, or both None)
    keep_last: int | None
    reached_at: datetime | None
    next_at: datetime | None
    created_at: str
    updated_at: str

    @property
    def kinds(self) -> tuple[str, ...]:
        """The kinds of event from a source that its trigger waits for."""
        return tuple(trees.kinds_of(self.tree))

    @property
    def asks(self) -> bool:
        return "ask_person" in self.packs


def _automation(row) -> Automation:
    return Automation(
        row["id"],
        row["name"],
        bool(row["enabled"]),
        json.loads(row["tree"]),
        row["timezone"],
        row["action"],
        row["prompt"],
        tuple(json.loads(row["documents"])),
        tuple(json.loads(row["packs"] or "[]")),
        tuple(json.loads(row["tools"] or "[]")),
        row["conversation_id"],
        row["project_id"],
        row["model"],
        bool(row["local_only"]),
        bool(row["per_run"]),
        row["catch_up"],
        row["overlap"],
        row["max_per_hour"],
        row["max_age_hours"],
        bool(row["include_self"]),
        bool(row["rerun_interrupted"]),
        row["attempts"],
        bool(row["announce"]),
        bool(row["in_history"]),
        row["mode"],
        row["keep_days"],
        row["keep_hours"],
        row["keep_last"],
        parse_iso(row["reached_at"]) if row["reached_at"] else None,
        parse_iso(row["next_at"]) if row["next_at"] else None,
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


Runner = Callable[
    [Automation, int, datetime, list[StoredEvent]], Awaitable[str]
]  # (automation, run id, due, events) -> answer
Closer = Callable[
    [Automation, Any], Awaitable[None]
]  # (the automation, the waiting run's row): a turn that finishes it
Waiting = Callable[[str], int]  # requests waiting in a conversation

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "timezone": None,
    "action": "run",
    "prompt": "",
    "documents": None,
    "packs": None,
    "tools": None,
    "model": "",
    "local_only": False,
    "per_run": None,  # None: by its trigger (see `_defaults_for`)
    "catch_up": None,
    "overlap": None,
    "max_per_hour": 20,
    "max_age_hours": 0,
    "include_self": False,
    "rerun_interrupted": None,
    "attempts": MAX_ATTEMPTS,
    "announce": True,
    "in_history": False,
    "keep_days": None,
    "keep_hours": None,
    "keep_last": None,
}


def defaults_for(root: dict) -> dict[str, Any]:
    """What an automation does when it is not told, by its trigger. One that waits for events of a source is what a hook was (a
    conversation for each run, every run queued behind the last, every event caught up, a run a stop cut not repeated); one that
    only has instants is what a schedule was (one shared conversation, a run skipped while the last goes on, nothing caught up
    but what is asked for, a run a stop cut run again)."""
    outside = bool(trees.kinds_of(root))
    return {
        "per_run": outside,
        "catch_up": "all" if outside else "skip",
        "overlap": "queue" if outside else "skip",
        "rerun_interrupted": not outside,
    }


class Automations(DueLoop):
    name = "automations"

    def __init__(
        self,
        db: Database,
        events: Events,
        home_zone: str,
        clock: Callable[[], datetime] = utcnow,
        projects: Any = None,
        tool_names: Callable[[], frozenset[str]] = lambda: frozenset(),
    ):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.home_zone = home_zone
        self.projects = projects
        self.tool_names = tool_names
        # the kinds of the event sources that exist (automations/sources.py keeps it)
        self.custom: dict[str, EventKind] = {}
        self.runner: Runner | None = None
        self.closer: Closer | None = None
        self.waiting: Waiting = lambda conversation_id: 0
        self.late: Callable[[str, float], None] | None = None
        self.late_after = 300.0
        self.alert: Callable[[Automation, str], None] | None = None
        self.remind_at = time(9, 0)
        self.check_packs: Callable[[list[str]], list[str]] = lambda ids: list(ids)
        self._running: dict[int, asyncio.Task] = {}
        self._instant_runs: dict[int, bool] = {}  # running automation -> started by an instant (its own pool of places)
        self._closing: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @property
    def firing(self) -> bool:
        return bool(self._running) or bool(self._closing)

    def local_now(self, zone_name: str) -> datetime:
        """The clock's moment in a zone (the services' own, so tests and the loop agree)."""
        return self._clock().astimezone(zone(zone_name))

    def zone_name(self, automation: Automation) -> str:
        return automation.timezone or self.home_zone

    def tz(self, automation: Automation):
        return zone(self.zone_name(automation))

    def kind(self, name: str) -> EventKind | None:
        """A kind of event that exists: the built-in ones, and the sources of events you made (one that was revoked is none)."""
        return KINDS.get(name) or self.custom.get(name)

    def tools_for(self, automation: Automation, events: list[StoredEvent]) -> tuple[str, ...]:
        """The only tools a run may use; () means every tool of the packs it has. Its list, else its packs; and when it lists none,
        those of the trigger that started the run (the read tools of the events' kinds, and `notify`: an instant has `notify`
        alone), never more than that trigger."""
        if automation.action != "run":
            return ()
        if automation.tools:
            return automation.tools
        if automation.packs:
            return ()
        found: list[str] = []
        for name in dict.fromkeys(event.kind for event in events):
            kind = self.kind(name)
            found += kind.read_tools if kind else ()
        return (*dict.fromkeys(found), NOTIFY)

    def is_tainted(self, events: list[StoredEvent]) -> bool:
        """A run is tainted for what started it: an event whose kind carries what a stranger wrote (a source you trust does not;
        a kind nobody knows any more, a source that was revoked, is taken as untrusted). An instant is not."""
        return any((kind := self.kind(event.kind)) is None or kind.tainted for event in events)

    def template(self, name: str) -> str:
        kind = self.kind(name)
        return kind.template if kind else "{title}"

    def wants(self, kind: str) -> bool:
        """Is any enabled automation waiting for this kind of event? (A source need not look if none is.)"""
        return (
            self.db.one("SELECT 1 FROM automations WHERE enabled = 1 AND kinds LIKE ? ESCAPE '\\'", (_like(kind),))
            is not None
        )

    def waiting_since(self, kind: str) -> str | None:
        """Since when an automation waits for this kind of event: the earliest of the enabled ones, as last made or changed
        (turning one on is a change). None if none does."""
        row = self.db.one(
            "SELECT MIN(updated_at) AS at FROM automations WHERE enabled = 1 AND kinds LIKE ? ESCAPE '\\'",
            (_like(kind),),
        )
        return row["at"]

    def find(self, automation_id: int) -> Automation | None:
        row = self.db.one("SELECT * FROM automations WHERE id = ?", (automation_id,))
        return _automation(row) if row else None

    def get(self, automation_id: int) -> Automation:
        found = self.find(automation_id)
        if found is None:
            raise AutomationError(f"No automation {automation_id}.")
        return found

    def by_name(self, name: str) -> Automation | None:
        row = self.db.one("SELECT * FROM automations WHERE name = ?", (name,))
        return _automation(row) if row else None

    def listing(self) -> list[Automation]:
        return [_automation(row) for row in self.db.all("SELECT * FROM automations ORDER BY id")]

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    @staticmethod
    def _name(name: Any) -> str:
        name = " ".join(str(name or "").split())
        if not name or len(name) > MAX_NAME:
            raise AutomationError(f"An automation needs a name of 1 to {MAX_NAME} characters.")
        return name

    @staticmethod
    def _documents(given: list[dict] | None, kept: tuple[dict, ...] = ()) -> list[dict]:
        old = {doc["name"]: doc for doc in kept}
        found = []
        for item in given or []:
            name = str(item.get("name") or "").strip()[:200]
            if not name:
                raise AutomationError("A document needs a name.")
            text = item.get("text")
            if text is None:
                if name not in old:
                    raise AutomationError(f"The document {name} has no text.")
                text = old[name]["text"]
            found.append({"name": name, "text": str(text)})
        if sum(len(doc["text"]) for doc in found) > MAX_DOCUMENTS_CHARS:
            raise AutomationError(f"The documents are too long: at most {MAX_DOCUMENTS_CHARS:,} characters in all.")
        return found

    def _tools(self, raw: Any) -> list[str]:
        if raw in (None, ""):
            return []
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise AutomationError("tools is a list of tool names or globs (mail_*).")
        found = list(dict.fromkeys(item.strip() for item in raw if item.strip()))
        if len(found) > 40:
            raise AutomationError("At most 40 tools.")
        known = self.tool_names()
        for item in found:
            if len(item) > 100 or not any(fnmatch.fnmatchcase(name, item) for name in known):
                raise AutomationError(f"{item!r} matches no tool of Clara's.")
        return found

    def _project(self, project: Any) -> int | None:
        if project in (None, "none", 0):
            return None
        if not isinstance(project, int) or isinstance(project, bool) or self.projects.get(project) is None:
            raise AutomationError(f"No project {project}.")
        return project

    def _zone(self, timezone: Any) -> tuple[str, Any]:
        try:
            return zone_of(timezone or None, self.home_zone)
        except MomentError as error:
            raise AutomationError(str(error)) from None

    def _tree(self, raw: Any, tz, old: dict | None = None) -> dict:
        """The trigger checked and put in its stored form. A date already past is refused unless the old trigger had it."""
        try:
            root = trees.normalize(raw, self.kind, tz)
        except trees.TreeError as error:
            raise AutomationError(str(error)) from None
        kept = {node["at"] for node in trees.instants_of(old) if node["type"] == "once"} if old else set()
        for node in trees.instants_of(root):
            if node["type"] == "once" and node["at"] not in kept and trees.once_moment(node, tz) <= self._clock():
                raise AutomationError(f"That moment is already past: {node['at'].replace('T', ' ')}.")
        return root

    def _normal(self, name: Any, root: dict, zone_name: str, values: dict) -> dict:
        """Every field checked and put in the form it is stored in."""
        action = values["action"]
        if action not in ACTIONS:
            raise AutomationError(f"action must be one of {', '.join(ACTIONS)}.")
        prompt = str(values["prompt"] or "").strip()
        docs = values["documents"] or []
        if action == "run" and not prompt and not docs:
            raise AutomationError("An automation that runs Clara needs a prompt: what to do when it starts.")
        if len(prompt) > (MAX_PROMPT if action == "run" else MAX_TEMPLATE):
            raise AutomationError(f"The {'prompt' if action == 'run' else 'text'} is too long.")
        tools = self._tools(values["tools"])
        packs = values["packs"] or []
        try:
            packs = self.check_packs(list(packs))
        except ValueError as error:
            raise AutomationError(str(error)) from None
        if action == "notify" and (tools or packs or docs):
            raise AutomationError("A notification uses no tools, no packs and no documents.")
        if action == "notify" and values["in_history"]:
            raise AutomationError("A notification has no conversation: it cannot be shown in the history.")
        if values["catch_up"] not in CATCH_UPS:
            raise AutomationError(f"catch_up must be one of {', '.join(CATCH_UPS)}.")
        if values["catch_up"] == "digest" and not trees.kinds_of(root):
            raise AutomationError(
                "catch_up digest takes the events found late together: it needs an event in the trigger."
            )
        if values["overlap"] not in OVERLAPS:
            raise AutomationError(f"overlap must be one of: {', '.join(OVERLAPS)}.")
        for field, low, high in (("max_per_hour", 1, 600), ("max_age_hours", 0, 8760), ("attempts", 1, MAX_ATTEMPTS)):
            if (
                not isinstance(values[field], int)
                or isinstance(values[field], bool)
                or not low <= values[field] <= high
            ):
                raise AutomationError(f"{field} must be a whole number from {low} to {high}.")
        model = str(values["model"] or "").strip()
        if len(model) > 200 or (model and ":" not in model):
            raise AutomationError("A model is written provider:model.")
        try:
            keep = check_own(values["keep_days"], values["keep_hours"], values["keep_last"])
        except RetentionError as error:
            raise AutomationError(str(error)) from None
        return {
            "name": self._name(name),
            "tree": json.dumps(root),
            "kinds": "," + ",".join(trees.kinds_of(root)) + ",",
            "timezone": zone_name,
            "action": action,
            "prompt": prompt,
            "documents": json.dumps(docs, ensure_ascii=False),
            "packs": json.dumps(packs),
            "tools": json.dumps(tools),
            "model": model,
            "catch_up": values["catch_up"],
            "overlap": values["overlap"],
            "max_per_hour": values["max_per_hour"],
            "max_age_hours": values["max_age_hours"],
            "attempts": values["attempts"],
            "keep_days": keep.days,
            "keep_hours": keep.hours,
            "keep_last": keep.last,
            **{
                field: int(bool(values[field]))
                for field in (
                    "enabled",
                    "local_only",
                    "per_run",
                    "include_self",
                    "rerun_interrupted",
                    "announce",
                    "in_history",
                )
            },
        }

    def _instants(self, root: dict, tz, reached: datetime, enabled: bool) -> str | None:
        found = trees.next_instant(root, reached, tz) if enabled else None
        return iso(found) if found else None

    def _fresh(self, name: Any, when: Any, project: Any, fields: dict) -> dict:
        """The row `create` would insert, or AutomationError."""
        unknown = set(fields) - set(DEFAULTS)
        if unknown:
            raise AutomationError(f"Unknown fields: {', '.join(sorted(unknown))}.")
        if int(self.db.value("SELECT COUNT(*) FROM automations", (), 0)) >= MAX_AUTOMATIONS:
            raise AutomationError(f"At most {MAX_AUTOMATIONS} automations: delete some first.")
        zone_name, tz = self._zone(fields.get("timezone"))
        root = self._tree(when, tz)
        given = {key: value for key, value in fields.items() if value is not None}
        if "documents" in given:
            given["documents"] = self._documents(given["documents"])
        values = self._normal(name, root, zone_name, {**DEFAULTS, **defaults_for(root), **given})
        if self.by_name(values["name"]) is not None:
            raise AutomationError(f"An automation named {values['name']!r} exists already.")
        values["project_id"] = self._project(project)
        now = self._clock()
        values["reached_at"] = iso(now)
        values["next_at"] = self._instants(root, tz, now, bool(values["enabled"]))
        return values

    def check_new(self, name: Any, when: Any, *, project: Any = "none", **fields: Any) -> dict:
        """Raise AutomationError when `create` would refuse these; nothing is made. Returns the trigger as it would be stored."""
        return json.loads(self._fresh(name, when, project, fields)["tree"])

    def tree_of(self, when: Any, timezone: Any = None, old: dict | None = None) -> dict:
        """A trigger as it would be stored, or AutomationError (what the card for a new or changed automation is made from)."""
        return self._tree(when, self._zone(timezone or None)[1], old)

    def create(self, name: Any, when: Any, *, project: Any = "none", **fields: Any) -> Automation:
        values = self._fresh(name, when, project, fields)
        now = now_iso()
        columns = list(values)
        automation_id = self.db.insert(
            f"INSERT INTO automations ({', '.join(columns)}, created_at, updated_at)"
            f" VALUES ({', '.join('?' * len(columns))}, ?, ?)",
            (*values.values(), now, now),
        )
        self.wake()
        return self.get(automation_id)

    def update(self, automation_id: int, **fields: Any) -> Automation:
        current = self.get(automation_id)
        project = fields.pop("project", "keep")
        when = fields.pop("when", None)
        name = fields.pop("name", None)
        unknown = set(fields) - set(DEFAULTS)
        if unknown:
            raise AutomationError(f"Unknown fields: {', '.join(sorted(unknown))}.")
        stored_zone = fields.get("timezone") or current.timezone  # '' follows the server's zone, as it did
        tz = self._zone(stored_zone)[1]
        root = self._tree(when, tz, current.tree) if when is not None else current.tree
        merged: dict[str, Any] = {
            "enabled": current.enabled,
            "action": current.action,
            "prompt": current.prompt,
            "documents": list(current.documents),
            "packs": list(current.packs),
            "tools": list(current.tools),
            "model": current.model,
            "local_only": current.local_only,
            "per_run": current.per_run,
            "catch_up": current.catch_up,
            "overlap": current.overlap,
            "max_per_hour": current.max_per_hour,
            "max_age_hours": current.max_age_hours,
            "include_self": current.include_self,
            "rerun_interrupted": current.rerun_interrupted,
            "attempts": current.attempts,
            "announce": current.announce,
            "in_history": current.in_history,
            "keep_days": current.keep_days,
            "keep_hours": current.keep_hours,
            "keep_last": current.keep_last,
        }
        merged.update({key: value for key, value in fields.items() if value is not None and key != "timezone"})
        if "documents" in fields and fields["documents"] is not None:
            merged["documents"] = self._documents(fields["documents"], current.documents)
        if any(key in fields for key in KEEP_FIELDS):  # null: follow the global limits again
            kept = merged_own(Own(current.keep_days, current.keep_hours, current.keep_last), fields)
            merged.update(keep_days=kept.days, keep_hours=kept.hours, keep_last=kept.last)
        values = self._normal(name if name is not None else current.name, root, stored_zone, merged)
        other = self.by_name(values["name"])
        if (
            values["name"] != current.name and other is not None
        ):  # two schedules may have shared a name before they were converted
            raise AutomationError(f"An automation named {values['name']!r} exists already.")
        values["project_id"] = current.project_id if project == "keep" else self._project(project)
        now = self._clock()
        if root != current.tree or stored_zone != current.timezone or (values["enabled"] and not current.enabled):
            values["reached_at"] = iso(now)  # what is looked at starts from now: a change never replays the past
        reached = parse_iso(values["reached_at"]) if "reached_at" in values else (current.reached_at or now)
        values["next_at"] = self._instants(root, tz, reached, bool(values["enabled"]))
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.db.transaction():
            self.db.execute(
                f"UPDATE automations SET {assignments}, updated_at = ? WHERE id = ?",
                (*values.values(), now_iso(), automation_id),
            )
            if (
                current.enabled and not values["enabled"]
            ):  # turned off: what waited does not wait for its turn to come back
                self._skip_queued(automation_id, "The automation was turned off.")
        self.wake()
        return self.get(automation_id)

    def delete(self, automation_id: int) -> bool:
        task = self._running.get(automation_id)
        if task is not None:
            task.cancel()
        deleted = self.db.execute("DELETE FROM automations WHERE id = ?", (automation_id,)).rowcount > 0
        if deleted:
            self.wake()
        return deleted

    def _skip_queued(self, automation_id: int, note: str) -> None:
        self.db.execute(
            "UPDATE automation_runs SET status = 'skipped', note = ?, finished_at = ?"
            " WHERE automation_id = ? AND status = 'queued'",
            (note, now_iso(), automation_id),
        )

    # ------------------------------------------------------------------
    # Describing
    # ------------------------------------------------------------------
    def effective_tools(self, automation: Automation) -> tuple[str, ...]:
        """What a run may use as the automation stands: its list, else the read tools of the events it waits for and `notify`."""
        if automation.action != "run":
            return ()
        if automation.tools:
            return automation.tools
        if automation.packs:
            return ()
        found: list[str] = []
        for name in automation.kinds:
            kind = self.kind(name)
            found += kind.read_tools if kind else ()
        return (*dict.fromkeys(found), NOTIFY)

    def words(self, automation: Automation) -> str:
        return trees.words(automation.tree, self.kind)

    def describe(self, automation: Automation) -> dict:
        last = self.db.one(
            "SELECT * FROM automation_runs WHERE automation_id = ? AND status NOT IN ('queued', 'skipped', 'missed', 'expired')"
            " ORDER BY id DESC LIMIT 1",
            (automation.id,),
        )
        waiting = self.waiting_run(automation.id)
        tz = self.tz(automation)

        def count(where: str) -> int:
            return int(
                self.db.value(
                    f"SELECT COUNT(*) FROM automation_runs WHERE automation_id = ? AND {where}", (automation.id,), 0
                )
            )

        return {
            "id": automation.id,
            "name": automation.name,
            "enabled": automation.enabled,
            "when": automation.tree,
            "when_words": self.words(automation),
            "kinds": list(automation.kinds),
            # the sources it waits for that were revoked
            "missing_kinds": [kind for kind in automation.kinds if self.kind(kind) is None],
            "timezone": self.zone_name(automation),
            "action": automation.action,
            "prompt": automation.prompt,
            "documents": [{"name": d["name"], "chars": len(d["text"])} for d in automation.documents],
            "packs": list(automation.packs),
            "tools": list(automation.tools),
            "effective_tools": list(self.effective_tools(automation)),
            "conversation": automation.conversation_id,
            "project": automation.project_id,
            "model": automation.model,
            "local_only": automation.local_only,
            "per_run": automation.per_run,
            "catch_up": automation.catch_up,
            "overlap": automation.overlap,
            "max_per_hour": automation.max_per_hour,
            "max_age_hours": automation.max_age_hours,
            "include_self": automation.include_self,
            "rerun_interrupted": automation.rerun_interrupted,
            "attempts": automation.attempts,
            "announce": automation.announce,
            "in_history": automation.in_history,
            "mode": automation.mode,
            "keep_days": automation.keep_days,
            "keep_hours": automation.keep_hours,
            "keep_last": automation.keep_last,
            "next_at": iso(automation.next_at) if automation.next_at else None,
            "next_local": local_text(automation.next_at, tz) if automation.next_at else None,
            "running": automation.id in self._running,
            "waiting_run": {
                "id": waiting["id"],
                "conversation": waiting["conversation_id"],
                "since": waiting["waiting_since"],
            }
            if waiting
            else None,
            "queued": count("status = 'queued'"),
            "runs": count("status NOT IN ('missed', 'queued', 'skipped', 'expired')"),
            "last": self._run_view(last) if last else None,
            "created_at": automation.created_at,
            "updated_at": automation.updated_at,
        }

    def kinds_listing(self) -> list[dict]:
        return [
            {
                "kind": kind.kind,
                "label": kind.label,
                "noun": kind.noun,
                "fields": kind.fields,
                "read_tools": list(kind.read_tools),
                "tainted": kind.tainted,
                "custom": kind.kind in self.custom,
            }
            for kind in (*KINDS.values(), *self.custom.values())
        ]

    # ------------------------------------------------------------------
    # Events in
    # ------------------------------------------------------------------
    def _arrival(self, event: SourceEvent) -> trees.Arrival:
        return trees.Arrival(
            event.kind, parse_iso(event.occurred_at) if event.occurred_at else self._clock(), event.data
        )

    def judge(
        self, automation: Automation, event: SourceEvent, at: datetime | None = None
    ) -> tuple[trees.Verdict, str]:
        """The tree's verdict for an event and, when it does not hold, why in one phrase. An event of Clara's own account is
        refused unless the automation takes those; one the filters accept and only the time refuses says so."""
        arrival = self._arrival(event)
        if at is not None:
            arrival = trees.Arrival(arrival.kind, at, arrival.data)
        tz = self.tz(automation)
        verdict = trees.evaluate(automation.tree, arrival, tz)
        if event.self_caused and not automation.include_self:
            return (
                verdict,
                "The event was caused by Clara's own account, and the automation does not take those (include_self).",
            )
        if verdict.ok:
            return verdict, ""
        if trees.evaluate(automation.tree, arrival, tz, ignore_time=True).ok:
            return verdict, "It is outside the time conditions: " + "; ".join(
                item.why for item in verdict.failed_times()
            )
        if event.kind not in automation.kinds:
            return (
                verdict,
                f"It is a {event.kind} event; the automation waits for {', '.join(automation.kinds) or 'instants'}.",
            )
        return verdict, "The trigger does not match."

    def ingest(self, events: list[SourceEvent], also: Callable[[], None] | None = None) -> int:
        """Store the events that are new, plan the runs of the automations that want them, and call `also` (a source moving its
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
            for automation in self.listing():
                if not automation.enabled or not automation.kinds:
                    continue
                mine, refused = [], []
                for event in stored:
                    if event.kind not in automation.kinds or (event.self_caused and not automation.include_self):
                        continue
                    verdict, why = self.judge(automation, event)
                    if verdict.ok:
                        mine.append(event)
                    elif why.startswith("It is outside the time"):  # the time refused it: recorded, started nothing
                        refused.append((event, why))
                for event, why in refused:
                    self._insert(automation.id, event.occurred_at, [event.id], "skipped", why)
                if mine:
                    count, overflow = self._plan(automation, mine, now)
                    planned += count
                    if overflow:
                        overflowed.append(automation)
            if also is not None:
                also()
        for automation in overflowed:
            if self.alert is not None:
                self.alert(
                    automation,
                    f"More than {MAX_QUEUED} runs of “{automation.name}” are waiting: the newest events were recorded but not run.",
                )
        if planned:
            self.wake()
        return planned

    def _insert(
        self, automation_id: int, due: str, event_ids: list[int], status: str, note: str = "", summary: str = ""
    ) -> int:
        now = now_iso()
        return self.db.insert(
            "INSERT INTO automation_runs (automation_id, due_at, event_ids, status, note, summary, queued_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                automation_id,
                due,
                json.dumps(event_ids),
                status,
                note,
                summary,
                now,
                now if status != "queued" else None,
            ),
        )

    def _queued_count(self, automation_id: int) -> int:
        return int(
            self.db.value(
                "SELECT COUNT(*) FROM automation_runs WHERE automation_id = ? AND status = 'queued'",
                (automation_id,),
                0,
            )
        )

    def _admit(self, automation: Automation, due: str, event_ids: list[int], *, backlog: bool = False) -> str:
        """Plan one run that is due: queued, or skipped as `overlap` says. Returns what it became."""
        busy = self.busy(automation.id) or self._queued_count(automation.id) > 0
        if busy and automation.overlap == "skip" and not backlog:  # a backlog waits its turn, whatever `overlap` says
            self._insert(automation.id, due, event_ids, "skipped", SKIPPED_BUSY)
            return "skipped"
        limit = MAX_QUEUED if event_ids else MAX_BACKLOG
        if self._queued_count(automation.id) >= limit:
            self._insert(
                automation.id, due, event_ids, "skipped", f"More than {limit} runs were waiting: this one was not run."
            )
            return "overflow"
        self._insert(automation.id, due, event_ids, "queued")
        if busy and automation.overlap == "replace" and not backlog:
            self.close_waiting(automation.id)
        return "queued"

    def _plan(self, automation: Automation, events: list[StoredEvent], now: datetime) -> tuple[int, bool]:
        """The runs these events make for this automation. Returns (runs queued, whether the queue was full)."""
        events = sorted(events, key=lambda event: (parse_iso(event.occurred_at), event.id))
        stale = [event for event in events if now - parse_iso(event.occurred_at) > STALE_AFTER]
        fresh = [event for event in events if event not in stale]
        groups: list[list[StoredEvent]] = []
        skipped: list[tuple[list[StoredEvent], str]] = []
        if stale:
            if automation.catch_up == "all":
                groups += [[event] for event in stale]
            elif automation.catch_up == "digest":
                groups.append(stale)
            elif automation.catch_up == "latest":
                groups.append(stale[-1:])
                skipped += [
                    ([event], "Found late (after a stop): only the newest of the backlog runs.") for event in stale[:-1]
                ]
            else:
                skipped += [([event], "Found late (after a stop): this automation skips a backlog.") for event in stale]
        groups += [[event] for event in fresh]
        queued, overflow = 0, False
        for group in groups:
            result = self._admit(
                automation,
                group[-1].occurred_at,
                [event.id for event in group],
                backlog=len(group) > 1 or group[0] in stale,
            )
            queued += result == "queued"
            overflow = overflow or result == "overflow"
        for group, note in skipped:
            self._insert(automation.id, group[-1].occurred_at, [event.id for event in group], "skipped", note)
        return queued, overflow

    # ------------------------------------------------------------------
    # Instants
    # ------------------------------------------------------------------
    def _due_instants(self, automation: Automation, now: datetime) -> None:
        """The instants of its cron and date nodes that came since it last looked, planned as its trigger and `catch_up` say; the
        place it has reached moves in the same transaction."""
        tz = self.tz(automation)
        reached = automation.reached_at or now
        dues, more = trees.instants_between(automation.tree, reached, now, tz, MAX_BACKLOG)
        following = trees.next_instant(automation.tree, now, tz)
        with self.db.transaction():
            self.db.execute(
                "UPDATE automations SET reached_at = ?, next_at = ? WHERE id = ?",
                (iso(now), iso(following) if following else None, automation.id),
            )
            held = [due for due in dues if trees.evaluate(automation.tree, trees.Arrival(trees.TIME, due), tz).ok]
            old = [due for due in held if (now - due).total_seconds() > LATE_SKIP_SECONDS]
            fresh = [due for due in held if due not in old]
            if automation.catch_up == "all":
                skipped, caught_up = [], old
            elif automation.catch_up in ("latest", "digest") and not fresh:
                skipped, caught_up = old[:-1], old[-1:]
            else:
                skipped, caught_up = old, []
            for due in skipped:
                self._record(
                    automation, due, "missed", f"The server was not running at {iso(due)}: this run was skipped."
                )
            for due in caught_up:  # a backlog waits its turn, whatever `overlap` says
                self._admit(automation, iso(due), [], backlog=True)
            for due in fresh:
                if self._admit(automation, iso(due), []) == "skipped":
                    self._tell(
                        automation, self._last_run(automation.id), "skipped", SKIPPED_BUSY, 0, origin="schedules"
                    )
            if more:
                self._record(
                    automation, now, "missed", f"{more} more instants were not looked at (too many at once): skipped."
                )

    def _last_run(self, automation_id: int) -> int:
        return int(self.db.value("SELECT MAX(id) FROM automation_runs WHERE automation_id = ?", (automation_id,), 0))

    def _record(self, automation: Automation, due: datetime, status: str, note: str) -> None:
        """A run of an instant that did not take place, recorded and told (as a schedule's always was)."""
        run_id = self._insert(automation.id, iso(due), [], status, note)
        self._tell(automation, run_id, status, note, 0, origin="schedules")

    # ------------------------------------------------------------------
    # Working off the queue
    # ------------------------------------------------------------------
    def _started_since(self, automation_id: int, since: datetime) -> list[str]:
        return [
            row[0]
            for row in self.db.all(
                "SELECT started_at FROM automation_runs WHERE automation_id = ? AND started_at > ? ORDER BY started_at",
                (automation_id, iso(since)),
            )
        ]

    def _expire(self, now: datetime) -> None:
        for automation in self.listing():
            if not automation.max_age_hours:
                continue
            limit = now - timedelta(hours=automation.max_age_hours)
            for row in self.db.all(
                "SELECT * FROM automation_runs WHERE automation_id = ? AND status = 'queued' ORDER BY id",
                (automation.id,),
            ):
                events = self.events_of(row)
                newest = parse_iso(events[-1].occurred_at) if events else parse_iso(row["due_at"])
                if newest < limit:  # even its newest event is too old
                    self.db.execute(
                        "UPDATE automation_runs SET status = 'expired', note = ?, finished_at = ? WHERE id = ?",
                        (f"Older than {automation.max_age_hours} hours when its turn came.", iso(now), row["id"]),
                    )

    def fire_due(self) -> int:
        if self.stopping:
            return 0
        now = self._clock()
        self._expire(now)
        for row in self.db.all(
            "SELECT * FROM automations WHERE enabled = 1 AND next_at IS NOT NULL AND next_at <= ? ORDER BY next_at",
            (iso(now),),
        ):
            self._due_instants(_automation(row), now)
        started = self._start_queued(now)
        self._remind(now)
        return started

    def _start_queued(self, now: datetime) -> int:
        started = 0
        seen: set[int] = set()
        for row in self.db.all(
            "SELECT r.* FROM automation_runs r JOIN automations a ON a.id = r.automation_id"
            " WHERE r.status = 'queued' AND a.enabled = 1 ORDER BY r.id"
        ):
            automation_id = row["automation_id"]
            if automation_id in seen:
                continue
            seen.add(automation_id)
            if self.busy(automation_id):
                continue
            instant = row["event_ids"] == "[]"
            if not self._has_slot(instant):
                if self._has_slot(not instant):
                    continue  # this pool is full, the other may still take a later run
                break
            automation = self.get(automation_id)
            if len(self._started_since(automation_id, now - timedelta(hours=1))) >= automation.max_per_hour:
                continue
            self._start(automation, row)
            started += 1
        return started

    def _remind_instant(self, since: datetime, tz) -> datetime:
        """The next CLARA_WAITING_REMIND_AT after three hours of waiting."""
        after = (since + REMIND_AFTER).astimezone(tz)
        candidate = datetime.combine(after.date(), self.remind_at)
        if resolve_local(candidate, tz) <= since + REMIND_AFTER:
            candidate += timedelta(days=1)
        return resolve_local(candidate, tz)

    def next_due(self) -> datetime | None:
        soon: list[datetime] = []
        value = self.db.value("SELECT MIN(next_at) FROM automations WHERE enabled = 1 AND next_at IS NOT NULL")
        if value:
            soon.append(parse_iso(value))
        now = self._clock()
        if self._has_slot(True) or self._has_slot(False):  # else nothing can start until a run ends, and its end wakes the loop
            seen: set[int] = set()
            for row in self.db.all(
                "SELECT r.automation_id, r.event_ids FROM automation_runs r JOIN automations a ON a.id = r.automation_id"
                " WHERE r.status = 'queued' AND a.enabled = 1 ORDER BY r.id"
            ):
                if row["automation_id"] in seen:
                    continue
                seen.add(row["automation_id"])
                if not self._has_slot(row["event_ids"] == "[]"):
                    continue
                if self.busy(row["automation_id"]):
                    continue  # its end wakes the loop
                automation = self.get(row["automation_id"])
                recent = self._started_since(automation.id, now - timedelta(hours=1))
                if len(recent) < automation.max_per_hour:
                    soon.append(now)
                else:
                    soon.append(parse_iso(recent[len(recent) - automation.max_per_hour]) + timedelta(hours=1))
        for run in self.db.all(
            "SELECT r.*, a.timezone FROM automation_runs r JOIN automations a ON a.id = r.automation_id"
            " WHERE r.status = 'waiting' AND r.reminded_at IS NULL AND r.waiting_since IS NOT NULL"
        ):
            soon.append(self._remind_instant(parse_iso(run["waiting_since"]), zone(run["timezone"] or self.home_zone)))
        return min(soon) if soon else None

    def _has_slot(self, instant: bool) -> bool:
        """Is a place free in the pool of runs started by an instant, or in the one of runs started by events?"""
        taken = sum(1 for is_instant in self._instant_runs.values() if is_instant == instant)
        return taken < (MAX_PARALLEL_INSTANTS if instant else MAX_PARALLEL)

    def busy(self, automation_id: int) -> bool:
        """A run of this automation is going, being closed, or waiting for an answer."""
        return (
            automation_id in self._running
            or automation_id in self._closing
            or self.waiting_run(automation_id) is not None
        )

    def run_now(self, automation_id: int) -> Automation:
        automation = self.get(automation_id)
        if automation.id in self._running:
            raise AutomationError("It is running already.")
        if self.waiting_run(automation.id) is not None or automation.id in self._closing:
            raise AutomationError("It is waiting for your answer: answer it, or close it first.")
        if self.stopping:
            raise AutomationError("The server is stopping.")
        now = now_iso()
        run_id = self.db.insert(
            "INSERT INTO automation_runs (automation_id, due_at, event_ids, status, queued_at) VALUES (?, ?, '[]', 'queued', ?)",
            (automation.id, now, now),
        )
        self._start(automation, self.db.one("SELECT * FROM automation_runs WHERE id = ?", (run_id,)))
        return automation

    def _start(self, automation: Automation, row) -> None:
        now = self._clock()
        late = 0 if json.loads(row["event_ids"]) else max(0, int((now - parse_iso(row["due_at"])).total_seconds()))
        self.db.execute(
            "UPDATE automation_runs SET status = 'running', started_at = ?, late_seconds = ? WHERE id = ?",
            (iso(now), late, row["id"]),
        )
        if self.late_after < late <= LATE_SKIP_SECONDS and self.late is not None:
            self.late(f"automation “{automation.name}”", late)
        task = asyncio.create_task(self._execute(automation, row["id"]))
        self._running[automation.id] = task
        self._instant_runs[automation.id] = not json.loads(row["event_ids"])
        task.add_done_callback(
            lambda _: (self._running.pop(automation.id, None), self._instant_runs.pop(automation.id, None), self.wake())
        )

    def events_of(self, row) -> list[StoredEvent]:
        ids = json.loads(row["event_ids"])
        if not ids:
            return []
        found = self.db.all(
            f"SELECT * FROM trigger_events WHERE id IN ({', '.join('?' * len(ids))}) ORDER BY occurred_at, id", ids
        )
        return [_stored(event) for event in found]

    @staticmethod
    def origin(row) -> str:
        """Where a run came from, for the notice's category: events (`triggers`, a hook's) or an instant (`schedules`)."""
        return "triggers" if json.loads(row["event_ids"]) else "schedules"

    async def pause_before_retry(self, attempt: int) -> None:
        """Wait before the attempt after `attempt`. A server that stops meanwhile ends the run: it runs again at the next start."""
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

    async def _execute(self, automation: Automation, run_id: int) -> None:
        row = self.db.one("SELECT * FROM automation_runs WHERE id = ?", (run_id,))
        events = self.events_of(row) if row else []
        origin = self.origin(row) if row else "schedules"
        status, summary, reply = "ok", "", ""
        try:
            if row is None or (json.loads(row["event_ids"]) and not events):
                raise AutomationError("The events of this run are gone.")
            if automation.action == "notify":
                summary = self._notify(automation, events)
            else:
                if self.runner is None:
                    raise AutomationError("Clara cannot run it now.")
                reply = await self.runner(automation, run_id, parse_iso(row["due_at"]), events)
                summary = summary_of(reply)
        except Stopping:
            self._cut(automation, run_id)
            return
        except Exception as error:
            log.warning("automation %s: the run failed (%s: %s)", automation.id, type(error).__name__, error)
            status, summary = "failed", f"Clara could not run it: {error or type(error).__name__}"
        current = self.db.one("SELECT status, conversation_id FROM automation_runs WHERE id = ?", (run_id,))
        if current is None or current["status"] in ("waiting", "ok", "closed"):
            self.wake()  # it asked and waits, or finish_run ended it: nothing more to record
            return
        automation = self.find(automation.id) or automation
        waiting = self.waiting(current["conversation_id"]) if current["conversation_id"] else 0
        self.db.execute(
            "UPDATE automation_runs SET status = ?, finished_at = ?, summary = ?, waiting = ? WHERE id = ?",
            (status, now_iso(), summary, waiting, run_id),
        )
        self._tell(automation, run_id, status, summary, waiting, reply, current["conversation_id"], origin=origin)
        self.wake()

    def _cut(self, automation: Automation, run_id: int) -> None:
        """A run the stop cut: started afresh if it had done nothing, else interrupted (or started afresh if you said so)."""
        row = self.db.one("SELECT conversation_id FROM automation_runs WHERE id = ?", (run_id,))
        began = self.did_something(row["conversation_id"] if row else None)
        if not began or automation.rerun_interrupted:
            self.db.execute(
                "UPDATE automation_runs SET status = 'queued', started_at = NULL, conversation_id = NULL WHERE id = ?",
                (run_id,),
            )
        else:
            self.db.execute(
                "UPDATE automation_runs SET status = 'interrupted', finished_at = ?, note = ? WHERE id = ?",
                (
                    now_iso(),
                    "The server stopped while it ran; it may have done part of its work, so it is not run again.",
                    run_id,
                ),
            )

    def recover(self) -> int:
        """At start: runs left `running` by a crash are put right as a stop would have."""
        rows = self.db.all("SELECT id, automation_id FROM automation_runs WHERE status = 'running'")
        for row in rows:
            automation = self.find(row["automation_id"])
            if automation is not None:
                self._cut(automation, row["id"])
        return len(rows)

    def _notify(self, automation: Automation, events: list[StoredEvent]) -> str:
        if not events:
            title, text = automation.name, automation.prompt or automation.name
        elif len(events) == 1:
            template = automation.prompt or self.template(events[0].kind)
            title, text = automation.name, fill(template, {"title": events[0].title, **events[0].data})
        else:
            template = automation.prompt or self.template(events[0].kind)
            title = f"{automation.name}: {len(events)} events"
            text = "\n".join(fill(template, {"title": event.title, **event.data}) for event in events[:10])
            text += f"\n…and {len(events) - 10} more." if len(events) > 10 else ""
        self.events.publish(
            "notification", title, text, source="triggers", targets=("web",) if automation.local_only else None
        )
        return summary_of(text)

    def _tell(
        self,
        automation: Automation,
        run_id: int,
        status: str,
        summary: str,
        waiting: int,
        reply: str = "",
        conversation: str | None = None,
        *,
        origin: str,
    ) -> None:
        """How a run ended: the short notification to every client, and when it lives in a forum thread the whole answer too."""
        if automation.action == "notify" or (status == "ok" and not automation.announce):
            return
        conversation = conversation or automation.conversation_id
        note = f"\n{waiting} request{'s' if waiting > 1 else ''} wait for your permission." if waiting else ""
        if status == "ok" and automation.in_history:
            delivery.say(
                self.events,
                f"Automation: {automation.name}",
                (reply.strip() or summary) + (f"\n{note}" if note else ""),
                conversation=conversation,
                source=origin,
                payload={"automation": automation.id, "run": run_id, "forum": True},
            )
        self.events.publish(
            "schedule",
            f"Automation: {automation.name}",
            summary + note,
            payload={"automation": automation.id, "run": run_id, "status": status, "waiting": waiting},
            source=origin,
            conversation_id=conversation,
            targets=("web",) if automation.local_only else None,
        )

    async def wait_running(self) -> None:
        await asyncio.gather(*self._running.values(), *self._closing.values(), return_exceptions=True)

    # ------------------------------------------------------------------
    # Waiting for an answer
    # ------------------------------------------------------------------
    def waiting_run(self, automation_id: int):
        return self.db.one(
            "SELECT * FROM automation_runs WHERE automation_id = ? AND status = 'waiting' ORDER BY id DESC LIMIT 1",
            (automation_id,),
        )

    def mark_waiting(self, run_id: int, question: str) -> None:
        """The run asked the person something: it waits (its turn goes on to its end, then the run stays `waiting`)."""
        self.db.execute(
            "UPDATE automation_runs SET status = 'waiting', waiting_since = COALESCE(waiting_since, ?), summary = ?"
            " WHERE id = ? AND status IN ('running', 'waiting')",
            (iso(self._clock()), summary_of(question), run_id),
        )

    def finish_run(self, run_id: int, summary: str, closed: bool = False) -> None:
        """The run is over (its tool `finish_run`, or the closing turn). One that waited ends with an `ask` event, which
        also takes the Discord thread away; the others end like any run, with a `schedule` event."""
        row = self.db.one("SELECT * FROM automation_runs WHERE id = ?", (run_id,))
        if row is None or row["status"] not in ("running", "waiting"):
            return
        status = "closed" if closed else "ok"
        text = summary_of(summary) if summary.strip() else "Finished."
        self.db.execute(
            "UPDATE automation_runs SET status = ?, finished_at = ?, summary = ? WHERE id = ?",
            (status, now_iso(), text, run_id),
        )
        automation = self.find(row["automation_id"])
        if automation is not None:
            if row["waiting_since"]:
                self.events.publish(
                    "ask",
                    automation.name,
                    text,
                    payload={
                        "conversation": row["conversation_id"],
                        "stage": "closed" if closed else "finished",
                        "run": run_id,
                        **({"forum": True} if automation.in_history else {}),
                    },
                    targets=("web",) if automation.local_only else ("web", "discord"),
                    source=self.origin(row),
                    conversation_id=row["conversation_id"],
                )
            else:
                self._tell(
                    automation, run_id, status, text, 0, conversation=row["conversation_id"], origin=self.origin(row)
                )
        self.wake()

    def close_waiting(self, automation_id: int) -> bool:
        """Close the waiting run of an automation from what it has (a turn of Clara's), in the background. False when there
        is none, or it is being closed."""
        run = self.waiting_run(automation_id)
        automation = self.find(automation_id)
        if run is None or automation is None or automation_id in self._closing or self.closer is None:
            return False
        task = asyncio.create_task(self._close(automation, run))
        self._closing[automation_id] = task
        task.add_done_callback(lambda _: (self._closing.pop(automation_id, None), self.wake()))
        return True

    async def _close(self, automation: Automation, run) -> None:
        try:
            assert self.closer is not None
            await self.closer(automation, run)
        except Stopping:
            return
        except Exception as error:
            log.warning(
                "automation %s: closing run %s failed (%s: %s)", automation.id, run["id"], type(error).__name__, error
            )
        current = self.db.one("SELECT * FROM automation_runs WHERE id = ?", (run["id"],))
        if current is not None and current["status"] == "waiting":  # Clara did not finish it: it is closed all the same
            self.finish_run(run["id"], "Closed without an answer.", closed=True)
        self.wake()

    def _remind(self, now: datetime) -> None:
        for run in self.db.all(
            "SELECT r.*, a.timezone, a.name, a.local_only, a.in_history FROM automation_runs r"
            " JOIN automations a ON a.id = r.automation_id"
            " WHERE r.status = 'waiting' AND r.reminded_at IS NULL AND r.waiting_since IS NOT NULL"
        ):
            if self._remind_instant(parse_iso(run["waiting_since"]), zone(run["timezone"] or self.home_zone)) > now:
                continue
            self.db.execute("UPDATE automation_runs SET reminded_at = ? WHERE id = ?", (iso(now), run["id"]))
            self.events.publish(
                "ask",
                run["name"],
                "Still waiting for your answer: answer in the conversation to finish.",
                payload={
                    "conversation": run["conversation_id"],
                    "stage": "reminder",
                    "run": run["id"],
                    **({"forum": True} if run["in_history"] else {}),
                },
                targets=("web",) if run["local_only"] else ("web", "discord"),
                source=self.origin(run),
                conversation_id=run["conversation_id"],
            )

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
        events = self.events_of(row)
        return {
            "id": row["id"],
            "automation": row["automation_id"],
            "status": row["status"],
            "note": row["note"],
            "summary": row["summary"],
            "due_at": row["due_at"],
            "queued_at": row["queued_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "late_seconds": row["late_seconds"],
            "waiting": row["waiting"],
            "waiting_since": row["waiting_since"],
            "conversation": row["conversation_id"],
            "events": [{"id": event.id, "title": event.title, "at": event.occurred_at} for event in events[:10]],
            "event_count": len(json.loads(row["event_ids"])),
        }

    def runs(self, automation_id: int, limit: int = 20) -> list[dict]:
        return [
            self._run_view(row)
            for row in self.db.all(
                "SELECT * FROM automation_runs WHERE automation_id = ? ORDER BY id DESC LIMIT ?", (automation_id, limit)
            )
        ]

    def test(self, automation: Automation, event: StoredEvent | SourceEvent | None, at: datetime | None = None) -> dict:
        """What would happen for this event (or, with none, at this moment), without doing it: whether the trigger holds, branch
        by branch and why, what the run would be told, what it could use, whether it would be tainted."""
        from .message import message_of

        tz = self.tz(automation)
        if event is None:
            moment = at or self._clock()
            verdict = trees.evaluate(automation.tree, trees.Arrival(trees.TIME, moment), tz)
            reason = "" if verdict.ok else "The trigger does not hold at this moment."
            shown, events = None, []
        else:
            verdict, reason = self.judge(automation, event, at)
            shown = event if isinstance(event, StoredEvent) else StoredEvent(**{**event.__dict__, "id": 0})
            events = [shown]
            moment = at or self._arrival(event).at
        wanted = verdict.ok and not (event is not None and event.self_caused and not automation.include_self)
        if automation.action == "notify":
            message = self._notify_text(automation, shown)
        else:
            message = message_of(automation, events)
        return {
            "matches": wanted,
            "why_not": "" if wanted else reason,
            "at": iso(moment),
            "tree": verdict.describe(),
            "action": automation.action,
            "message": message,
            "tools": list(self.tools_for(automation, events) or self.effective_tools(automation)),
            "tainted": self.is_tainted(events) and automation.action == "run",
        }

    def _notify_text(self, automation: Automation, event: StoredEvent | None) -> str:
        if event is None:
            return automation.prompt or automation.name
        return fill(automation.prompt or self.template(event.kind), {"title": event.title, **event.data})

    def prune(self, retention_days: int) -> None:
        """Old events (30 days) not wanted by a run still waiting, and old runs (the audit retention) that no conversation
        holds: a run is the link between a conversation and its automation, so one with a conversation stays until the
        conversation goes."""
        active: set[int] = set()
        for row in self.db.all("SELECT event_ids FROM automation_runs WHERE status IN ('queued', 'running')"):
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
            "DELETE FROM automation_runs WHERE finished_at IS NOT NULL AND finished_at < ? AND conversation_id IS NULL",
            (iso(self._clock() - timedelta(days=retention_days)),),
        )


def _like(kind: str) -> str:
    """A LIKE pattern for `,kind,` inside the `kinds` column, with the characters LIKE reads as wildcards escaped."""
    return "%," + re.sub(r"([\\%_])", r"\\\1", kind) + ",%"
