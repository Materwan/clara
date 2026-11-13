"""Modes: what a conversation may use, and what happens to each tool.

A **mode** is a name, a list of tool packs and **verdict lines**. A line says `approve`, `ask` or `refuse` for the calls
it matches, in two flavours: for a normal turn (`verdict`) and for a turn that read untrusted content (`tainted`, never
more permissive than the first). A line names a tool (a name or a glob), an action class (actions.CLASSES) and/or a
target (a glob), at least one of them. A call is decided by the most specific line that matches it: more fields set,
then the longer literal target, then the longer literal tool; on a tie the stricter verdict. A call a mode has no line
for is decided by the **default mode**.

The default mode is the only predefined one. It is a TOML file the server reads at start (`CLARA_DEFAULT_MODE_FILE`,
root-owned: neither the server, a script nor any token can change it; changing it takes a restart). With no file the
built-in copy below is used (what the default level 3 did) and an alert says so; an invalid file stops the server.
The other modes live in the database (`modes`, `mode_packs`, `mode_verdicts`) and only a token that may answer every
request changes them (api/modes.py).

A conversation's mode is its own, else its schedule's or hook's, else its project's, else the default (`Modes.where`).
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ..db import Database
from ..memory.conversations import Conversations
from ..timeutil import now_iso
from .actions import (
    CLASSES,
    LEVEL_KEYS,
    RAISED_WHEN_TAINTED,
    Action,
    Decision,
    Situation,
    glob_regex,
    literal_length,
    normal_target,
)

if TYPE_CHECKING:
    from .engine import Audit

log = logging.getLogger(__name__)

VERDICTS = ("approve", "ask", "refuse")
VERDICT_EFFECT = {"approve": "allow", "ask": "ask", "refuse": "refuse"}
STRICT = {"approve": 0, "ask": 1, "refuse": 2}
DEFAULT = "default"  # the name of the default mode, which no other mode may take
MAX_NAME = 30
STORED = ("conversations", "projects", "schedules", "triggers")  # the tables with a `mode` column
FIELDS = {"tool", "class", "target", "verdict", "tainted"}
_NAME = re.compile(rf"^[^\x00-\x1f/\\?#%]{{1,{MAX_NAME}}}$")


class ModeError(ValueError):
    """A mode, a line or a change that cannot be made: the message says why."""


class DefaultModeError(ModeError):
    """The default-mode file is invalid: the server does not start."""


class MissingRow(ModeError):
    """The project, schedule, hook or conversation a mode was to be stored on does not exist."""


class NeedsConfirmation(ModeError):
    """Deleting a mode takes the numbers of what uses it: these are the current ones."""

    def __init__(self, uses: dict[str, int]):
        self.uses = uses
        super().__init__(
            f"{uses['conversations']} conversation(s), {uses['automation_conversations']} automation conversation(s), "
            f"{uses['automations']} automation(s) and {uses['projects']} project(s) use this mode: "
            "they all move to the default mode. Send these numbers to confirm."
        )


def stricter(verdict: str, other: str) -> str:
    return verdict if STRICT[verdict] >= STRICT[other] else other


_regex = lru_cache(maxsize=1024)(glob_regex)


# ----------------------------------------------------------------------
# A line
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    verdict: str
    tool: str = ""
    klass: str = ""
    target: str = ""
    tainted: str = ""  # the verdict in a tainted turn; "" is made the same as `verdict`

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool", self.tool.strip())
        object.__setattr__(self, "target", self.target.strip())
        object.__setattr__(self, "tainted", self.tainted or self.verdict)
        if self.verdict not in VERDICTS or self.tainted not in VERDICTS:
            raise ModeError(f"A verdict is one of {', '.join(VERDICTS)}.")
        if STRICT[self.tainted] < STRICT[self.verdict]:
            raise ModeError(
                f"The verdict when tainted ({self.tainted}) is more permissive than the normal one ({self.verdict}): "
                "a tainted conversation never gets more than a normal one."
            )
        if self.klass and self.klass not in CLASSES:
            raise ModeError(f"No action class {self.klass}.")
        if len(self.tool) > 100 or len(self.target) > 400:
            raise ModeError("The tool or the target is too long.")
        if not (self.tool or self.klass or self.target):
            raise ModeError("A line needs at least one of tool, class, target: it would match everything.")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tool, self.klass, self.target)

    def when(self, tainted: bool) -> str:
        return self.tainted if tainted else self.verdict

    def matches(self, action: Action, tainted: bool) -> bool:
        if self.tool and not _regex(self.tool).match(action.tool):
            return False
        if self.klass and self.klass != action.klass:
            return False
        if self.target:
            pattern = _regex(normal_target(self.target))
            hits = [bool(pattern.match(target)) for target in action.targets]
            if not hits:
                return False
            # a refusal needs one target to match, an approval or a question every one (a move: from and to)
            if self.when(tainted) == "refuse":
                return any(hits)
            return all(hits)
        return True

    def specificity(self, tainted: bool) -> tuple[int, int, int, int]:
        return (
            sum(bool(value) for value in (self.tool, self.klass, self.target)),
            literal_length(normal_target(self.target)) if self.target else -1,
            literal_length(self.tool) if self.tool else -1,
            STRICT[self.when(tainted)],
        )

    def describe(self) -> dict:
        return {
            "tool": self.tool,
            "class": self.klass,
            "target": self.target,
            "verdict": self.verdict,
            "tainted": self.tainted,
        }

    def text(self) -> str:
        parts = [f"{name} {value}" for name, value in (("tool", self.tool), ("class", self.klass)) if value]
        if self.target:
            parts.append(f"target {self.target}")
        flavour = f"{self.verdict} (if tainted: {self.tainted})" if self.tainted != self.verdict else self.verdict
        return f"{', '.join(parts)} → {flavour}"

    @classmethod
    def from_dict(cls, item: dict) -> Line:
        unknown = set(item) - FIELDS
        if unknown:
            raise ModeError(f"Unknown field: {', '.join(sorted(unknown))} (a line has {', '.join(sorted(FIELDS))}).")
        for name in FIELDS:
            if not isinstance(item.get(name, ""), str):
                raise ModeError(f"{name} must be a text.")
        return cls(
            item.get("verdict", ""),
            item.get("tool", ""),
            item.get("class", ""),
            item.get("target", ""),
            item.get("tainted", ""),
        )


# ----------------------------------------------------------------------
# A mode
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Mode:
    name: str
    packs: tuple[str, ...] = ()
    lines: tuple[Line, ...] = ()

    def best(self, action: Action, tainted: bool) -> Line | None:
        found = [line for line in self.lines if line.matches(action, tainted)]
        return max(found, key=lambda line: line.specificity(tainted)) if found else None

    def with_lines(self, *lines: Line) -> Mode:
        """These lines added, replacing the ones for the same tool, class and target."""
        own = {line.key: line for line in self.lines}
        own.update({line.key: line for line in lines})
        return replace(self, lines=tuple(own.values()))

    def describe(self) -> dict:
        return {"name": self.name, "packs": list(self.packs), "verdicts": [line.describe() for line in self.lines]}


def check_packs(packs: Iterable[str]) -> tuple[str, ...]:
    from ..packs.registry import BY_ID, PACKS

    wanted = [str(item) for item in packs]
    unknown = [item for item in wanted if item not in BY_ID]
    if unknown:
        raise ModeError(f"No such tool pack: {', '.join(unknown)}.")
    return tuple(pack.id for pack in PACKS if pack.id in wanted)


def check_tool(tool: str, known: Iterable[str]) -> None:
    """A line's tool is a name that exists, or a glob that matches at least one tool."""
    if not tool:
        return
    names = set(known)
    if re.search(r"[*?\[]", tool):
        if not any(_regex(tool).match(name) for name in names):
            raise ModeError(f"No tool matches {tool}.")
    elif tool not in names:
        raise ModeError(f"No tool {tool}.")


