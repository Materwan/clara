"""The gate: every tool call resolves to allow, ask or refuse, in this order.

1. **The floor** refuses, and nothing changes it: touching `.git` in the workspace (scripts and tools never see it), and
   any target the tools could not have produced. Writing outside the workspace, reading the database or the secrets, and
   running a script with the server's environment are impossible by construction (path resolution, the container
   runner); the floor is the last check behind them.
2. **Local only**: a conversation that must not send anything off the machine refuses what would.
3. **Chat only**: what reads your computer through an open web chat is refused to a run nobody watches.
4. **The verdict of the conversation's mode** (modes.py) for this tool, class and target: approve, ask or refuse. A turn
   that read untrusted content (a web page, a fetched file, an issue…) gets the line's `(if tainted)` verdict. A call the
   mode has no line for takes the default mode's. A refusal is final.
5. **The secret check**, which only ever makes it stricter: content that looks like a secret, or repeats private text the
   conversation read (a search query: egress.py), leaving the machine becomes at least `ask`, always, in every mode.

The model has no tool to read, edit or approve any of this.
"""

from __future__ import annotations

import hashlib
import json

from ..db import Database
from ..timeutil import now_iso
from .actions import (
    CHAT_ONLY,
    CLASSES,
    LEAVES_MACHINE,
    OUTSIDE,
    STRICTNESS,
    Action,
    Decision,
    Situation,
)
from .modes import DEFAULT, VERDICT_EFFECT, Modes


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
    def __init__(self, modes: Modes, *, vault_push: bool = True):
        self.modes = modes
        self.local_refused = LEAVES_MACHINE if vault_push else LEAVES_MACHINE - {"vault_write", "vault_destroy"}

    def decide(self, action: Action, situation: Situation, *, approved: bool = False) -> Decision:
        """`approved`: you already answered a request for this very call, so a mode that approves or asks has nothing more to say
        (the floor, the local-only rule and a mode that refuses can still stop it).

        A call of the model must come with `situation.mode_name` set to its conversation's mode (Toolbox.call does it at every
        call); without one the default mode decides, which only suits a question asked on no conversation's behalf."""
        why = floor(action)
        if why:
            return Decision("refuse", why, floor=True)
        if situation.local_only and action.klass in self.local_refused:
            return Decision("refuse", "this conversation is local only: nothing may leave your machines", floor=True)
        if situation.mode == "unattended" and action.klass in CHAT_ONLY:
            return Decision(
                "refuse", "this reads your computer through an open web chat: a run nobody watches cannot", floor=True
            )
        mode, line, verdict = self.modes.verdict(action, situation)
        reason = self.modes.reason(mode, line, verdict, action.tool, situation.tainted)
        if verdict == "refuse":
            return Decision("refuse", reason, by_mode=True)
        effect = "allow" if approved else VERDICT_EFFECT[verdict]
        raised: list[str] = []
        if not approved and self.secret_raise(action):
            if effect == "allow":
                raised.append(
                    f"the content looks like a secret or repeats private text ({', '.join(action.secret_kinds)})"
                )
            effect = stricter(effect, "ask")
        return Decision(effect, reason, raised=tuple(raised))

    @staticmethod
    def secret_raise(action: Action) -> bool:
        """Does this action carry content that looks like a secret, or repeats private text, out of the machine?"""
        return bool(action.secret_kinds) and action.klass in OUTSIDE

    def explain(self, action: Action, situation: Situation) -> dict:
        """For the mode tester: what would decide this action, which line, and the verdicts that line gives."""
        decision = self.decide(action, situation)
        mode, line, _ = self.modes.verdict(action, situation)
        return {
            "effect": decision.effect,
            "reason": decision.reason,
            "floor": decision.floor,
            "by_mode": decision.by_mode,
            "raised": list(decision.raised),
            "mode": mode.name,
            "from_default": mode is self.modes.default and (situation.mode_name or DEFAULT).lower() != DEFAULT,
            "line": line.describe() if line else None,
            "verdicts": {"normal": line.verdict, "tainted": line.tainted} if line else None,
            "class": {"name": action.klass, "description": CLASSES[action.klass][2]},
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
                None,
                decision.effect,
                outcome,
                approval_id,
                (detail or "; ".join((decision.reason, *decision.raised)))[:500],
            ),
        )

    def recent(self, limit: int = 200, before_id: int | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit_log WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id or 2**62, limit))
        return [dict(row) for row in rows]
