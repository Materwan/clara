"""Deferred tool calls: a tool and its exact arguments, booked in a chat to run later.

Clara books a call (`deferred_book`), you read it and its date in the approval request and allow it once, and the server
runs it at the due time with the saved arguments. There is no model turn then and nobody in the conversation. What
the call would do is saved with it (`summary`, the text you read) and compared again at the due time: a call that now
says something else is not run.

A booked call goes through these states:

    pending     waiting for its date
    running     being run (a crash while it ran leaves it `failed`: it may or may not have been done)
    held        due, but a rule of yours asks about it: an ordinary approval request waits for your answer
    done, failed, refused (the gate, a rule, or you refused it), expired (nobody answered the request in time)
    cancelled   by you, before its date

A call missed while the server was down runs once when it comes back and raises a late alert, like a reminder. The
time services share one loop shape (dueloop.py); each run is a task of its own so a slow tool never stops the loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..db import Database
from ..events import TARGETS, Events
from ..gate.outside import plain
from ..timeutil import iso, parse_iso, utcnow, zone
from .clock import MomentError, local_text, read_moment, zone_of
from .dueloop import DueLoop

if TYPE_CHECKING:
    from ..gate.approvals import Approvals

log = logging.getLogger(__name__)

MAX_PENDING = 50  # booked calls waiting for their date, or being run, or held
MAX_AHEAD = timedelta(days=366)
MIN_AHEAD = timedelta(minutes=1)
MAX_SUMMARY = 24_000  # the whole request is read: a call that cannot be shown in full is not booked
MAX_ARGUMENTS = 64_000
KEEP_DAYS = 30  # a finished call stays in the list this long
RESULT_KEPT = 20_000
NOTICE_SHOWN = 300
ACTIVE = ("pending", "running", "held")
PRIVATE_TARGETS = tuple(
    target for target in TARGETS if target not in ("discord", "script")
)  # a local-only call stays here
STOPPED = "The server stopped while this ran: it may or may not have been done. Check before booking it again."


class DeferredError(ValueError):
    pass


@dataclass(frozen=True)
class DeferredCall:
    id: int
    tool: str
    arguments: dict
    target: str
    level: str
    action_class: str
    summary: str
    due_at: datetime
    timezone: str
    status: str
    result: str
    conversation_id: str | None
    local_only: bool
    incognito: bool
    project_id: int | None
    approval_id: int | None
    request_id: int | None
    booked_at: str
    started_at: str | None
    finished_at: str | None
    tainted: bool = False  # the turn that booked it had read untrusted content: the rules are asked so when it is due

    def describe(self, *, private: bool = False) -> dict:
        """For the clients. `private`: hide what a local-only call holds (its summary, arguments and result)."""
        hidden = private and self.local_only
        return {
            "id": self.id,
            "tool": self.tool,
            "summary": "" if hidden else self.summary,
            "arguments": {} if hidden else self.arguments,
            "target": self.target,
            "level": self.level,
            "class": self.action_class,
            "due_at": iso(self.due_at),
            "local": local_text(self.due_at, zone(self.timezone)),
            "timezone": self.timezone,
            "status": self.status,
            "result": "" if hidden else self.result[:2000],
            "conversation": self.conversation_id,
            "local_only": self.local_only,
            "approval": self.approval_id,
            "request": self.request_id,
            "booked_at": self.booked_at,
            "finished_at": self.finished_at,
        }

    def line(self, *, private: bool = False) -> str:
        """One line for the list Clara reads: without the subject or the file name the request shows you (`outside.py`)."""
        what = (
            "(local only: read it from a local-only conversation)" if private and self.local_only else self._first(True)
        )
        return f"[{self.id}] {local_text(self.due_at, zone(self.timezone))} {self.tool} ({self.status}): {what}"

    @property
    def brief(self) -> str:
        """The first line of the summary: what the call does."""
        return self._first(False)

    def _first(self, plainly: bool) -> str:
        lines = [line for line in self.summary.splitlines() if line.strip()]
        found = (plain(lines[0]) if plainly else lines[0]) if lines else self.tool
        return found if len(found) <= 200 else found[:200] + "…"


def _call(row) -> DeferredCall:
    return DeferredCall(
        row["id"],
        row["tool"],
        json.loads(row["arguments"]),
        row["target"],
        row["level"],
        row["action_class"],
        row["summary"],
        parse_iso(row["due_at"]),
        row["timezone"],
        row["status"],
        row["result"],
        row["conversation_id"],
        bool(row["local_only"]),
        bool(row["incognito"]),
        row["project_id"],
        row["approval_id"],
        row["request_id"],
        row["booked_at"],
        row["started_at"],
        row["finished_at"],
        bool(row["tainted"]),
    )


# What the runner answers: (status, what it says, the request made when a rule asked: its number)
Outcome = tuple[str, str, int | None]
Runner = Callable[[DeferredCall], Awaitable[Outcome]]
Lateness = Callable[[str, float], None]  # (what, seconds late)


class DeferredCalls(DueLoop):
    name = "deferred"

    def __init__(self, db: Database, events: Events, home_zone: str, clock: Callable[[], datetime] = utcnow):
        super().__init__(clock)
        self.db = db
        self.events = events
        self.home_zone = home_zone
        self.runner: Runner | None = None  # runs a due call through the gate (app.run_deferred)
        self.approvals: Approvals | None = None  # the requests made when a rule asks at the due time
        self.late: Lateness | None = None
        self.late_after = 300.0
        self._running: dict[int, asyncio.Task] = {}

    def _now(self) -> str:
        return iso(self._clock())

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------
    def moment(self, at: str, timezone: str | None) -> tuple[datetime, str]:
        """(the instant in UTC, the zone's name) of a date a call may be booked for; DeferredError when it may not."""
        try:
            zone_name, tz = zone_of(timezone, self.home_zone)
            due, _ = read_moment(at, tz)
        except (MomentError, ValueError) as error:
            raise DeferredError(str(error)) from None
        now = self._clock()
        if due < now + MIN_AHEAD:
            raise DeferredError("That moment is past, or less than a minute away: book a call for a later moment.")
        if due > now + MAX_AHEAD:
            raise DeferredError(f"A call is booked at most {MAX_AHEAD.days} days ahead.")
        return due, zone_name

    def room(self) -> None:
        if self.waiting() >= MAX_PENDING:
            raise DeferredError(f"At most {MAX_PENDING} calls can be booked at a time: cancel some first.")

    def waiting(self) -> int:
        marks = ",".join("?" * len(ACTIVE))
        return int(self.db.value(f"SELECT COUNT(*) FROM deferred_calls WHERE status IN ({marks})", ACTIVE, 0))

    def book(
        self,
        tool: str,
        arguments: dict,
        *,
        target: str,
        level: str,
        action_class: str,
        summary: str,
        at: str,
        timezone: str | None,
        conversation_id: str | None = None,
        local_only: bool = False,
        incognito: bool = False,
        project_id: int | None = None,
        approval_id: int | None = None,
        tainted: bool = False,
    ) -> DeferredCall:
        due, zone_name = self.moment(at, timezone)
        self.room()
        saved = json.dumps(arguments, ensure_ascii=False)
        if len(saved) > MAX_ARGUMENTS:
            raise DeferredError(f"The arguments of a booked call are at most {MAX_ARGUMENTS:,} characters long.")
        if len(summary) > MAX_SUMMARY:
            raise DeferredError("This call is too long to be read in full in the request, so it cannot be booked.")
        call_id = self.db.insert(
            "INSERT INTO deferred_calls (tool, arguments, target, level, action_class, summary, due_at, timezone,"
            " conversation_id, local_only, incognito, project_id, approval_id, booked_at, tainted)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tool,
                saved,
                target,
                level,
                action_class,
                summary,
                iso(due),
                zone_name,
                None if incognito else (conversation_id or None),
                int(local_only),
                int(incognito),
                project_id,
                approval_id,
                self._now(),
                int(tainted),
            ),
        )
        self.wake()
        call = self.get(call_id)
        assert call is not None
        return call

    # ------------------------------------------------------------------
    # Reading and cancelling
    # ------------------------------------------------------------------
    def get(self, call_id: int) -> DeferredCall | None:
        row = self.db.one("SELECT * FROM deferred_calls WHERE id = ?", (call_id,))
        return _call(row) if row else None

    def listing(self, recent: int = 100) -> list[DeferredCall]:
        """The calls that wait (by date), then the ones that ended (the latest first)."""
        marks = ",".join("?" * len(ACTIVE))
        active = self.db.all(f"SELECT * FROM deferred_calls WHERE status IN ({marks}) ORDER BY due_at, id", ACTIVE)
        ended = self.db.all(
            f"SELECT * FROM deferred_calls WHERE status NOT IN ({marks}) ORDER BY COALESCE(finished_at, booked_at) DESC,"
            " id DESC LIMIT ?",
            (*ACTIVE, recent),
        )
        return [_call(row) for row in (*active, *ended)]

    def cancel(self, call_id: int) -> bool:
        """Cancel a call that still waits for its date. One that is running or held (a request waits for you) is not."""
        changed = (
            self.db.execute(
                "UPDATE deferred_calls SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'pending'",
                (self._now(), call_id),
            ).rowcount
            > 0
        )
        if changed:
            self.wake()
        return changed

    # ------------------------------------------------------------------
    # The clock
    # ------------------------------------------------------------------
    @property
    def firing(self) -> bool:
        return bool(self._running)

    def next_due(self) -> datetime | None:
        value = self.db.value("SELECT MIN(due_at) FROM deferred_calls WHERE status = 'pending'")
        return parse_iso(value) if value else None

    def fire_due(self) -> int:
        self.settle_held()
        now = self._clock()
        started = 0
        for row in self.db.all(
            "SELECT * FROM deferred_calls WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id", (iso(now),)
        ):
            claimed = self.db.execute(
                "UPDATE deferred_calls SET status = 'running', started_at = ? WHERE id = ? AND status = 'pending'",
                (self._now(), row["id"]),
            ).rowcount
            if not claimed:  # cancelled a moment ago
                continue
            call = self.get(row["id"])
            assert call is not None
            late = (now - call.due_at).total_seconds()
            if late > self.late_after and self.late is not None:
                try:  # a failing alert must not leave the call marked running with nothing to run it
                    self.late(f"booked call #{call.id} ({call.tool})", late)
                except Exception:
                    log.exception("could not raise the late alert of booked call %s", call.id)
            task = asyncio.create_task(self._execute(call), name=f"deferred-{call.id}")
            self._running[call.id] = task
            task.add_done_callback(lambda _, number=call.id: (self._running.pop(number, None), self.wake()))
            started += 1
        return started

    async def _execute(self, call: DeferredCall) -> None:
        try:
            if self.runner is None:
                status, text, request = "failed", "The server cannot run booked calls right now.", None
            else:
                status, text, request = await self.runner(call)
        except asyncio.CancelledError:
            raise  # it stays `running`: the next start marks it failed (recover)
        except Exception:
            log.exception("booked call %s (%s) crashed", call.id, call.tool)
            status, text, request = "failed", "The booked call crashed: see the server log.", None
        if status == "held":
            self.db.execute(
                "UPDATE deferred_calls SET status = 'held', request_id = ?, result = ? WHERE id = ? AND status = 'running'",
                (request, text[:RESULT_KEPT], call.id),
            )
            return
        self._finish(call.id, status, text)

    def settle_held(self) -> int:
        """A held call ends when the request made for it ends: done, failed, refused by you, or expired."""
        if self.approvals is None:
            return 0
        settled = 0
        for row in self.db.all("SELECT id, request_id FROM deferred_calls WHERE status = 'held'"):
            request = self.approvals.get(row["request_id"]) if row["request_id"] else None
            if request is not None and request.status in ("pending", "approved"):
                continue
            if request is None:
                status, text = "failed", "The request made for this call is gone: check whether it was done."
            elif request.status == "done":
                status, text = "done", request.result
            elif request.status == "failed":
                status, text = "failed", request.result
            elif request.status == "denied":
                status, text = "refused", "You refused the request: it was not done."
            else:
                status, text = "expired", "Nobody answered the request in time: it was not done."
            settled += self._finish(row["id"], status, text, notice=False)
        return settled

    def _finish(self, call_id: int, status: str, text: str, *, notice: bool = True) -> int:
        changed = self.db.execute(
            "UPDATE deferred_calls SET status = ?, result = ?, finished_at = ? WHERE id = ? AND status IN ('running', 'held')",
            (status, text[:RESULT_KEPT], self._now(), call_id),
        ).rowcount
        call = self.get(call_id)
        if changed and notice and call is not None:
            self._notice(call)
        return changed

    def _notice(self, call: DeferredCall) -> None:
        words = {
            "done": "done",
            "failed": "failed",
            "refused": "not run",
            "expired": "expired",
        }.get(call.status, call.status)
        text = f"{call.tool} (call {call.id}), {words}: {call.brief}\n{call.result[:NOTICE_SHOWN]}".strip()
        try:
            self.events.publish(
                "notification",
                f"Booked call {words}",
                text,
                payload={"deferred": call.describe(), "status": call.status},
                targets=PRIVATE_TARGETS if call.local_only else (),
                source="deferred",
                conversation_id=call.conversation_id,
            )
        except Exception:
            log.exception("could not tell that booked call %s ended", call.id)

    # ------------------------------------------------------------------
    # Upkeep
    # ------------------------------------------------------------------
    def recover(self) -> int:
        """At start: a call that was running when the server stopped may have been done, so it is not run again."""
        running = [row["id"] for row in self.db.all("SELECT id FROM deferred_calls WHERE status = 'running'")]
        for call_id in running:
            self._finish(call_id, "failed", STOPPED)
        return len(running)

    def sweep(self) -> None:
        marks = ",".join("?" * len(ACTIVE))
        self.db.execute(
            f"DELETE FROM deferred_calls WHERE status NOT IN ({marks}) AND COALESCE(finished_at, booked_at) < ?",
            (*ACTIVE, iso(self._clock() - timedelta(days=KEEP_DAYS))),
        )

    async def close(self) -> None:
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