def check_lines(lines: Iterable[Line], known: Iterable[str]) -> tuple[Line, ...]:
    known = set(known)
    seen: set[tuple[str, str, str]] = set()
    for line in lines:
        check_tool(line.tool, known)
        if line.key in seen:
            raise ModeError(f"Two lines name the same tool, class and target: {line.text()}.")
        seen.add(line.key)
    return tuple(lines)


# ----------------------------------------------------------------------
# The built-in default mode and its file
# ----------------------------------------------------------------------
def verdict_at(level: int, top: int, ask_from: int, approve_from: int) -> str:
    """What a tool with these thresholds did at a level of the old ladder: level 0 refused, the last level approved."""
    if level <= 0:
        return "refuse"
    if level >= top:
        return "approve"
    return "approve" if level >= approve_from else "ask" if level >= ask_from else "refuse"


def tainted_flavour(klass: str, verdict: str) -> str:
    """What the tainted-turn raise did to a class: at least `ask` for the ones it raised, nothing for the others."""
    return stricter(verdict, "ask") if klass in RAISED_WHEN_TAINTED else verdict


def builtin_default() -> Mode:
    """The default mode as the default level 3 behaved: each class decided by its thresholds at level 3, and the classes
    the tainted-turn rule raised asking at least when tainted. Used when the file is missing."""
    position = {key: index for index, key in enumerate(LEVEL_KEYS)}
    lines = []
    for name, (ask_key, approve_key, _) in CLASSES.items():
        verdict = verdict_at(position["files_vault"], len(LEVEL_KEYS) - 1, position[ask_key], position[approve_key])
        lines.append(Line(verdict, klass=name, tainted=tainted_flavour(name, verdict)))
    return Mode(DEFAULT, (), tuple(lines))


