"""`clara mode import-legacy`: a default-mode file made from the auto levels and the rules the modes replaced.

Migration 0031 keeps them as the unread tables `legacy_auto_levels`, `legacy_tool_levels`, `legacy_rules` and `legacy_settings`
(the default level, the tainted-turn rule). This reads them and PRINTS a file for you to review and install as the default mode
(docs/OPERATIONS.md); it writes nothing. Each class gets the verdict its thresholds gave at the old default level, a tool's own
thresholds become lines for that tool, and each enabled rule becomes lines for its tool and target, never more permissive than
the level that sat under it (a level's refusal could not be opened by a rule). A rule with no tool, or scoped to a situation or
to an action level, has no line that means the same: it is left in a comment.
"""

from __future__ import annotations

import json

from ..db import Database
from .actions import CLASSES, LEVEL_KEYS
from .modes import DEFAULT, Line, Mode, _regex, stricter, tainted_flavour, to_toml, verdict_at

HEADER = """\
# A default mode made by `clara mode import-legacy` from the auto levels, the tool thresholds and the rules that modes replaced.
# Read it before you install it: it holds the verdict each class had at the old default level, the thresholds you set for single
# tools, and your enabled rules. Install it as /etc/clara/default-mode.toml (owned by root, mode 0644) and restart. The header of
# deploy/default-mode.toml says what each field means."""
WORDS = {"allow": "approve", "ask": "ask", "refuse": "refuse"}


def legacy_default(
    db: Database, tools: dict[str, tuple[str, ...]]
) -> tuple[Mode, str, dict[tuple[str, str, str], str]]:
    """(the default mode the legacy tables say, the header of its file, a note for some lines). `tools` maps each tool to its
    action classes."""
    keys = {row["key"]: row["position"] for row in db.all("SELECT position, key FROM legacy_auto_levels") if row["key"]}
    top = int(db.value("SELECT MAX(position) FROM legacy_auto_levels", (), len(LEVEL_KEYS) - 1))
    saved = {row["key"]: json.loads(row["value"]) for row in db.all("SELECT key, value FROM legacy_settings")}

    def position(key: str) -> int:  # a built-in level that was removed counts as the next one above it
        return next((keys[each] for each in LEVEL_KEYS[LEVEL_KEYS.index(key) :] if each in keys), top)

    level = max(0, min(int(saved.get("auto_level_default", position("files_vault"))), top))
    raised = bool(saved.get("tainted_rule", True))
    header = [
        HEADER,
        f"#\n# The old default level was {level} (of 0 to {top}); the tainted-turn rule was {'on' if raised else 'off'}.",
    ]
    if saved.get("auto_level_unattended", level) != level:
        header.append(
            f"# Runs nobody watched were at level {saved['auto_level_unattended']}: one default mode now serves them all."
        )
    overrides: dict[str, dict[str, tuple[int, int]]] = {}
    for row in db.all("SELECT tool, klass, ask_from, approve_from FROM legacy_tool_levels"):
        overrides.setdefault(row["tool"], {})[row["klass"]] = (min(row["ask_from"], top), min(row["approve_from"], top))

    def thresholds(tool: str, klass: str) -> tuple[int, int]:
        own = overrides.get(tool, {})
        if klass in own or "" in own:
            return own.get(klass) or own[""]
        ask_key, approve_key, _ = CLASSES[klass]
        approve = max(1, position(approve_key))
        return min(max(1, position(ask_key)), approve), approve

    def flavour(klass: str, verdict: str) -> str:
        return tainted_flavour(klass, verdict) if raised else verdict

    lines: dict[tuple[str, str, str], Line] = {}
    notes: dict[tuple[str, str, str], str] = {}

    by_rule: set[tuple[str, str, str]] = set()

    def add(line: Line, note: str = "", rule: bool = False) -> None:
        kept = lines.get(line.key)
        # a rule beat the thresholds under it; two rules for one line: the stricter
        if kept is None or (rule and line.key not in by_rule) or stricter(line.verdict, kept.verdict) == line.verdict:
            lines[line.key] = line
            if note:
                notes[line.key] = note
            if rule:
                by_rule.add(line.key)

    for klass in CLASSES:
        verdict = verdict_at(level, top, *thresholds("", klass))
        add(Line(verdict, klass=klass, tainted=flavour(klass, verdict)))
    for tool in sorted(overrides):
        if tool not in tools:
            header.append(f"# Not imported: the thresholds you set for {tool}, which is not a tool of this Clara.")
        for klass in tools.get(tool, ()):
            verdict = verdict_at(level, top, *thresholds(tool, klass))
            base = lines[("", klass, "")]
            if (verdict, flavour(klass, verdict)) != (base.verdict, base.tainted):
                add(
                    Line(verdict, tool, klass, tainted=flavour(klass, verdict)),
                    f"{tool}: the thresholds you set for it",
                )
    for rule in db.all("SELECT * FROM legacy_rules WHERE enabled = 1 ORDER BY id"):
        said = f"rule #{rule['id']}: {rule['effect']} tool={rule['tool']} target={rule['target']}" + (
            f" ({rule['note']})" if rule["note"] else ""
        )
        matched = [
            (name, klasses) for name, klasses in tools.items() if rule["tool"] and _regex(rule["tool"]).match(name)
        ]
        why = (
            "it names no tool"
            if not rule["tool"]
            else f"it is for the situation {rule['situation']}"
            if rule["situation"]
            else f"it is for the action level {rule['level']}"
            if rule["level"]
            else "no tool of this Clara matches it"
            if not matched
            else ""
        )
        if why:
            header.append(f"# Not imported, {why}: {said}")
            continue
        for name, klasses in matched:
            for klass in klasses:
                refused = (
                    verdict_at(level, top, *thresholds(name, klass)) == "refuse"
                )  # a rule never opened what a level refused
                effect = "refuse" if refused else WORDS[rule["effect"]]
                add(Line(effect, name, klass, rule["target"], flavour(klass, effect)), said, rule=True)
    return Mode(DEFAULT, (), tuple(lines.values())), "\n".join(header), notes


def import_legacy(db: Database, tools: dict[str, tuple[str, ...]]) -> str:
    """The text of the default-mode file the legacy tables make."""
    mode, header, notes = legacy_default(db, tools)
    return to_toml(mode, header, notes)
