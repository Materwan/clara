"""Tasks: your to-do list, every task with the reminders still to come.

A task has a title, a description, an optional deadline, a queue of reminders and a count of those sent. It can be
divided into sub tasks (nested to any depth): each has its own description, deadline and reminders, none later than
the deadline of the tasks it is part of. A task is done when all its sub tasks are; finishing a task finishes its sub
tasks; reopening a sub task reopens the done tasks above it; deleting a task deletes its sub tasks. An open task can be
moved under another open task, or back to the top, and ordered among its siblings.

**Every task has reminders.** Given none, Clara picks them (a one-shot turn, at most 30 s; taskai.py) and plain rules
stand in when she cannot: a day before, an hour before and at the deadline, or tomorrow 09:00. When a reminder comes
due Clara writes the notification and may replace the queue (closer to a deadline, further apart, or stop); if you
edited the task meanwhile, your queue wins. After CLARA_TASK_MAX_REMINDERS reminders the queue is emptied and the last
notification says so. Done tasks are never reminded.

Times are read and shown on the task's IANA zone (the client's zone when it was made, else CLARA_TIMEZONE).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import Database
from ..events import Events, clean_targets
from ..timeutil import iso, parse_iso, resolve_local, utcnow, zone
from .clock import MomentError, local_text, read_moment, zone_of
from .dueloop import DueLoop
from .reminders import AnnounceFailed

log = logging.getLogger(__name__)

OPEN, DONE = "open", "done"
STATUSES = (OPEN, DONE)
MAX_TITLE = 200
MAX_DESCRIPTION = 2000
MAX_OPEN = 200
MAX_TASKS = 1000
MAX_SUBTASKS = 50
MAX_QUEUE = 10
HORIZON = timedelta(days=366)
MORNING = 9
PLAN_TIMEOUT = 30.0
NOTIFICATION_TEXT = 1000
NO_DUE = object()
NO_PARENT = object()


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class Task:
    id: int
    parent_id: int | None
    position: int
    title: str
    description: str
    due_at: datetime | None
    status: str
    reminders_sent: int
    timezone: str
    targets: tuple[str, ...]
    conversation_id: str | None
    created_at: datetime
    updated_at: datetime
    done_at: datetime | None
    next: tuple[datetime, ...] = ()

    @property
    def next_reminder(self) -> datetime | None:
        return self.next[0] if self.next else None

    @property
    def tz(self):
        return zone(self.timezone)


@dataclass(frozen=True)
class Followup:
    message: str | None  # the notification (None: the rules write it)
    next: list[datetime] | None  # the new queue; []: stop; None: keep the queue


Planner = Callable[[Task, datetime, list[str]], Awaitable["list[datetime] | None"]]
Follower = Callable[[Task, datetime, list[str]], Awaitable["Followup | None"]]


def _moment(text: str | None) -> datetime | None:
    return parse_iso(text) if text else None


def _morning(local: datetime) -> datetime:
    return local.replace(hour=MORNING, minute=0, second=0, microsecond=0)


def default_reminders(due: datetime | None, now: datetime, zone_name: str) -> list[datetime]:
    """A day before the deadline, an hour before, and at it; without a deadline, the next morning (09:00)."""
    soon = now + timedelta(minutes=1)
    if due is not None and due > soon:
        return [t for t in (due - timedelta(days=1), due - timedelta(hours=1), due) if t > soon][:MAX_QUEUE]
    tz = zone(zone_name)
    local = now.astimezone(tz).replace(tzinfo=None)
    morning = _morning(local)
    if morning <= local + timedelta(hours=2):
        morning = _morning(local + timedelta(days=1))
    return [resolve_local(morning, tz).astimezone(UTC)]


def default_follow_up(task: Task, now: datetime) -> list[datetime]:
    """Halfway to the deadline, then the deadline; once it is past (or without one), a morning further away each time
    (1, 2, 4, then 7 days)."""
    if task.due_at is not None and task.due_at - now > timedelta(seconds=60):
        left = task.due_at - now
        if left <= timedelta(hours=2):
            return [task.due_at]
        return [(now + left / 2).replace(second=0, microsecond=0)]
    days = 1 if task.due_at is not None else min(2 ** max(task.reminders_sent, 0), 7)
    tz = task.tz
    local = now.astimezone(tz).replace(tzinfo=None)
    return [resolve_local(_morning(local + timedelta(days=days)), tz).astimezone(UTC)]


def valid_reminders(moments: Iterable[datetime], now: datetime) -> list[datetime]:
    kept = {m.astimezone(UTC) for m in moments if now + timedelta(seconds=30) < m <= now + HORIZON}
    return sorted(kept)[:MAX_QUEUE]


class Tasks(DueLoop):
    name = "tasks"

    def __init__(self, db: Database, events: Events, home_zone: str, *, max_reminders: int = 10,
                 plan_timeout: float = PLAN_TIMEOUT, clock: Callable[[], datetime] = utcnow):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.home_zone = home_zone
        self.max_reminders = max_reminders
        self.plan_timeout = plan_timeout
        self.planner: Planner | None = None
        self.follower: Follower | None = None
        self.late: Callable[[str, float], None] | None = None
        self.late_after = 300.0

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def _tasks(self, rows) -> list[Task]:
        rows = list(rows)
        queued: dict[int, list[datetime]] = {}
        if rows:
            marks = ",".join("?" * len(rows))
            for reminder in self.db.all(
                f"SELECT task_id, at FROM task_reminders WHERE task_id IN ({marks}) ORDER BY at, id",
                [row["id"] for row in rows],
            ):
                queued.setdefault(reminder["task_id"], []).append(parse_iso(reminder["at"]))
        return [
            Task(row["id"], row["parent_id"], row["position"], row["title"], row["description"], _moment(row["due_at"]),
                 row["status"], row["reminders_sent"], row["timezone"],
                 tuple(t for t in row["targets"].split("|") if t), row["conversation_id"], parse_iso(row["created_at"]),
                 parse_iso(row["updated_at"]), _moment(row["done_at"]), tuple(queued.get(row["id"], ())))
            for row in rows
        ]

    def find(self, task_id: int) -> Task | None:
        found = self._tasks(self.db.all("SELECT * FROM tasks WHERE id = ?", (task_id,)))
        return found[0] if found else None

    def get(self, task_id: int) -> Task:
        task = self.find(task_id)
        if task is None:
            raise TaskError(f"No task [{task_id}].")
        return task

    def listing(self, status: str | None = OPEN) -> list[Task]:
        """Those with the nearest reminder first, then those with a deadline, then the others."""
        if status is not None and status not in STATUSES:
            raise TaskError("status must be open, done or all.")
        rows = self.db.all("SELECT * FROM tasks" + (" WHERE status = ?" if status else ""), (status,) if status else ())
        far = datetime.max.replace(tzinfo=UTC)
        return sorted(self._tasks(rows), key=lambda t: (t.status != OPEN, t.next_reminder or t.due_at or far,
                                                        t.due_at or far, t.id))

    def children(self, task_id: int) -> list[Task]:
        return self._tasks(self.db.all("SELECT * FROM tasks WHERE parent_id = ? ORDER BY position, id", (task_id,)))

    def siblings(self, parent_id: int | None) -> list[Task]:
        found = [task for task in self.listing(None) if task.parent_id == parent_id]
        return sorted(found, key=(lambda t: (t.position, t.id)) if parent_id is not None else (lambda t: t.position))

    def descendants(self, task_id: int) -> list[Task]:
        return self._tasks(self.db.all(
            "WITH RECURSIVE sub(id) AS (SELECT id FROM tasks WHERE parent_id = ?"
            " UNION SELECT t.id FROM tasks t JOIN sub ON t.parent_id = sub.id)"
            " SELECT * FROM tasks WHERE id IN (SELECT id FROM sub) ORDER BY id",
            (task_id,),
        ))

    def ancestors(self, task: Task) -> list[Task]:
        found: list[Task] = []
        seen = {task.id}
        parent = task.parent_id
        while parent is not None and parent not in seen:
            up = self.find(parent)
            if up is None:
                break
            found.append(up)
            seen.add(up.id)
            parent = up.parent_id
        return found

    def counts(self, task_id: int) -> tuple[int, int]:
        kids = self.children(task_id)
        return len(kids), sum(kid.status == DONE for kid in kids)

    def describe(self, task: Task) -> dict[str, Any]:
        total, done = self.counts(task.id)
        limit = self.limit_for(task)[0]
        when = lambda moment: iso(moment) if moment else None  # noqa: E731
        return {
            "id": task.id, "parent_id": task.parent_id, "position": task.position,
            "subtasks": {"total": total, "done": done}, "due_limit": when(limit), "title": task.title,
            "description": task.description, "status": task.status, "due_at": when(task.due_at),
            "reminders_sent": task.reminders_sent, "max_reminders": self.max_reminders,
            "next_reminder": when(task.next_reminder), "reminders": [when(at) for at in task.next],
            "timezone": task.timezone, "targets": list(task.targets), "conversation": task.conversation_id,
            "created_at": when(task.created_at), "updated_at": when(task.updated_at), "done_at": when(task.done_at),
        }

    def line(self, task: Task) -> str:
        total, done = self.counts(task.id)
        parts = [f"[{task.id}] {task.title}", task.status]
        if task.parent_id is not None:
            parts.append(f"sub task of [{task.parent_id}]")
        if total:
            parts.append(f"{done}/{total} sub tasks done")
        if task.due_at:
            parts.append(f"due {local_text(task.due_at, task.tz)}")
        parts.append(f"{task.reminders_sent} reminder{'s' if task.reminders_sent != 1 else ''} sent")
        if task.status == OPEN:
            parts.append(f"next reminder {local_text(task.next[0], task.tz)}" if task.next else "no reminder to come")
        return " | ".join(parts)

    def detail(self, task: Task) -> str:
        limit, owner = self.limit_for(task)
        lines = [self.line(task), f"Description: {task.description or '(none)'}"]
        if limit and owner:
            lines.append(f"Nothing of it may be later than {local_text(limit, task.tz)} (the deadline of “{owner.title}”).")
        lines.extend(f"Sub task: {self.line(kid)}" for kid in self.children(task.id))
        if len(task.next) > 1:
            lines.append("Reminders to come: " + ", ".join(local_text(at, task.tz) for at in task.next))
        if task.targets:
            lines.append("Shown on: " + ", ".join(task.targets))
        return "\n".join(lines)

    def family(self, task: Task) -> list[str]:
        """Where a task stands among the others, for Clara's planning."""
        lines = []
        limit, owner = self.limit_for(task)
        parent = self.find(task.parent_id) if task.parent_id else None
        if parent is not None:
            lines.append(f"This is a sub task of: {parent.title}")
        if limit is not None and owner is not None:
            lines.append(f"No reminder may be after {local_text(limit, task.tz)} (the deadline of “{owner.title}”)")
        kids = self.children(task.id)
        if kids:
            lines.append(f"Its sub tasks ({sum(k.status == DONE for k in kids)} of {len(kids)} done): "
                         + "; ".join(f"{k.title} ({k.status})" for k in kids[:10]))
        return lines

    # ------------------------------------------------------------------
    # Sub task limits
    # ------------------------------------------------------------------
    def limit_for(self, task: Task) -> tuple[datetime | None, Task | None]:
        return self._limit_above(self.find(task.parent_id) if task.parent_id else None)

    def _limit_above(self, parent: Task | None) -> tuple[datetime | None, Task | None]:
        if parent is None:
            return None, None
        with_due = [t for t in (parent, *self.ancestors(parent)) if t.due_at is not None]
        if not with_due:
            return None, None
        owner = min(with_due, key=lambda t: t.due_at)  # type: ignore[arg-type, return-value]
        return owner.due_at, owner

    @staticmethod
    def _inside(limit: datetime | None, owner: Task | None, zone_name: str, due_at: datetime | None,
                moments: Iterable[datetime] = ()) -> None:
        if limit is None or owner is None:
            return
        tz = zone(zone_name)
        when = f"{local_text(limit, tz)}, the deadline of “{owner.title}”"
        if due_at is not None and due_at > limit:
            raise TaskError(f"A sub task cannot be due after the task it is part of: the limit is {when}.")
        late = [m for m in moments if m > limit]
        if late:
            raise TaskError(f"A reminder of a sub task cannot be after the deadline of the task it is part of: "
                            f"{local_text(min(late), tz)} is too late, the limit is {when}.")

    def _inside_subtasks(self, task: Task, due_at: datetime) -> None:
        for sub in self.descendants(task.id):
            if (sub.due_at is not None and sub.due_at > due_at) or any(at > due_at for at in sub.next):
                raise TaskError(f"The sub task [{sub.id}] “{sub.title}” is due or reminded after "
                                f"{local_text(due_at, task.tz)}: change it first.")

    @staticmethod
    def _before(limit: datetime | None, moments: Iterable[datetime]) -> list[datetime]:
        return [m for m in moments if limit is None or m <= limit]

    def _default_queue(self, due: datetime | None, now: datetime, zone_name: str, limit: datetime | None) -> list[datetime]:
        queue = self._before(limit, default_reminders(due, now, zone_name))
        if not queue and limit is not None and limit > now + timedelta(minutes=1):
            queue = [limit]
        return queue

    def _settle(self, parent_id: int | None, now: datetime) -> None:
        """A task is done once all its sub tasks are, and so on up."""
        while parent_id is not None:
            parent = self.find(parent_id)
            kids = self.children(parent_id) if parent else []
            if parent is None or parent.status != OPEN or not kids or any(k.status != DONE for k in kids):
                return
            self._set_status(parent.id, DONE, now)
            parent_id = parent.parent_id

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    @staticmethod
    def _title(title: str | None) -> str:
        title = " ".join((title or "").split())
        if not title:
            raise TaskError("A task needs a title.")
        if len(title) > MAX_TITLE:
            raise TaskError(f"A title is at most {MAX_TITLE} characters long.")
        return title

    @staticmethod
    def _description(description: str | None) -> str:
        description = (description or "").strip()
        if len(description) > MAX_DESCRIPTION:
            raise TaskError(f"A description is at most {MAX_DESCRIPTION} characters long.")
        return description

    @staticmethod
    def _targets(targets: Iterable[str] | None) -> tuple[str, ...]:
        try:
            return clean_targets(targets)
        except ValueError as error:
            raise TaskError(str(error)) from None

    def _read(self, text: str, zone_name: str) -> datetime:
        try:
            return read_moment(str(text), zone(zone_name))[0]
        except MomentError as error:
            raise TaskError(str(error)) from None

    def _given(self, reminders: Iterable[str], zone_name: str, now: datetime) -> list[datetime]:
        moments = []
        for text in reminders:
            moment = self._read(text, zone_name)
            if moment <= now:
                raise TaskError(f"The reminder {text!r} is already past.")
            moments.append(moment)
        moments = sorted(set(moments))
        if len(moments) > MAX_QUEUE:
            raise TaskError(f"At most {MAX_QUEUE} reminders for one task.")
        return moments

    def _queue(self, tx, task_id: int, reminders: Iterable[datetime]) -> None:
        tx.execute("DELETE FROM task_reminders WHERE task_id = ?", (task_id,))
        tx.executemany("INSERT INTO task_reminders (task_id, at) VALUES (?, ?)",
                       [(task_id, iso(at)) for at in sorted(set(reminders))])

    def _set_reminders(self, task_id: int, reminders: Iterable[datetime], now: datetime) -> None:
        with self.db.transaction() as tx:
            self._queue(tx, task_id, reminders)
            tx.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (iso(now), task_id))

    def _set_status(self, task_id: int, status: str, now: datetime) -> None:
        with self.db.transaction() as tx:
            if status == DONE:
                tx.execute("DELETE FROM task_reminders WHERE task_id = ?", (task_id,))
            tx.execute("UPDATE tasks SET status = ?, done_at = ?, updated_at = ? WHERE id = ?",
                       (status, iso(now) if status == DONE else None, iso(now), task_id))

    async def create(self, title: str, description: str = "", due: str | None = None,
                     reminders: Iterable[str] | None = None, *, timezone: str | None = None,
                     targets: Iterable[str] | None = None, parent_id: int | None = None,
                     conversation_id: str | None = None) -> Task:
        title, description, shown = self._title(title), self._description(description), self._targets(targets)
        now = self._clock()
        parent = None
        if parent_id is not None:
            parent = self.find(parent_id)
            if parent is None:
                raise TaskError(f"The task [{parent_id}] it should be part of does not exist.")
            if parent.status != OPEN:
                raise TaskError(f"“{parent.title}” is done: reopen it before adding a sub task.")
            if self.counts(parent.id)[0] >= MAX_SUBTASKS:
                raise TaskError(f"At most {MAX_SUBTASKS} sub tasks under one task.")
        try:
            zone_name = zone_of(timezone, parent.timezone if parent else self.home_zone)[0]
        except MomentError as error:
            raise TaskError(str(error)) from None
        limit, owner = self._limit_above(parent)
        due_at = None
        if due:
            due_at = self._read(due, zone_name)
            if due_at <= now:
                raise TaskError("That deadline is already past.")
        given = self._given(list(reminders or ()), zone_name, now)
        self._inside(limit, owner, zone_name, due_at, given)
        if int(self.db.value("SELECT COUNT(*) FROM tasks WHERE status = 'open'", (), 0)) >= MAX_OPEN:
            raise TaskError(f"At most {MAX_OPEN} open tasks at a time: finish or delete some first.")
        if int(self.db.value("SELECT COUNT(*) FROM tasks", (), 0)) >= MAX_TASKS:
            raise TaskError(f"At most {MAX_TASKS} tasks, done ones included: delete some first.")
        queue = given or self._default_queue(due_at, now, zone_name, limit)
        with self.db.transaction() as tx:
            last = tx.execute("SELECT MAX(position) FROM tasks WHERE parent_id IS ?", (parent_id,)).fetchone()[0] or 0
            task_id = tx.execute(
                "INSERT INTO tasks (parent_id, position, title, description, due_at, timezone, targets, conversation_id,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (parent_id, last + 1 if last else 0, title, description, iso(due_at) if due_at else None, zone_name,
                 "|".join(shown), conversation_id, iso(now), iso(now)),
            ).lastrowid
            self._queue(tx, int(task_id or 0), queue)
        self.wake()
        task = self.get(int(task_id or 0))
        return task if given else await self._plan(task)

    def update(self, task_id: int, *, title: str | None = None, description: str | None = None, due: Any = NO_DUE,
               reminders: Iterable[str] | None = None, targets: Iterable[str] | None = None,
               timezone: str | None = None) -> Task:
        """Change only what is given. `due` "" or None removes the deadline; `reminders` replaces the queue ([]: stop)."""
        task = self.get(task_id)
        now = self._clock()
        limit, owner = self.limit_for(task)
        zone_name = task.timezone
        if timezone:
            try:
                zone_name = zone_of(timezone, task.timezone)[0]
            except MomentError as error:
                raise TaskError(str(error)) from None
        fields: dict[str, Any] = {}
        if title is not None:
            fields["title"] = self._title(title)
        if description is not None:
            fields["description"] = self._description(description)
        if targets is not None:
            fields["targets"] = "|".join(self._targets(targets))
        if due is not NO_DUE:
            if due:
                due_at = self._read(str(due), zone_name)
                if due_at <= now:
                    raise TaskError("That deadline is already past.")
                self._inside(limit, owner, zone_name, due_at)
                self._inside_subtasks(task, due_at)
                fields["due_at"] = iso(due_at)
            else:
                fields["due_at"] = None
        queue: list[datetime] | None = None
        if reminders is not None:
            if task.status != OPEN:
                raise TaskError("The task is done: reopen it before setting reminders.")
            queue = self._given(list(reminders), zone_name, now)
            self._inside(limit, owner, zone_name, None, queue)
        with self.db.transaction() as tx:
            if fields:
                assignments = ", ".join(f"{name} = ?" for name in fields)
                tx.execute(f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
                           (*fields.values(), iso(now), task_id))
            if queue is not None:
                self._queue(tx, task_id, queue)
                tx.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (iso(now), task_id))
        self.wake()
        return self.get(task_id)

    def complete(self, task_id: int) -> Task:
        task = self.get(task_id)
        if task.status != DONE:
            now = self._clock()
            for open_one in (task, *(d for d in self.descendants(task.id) if d.status == OPEN)):
                self._set_status(open_one.id, DONE, now)
            self._settle(task.parent_id, now)
            self.wake()
        return self.get(task_id)

    async def reopen(self, task_id: int, reminders: Iterable[str] | None = None) -> Task:
        task = self.get(task_id)
        if task.status == OPEN:
            return task
        now = self._clock()
        given = self._given(list(reminders or ()), task.timezone, now)
        limit, owner = self.limit_for(task)
        self._inside(limit, owner, task.timezone, None, given)
        above = [a for a in self.ancestors(task) if a.status == DONE]
        for parent in reversed(above):
            self._set_status(parent.id, OPEN, now)
            self._set_reminders(parent.id, self._default_queue(parent.due_at, now, parent.timezone,
                                                               self.limit_for(parent)[0]), now)
        self._set_status(task.id, OPEN, now)
        self._set_reminders(task.id, given or self._default_queue(task.due_at, now, task.timezone, limit), now)
        self.wake()
        task = self.get(task_id)
        return task if given else await self._plan(task)

    def move(self, task_id: int, parent: Any = NO_PARENT, before: Any = NO_PARENT) -> Task:
        """Put a task under `parent` (None: a main task) and/or before `before` among its siblings (None: last)."""
        task = self.get(task_id)
        if task.status != OPEN:
            raise TaskError(f"“{task.title}” is done: reopen it before moving it.")
        new_parent_id = task.parent_id if parent is NO_PARENT else parent
        new_parent = None
        if new_parent_id is not None:
            new_parent = self.find(new_parent_id)
            if new_parent is None:
                raise TaskError(f"The task [{new_parent_id}] it should be part of does not exist.")
            if new_parent.id == task.id or new_parent.id in {d.id for d in self.descendants(task.id)}:
                raise TaskError("A task cannot be part of itself or of one of its own sub tasks.")
            if new_parent.status != OPEN:
                raise TaskError(f"“{new_parent.title}” is done: reopen it before adding a sub task.")
        changing = new_parent_id != task.parent_id
        if changing:
            if new_parent is not None and self.counts(new_parent.id)[0] >= MAX_SUBTASKS:
                raise TaskError(f"At most {MAX_SUBTASKS} sub tasks under one task.")
            limit, owner = self._limit_above(new_parent)
            for part in (task, *self.descendants(task.id)):
                self._inside(limit, owner, part.timezone, part.due_at, part.next)
        order = [t.id for t in self.siblings(new_parent_id) if t.id != task.id]
        if before == task.id:
            before = NO_PARENT
        if before is NO_PARENT and not changing:
            return task
        if before is not NO_PARENT and before is not None and before not in order:
            raise TaskError(f"The task [{before}] is not among the tasks it would be put in.")
        if before is NO_PARENT or before is None:
            order.append(task.id)
        else:
            order.insert(order.index(before), task.id)
        now = self._clock()
        with self.db.transaction() as tx:
            tx.execute("UPDATE tasks SET parent_id = ?, updated_at = ? WHERE id = ? AND parent_id IS NOT ?",
                       (new_parent_id, iso(now), task.id, new_parent_id))
            tx.executemany("UPDATE tasks SET position = ? WHERE id = ?",
                           [(number, other) for number, other in enumerate(order, 1)])
        if changing:
            self._settle(task.parent_id, now)
        self.wake()
        return self.get(task_id)

    def delete(self, task_id: int) -> bool:
        task = self.find(task_id)
        deleted = self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0
        if deleted:
            if task is not None:
                self._settle(task.parent_id, self._clock())
            self.wake()
        return deleted

    async def _plan(self, task: Task) -> Task:
        if self.planner is None or self.stopping:
            return task
        now = self._clock()
        try:
            planned = await asyncio.wait_for(self.planner(task, now, self.family(task)), self.plan_timeout)
        except (AnnounceFailed, TimeoutError) as error:
            log.warning("task %s: Clara could not pick the reminders (%s)", task.id, error or "timeout")
            return task
        except Exception:
            log.exception("task %s: could not pick the reminders", task.id)
            return task
        picked = self._before(self.limit_for(task)[0], valid_reminders(planned or (), now))
        if not picked or self.find(task.id) is None:
            return task
        self._set_reminders(task.id, picked, now)
        self.wake()
        return self.find(task.id) or task

    # ------------------------------------------------------------------
    # The clock
    # ------------------------------------------------------------------
    def next_due(self) -> datetime | None:
        value = self.db.value(
            "SELECT MIN(r.at) FROM task_reminders r JOIN tasks t ON t.id = r.task_id WHERE t.status = 'open'")
        return parse_iso(value) if value else None

    def _due(self, now: datetime) -> list[Task]:
        return self._tasks(self.db.all(
            "SELECT t.* FROM tasks t JOIN task_reminders r ON r.task_id = t.id WHERE t.status = 'open' AND r.at <= ?"
            " GROUP BY t.id ORDER BY MIN(r.at), t.id",
            (iso(now),),
        ))

    async def fire_due(self) -> int:
        started = self._clock()
        due = self._due(started)
        if not due:
            return 0
        decided = await asyncio.gather(*(self._follow(task, started) for task in due))
        now = self._clock()
        fired = 0
        for task, follow in zip(due, decided):
            current = self.find(task.id)
            if current is None or current.status != OPEN:
                continue
            self._send(current, follow, now, started, edited=current.updated_at > task.updated_at)
            late = (now - task.next[0]).total_seconds() if task.next else 0
            if late > self.late_after and self.late is not None:
                self.late(f"task reminder [{task.id}] ({task.title[:60]})", late)
            fired += 1
        return fired

    async def _follow(self, task: Task, now: datetime) -> Followup | None:
        if self.follower is None or self.stopping:
            return None
        try:
            return await self.follower(task, now, self.family(task))
        except AnnounceFailed as error:
            log.warning("task %s: Clara could not follow it up (%s)", task.id, error)
        except Exception:
            log.exception("task %s: could not follow it up", task.id)
        return None

    def _send(self, task: Task, follow: Followup | None, now: datetime, started: datetime, edited: bool) -> None:
        sent = task.reminders_sent + 1
        last = sent >= self.max_reminders
        decided = None
        if follow is not None and follow.next is not None and not edited:
            decided = valid_reminders(follow.next, now) if follow.next else []
            if follow.next and not decided:
                decided = None
        kept = [at for at in task.next if at > started]
        if last:
            remaining: list[datetime] = []
        elif decided is not None:
            remaining = decided
        elif kept:
            remaining = kept
        else:
            remaining = default_follow_up(replace(task, reminders_sent=sent), now)
        remaining = self._before(self.limit_for(task)[0], remaining)
        text = follow.message if follow and follow.message else self._plain_text(task, sent)
        if last and not (follow and follow.message):
            text += " This was the last reminder of this task: mark it done, or ask me to remind you again."
        with self.db.transaction() as tx:
            self._queue(tx, task.id, remaining)
            tx.execute("UPDATE tasks SET reminders_sent = reminders_sent + 1, updated_at = ? WHERE id = ?",
                       (iso(now), task.id))
        self.events.publish("task", f"Task: {task.title}", text[:NOTIFICATION_TEXT],
                            payload={"task": self.describe(self.get(task.id)), "last": last},
                            targets=task.targets, source="tasks", conversation_id=task.conversation_id)

    @staticmethod
    def _plain_text(task: Task, sent: int) -> str:
        text = f"Reminder {sent} for your task: {task.title}."
        if task.due_at:
            text += f" Due {local_text(task.due_at, task.tz)}."
        if task.description:
            text += f"\n{task.description}"
        return text
