"""The rules: allow, ask or refuse for the actions they match. You write them; Clara never reads or edits them.

A rule matches an action when every field it sets matches: the tool (a name or glob), the target (a glob), the
situation (interactive, unattended, or tainted) and the level. An action with several targets (a move: from and to)
needs every target to match an `allow` or `ask` rule, but one target is enough for a `refuse` rule.

The most specific matching rule wins: the one with more fields set, then the longer literal target, then the longer
literal tool. On a tie, the stricter effect wins: refuse, then ask, then allow.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ..timeutil import now_iso
from .actions import (
    EFFECTS,
    LEVELS,
    SITUATIONS,
    STRICTNESS,
    Action,
    Situation,
    glob_regex,
    literal_length,
    normal_target,
)

ORIGINS = ("console", "approval")


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: int
    effect: str
    tool: str
    target: str
    situation: str
    level: str
    origin: str
    note: str
    enabled: bool
    hits: int
    last_hit_at: str | None
    created_at: str

    def matches(self, action: Action, situation: Situation) -> bool:
        if not self.enabled:
            return False
        if self.tool and not glob_regex(self.tool).match(action.tool):
            return False
        if self.level and self.level != action.level:
            return False
        if self.situation:
            if self.situation == "tainted" and not situation.tainted:
                return False
            if self.situation in ("interactive", "unattended") and self.situation != situation.mode:
                return False
        if self.target:
            pattern = glob_regex(normal_target(self.target))
            hits = [bool(pattern.match(target)) for target in action.targets]
            if not hits:
                return False
            if self.effect == "refuse" and not any(hits):
                return False
            if self.effect != "refuse" and not all(hits):
                return False
        return True

    def specificity(self) -> tuple[int, int, int, int]:
        fields = sum(bool(value) for value in (self.tool, self.target, self.situation, self.level))
        return (fields, literal_length(normal_target(self.target)) if self.target else -1,
                literal_length(self.tool) if self.tool else -1, STRICTNESS[self.effect])

    def describe(self) -> dict:
        return {"id": self.id, "effect": self.effect, "tool": self.tool, "target": self.target,
                "situation": self.situation, "level": self.level, "origin": self.origin, "note": self.note,
                "enabled": self.enabled, "hits": self.hits, "last_hit_at": self.last_hit_at,
                "created_at": self.created_at}

    def line(self) -> str:
        parts = [f"#{self.id} {self.effect}"]
        for name in ("tool", "target", "situation", "level"):
            value = getattr(self, name)
            if value:
                parts.append(f"{name}={value}")
        if not self.enabled:
            parts.append("(off)")
        if self.note:
            parts.append(f"— {self.note}")
        return " ".join(parts)


def _rule(row) -> Rule:
    return Rule(row["id"], row["effect"], row["tool"], row["target"], row["situation"], row["level"], row["origin"],
                row["note"], bool(row["enabled"]), row["hits"], row["last_hit_at"], row["created_at"])


def check_fields(effect: str, tool: str, target: str, situation: str, level: str) -> None:
    if effect not in EFFECTS:
        raise RuleError(f"effect must be one of {', '.join(EFFECTS)}.")
    if situation and situation not in SITUATIONS:
        raise RuleError(f"situation must be one of {', '.join(SITUATIONS)} (or empty for any).")
    if level and level not in LEVELS:
        raise RuleError(f"level must be one of {', '.join(LEVELS)} (or empty for any).")
    if len(tool) > 100 or len(target) > 400:
        raise RuleError("The tool or the target is too long.")
    if not (tool or target or situation or level):
        raise RuleError("A rule needs at least one of tool, target, situation, level: it would match everything.")


class Rules:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[Rule]:
        return [_rule(row) for row in self.db.all("SELECT * FROM rules ORDER BY id")]

    def get(self, rule_id: int) -> Rule | None:
        row = self.db.one("SELECT * FROM rules WHERE id = ?", (rule_id,))
        return _rule(row) if row else None

    def add(self, effect: str, *, tool: str = "", target: str = "", situation: str = "", level: str = "",
            origin: str = "console", note: str = "") -> Rule:
        tool, target, situation, level = tool.strip(), target.strip(), situation.strip(), level.strip()
        check_fields(effect, tool, target, situation, level)
        if origin not in ORIGINS:
            raise RuleError(f"origin must be one of {', '.join(ORIGINS)}")
        now = now_iso()
        rule_id = self.db.insert(
            "INSERT INTO rules (effect, tool, target, situation, level, origin, note, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (effect, tool, target, situation, level, origin, note.strip()[:200], now, now),
        )
        rule = self.get(rule_id)
        assert rule is not None
        return rule

    def update(self, rule_id: int, **fields) -> Rule:
        rule = self.get(rule_id)
        if rule is None:
            raise RuleError(f"No rule {rule_id}.")
        allowed = {"effect", "tool", "target", "situation", "level", "note", "enabled"}
        unknown = set(fields) - allowed
        if unknown:
            raise RuleError(f"Unknown fields: {', '.join(sorted(unknown))}")
        merged = {**rule.describe(), **{key: value for key, value in fields.items() if value is not None}}
        check_fields(merged["effect"], merged["tool"].strip(), merged["target"].strip(), merged["situation"],
                     merged["level"])
        self.db.execute(
            "UPDATE rules SET effect = ?, tool = ?, target = ?, situation = ?, level = ?, note = ?, enabled = ?,"
            " updated_at = ? WHERE id = ?",
            (merged["effect"], merged["tool"].strip(), merged["target"].strip(), merged["situation"], merged["level"],
             str(merged["note"]).strip()[:200], int(bool(merged["enabled"])), now_iso(), rule_id),
        )
        updated = self.get(rule_id)
        assert updated is not None
        return updated

    def delete(self, rule_id: int) -> bool:
        return self.db.execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount > 0

    def best(self, action: Action, situation: Situation) -> Rule | None:
        """The rule that decides this action, or None (the default of its class applies)."""
        matching = [rule for rule in self.list() if rule.matches(action, situation)]
        if not matching:
            return None
        return max(matching, key=lambda rule: rule.specificity())

    def hit(self, rule_id: int) -> None:
        self.db.execute("UPDATE rules SET hits = hits + 1, last_hit_at = ? WHERE id = ?", (now_iso(), rule_id))