FILE_HEADER = """\
# Clara's default mode: what happens to every tool call that the conversation's own mode does not decide.
#
# The server reads this file when it starts, so a change needs a restart. Keep it owned by root (mode 0644): then
# neither the server nor a script it runs can change it, and no token can. If it is missing, the server uses its
# built-in copy of this file and raises the alert `default_mode_file`; if it is invalid, the server does not start and
# says which line is wrong (`clara-server --check` tells you without starting).
#
# packs: tool packs switched on in every conversation that has no mode of its own (and added to the ones a mode lists).
#
# Each [[verdict]] is a line. `tool` is a tool name or a glob (github_*), `class` an action class, `target` a glob
# (ws:scratch/**, mail:to:*@example.com, domain:*.wikipedia.org); at least one of the three. `verdict` is approve (it
# runs), ask (a card asks you) or refuse. `tainted` is the verdict once the conversation read untrusted content (a web
# page, a mail, an issue); it is never more permissive than `verdict`, and left out it is the same.
# The most specific line wins: more of tool, class and target set, then the longer literal target, then the longer
# literal tool; on a tie the stricter verdict. A class with no line here takes the built-in one.
# What no mode changes: the floor (the workspace's .git, a path that leaves the workspace), the local-only and chat-only
# limits, and content that looks like a secret leaving the machine (it always asks).
"""


def to_toml(mode: Mode, header: str = FILE_HEADER, notes: dict[tuple[str, str, str], str] | None = None) -> str:
    """The mode as a default-mode file. `notes` puts a comment above the line with that (tool, class, target)."""
    out = [header.rstrip("\n"), "", f"packs = [{', '.join(json.dumps(pack) for pack in mode.packs)}]"]
    for line in mode.lines:
        out.append("")
        if notes and line.key in notes:
            out.append(f"# {notes[line.key]}")
        elif line.klass and not line.tool and not line.target:
            out.append(f"# {CLASSES[line.klass][2]}")
        out.append("[[verdict]]")
        out += [
            f"{name} = {json.dumps(value)}" for name, value in (("tool", line.tool), ("class", line.klass)) if value
        ]
        if line.target:
            out.append(f"target = {json.dumps(line.target)}")
        out.append(f"verdict = {json.dumps(line.verdict)}")
        if line.tainted != line.verdict:
            out.append(f"tainted = {json.dumps(line.tainted)}")
    return "\n".join(out) + "\n"


