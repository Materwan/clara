"""The rule gate: every tool call resolves to allow, ask or refuse, in this order.

1. **The floor** refuses, and no rule can change it: touching `.git` in the workspace (scripts and tools never see
   it), and any target the tools could not have produced. Writing outside the workspace, reading the database or the
   secrets, and running a script with the server's environment are impossible by construction (path resolution, the
   container runner); the floor is the last check behind them.
2. **The level** of the conversation (levels.py): where its table refuses the tool, it is refused and no rule can
   change that (level 0 refuses every tool).
3. **The rules** you wrote: the most specific matching one decides.
4. **The level again**, when no rule matches: its table says allow or ask.
5. **Raises**, which only ever make it stricter: in a *tainted* turn (it read a web page, a fetched file, an issue…),
   outside-world writes and networked scripts become at least `ask` (runtime setting `tainted_rule`); content that
   looks like a secret leaving the machine becomes at least `ask`, always. The last level of a chat suspends both
   (DECISIONS.md, « Auto levels »); a run nobody watches never does.

The model has no tool to read, edit or approve any of this.
"""

from __future__ import annotations

import hashlib
import json

from ..db import Database
from ..runtime import RuntimeSettings
from ..timeutil import now_iso
from .actions import (
    CHAT_ONLY,
    CLASSES,
    LEAVES_MACHINE,
    OUTSIDE,
    RAISED_WHEN_TAINTED,
    STRICTNESS,
    Action,
    Decision,
    Situation,
)
from .levels import Levels
from .rules import Rules


def args_digest(arguments: dict) -> str:
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def floor(action: Action) -> str | None:
    """Why the floor refuses this action (None: it does not)."""
    for target in action.targets:
        if target.startswith("ws:"):
            parts = target[3:].split("/")
            if ".git" in parts:
                return "the workspace's .git folder is never reachable"
            if target[3:].startswith("/") or ".." in parts:
                return "a workspace path may not leave the workspace"
    return None


def stricter(effect: str, other: str) -> str:
    return effect if STRICTNESS[effect] >= STRICTNESS[other] else other


class Gate:
    def __init__(
        self, db: Database, rules: Rules, runtime: RuntimeSettings, levels: Levels, *, vault_push: bool = True
    ):
        self.db = db
        self.rules = rules
        self.runtime = runtime
        self.levels = levels
        self.local_refused = LEAVES_MACHINE if vault_push else LEAVES_MACHINE - {"vault_write", "vault_destroy"}

    def decide(
        self, action: Action, situation: Situation, *, count_hit: bool = True, approved: bool = False
    ) -> Decision:
        """`approved`: you already answered a request for this very call, so the level has nothing more to say (only the floor,
        the local-only rule and a rule that refuses can still stop it)."""
        why = floor(action)
        if why:
            return Decision("refuse", why, floor=True)
        if situation.local_only and action.klass in self.local_refused:
            return Decision("refuse", "this conversation is local only: nothing may leave your machines", floor=True)
        if situation.mode == "unattended" and action.klass in CHAT_ONLY:
            return Decision(
                "refuse", "this reads your computer through an open web chat: a run nobody watches cannot", floor=True
            )
        level = self.level_of(situation)
        table = "allow" if approved else self.levels.effect(action.tool, action.klass, level)
        if table == "refuse":
            return Decision("refuse", self.levels.reason(action.tool, action.klass, level, table), by_level=True)
        rule = self.rules.best(action, situation)
        if rule is not None:
            effect, reason = rule.effect, f"rule #{rule.id}"
            if count_hit:
                self.rules.hit(rule.id)
        else:
            effect, reason = table, self.levels.reason(action.tool, action.klass, level, table)
        raised: list[str] = []
        if not approved:
            if self.tainted_raise(action, situation):
                if effect == "allow":
                    raised.append("this turn read untrusted content")
                effect = stricter(effect, "ask")
            if self.secret_raise(action, situation):
                if effect == "allow":
                    raised.append(f"the content looks like a secret ({', '.join(action.secret_kinds)})")
                effect = stricter(effect, "ask")
        return Decision(effect, reason, rule.id if rule else None, raised=tuple(raised))

    def level_of(self, situation: Situation) -> int:
        """The level a situation runs at: the one it carries, else the default of its mode."""
        if situation.level is None:
            return self.levels.default_level(situation.mode)
        return max(0, min(situation.level, self.levels.top))

    def raises_suspended(self, situation: Situation) -> bool:
        """The last level of a chat suspends the raises. A run nobody watches never does: a hook starts tainted."""
        return situation.mode == "interactive" and self.level_of(situation) >= self.levels.top

    def tainted_raise(self, action: Action, situation: Situation) -> bool:
        """Does the tainted-turn rule apply to this action, whatever the effect would be otherwise?"""
        return (
            situation.tainted
            and action.klass in RAISED_WHEN_TAINTED
            and bool(self.runtime.get("tainted_rule"))
            and not self.raises_suspended(situation)
        )

    def secret_raise(self, action: Action, situation: Situation) -> bool:
        """Does this action carry content that looks like a secret out of the machine?"""
        return bool(action.secret_kinds) and action.klass in OUTSIDE and not self.raises_suspended(situation)

    def explain(self, action: Action, situation: Situation) -> dict:
        """For the rule tester: what would decide this action, without counting a hit."""
        decision = self.decide(action, situation, count_hit=False)
        matching = [rule.describe() for rule in self.rules.list() if rule.matches(action, situation)]
        level = self.level_of(situation)
        ask, approve = self.levels.thresholds(action.tool, action.klass)
        return {
            "effect": decision.effect,
            "reason": decision.reason,
            "rule": decision.rule_id,
            "floor": decision.floor,
            "by_level": decision.by_level,
            "raised": list(decision.raised),
            "matching": matching,
            "level": {"position": level, "name": self.levels.name(level), "ask_from": ask, "approve_from": approve},
            "default": {"description": CLASSES[action.klass][2], "ask_from": ask, "approve_from": approve},
        }


class Audit:
    """Every gate decision and how it ended, in `audit_log` (arguments only as a digest)."""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        action: Action,
        situation: Situation,
        digest: str,
        decision: Decision,
        outcome: str,
        *,
        conversation_id: str | None = None,
        turn_id: str = "",
        approval_id: int | None = None,
        detail: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, conversation_id, turn_id, tool, target, level, situation, args_digest, rule_id,"
            " effect, outcome, approval_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now_iso(),
                conversation_id,
                turn_id,
                action.tool,
                action.target[:500],
                action.level,
                situation.describe(),
                digest,
                decision.rule_id,
                decision.effect,
                outcome,
                approval_id,
                (detail or "; ".join((decision.reason, *decision.raised)))[:500],
            ),
        )

    def recent(self, limit: int = 200, before_id: int | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit_log WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id or 2**62, limit))
        return [dict(row) for row in rows]
