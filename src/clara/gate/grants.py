"""Approve all next: « allow once » said for you, for a while, to one tool in one conversation.

You press the button on an approval card of the web site. The request on the card is allowed, and the next calls of the
same tool in that conversation that would ask are allowed without a card, until the grant ends:

    a limit    `approve_all_minutes` (30) since the press
    Stop       the banner's button, or the page leaving the conversation
    a restart  a grant lives in memory only

It covers any tool the button is pressed on, and answers the plain « ask » of the conversation's mode and nothing else:
where the mode refuses, or approves, there is nothing to answer, and in a tainted turn it only answers a tool whose
`(if tainted)` verdict is `ask` (the gate has already chosen that verdict when the grant is asked). It never covers the
secret check (content that looks like a secret leaving the machine), only a turn from the web, and never an unattended
run. It writes no rule. Every call it lets through is in the audit log with the grant's number, and the web site still
prints the tool call.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .actions import Action, Decision, Situation

if TYPE_CHECKING:
    from ..auth.tokens import TokenInfo
    from ..runtime import RuntimeSettings
    from .approvals import Approval
    from .engine import Audit

CLIENT = "web"


class GrantError(ValueError):
    pass


@dataclass
class Grant:
    id: int
    conversation_id: str
    tool: str
    klass: str  # the class and level of the request it was pressed on: for the audit log only
    level: str
    token: str  # name of the token that pressed it
    started: float
    calls: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.conversation_id, self.tool)


class Grants:
    def __init__(self, runtime: RuntimeSettings, audit: Audit, *, clock: Callable[[], float] = time.monotonic):
        self.runtime = runtime
        self.audit = audit
        self.clock = clock
        self._active: dict[tuple[str, str], Grant] = {}
        self._ended: dict[str, list[dict]] = {}  # conversation -> ends the page has not been told about yet
        self._last = 0

    @property
    def max_seconds(self) -> int:
        return int(self.runtime.get("approve_all_minutes")) * 60

    # ------------------------------------------------------------------
    # Pressing, reading, stopping
    # ------------------------------------------------------------------
    def arm(self, approval: Approval, token: TokenInfo) -> Grant:
        """Start a grant for the tool of a request that is still waiting (the caller answers that request)."""
        if token.client != CLIENT:
            raise GrantError("Only the web site can press Approve all next.")
        if not approval.conversation_id:
            raise GrantError("This request belongs to no conversation.")
        if not approval.situation.startswith("interactive"):
            raise GrantError("Approve all next covers a chat, not an unattended run.")
        self._expire()
        self._last += 1
        grant = Grant(
            self._last,
            approval.conversation_id,
            approval.tool,
            approval.action_class,
            approval.level,
            token.name,
            self.clock(),
        )
        replaced = self._active.get(grant.key)
        if replaced is not None:
            self._end(replaced, "pressed again")
        self._active[grant.key] = grant
        self._record(grant, "grant", f"armed by {token.name}: {self.max_seconds // 60} minutes")
        return grant

    def state(self, conversation_id: str) -> dict:
        """What is on, and what ended since the page last asked."""
        self._expire()
        return {"grants": self.describe(conversation_id), "ended": self._ended.pop(conversation_id, [])}

    def stop(self, conversation_id: str, reason: str = "stopped") -> int:
        self._expire()
        grants = self._of(conversation_id)
        for grant in grants:
            self._end(grant, reason)
        return len(grants)

    def cancel(self, grant: Grant, reason: str) -> None:
        """The request it was pressed on could not be allowed after all: the grant never starts."""
        self._end(grant, reason)

    def describe(self, conversation_id: str) -> list[dict]:
        now = self.clock()
        return [
            {
                "id": grant.id,
                "tool": grant.tool,
                "calls": grant.calls,
                "seconds_left": max(0, int(grant.started + self.max_seconds - now)),
                "pressed_by": grant.token,
            }
            for grant in self._of(conversation_id)
        ]

    # ------------------------------------------------------------------
    # The gate asks
    # ------------------------------------------------------------------
    def cover(self, action: Action, situation: Situation, conversation_id: str, client: str) -> Grant | None:
        """The grant that lets this `ask` through, counted as one call; None if none does. The caller has already
        left alone what the secret check raised."""
        if situation.mode != "interactive" or client != CLIENT or not conversation_id:
            return None
        self._expire()
        grant = self._active.get((conversation_id, action.tool))
        if grant is None:
            return None
        grant.calls += 1
        return grant

    def decision(self, grant: Grant) -> Decision:
        return Decision("ask", f"Approve all next #{grant.id}")

    def detail(self, grant: Grant) -> str:
        return f"auto-approved by Approve all next #{grant.id}, pressed by {grant.token}: call {grant.calls}"

    # ------------------------------------------------------------------
    # Ending
    # ------------------------------------------------------------------
    def _of(self, conversation_id: str) -> list[Grant]:
        return [grant for grant in self._active.values() if grant.conversation_id == conversation_id]

    def _expire(self) -> None:
        now = self.clock()
        for grant in list(self._active.values()):
            if now - grant.started > self.max_seconds:
                self._end(grant, f"{self.max_seconds // 60} minutes passed")

    def _end(self, grant: Grant, reason: str) -> None:
        if self._active.get(grant.key) is not grant:
            return
        del self._active[grant.key]
        self._ended.setdefault(grant.conversation_id, []).append(
            {"id": grant.id, "tool": grant.tool, "calls": grant.calls, "reason": reason}
        )
        self._record(grant, "grant_end", f"{reason}; {grant.calls} calls")

    # ------------------------------------------------------------------
    # The audit log
    # ------------------------------------------------------------------
    def _record(self, grant: Grant, outcome: str, detail: str) -> None:
        action = Action(grant.tool, grant.level, grant.klass, (), f"Approve all next #{grant.id}")
        self.audit.record(
            action,
            Situation("interactive"),
            "",
            Decision("ask", f"Approve all next #{grant.id}"),
            outcome,
            conversation_id=grant.conversation_id,
            detail=f"#{grant.id} {detail}",
        )
