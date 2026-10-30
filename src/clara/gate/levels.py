"""Auto levels: how much a conversation lets its tools do without asking.

A **ladder** of levels runs from 0 (no tool: everything is refused) to the last one (every tool runs). Five are there at first
(0 no tool, 1 trusted read, 2 any read, 3 files and vault, 4 mail, 5 everything); the owner may add, rename and remove the ones
between the first and the last. A tool has two thresholds for each of its action classes, the level it starts to **ask** at and
the level it starts to **run** at: below the first it is refused, from the second it runs. A class that was not given thresholds
follows the default of its action class (actions.CLASSES), which is tied to the built-in levels by key so that it still finds them
after the ladder was edited. A setting may also be for the whole tool (every class that has none of its own): the console writes that.

A conversation's level is stored on the conversation, else on the schedule or hook it belongs to, else on its project, else it is
the server default (`auto_level_default` for a chat, `auto_level_unattended` for a run nobody watches): a conversation *follows*
until a level is set on it. Levels are stored as positions; adding or removing a level moves the stored positions with it, in one
transaction, so that nothing silently gets a different meaning.

  adding a level    the new one acts like the level below it for every tool (nothing changes until you edit it)
  removing a level  what was set to it moves to the level below (a conversation) or above (a threshold): never looser
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db import Database
from ..memory.conversations import Conversations
from ..runtime import RuntimeSettings
from .actions import CLASSES, LEVEL_KEYS, Action, Decision, Situation

if TYPE_CHECKING:
    from .engine import Audit

MAX_TOP = 9  # the last level is at most this
MAX_NAME = 30
KNOBS = ("auto_level_default", "auto_level_unattended")
STORED = ("conversations", "projects", "schedules", "triggers")  # the tables with an `auto_level` column
WHOLE = ""  # the class of a setting that is for every class of the tool


class LevelError(ValueError):
    """A change to the ladder or to a tool that cannot be made: the message says why."""


class MissingRow(LevelError):
    """The project, schedule, hook or conversation a level was to be stored on does not exist."""


@dataclass(frozen=True)
class Level:
    position: int
    name: str
    key: str  # one of actions.LEVEL_KEYS for the built-in levels, '' for one you added

    def describe(self) -> dict:
        return {"position": self.position, "name": self.name, "key": self.key}


class Levels:
    def __init__(self, db: Database, runtime: RuntimeSettings):
        self.db = db
        self.runtime = runtime
        self.audit: Audit | None = None  # set once built: the changes made to levels are written to the audit log
        self._ladder: list[Level] | None = None
        self._tools: dict[tuple[str, str], tuple[int, int]] | None = None

    # ------------------------------------------------------------------
    # The ladder
    # ------------------------------------------------------------------
    def ladder(self) -> list[Level]:
        if self._ladder is None:
            rows = self.db.all("SELECT position, name, key FROM auto_levels ORDER BY position")
            self._ladder = [Level(row["position"], row["name"], row["key"]) for row in rows]
        return self._ladder

    @property
    def top(self) -> int:
        return self.ladder()[-1].position

    def name(self, position: int) -> str:
        return self.ladder()[max(0, min(position, self.top))].name

    def label(self, position: int) -> str:
        return f"{position} ({self.name(position)})"

    def position_of(self, key: str) -> int:
        """The position of a built-in level; one that was removed counts as the next built-in level above it."""
        keys = {level.key: level.position for level in self.ladder() if level.key}
        for candidate in LEVEL_KEYS[LEVEL_KEYS.index(key) :]:
            if candidate in keys:
                return keys[candidate]
        return self.top

    def _changed(self) -> None:
        self._ladder = None
        self._tools = None

    def add(self, name: str, before: int | None = None) -> Level:
        """A level just below `before` (default: just below the last level), acting like the level under it at first."""
        name = self._check_name(name)
        top = self.top
        before = top if before is None else before
        if not 1 <= before <= top:
            raise LevelError(f"A level goes between 1 and {top}: before {before} is not possible.")
        if top >= MAX_TOP:
            raise LevelError(f"There are {MAX_TOP + 1} levels already, the most there can be.")
        with self.db.transaction() as tx:
            tx.execute("UPDATE auto_levels SET position = -(position + 1) WHERE position >= ?", (before,))
            tx.execute("UPDATE auto_levels SET position = -position WHERE position < 0")
            tx.execute("INSERT INTO auto_levels (position, name, key) VALUES (?, ?, '')", (before, name))
            self._shift_stored(tx, "auto_level >= ?", before, +1)
            tx.execute(
                "UPDATE tool_levels SET ask_from = ask_from + (ask_from >= ?), approve_from = approve_from + (approve_from >= ?)",
                (before, before),
            )
            for knob in KNOBS:
                value = self.runtime.get(knob)
                if value >= before:
                    self.runtime.set(
                        knob, min(value + 1, MAX_TOP)
                    )  # a knob past the last level is read as the last one
        self._changed()
        return Level(before, name, "")

    def rename(self, position: int, name: str) -> None:
        if not 1 <= position < self.top:
            raise LevelError("The first and the last level are fixed: only the ones between can be renamed.")
        self.db.execute(
            "UPDATE auto_levels SET name = ? WHERE position = ?", (self._check_name(name, position), position)
        )
        self._changed()

    def remove(self, position: int) -> None:
        """Take a level out. What was set to it moves to the level below, and the thresholds that named it to the level that
        takes its place (the one above): the result is never more permissive than before."""
        if not 1 <= position < self.top:
            raise LevelError("The first and the last level are fixed: only the ones between can be removed.")
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM auto_levels WHERE position = ?", (position,))
            tx.execute("UPDATE auto_levels SET position = -(position - 1) WHERE position > ?", (position,))
            tx.execute("UPDATE auto_levels SET position = -position WHERE position < 0")
            for table in STORED:
                tx.execute(
                    f"UPDATE {table} SET auto_level = CASE WHEN auto_level > ? THEN auto_level - 1 ELSE ? END"
                    " WHERE auto_level >= ?",
                    (position, position - 1, position),
                )
            tx.execute(
                "UPDATE tool_levels SET ask_from = ask_from - (ask_from > ?), approve_from = approve_from - (approve_from > ?)",
                (position, position),
            )
            for knob in KNOBS:
                value = self.runtime.get(knob)
                if value >= position:
                    self.runtime.set(knob, value - 1 if value > position else position - 1)
        self._changed()

    @staticmethod
    def _shift_stored(tx, where: str, bound: int, by: int) -> None:
        for table in STORED:
            tx.execute(f"UPDATE {table} SET auto_level = auto_level + ? WHERE {where}", (by, bound))

    def _check_name(self, name: str, keep: int | None = None) -> str:
        name = " ".join(str(name).split())
        if not name or len(name) > MAX_NAME:
            raise LevelError(f"A level's name is 1 to {MAX_NAME} characters.")
        for level in self.ladder():
            if level.position != keep and level.name.lower() == name.lower():
                raise LevelError(f"There is a level named {level.name} already.")
        return name

    # ------------------------------------------------------------------
    # The thresholds of the tools
    # ------------------------------------------------------------------
    def overrides(self) -> dict[tuple[str, str], tuple[int, int]]:
        """The thresholds that were changed: {(tool, class): (ask_from, approve_from)}, the class being WHOLE for a setting
        that is for every class of the tool."""
        if self._tools is None:
            rows = self.db.all("SELECT tool, klass, ask_from, approve_from FROM tool_levels")
            self._tools = {(row["tool"], row["klass"]): (row["ask_from"], row["approve_from"]) for row in rows}
        return self._tools

    def default_thresholds(self, klass: str) -> tuple[int, int]:
        ask_key, approve_key, _ = CLASSES[klass]
        approve = max(1, self.position_of(approve_key))
        return min(max(1, self.position_of(ask_key)), approve), approve

    def class_defaults(self) -> list[dict]:
        """The default thresholds of every action class, as the Rules and Auto mode pages list them."""
        rows = []
        for name, (_, _, what) in CLASSES.items():
            ask, approve = self.default_thresholds(name)
            rows.append({"class": name, "description": what, "ask_from": ask, "approve_from": approve})
        return rows

    def thresholds(self, tool: str, klass: str) -> tuple[int, int]:
        """(ask_from, approve_from) for a call of a tool: what its owner set for that class, else for the whole tool, else the
        default of the class."""
        overrides = self.overrides()
        found = overrides.get((tool, klass)) or overrides.get((tool, WHOLE)) or self.default_thresholds(klass)
        top = self.top
        return min(found[0], top), min(found[1], top)

    def set_tool(self, tool: str, ask_from: int, approve_from: int, klass: str = WHOLE) -> None:
        """Set the thresholds of one class of a tool, or (no class) of all its classes: the setting for the whole tool replaces
        the ones made class by class."""
        top = self.top
        if not (1 <= ask_from <= approve_from <= top):
            raise LevelError(
                f"The tool must ask from a level between 1 and {top}, and run from that level or a higher one "
                f"(at most {top}); level 0 only refuses and level {top} only runs."
            )
        if klass != WHOLE and klass not in CLASSES:
            raise LevelError(f"No action class {klass}.")
        with self.db.transaction() as tx:
            if klass == WHOLE:
                tx.execute("DELETE FROM tool_levels WHERE tool = ?", (tool,))
            tx.execute(
                "INSERT INTO tool_levels (tool, klass, ask_from, approve_from) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (tool, klass) DO UPDATE SET ask_from = excluded.ask_from, approve_from = excluded.approve_from",
                (tool, klass, ask_from, approve_from),
            )
        self._changed()

    def reset_tool(self, tool: str, klass: str | None = None) -> None:
        """Back to the default of the action class: of every class of the tool, or of one. If the tool has a setting for
        all its classes, the class that is reset gets its own default written beside it, so that the others keep theirs."""
        with self.db.transaction() as tx:
            if klass is None:
                tx.execute("DELETE FROM tool_levels WHERE tool = ?", (tool,))
            else:
                tx.execute("DELETE FROM tool_levels WHERE tool = ? AND klass = ?", (tool, klass))
                whole = tx.execute("SELECT 1 FROM tool_levels WHERE tool = ? AND klass = ?", (tool, WHOLE)).fetchone()
                if whole is not None and klass in CLASSES:
                    ask, approve = self.default_thresholds(klass)
                    tx.execute(
                        "INSERT INTO tool_levels (tool, klass, ask_from, approve_from) VALUES (?, ?, ?, ?)",
                        (tool, klass, ask, approve),
                    )
        self._changed()

    def effect(self, tool: str, klass: str, level: int) -> str:
        """allow, ask or refuse for a tool at a level. Level 0 only refuses and the last level only allows."""
        if level <= 0:
            return "refuse"
        if level >= self.top:
            return "allow"
        ask, approve = self.thresholds(tool, klass)
        return "allow" if level >= approve else "ask" if level >= ask else "refuse"

    def reason(self, tool: str, klass: str, level: int, effect: str) -> str:
        ask, approve = self.thresholds(tool, klass)
        here = f"level {self.label(level)}"
        if effect == "allow":
            return here
        if effect == "ask":
            return f"{here} asks for {tool} (it runs from level {self.label(approve)})"
        return f"{tool} is refused at {here}: it asks from level {self.label(ask)} and runs from level {self.label(approve)}"

    # ------------------------------------------------------------------
    # The level of a conversation
    # ------------------------------------------------------------------
    def default_level(self, mode: str) -> int:
        knob = "auto_level_unattended" if mode == "unattended" else "auto_level_default"
        return max(0, min(int(self.runtime.get(knob)), self.top))

    def resolve(self, conversation_id: str, mode: str) -> int:
        """The level a conversation runs at, and nothing else about it (see `where` for the reason)."""
        return self.where(conversation_id, mode)[0]

    def where(self, conversation_id: str, mode: str, *, own: bool = True) -> tuple[int, str]:
        """(level, where it comes from): the conversation, its schedule or hook, its project, or the server default. With
        `own` false the conversation's own level is left out: what it would have if it followed."""
        if conversation_id:
            row = self.db.one(
                "SELECT c.auto_level AS own, p.auto_level AS project,"
                f" COALESCE((SELECT s.auto_level FROM schedules s WHERE s.id = {Conversations._SCHEDULE}),"
                f" (SELECT t.auto_level FROM triggers t WHERE t.id = {Conversations._HOOK})) AS automation"
                " FROM conversations c LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = ?",
                (conversation_id,),
            )
            if row is not None:
                for source in ("own", "automation", "project"):
                    if row[source] is not None and (own or source != "own"):
                        return max(0, min(int(row[source]), self.top)), source
        return self.default_level(mode), "default"

    def mode_of(self, conversation_id: str) -> str:
        """How a conversation is run: `unattended` for the conversation of a schedule or of a hook run, else `interactive`."""
        row = self.db.one(
            f"SELECT c.kind AS kind, {Conversations._SCHEDULE} AS schedule, {Conversations._HOOK} AS hook"
            " FROM conversations c WHERE c.id = ?",
            (conversation_id,),
        )
        if row is not None and (row["kind"] == "schedule" or row["schedule"] is not None or row["hook"] is not None):
            return "unattended"
        return "interactive"

    def set_stored(self, table: str, row_id: int | str, level: int | None) -> None:
        """Set (or, with None, clear) the level stored on a project, schedule, hook or conversation."""
        if table not in STORED:
            raise LevelError(f"No level is stored on {table}.")
        level = self.check_level(level)
        with self.db.transaction() as tx:
            found = tx.execute(f"UPDATE {table} SET auto_level = ? WHERE id = ?", (level, row_id)).rowcount
        if not found:
            raise MissingRow(f"No such {table[:-1]}.")

    def log(self, what: str, detail: str, conversation_id: str | None = None) -> None:
        """Write a change of levels in the audit log (`level`), so that who lowered or raised what stays traceable."""
        if self.audit is not None:
            action = Action("auto_level", "write", "memory", (what,), detail)
            self.audit.record(
                action,
                Situation("interactive"),
                "",
                Decision("allow", "you changed it"),
                "level",
                conversation_id=conversation_id,
                detail=detail,
            )

    def check_level(self, level: int | None) -> int | None:
        """A level a client may set (None: follow)."""
        if level is None:
            return None
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= self.top:
            raise LevelError(f"A level is a whole number from 0 to {self.top}.")
        return level
