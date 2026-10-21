"""Approval requests: what happens when the gate says *ask*.

    asked        the action is frozen (tool and exact arguments) and stored as a pending request.
                 In a chat, the turn shows it and waits for your answer (CLARA_APPROVAL_WAIT, 10 minutes).
                 Unattended, the turn never waits: you are alerted and the run goes on without it.
    pushed       a request still waiting after a minute is pushed to your other clients (Discord's #approval, with buttons…)
    answered     allow once / allow always / refuse once / refuse always. The two "always" answers write a rule, so
                 the rule list only grows through your decisions. An allowed action runs exactly as it was frozen.
                 If no turn is waiting for it any more, a short follow-up turn tells Clara how it ended.
    expired      after 24 hours nobody answered: it is not done, and Clara is told at her next turn there.

Only a client token can answer (the API, the web, the console, the Discord bot), and only one whose `approvals`
scope covers the action's level: the Discord bot's default scope leaves destructive actions to the web and console.
The model has no tool to answer a request.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from ..auth.tokens import TokenInfo
from ..db import Database
from ..events import Events
from ..timeutil import iso, now_iso, parse_iso, utcnow
from .actions import Action, Decision, Situation
from .engine import Audit, args_digest
from .rules import RuleError, Rules, check_fields

if TYPE_CHECKING:
    from ..ops.alerts import Alerts
    from ..tools.base import Toolbox, ToolContext

log = logging.getLogger(__name__)

ANSWERS = ("allow_once", "allow_always", "refuse_once", "refuse_always")
PUSH_AFTER = 60  # seconds a request from a chat waits before it is pushed to your other clients
KEEP_DAYS = 30  # finished requests are kept this long (the audit log keeps the decisions longer)
FOLLOWUP_DELAY = 3.0  # answers given within this long make one follow-up turn
MAX_PENDING = 10  # requests one conversation may have waiting
RESULT_SHOWN = 300
SUMMARY_SHOWN = 500
SUMMARY_FULL = {"mail_send": 8_000}  # classes whose request shows everything that would leave (the text of an email)

WAITING = (
    "Waiting for permission (request #{id}): this was NOT done, and the person was asked. Go on with anything that does "
    "not depend on it, do not ask again, and do not say it is done: you will be told how it ended."
)
DENIED = "The person refused this (request #{id}): it was NOT done. Do not try another way round it."


class ApprovalError(ValueError):
    pass


class AlreadyAnswered(ApprovalError):
    pass


class NoRuleError(ApprovalError):
    """An "always" answer whose rule cannot be written: the request stays pending, to be answered once."""


@dataclass(frozen=True)
class Approval:
    id: int
    conversation_id: str | None
    turn_id: str
    tool: str
    arguments: dict
    target: str
    level: str
    action_class: str
    situation: str
    summary: str
    reason: str
    status: str
    result: str
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: str
    answer: str
    told: bool

    def describe(self) -> dict:
        return {
            "id": self.id,
            "conversation": self.conversation_id,
            "tool": self.tool,
            "target": self.target,
            "level": self.level,
            "class": self.action_class,
            "situation": self.situation,
            "summary": self.summary,
            "reason": self.reason,
            "status": self.status,
            "result": self.result[:2000],
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "answer": self.answer,
        }

    def outcome_line(self) -> str:
        if self.status == "done":
            return f"- #{self.id} approved and done: {self.brief}. Result: {self.result[:RESULT_SHOWN]}"
        if self.status == "failed":
            return f"- #{self.id} approved, but it failed: {self.brief}. {self.result[:RESULT_SHOWN]}"
        if self.status == "denied":
            return f"- #{self.id} refused: {self.brief}. Do not do it, nor find another way round."
        return f"- #{self.id} nobody answered in time, so it was not done: {self.brief}."

    @property
    def brief(self) -> str:
        """The summary as Clara is told it: the whole text of an email is for you to read, not for her prompt again."""
        return self.summary if len(self.summary) <= SUMMARY_SHOWN else self.summary[:SUMMARY_SHOWN] + "…"


def _approval(row) -> Approval:
    return Approval(
        row["id"],
        row["conversation_id"],
        row["turn_id"],
        row["tool"],
        json.loads(row["arguments"]),
        row["target"],
        row["level"],
        row["action_class"],
        row["situation"],
        row["summary"],
        row["reason"],
        row["status"],
        row["result"],
        row["created_at"],
        row["expires_at"],
        row["decided_at"],
        row["decided_by"],
        row["answer"],
        bool(row["told"]),
    )


ContextFactory = Callable[[Approval], "ToolContext"]
FollowUp = Callable[[str, str], Awaitable[None]]  # (conversation, message)


class Approvals:
    def __init__(
        self,
        db: Database,
        events: Events,
        rules: Rules,
        audit: Audit,
        alerts: Alerts | None = None,
        *,
        expire_after: int = 86_400,
        push_after: int = PUSH_AFTER,
    ):
        self.db = db
        self.events = events
        self.rules = rules
        self.audit = audit
        self.alerts = alerts
        self.expire_after = expire_after
        self.push_after = push_after
        self.toolbox: Toolbox | None = None
        self.context_factory: ContextFactory | None = None  # builds the context an approved call runs in
        self.followup: FollowUp | None = None  # runs the turn that tells Clara how her held requests ended
        self.followup_delay = FOLLOWUP_DELAY
        self._waiters: dict[int, asyncio.Future] = {}
        self._held: dict[str, set[int]] = {}  # turn id -> the requests that turn waits for
        self._scheduled: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Asking
    # ------------------------------------------------------------------
    def create(
        self, action: Action, situation: Situation, context: ToolContext, arguments: dict, decision: Decision
    ) -> Approval:
        conversation_id = context.turn.conversation_id or None
        reasons = [decision.reason, *decision.raised]
        now = utcnow()
        approval_id = self.db.insert(
            "INSERT INTO approvals (conversation_id, turn_id, tool, arguments, args_digest, target, level, action_class,"
            " situation, summary, reason, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " 'pending', ?, ?)",
            (
                conversation_id,
                context.turn.turn_id,
                action.tool,
                json.dumps(arguments, ensure_ascii=False),
                args_digest(arguments),
                action.target,
                action.level,
                action.klass,
                situation.describe(),
                action.summary[: SUMMARY_FULL.get(action.klass, SUMMARY_SHOWN)],
                "; ".join(reason for reason in reasons if reason),
                iso(now),
                iso(now + timedelta(seconds=self.expire_after)),
            ),
        )
        approval = self.get(approval_id)
        assert approval is not None
        if context.turn.waits_for_approvals:
            self.hold(approval_id, context.turn.turn_id)
        if situation.mode == "unattended":
            self.push(approval)
        return approval

    def waiting_text(self, approval: Approval) -> str:
        return WAITING.format(id=approval.id)

    def pending_count(self, conversation_id: str) -> int:
        return int(
            self.db.value(
                "SELECT COUNT(*) FROM approvals WHERE conversation_id = ? AND status = 'pending'", (conversation_id,), 0
            )
        )

    def push(self, approval: Approval) -> None:
        """Send the request to your clients (they show it; the Discord bot adds buttons)."""
        self.db.execute("UPDATE approvals SET notified = 1 WHERE id = ?", (approval.id,))
        self.events.publish(
            "approval",
            "Clara needs your permission",
            approval.summary,
            payload={"approval": approval.describe()},
            conversation_id=approval.conversation_id,
        )

    def hold(self, approval_id: int, turn_id: str) -> None:
        """The turn that asked will wait for the answer. It is registered with the request, not when the wait begins:
        an answer that comes before then (the turn is still showing the request, or running the other calls of its
        round) is handed to the turn. Otherwise `decide` would run the action itself and the turn would run it again."""
        self._waiters[approval_id] = asyncio.get_running_loop().create_future()
        self._held.setdefault(turn_id, set()).add(approval_id)

    def release(self, turn_id: str) -> None:
        """The turn ended (or was cancelled) and lets go of its requests. An answer it did not take is carried out
        here, so a request that was allowed is never left approved and unrun."""
        for approval_id in self._held.pop(turn_id, ()):
            waiter = self._waiters.pop(approval_id, None)
            if waiter is None or not waiter.done() or waiter.cancelled() or not waiter.result():
                continue
            task = asyncio.ensure_future(self._carry_out(approval_id, waiter.result().startswith("allow")))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def wait(self, approval_id: int, seconds: float) -> str | None:
        """A chat waits here for your answer: the answer given, or None if none came in time (still pending)."""
        future = self._waiters.get(approval_id)
        if future is None:
            approval = self.get(approval_id)
            if approval is None or approval.status != "pending":
                # answered with nobody holding it: `decide` has already run it (or refused it), so only a refusal is
                # worth handing back; an allowed answer returned here would make the turn run the action twice
                return approval.answer if approval and approval.status == "denied" else None
            future = self._waiters[approval_id] = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(asyncio.shield(future), seconds)
        except TimeoutError:
            return None
        finally:
            if self._waiters.get(approval_id) is future:
                del self._waiters[approval_id]

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------
    def get(self, approval_id: int) -> Approval | None:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return _approval(row) if row else None

    def list(self, status: str | None = None, limit: int = 100) -> list[Approval]:
        if status:
            rows = self.db.all("SELECT * FROM approvals WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            rows = self.db.all("SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,))
        return [_approval(row) for row in rows]

    async def decide(self, approval_id: int, answer: str, token: TokenInfo) -> Approval:
        if answer not in ANSWERS:
            raise ApprovalError(f"answer must be one of {', '.join(ANSWERS)}.")
        approval = self.get(approval_id)
        if approval is None:
            raise ApprovalError(f"No request {approval_id}.")
        if approval.status != "pending":
            raise AlreadyAnswered(f"Request #{approval_id} is already {approval.status}.")
        if not token.may_answer(approval.level):
            raise ApprovalError(
                f"The token {token.name} may not answer {approval.level} requests: answer it from the web or the console."
            )
        allow = answer.startswith("allow")
        effect = "allow" if allow else "refuse"
        if answer.endswith("_always"):  # before the answer is committed: a rule that cannot be made leaves it pending
            try:
                check_fields(effect, approval.tool.strip(), approval.target.strip(), "", approval.level.strip())
            except RuleError as error:
                raise NoRuleError(
                    f"Request #{approval_id} cannot be remembered as a rule: {error} Answer allow_once or refuse_once."
                ) from None
        changed = self.db.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, answer = ? WHERE id = ? AND status = 'pending'",
            ("approved" if allow else "denied", now_iso(), token.name, answer, approval_id),
        ).rowcount
        if not changed:
            raise AlreadyAnswered(f"Request #{approval_id} was answered a moment ago.")
        if answer.endswith("_always"):
            try:
                rule = self.rules.add(
                    effect,
                    tool=approval.tool,
                    target=approval.target,
                    level=approval.level,
                    origin="approval",
                    note=f"from request #{approval_id}",
                )
                self.db.execute("UPDATE approvals SET rule_id = ? WHERE id = ?", (rule.id, approval_id))
            except Exception:  # the answer is already given: it is carried out, and the rule is the only thing lost
                log.exception(
                    "request #%s: the rule could not be written; the answer applies to this request only", approval_id
                )
        waiter = self._waiters.get(approval_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(answer)  # the chat that asked runs it (or not) and tells the model itself
        else:
            await self._conclude(approval_id, allow)
        return await self._announce(approval_id, handed_over=waiter is not None)

    async def _conclude(self, approval_id: int, allow: bool) -> None:
        """Nobody is waiting for the answer: run the allowed request, or settle the refused one."""
        if allow:
            await self._execute(approval_id)
        else:
            self.finish(approval_id, "denied", "", told=False)

    async def _announce(self, approval_id: int, *, handed_over: bool) -> Approval:
        settled = self.get(approval_id)
        assert settled is not None
        self.events.publish(
            "approval_resolved",
            "Request settled",
            f"{settled.summary}: {settled.status}",
            payload={"approval": settled.describe()},
            conversation_id=settled.conversation_id,
        )
        if not handed_over and settled.conversation_id:
            self.followup_soon(settled.conversation_id)
        return settled

    async def _carry_out(self, approval_id: int, allow: bool) -> None:
        """An answer the chat that held the request never took: do what `decide` does when nobody is waiting."""
        try:
            await self._conclude(approval_id, allow)
            await self._announce(approval_id, handed_over=False)
        except Exception:
            log.exception("could not carry out request #%s after its chat ended", approval_id)

    async def _execute(self, approval_id: int) -> None:
        """Run an approved request nobody is waiting for, as it was frozen."""
        approval = self.get(approval_id)
        assert approval is not None
        if self.toolbox is None or self.context_factory is None:
            self.finish(approval_id, "failed", "Error: the server cannot run approved requests right now.", told=False)
            return
        context = self.context_factory(approval)
        text, ok = await self.toolbox.execute(approval.tool, context, approval.arguments)
        self.finish(approval_id, "done" if ok else "failed", text, told=False)

    def finish(self, approval_id: int, status: str, result: str, *, told: bool) -> None:
        self.db.execute(
            "UPDATE approvals SET status = ?, result = ?, told = ? WHERE id = ?",
            (status, result[:20_000], int(told), approval_id),
        )
        approval = self.get(approval_id)
        if approval is not None:
            row = self.db.one("SELECT args_digest, rule_id FROM approvals WHERE id = ?", (approval_id,))
            action = Action(
                approval.tool,
                approval.level,
                approval.action_class,
                tuple(approval.target.split(" ")) if approval.target else (),
                approval.summary,
            )
            situation = Situation(
                "interactive" if approval.situation.startswith("interactive") else "unattended",
                "tainted" in approval.situation,
                "local only" in approval.situation,
            )
            self.audit.record(
                action,
                situation,
                row["args_digest"],
                Decision("ask", approval.answer or status, row["rule_id"]),
                status,
                conversation_id=approval.conversation_id,
                turn_id=approval.turn_id,
                approval_id=approval_id,
            )

    # ------------------------------------------------------------------
    # Telling Clara
    # ------------------------------------------------------------------
    def news(self, conversation_id: str) -> list[Approval]:
        """Requests of this conversation that ended and that Clara was not told about."""
        rows = self.db.all(
            "SELECT * FROM approvals WHERE conversation_id = ? AND told = 0 AND status IN ('done', 'failed', 'denied',"
            " 'expired') ORDER BY id",
            (conversation_id,),
        )
        return [_approval(row) for row in rows]

    def waiting(self, conversation_id: str) -> list[Approval]:
        rows = self.db.all(
            "SELECT * FROM approvals WHERE conversation_id = ? AND status = 'pending' ORDER BY id", (conversation_id,)
        )
        return [_approval(row) for row in rows]

    def mark_told(self, ids: list[int], told: bool = True) -> None:
        self.db.executemany("UPDATE approvals SET told = ? WHERE id = ?", [(int(told), i) for i in ids])

    def followup_soon(self, conversation_id: str) -> None:
        if self.followup is None or conversation_id in self._scheduled:
            return
        self._scheduled.add(conversation_id)
        task = asyncio.ensure_future(self._followup(conversation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _followup(self, conversation_id: str) -> None:
        await asyncio.sleep(self.followup_delay)
        self._scheduled.discard(conversation_id)
        ended = self.news(conversation_id)
        if not ended or self.followup is None:
            return
        self.mark_told([item.id for item in ended])
        lines = [
            "[Approval update] The person answered requests you had put on hold:",
            *(item.outcome_line() for item in ended),
            "Go on with the task using this, and tell the person briefly how it ended.",
        ]
        try:
            await self.followup(conversation_id, "\n".join(lines))
        except Exception as error:  # the model is down: Clara is told at the next turn instead
            log.warning("follow-up of %s failed: %s", conversation_id, error)
            self.mark_told([item.id for item in ended], told=False)

    # ------------------------------------------------------------------
    # The clock
    # ------------------------------------------------------------------
    def sweep(self) -> None:
        """Push requests from a chat nobody answered within a minute; expire old ones; drop finished ones."""
        now = utcnow()
        self.db.execute(
            "DELETE FROM approvals WHERE status != 'pending' AND created_at < ?",
            (iso(now - timedelta(days=KEEP_DAYS)),),
        )
        for row in self.db.all("SELECT * FROM approvals WHERE status = 'pending' AND notified = 0"):
            if parse_iso(row["created_at"]) + timedelta(seconds=self.push_after) <= now:
                self.push(_approval(row))
        for row in self.db.all("SELECT * FROM approvals WHERE status = 'pending' AND expires_at <= ?", (iso(now),)):
            if self.db.execute(
                "UPDATE approvals SET status = 'expired', decided_at = ? WHERE id = ? AND status = 'pending'",
                (iso(now), row["id"]),
            ).rowcount:
                expired = self.get(row["id"])
                assert expired is not None
                waiter = self._waiters.get(expired.id)
                if waiter is not None and not waiter.done():
                    waiter.set_result(None)
                self.finish(expired.id, "expired", "", told=False)
                self.events.publish(
                    "approval_resolved",
                    "Request expired",
                    expired.summary,
                    payload={"approval": expired.describe()},
                    conversation_id=expired.conversation_id,
                )

    def recover(self) -> int:
        """At start: requests approved but not finished when the server stopped are not run again (they may have been
        done): they are marked failed, with a note saying to check."""
        rows = self.db.all("SELECT id FROM approvals WHERE status = 'approved'")
        for row in rows:
            self.finish(
                row["id"],
                "failed",
                "The server stopped before this finished: it may or may not have been done. "
                "Check before doing it again.",
                told=False,
            )
        return len(rows)

    async def close(self) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.cancel()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
