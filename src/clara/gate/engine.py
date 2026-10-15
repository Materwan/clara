"""The rule gate: every tool call resolves to allow, ask or refuse, in this order.

1. **The floor** refuses, and no rule can change it: touching `.git` in the workspace (scripts and tools never see
   it), and any target the tools could not have produced. Writing outside the workspace, reading the database or the
   secrets, and running a script with the server's environment are impossible by construction (path resolution, the
   container runner); the floor is the last check behind them.
2. **The rules** you wrote: the most specific matching one decides.
3. **The default** of the action's class, for a chat or an unattended run (actions.CLASSES).
4. **Raises**, which only ever make it stricter: in a *tainted* turn (it read a web page, a fetched file, an issue…),
   outside-world writes and networked scripts become at least `ask` (runtime setting `tainted_rule`); content that
   looks like a secret leaving the machine becomes at least `ask`, always.

The model has no tool to read, edit or approve any of this.
"""

from __future__ import annotations

import hashlib
import json

from ..db import Database
from ..runtime import RuntimeSettings
from ..timeutil import now_iso
from .actions import CLASSES, LEAVES_MACHINE, OUTSIDE, RAISED_WHEN_TAINTED, STRICTNESS, Action, Decision, Situation
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
    def __init__(self, db: Database, rules: Rules, runtime: RuntimeSettings, *, vault_push: bool = True):
        self.db = db
        self.rules = rules
        self.runtime = runtime
        self.local_refused = LEAVES_MACHINE if vault_push else LEAVES_MACHINE - {"vault_write", "vault_destroy"}

    def decide(self, action: Action, situation: Situation, *, count_hit: bool = True) -> Decision:
        why = floor(action)
        if why:
            return Decision("refuse", why, floor=True)
        if situation.local_only and action.klass in self.local_refused:
            return Decision("refuse", "this conversation is local only: nothing may leave your machines", floor=True)
        rule = self.rules.best(action, situation)
        if rule is not None:
            effect, reason = rule.effect, f"rule #{rule.id}"
            if count_hit:
                self.rules.hit(rule.id)
        else:
            chat, unattended, _ = CLASSES[action.klass]
            effect = chat if situation.mode == "interactive" else unattended
            reason = "default"
        raised: list[str] = []
        if situation.tainted and action.klass in RAISED_WHEN_TAINTED and self.runtime.get("tainted_rule"):
            if effect == "allow":
                raised.append("this turn read untrusted content")
            effect = stricter(effect, "ask")
        if action.secret_kinds and action.klass in OUTSIDE:
            if effect == "allow":
                raised.append(f"the content looks like a secret ({', '.join(action.secret_kinds)})")
            effect = stricter(effect, "ask")
        return Decision(effect, reason, rule.id if rule else None, raised=tuple(raised))

    def explain(self, action: Action, situation: Situation) -> dict:
        """For the rule tester: what would decide this action, without counting a hit."""
        decision = self.decide(action, situation, count_hit=False)
        matching = [rule.describe() for rule in self.rules.list() if rule.matches(action, situation)]
        return {
            "effect": decision.effect, "reason": decision.reason, "rule": decision.rule_id, "floor": decision.floor,
            "raised": list(decision.raised), "matching": matching,
            "default": dict(zip(("interactive", "unattended", "description"), CLASSES[action.klass])),
        }


class Audit:
    """Every gate decision and how it ended, in `audit_log` (arguments only as a digest)."""

    def __init__(self, db: Database):
        self.db = db

    def record(self, action: Action, situation: Situation, digest: str, decision: Decision, outcome: str, *,
               conversation_id: str | None = None, turn_id: str = "", approval_id: int | None = None,
               detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, conversation_id, turn_id, tool, target, level, situation, args_digest, rule_id,"
            " effect, outcome, approval_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), conversation_id, turn_id, action.tool, action.target[:500], action.level, situation.describe(),
             digest, decision.rule_id, decision.effect, outcome, approval_id,
             (detail or "; ".join((decision.reason, *decision.raised)))[:500]),
        )

    def recent(self, limit: int = 200, before_id: int | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit_log WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id or 2**62, limit))
        return [dict(row) for row in rows]
