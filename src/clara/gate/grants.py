"""Approve all next: « allow once » said for you, for a while, to one tool in one conversation.

You press the button on an approval card of the web site. The request on the card is allowed, and the next requests of
the same tool (and the same action class) in that conversation are allowed without a card, until the grant ends:

    the page stops beating   the page sends a beat every BEAT seconds; SILENCE without one ends the grant (a page that
                             is closed, reloaded or left, or a computer that sleeps; a hidden tab's timers may slow
                             to one a minute, so SILENCE is longer than that)
    a limit                  `approve_all_minutes` since the press, or `approve_all_calls` calls (runtime settings)
    Stop                     the banner's button, or the page leaving the conversation
    a restart                a grant lives in memory only

A grant answers the plain default « ask » and nothing else. It never covers what the gate raised on purpose (a tainted
turn, content that looks like a secret), only workspace and vault actions and a mail moved to the Trash (GRANTABLE: a
workspace or vault change is a git commit, so it can be undone; a trashed mail stays in the Trash, and a tainted turn
does not raise it), only a turn from the web, and never an unattended run. It writes no rule. Every call it
lets through is in the audit log with the grant's number, and the web site still prints the tool call.
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

GRANTABLE = frozenset({"ws_write", "ws_destroy", "vault_write", "vault_destroy", "mail_trash"})
BEAT = 10  # seconds between the page's beats
SILENCE = 90  # seconds without a beat that end a grant
CLIENT = "web"


class GrantError(ValueError):
    pass


@dataclass
class Grant:
    id: int
    conversation_id: str
    tool: str
    klass: str
    level: str
    tab: str  # the page that pressed it: no other page keeps it alive
    token: str  # name of the token that pressed it
    started: float
    beat: float
    calls: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.conversation_id, self.tool, self.klass)


class Grants:
    def __init__(self, runtime: RuntimeSettings, audit: Audit, *, clock: Callable[[], float] = time.monotonic):
        self.runtime = runtime
        self.audit = audit
        self.clock = clock
        self._active: dict[tuple[str, str, str], Grant] = {}
        self._ended: dict[str, list[dict]] = {}  # conversation -> ends the page has not been told about yet
        self._last = 0

    # ------------------------------------------------------------------
    # The limits you set
    # ------------------------------------------------------------------
    @property
    def max_seconds(self) -> int:
        return int(self.runtime.get("approve_all_minutes")) * 60

    @property
    def max_calls(self) -> int:
        return int(self.runtime.get("approve_all_calls"))

    # ------------------------------------------------------------------
    # Pressing, beating, stopping
    # ------------------------------------------------------------------
    def arm(self, approval: Approval, tab: str, token: TokenInfo) -> Grant:
        """Start a grant for the tool and class of a request that is still waiting (the caller answers that request)."""
        if token.client != CLIENT:
            raise GrantError("Only the web site can press Approve all next.")
        if not approval.conversation_id:
            raise GrantError("This request belongs to no conversation.")
        if approval.action_class not in GRANTABLE:
            raise GrantError(
                f"Approve all next covers workspace and vault changes and mail trashing only, not {approval.action_class}."
            )
        if not approval.situation.startswith("interactive"):
            raise GrantError("Approve all next covers a chat, not an unattended run.")
        self._expire()
        now = self.clock()
        self._last += 1
        grant = Grant(
            self._last,
            approval.conversation_id,
            approval.tool,
            approval.action_class,
            approval.level,
            tab,
            token.name,
            now,
            now,
        )
        replaced = self._active.get(grant.key)
        if replaced is not None:
            self._end(replaced, "pressed again")
        self._active[grant.key] = grant
        self._record(
            grant, "grant", f"armed by {token.name}: {self.max_calls} calls or {self.max_seconds // 60} minutes"
        )
        return grant

    def beat(self, conversation_id: str, tab: str) -> dict:
        """The page is still there: what is on, and what ended since its last beat."""
        self._expire()
        now = self.clock()
        for grant in self._of(conversation_id):
            if grant.tab == tab:
                grant.beat = now
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
                "class": grant.klass,
                "calls": grant.calls,
                "max_calls": self.max_calls,
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
        left alone what the gate raised on purpose."""
        if situation.mode != "interactive" or client != CLIENT or not conversation_id:
            return None
        self._expire()
        grant = self._active.get((conversation_id, action.tool, action.klass))
        if grant is None:
            return None
        grant.calls += 1
        return grant

    def decision(self, grant: Grant) -> Decision:
        return Decision("ask", f"Approve all next #{grant.id}", None)

    def detail(self, grant: Grant) -> str:
        return f"auto-approved by Approve all next #{grant.id}, pressed by {grant.token}: call {grant.calls} of {self.max_calls}"

    # ------------------------------------------------------------------
    # Ending
    # ------------------------------------------------------------------
    def _of(self, conversation_id: str) -> list[Grant]:
        return [grant for grant in self._active.values() if grant.conversation_id == conversation_id]

    def _expire(self) -> None:
        now = self.clock()
        for grant in list(self._active.values()):
            if now - grant.beat > SILENCE:
                self._end(grant, "the page stopped beating")
            elif now - grant.started > self.max_seconds:
                self._end(grant, f"{self.max_seconds // 60} minutes passed")
            elif grant.calls >= self.max_calls:
                self._end(grant, f"{self.max_calls} calls were made")

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
            Decision("ask", f"Approve all next #{grant.id}", None),
            outcome,
            conversation_id=grant.conversation_id,
            detail=f"#{grant.id} {detail}",
        )