def parse_default(text: str, path: Path | str, known_tools: Iterable[str]) -> Mode:
    """The default mode a file says, or DefaultModeError naming the file and what is wrong (with its line when it has one)."""

    def fail(reason: str, index: int | None = None) -> DefaultModeError:
        where = ""
        if index is not None:
            starts = [n for n, row in enumerate(text.splitlines(), 1) if re.match(r"\s*\[\[verdict\]\]", row)]
            where = f" (line {starts[index]})" if index < len(starts) else ""
        return DefaultModeError(f"{path}: {reason}{where}")

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise DefaultModeError(f"{path}: not valid TOML: {error}") from None
    unknown = set(data) - {"packs", "verdict"}
    if unknown:
        raise fail(f"unknown field {', '.join(sorted(unknown))} (the file has packs and [[verdict]])")
    packs = data.get("packs", [])
    if not isinstance(packs, list) or not all(isinstance(item, str) for item in packs):
        raise fail("packs must be a list of pack names")
    try:
        checked = check_packs(packs)
    except ModeError as error:
        raise fail(str(error)) from None
    entries = data.get("verdict", [])
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise fail("verdict must be written as [[verdict]] tables")
    lines: list[Line] = []
    for index, item in enumerate(entries):
        try:
            lines.append(Line.from_dict(item))
            check_tool(lines[-1].tool, known_tools)
        except ModeError as error:
            raise fail(str(error), index) from None
    try:
        check_lines(lines, known_tools)
    except ModeError as error:
        raise fail(str(error)) from None
    return Mode(DEFAULT, checked, tuple(lines))


def complete(mode: Mode) -> tuple[Mode, list[str]]:
    """The mode with the built-in line for each class it has no line for alone (no tool, no target): a file written before
    a class was added still decides it. Returns the mode and the classes that were filled in."""
    covered = {line.klass for line in mode.lines if line.klass and not line.tool and not line.target}
    missing = [line for line in builtin_default().lines if line.klass not in covered]
    return replace(mode, lines=(*mode.lines, *missing)), [line.klass for line in missing]


def load_default(path: Path, known_tools: Iterable[str]) -> tuple[Mode, str, bool]:
    """(the default mode, what is to be said about it, whether it came from the file). With no file the built-in copy is
    used and the second item says so; a file that is unreadable or invalid is DefaultModeError."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            builtin_default(),
            f"{path} does not exist: the built-in default mode is used (deploy/default-mode.toml).",
            False,
        )
    except (OSError, UnicodeDecodeError) as error:
        raise DefaultModeError(f"{path}: cannot be read: {error}") from None
    mode, filled = complete(parse_default(text, path, known_tools))
    return (
        mode,
        (f"{path} has no line for the classes {', '.join(filled)}: the built-in ones apply." if filled else ""),
        True,
    )


# ----------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------
class Modes:
    def __init__(self, db: Database, default: Mode, known_tools: Callable[[], Iterable[str]]):
        self.db = db
        self.default = default
        self.known_tools = known_tools
        self.audit: Audit | None = None  # set once built: the changes made to modes are written to the audit log
        self._cache: dict[str, Mode] = {}

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def _changed(self) -> None:
        self._cache.clear()

    def names(self) -> list[str]:
        return [row["name"] for row in self.db.all("SELECT name FROM modes ORDER BY lower(name)")]

    def get(self, name: str | None) -> Mode | None:
        """A mode by its name (any case); the default mode for `default`."""
        key = (name or "").strip().lower()
        if not key:
            return None
        if key == DEFAULT:
            return self.default
        if key not in self._cache:
            found = next((each for each in self.names() if each.lower() == key), None)  # SQLite's lower() is ASCII only
            if found is None:
                return None
            row = {"name": found}
            from ..packs.registry import BY_ID

            packs = self.db.all("SELECT pack FROM mode_packs WHERE mode = ? ORDER BY rowid", (row["name"],))
            lines = []
            for r in self.db.all("SELECT * FROM mode_verdicts WHERE mode = ? ORDER BY rowid", (row["name"],)):
                try:
                    lines.append(Line(r["verdict"], r["tool"], r["klass"], r["target"], r["tainted"]))
                except ModeError:  # a class that no longer exists: the line says nothing, the mode still works
                    log.warning(
                        "mode %s: the line %s/%s is not valid any more and is ignored",
                        row["name"],
                        r["tool"],
                        r["klass"],
                    )
            self._cache[key] = Mode(row["name"], tuple(r["pack"] for r in packs if r["pack"] in BY_ID), tuple(lines))
        return self._cache[key]

    def require(self, name: str) -> Mode:
        found = self.get(name)
        if found is None:
            raise MissingRow(f"No mode {name}.")
        return found

    def verdict(self, action: Action, situation: Situation) -> tuple[Mode, Line | None, str]:
        """(the mode that decided, its line, the verdict): the conversation's mode first, else the default mode."""
        named = self.get(situation.mode_name)
        for mode in (named, self.default):
            if mode is not None:
                line = mode.best(action, situation.tainted)
                if line is not None:
                    return mode, line, line.when(situation.tainted)
        return self.default, None, "refuse"  # a default mode with no line for it: nothing is decided, so nothing runs

    @staticmethod
    def reason(mode: Mode, line: Line | None, verdict: str, tool: str, tainted: bool) -> str:
        if line is None:
            return f"no mode has a verdict for {tool}"
        word = {"approve": "approves", "ask": "asks for", "refuse": "refuses"}[verdict]
        when = " (the conversation read untrusted content)" if tainted and line.tainted != line.verdict else ""
        return f"mode {mode.name} {word} {tool}{when}: {line.text()}"

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def check_name(self, name: str, keep: str | None = None) -> str:
        name = " ".join(str(name).split())
        if not _NAME.match(name):
            raise ModeError(f"A mode's name is 1 to {MAX_NAME} characters, with no / \\ ? # %.")
        if name.lower() == DEFAULT:
            raise ModeError("The default mode is fixed: it is a file the server reads (see CLARA_DEFAULT_MODE_FILE).")
        found = self.get(name)
        if found is not None and found.name != keep:
            raise ModeError(f"There is a mode named {found.name} already.")
        return name

    def _store(self, tx, name: str, packs: tuple[str, ...] | None, lines: tuple[Line, ...] | None) -> None:
        if packs is not None:
            tx.execute("DELETE FROM mode_packs WHERE mode = ?", (name,))
            tx.executemany("INSERT INTO mode_packs (mode, pack) VALUES (?, ?)", [(name, pack) for pack in packs])
        if lines is not None:
            tx.execute("DELETE FROM mode_verdicts WHERE mode = ?", (name,))
            tx.executemany(
                "INSERT INTO mode_verdicts (mode, tool, klass, target, verdict, tainted) VALUES (?, ?, ?, ?, ?, ?)",
                [(name, ln.tool, ln.klass, ln.target, ln.verdict, ln.tainted) for ln in lines],
            )
        tx.execute("UPDATE modes SET updated_at = ? WHERE name = ?", (now_iso(), name))

    def create(self, name: str, packs: Iterable[str] = (), lines: Iterable[Line] = ()) -> Mode:
        name = self.check_name(name)
        packs, lines = check_packs(packs), check_lines(lines, self.known_tools())
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO modes (name, created_at, updated_at) VALUES (?, ?, ?)", (name, now_iso(), now_iso())
            )
            self._store(tx, name, packs, lines)
        self._changed()
        return self.require(name)

    def update(self, name: str, packs: Iterable[str] | None = None, lines: Iterable[Line] | None = None) -> Mode:
        """Replace the packs and/or every line of a mode (what is not given stays)."""
        mode = self.require(name)
        if mode is self.default:
            raise ModeError("The default mode is a file the server reads: no request changes it.")
        new_packs = check_packs(packs) if packs is not None else None
        new_lines = check_lines(lines, self.known_tools()) if lines is not None else None
        with self.db.transaction() as tx:
            self._store(tx, mode.name, new_packs, new_lines)
        self._changed()
        return self.require(name)

    def set_line(self, name: str, line: Line) -> Mode:
        mode = self.require(name)
        return self.update(name, lines=mode.with_lines(line).lines)

    def remove_line(self, name: str, tool: str = "", klass: str = "", target: str = "") -> Mode:
        mode = self.require(name)
        kept = tuple(line for line in mode.lines if line.key != (tool.strip(), klass, target.strip()))
        if len(kept) == len(mode.lines):
            raise MissingRow("This mode has no such line.")
        return self.update(name, lines=kept)

    def uses(self, name: str) -> dict[str, int]:
        """What stores this mode: conversations of its own, conversations of automations, automations, projects."""
        automation = (
            f"(c.kind = 'schedule' OR {Conversations._SCHEDULE} IS NOT NULL OR {Conversations._HOOK} IS NOT NULL)"
        )

        def count(sql: str) -> int:
            return int(self.db.value(sql, (name,), 0))

        return {
            "conversations": count(f"SELECT COUNT(*) FROM conversations c WHERE c.mode = ? AND NOT {automation}"),
            "automation_conversations": count(
                f"SELECT COUNT(*) FROM conversations c WHERE c.mode = ? AND {automation}"
            ),
            "automations": count(
                "SELECT (SELECT COUNT(*) FROM schedules WHERE mode = ?1) + (SELECT COUNT(*) FROM triggers WHERE mode = ?1)"
            ),
            "projects": count("SELECT COUNT(*) FROM projects WHERE mode = ?"),
        }

    def delete(self, name: str, confirmed: dict[str, int]) -> dict[str, int]:
        """Delete a mode once the numbers of what uses it are confirmed; all of it moves to the default mode (follow)."""
        mode = self.require(name)
        if mode is self.default:
            raise ModeError("The default mode cannot be deleted.")
        uses = self.uses(mode.name)
        if confirmed != uses:
            raise NeedsConfirmation(uses)
        with self.db.transaction() as tx:
            for table in STORED:
                tx.execute(f"UPDATE {table} SET mode = NULL WHERE mode = ?", (mode.name,))
            tx.execute("DELETE FROM modes WHERE name = ?", (mode.name,))
        self._changed()
        return uses

    # ------------------------------------------------------------------
    # The mode of a conversation
    # ------------------------------------------------------------------
    def where(self, conversation_id: str, *, own: bool = True) -> tuple[str, str]:
        """(the mode's name, where it comes from): the conversation, its schedule or hook, its project, or the default.
        With `own` false the conversation's own mode is left out: what it would have if it followed."""
        if conversation_id:
            row = self.db.one(
                "SELECT c.mode AS own, p.mode AS project,"
                f" COALESCE((SELECT s.mode FROM schedules s WHERE s.id = {Conversations._SCHEDULE}),"
                f" (SELECT t.mode FROM triggers t WHERE t.id = {Conversations._HOOK})) AS automation"
                " FROM conversations c LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = ?",
                (conversation_id,),
            )
            if row is not None:
                for source in ("own", "automation", "project"):
                    if row[source] is not None and (own or source != "own") and self.get(row[source]) is not None:
                        return row[source], source
        return DEFAULT, "default"

    def resolve(self, conversation_id: str) -> str:
        return self.where(conversation_id)[0]

    def check_mode(self, name: str | None) -> str | None:
        """The name of an existing mode a client may store on a conversation, a project, a schedule or a hook (None: follow).
        The default mode is what they follow, so it is not stored by name."""
        if name is None:
            return None
        found = self.require(name)
        if found is self.default:
            raise ModeError("The default mode is what follows when nothing else says: choose follow (null) for it.")
        return found.name

    def set_stored(self, table: str, row_id: int | str, name: str | None) -> None:
        """Set (or, with None, clear) the mode stored on a project, schedule, hook or conversation."""
        if table not in STORED:
            raise ModeError(f"No mode is stored on {table}.")
        name = self.check_mode(name)
        with self.db.transaction() as tx:
            found = tx.execute(f"UPDATE {table} SET mode = ? WHERE id = ?", (name, row_id)).rowcount
        if not found:
            raise MissingRow(f"No such {table[:-1]}.")

    def log(self, what: str, detail: str, conversation_id: str | None = None) -> None:
        """Write a change of modes in the audit log (outcome `mode`), so that who changed what stays traceable."""
        if self.audit is not None:
            action = Action("mode", "write", "memory", (what,), detail)
            self.audit.record(
                action,
                Situation("interactive"),
                "",
                Decision("allow", "you changed it"),
                "mode",
                conversation_id=conversation_id,
                detail=detail,
            )
