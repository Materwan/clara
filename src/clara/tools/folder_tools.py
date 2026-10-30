"""Folders on the person's computer, read in the browser of a web chat (DECISIONS.md, « Folders on your computer »).

The server never reads these files. The four tools are `Remote` tools: the gate decides as for any tool, then the call goes
to the page, which holds the folder the person picked, and the page answers with JSON:

    {"text": "what the model reads", "clear": ["a/b.txt", ...]}        or        {"error": "why it failed"}

`clear` names the files the answer found that are neither hidden nor ignored: Clara may read those without a card. Any
other file (a dot name, an ignored one, a file no answer named yet) asks the person first. The clear names are kept per
conversation in memory, so after a restart every read asks again, which is the safe side.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..gate.actions import Action
from ..timeutil import now_iso
from .base import Remote, Tool, ToolContext, ToolError, acts_as, flag

if TYPE_CHECKING:
    from ..db import Database
    from ..runtime import RuntimeSettings

TOOL_FOLDERS = frozenset({"folder_list", "folder_find", "folder_grep", "folder_read"})
BUDGETED = frozenset({"folder_find", "folder_grep", "folder_read"})  # what their answers say counts against the budget
PATH_LIMIT = 500  # characters in one path
RESULT_CHARS = 60_000  # the most one answer may put into the context, whatever the page sent
CLEAR_LIMIT = 50_000  # clear names kept per conversation


@dataclass(frozen=True)
class FolderRequest:
    """The folder a web chat sent with its message: the browser's id for this grant, the folder's name, the budget asked
    for in bytes (None: the runtime setting)."""

    id: str
    name: str
    budget: int | None = None


@dataclass(frozen=True)
class FolderGrant:
    """The grant a turn runs under. Its budget and what was read live in the database (`FolderGrants`)."""

    id: str
    name: str


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
def clean(value: Any) -> str:
    """A path inside the folder: relative, `/` between names, no `.` or `..` name, no drive. '' is the folder's top."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ToolError("the path must be a text")
    text = value.strip().replace("\\", "/")
    if len(text) > PATH_LIMIT or "\x00" in text:
        raise ToolError("the path is too long, or has a character no file name has")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ToolError("the path must be relative to the folder the person opened")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ToolError("the path may not leave the folder the person opened")
    return "/".join(parts)


def hidden(path: str) -> bool:
    """A name that starts with a dot, anywhere in the path (the only hidden mark a browser can see)."""
    return any(part.startswith(".") for part in path.split("/") if part)


def _shown(path: str) -> str:
    return path or "the top of the folder"


def _name(value: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
    return text[:200] or "folder"


# ----------------------------------------------------------------------
# The budget and the clear names: one grant per conversation
# ----------------------------------------------------------------------
class FolderGrants:
    def __init__(self, db: Database, runtime: RuntimeSettings):
        self.db = db
        self.runtime = runtime
        self._clear: dict[str, set[str]] = {}

    def begin(self, conversation_id: str, request: FolderRequest) -> FolderGrant:
        """The web chat's folder for this message. A new grant starts a new budget and forgets the old clear names; the
        same grant keeps what it has read, and may have its budget raised (up to `folder_budget_max`)."""
        ceiling = int(self.runtime.get("folder_budget_max"))
        asked = int(self.runtime.get("folder_budget")) if request.budget is None else request.budget
        budget = max(0, min(asked, ceiling))
        name = _name(request.name)
        row = self.db.one("SELECT grant_id FROM folder_grants WHERE conversation_id = ?", (conversation_id,))
        if row is None or row["grant_id"] != request.id:
            self.db.execute(
                "INSERT INTO folder_grants (conversation_id, grant_id, folder_name, budget_bytes, used_bytes, granted_at) "
                "VALUES (?, ?, ?, ?, 0, ?) ON CONFLICT (conversation_id) DO UPDATE SET grant_id = excluded.grant_id, "
                "folder_name = excluded.folder_name, budget_bytes = excluded.budget_bytes, used_bytes = 0, "
                "granted_at = excluded.granted_at",
                (conversation_id, request.id, name, budget, now_iso()),
            )
            self._clear.pop(conversation_id, None)
        else:
            self.db.execute(
                "UPDATE folder_grants SET folder_name = ?, budget_bytes = ? WHERE conversation_id = ?",
                (name, budget, conversation_id),
            )
        return FolderGrant(request.id, name)

    def budget(self, conversation_id: str, grant_id: str) -> tuple[int, int] | None:
        """(budget, used) in bytes of file content, or None when this grant is not the conversation's."""
        row = self.db.one(
            "SELECT budget_bytes, used_bytes FROM folder_grants WHERE conversation_id = ? AND grant_id = ?",
            (conversation_id, grant_id),
        )
        return None if row is None else (int(row["budget_bytes"]), int(row["used_bytes"]))

    def charge(self, conversation_id: str, grant_id: str, wanted: int) -> int:
        """Takes up to `wanted` bytes off the budget and returns how many were taken."""
        granted = self.budget(conversation_id, grant_id)
        if granted is None:
            return 0
        budget, used = granted
        taken = max(0, min(wanted, budget - used))
        if taken:
            self.db.execute(
                "UPDATE folder_grants SET used_bytes = used_bytes + ? WHERE conversation_id = ? AND grant_id = ?",
                (taken, conversation_id, grant_id),
            )
        return taken

    def remember_clear(self, conversation_id: str, paths: list[str]) -> None:
        if not conversation_id:
            return
        names = self._clear.setdefault(conversation_id, set())
        for path in paths:
            try:
                cleaned = clean(path)
            except ToolError:
                continue
            if cleaned and not hidden(cleaned) and len(names) < CLEAR_LIMIT:
                names.add(cleaned)

    def is_clear(self, conversation_id: str, path: str) -> bool:
        return path in self._clear.get(conversation_id, ())


# ----------------------------------------------------------------------
# Describing each call for the gate
# ----------------------------------------------------------------------
def _action(klass: str, path: str, summary: str) -> Action:
    return Action("", "read", klass, (f"folder:{path}",), summary)


@acts_as("folder_read")
def _describe_list(context: ToolContext, arguments: dict) -> Action:
    path = clean(arguments.get("path"))
    return _action("folder_read", path, f"List {_shown(path)}")


def _search(verb: str) -> Callable[[ToolContext, dict], Action]:
    def describe(context: ToolContext, arguments: dict) -> Action:
        path = clean(arguments.get("path"))
        pattern = str(arguments.get("pattern") or "").strip()
        if not pattern:
            raise ToolError("the pattern is required")
        if flag(arguments.get("include_hidden")):
            return _action(
                "folder_sensitive", path, f"{verb} {pattern!r} in {_shown(path)}, hidden and ignored files included"
            )
        # a search that starts inside `.ssh` or `.git` reads hidden files, whatever include_hidden says
        if hidden(path):
            return _action("folder_sensitive", path, f"{verb} {pattern!r} in {path}, a hidden folder")
        return _action("folder_read", path, f"{verb} {pattern!r} in {_shown(path)}")

    describe.classes = ("folder_read", "folder_sensitive")  # type: ignore[attr-defined]
    return describe


@acts_as("folder_read", "folder_sensitive")
def _describe_read(context: ToolContext, arguments: dict) -> Action:
    path = clean(arguments.get("path"))
    if not path:
        raise ToolError("the path of the file to read is required")
    clear = context.services.folders.is_clear(context.turn.conversation_id, path)
    if hidden(path) or not clear:
        return _action("folder_sensitive", path, f"Read {path}: hidden, ignored, or not found by a listing yet")
    return _action("folder_read", path, f"Read {path}")


# ----------------------------------------------------------------------
# The page's answers
# ----------------------------------------------------------------------
def _answer(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ToolError("the folder's answer was not understood") from None
    if not isinstance(data, dict):
        raise ToolError("the folder's answer was not understood")
    if "error" in data:
        raise ToolError(str(data["error"])[:500])
    text = data.get("text")
    if not isinstance(text, str):
        raise ToolError("the folder's answer has no text")
    clear = data.get("clear")
    names = [item for item in clear if isinstance(item, str)][:CLEAR_LIMIT] if isinstance(clear, list) else []
    return {"text": text, "clear": names}


def _cut(text: str) -> str:
    if len(text) <= RESULT_CHARS:
        return text
    return text[:RESULT_CHARS] + f"\n[the answer was cut at {RESULT_CHARS:,} characters]"


def _remote(name: str) -> Remote:
    def before(context: ToolContext, arguments: dict) -> str | None:
        grant = context.turn.folder
        if grant is None:
            raise ToolError("no folder is open in this conversation: the person opens one with the + menu")
        if name not in BUDGETED:
            return None
        granted = context.services.folders.budget(context.turn.conversation_id, grant.id)
        if granted is None:
            raise ToolError("this folder is no longer granted: the person opens it again")
        budget, used = granted
        if used >= budget:
            return (
                f"The folder budget is spent: {used:,} of {budget:,} bytes of file content were read. Tell the person; "
                "they can raise the budget of the folder, or open it again."
            )
        return None

    def after(context: ToolContext, arguments: dict, answer: str) -> str:
        data = _answer(answer)
        text = _cut(data["text"])
        folders = context.services.folders
        conversation = context.turn.conversation_id
        # a listing or a search names the files that are clear (a read is already one of them)
        if name != "folder_read":
            folders.remember_clear(conversation, data["clear"])
        if name not in BUDGETED:
            return text
        size = len(text.encode("utf-8"))
        taken = folders.charge(conversation, context.turn.folder.id, size)
        if taken < size:
            kept = text.encode("utf-8")[:taken].decode("utf-8", "ignore")
            text = kept + "\n[the folder budget ends here: the rest of this file was not read]"
        return text

    return Remote(before=before, after=after)


def _runs_in_the_browser(context: ToolContext, **arguments: Any) -> str:
    raise ToolError(
        "this runs in the person's browser, not on the server"
    )  # never reached: a remote tool is not run here


# ----------------------------------------------------------------------
# The tools
# ----------------------------------------------------------------------
PATH_PARAMETER = {
    "type": "string",
    "description": "A folder inside the one the person opened, with / between names. Empty: its top.",
}
INCLUDE_HIDDEN_PARAMETER = {
    "type": "boolean",
    "description": "Also look in hidden and ignored files. The person is asked first.",
}

LIST_DESCRIPTION = (
    "List a folder of the folder the person opened on their own computer in this conversation. Folders end with /. "
    "Hidden and ignored entries are listed too, so you can see what is there. Up to 500 entries. Read-only."
)
FIND_DESCRIPTION = (
    "Find files and folders by name in the folder the person opened on their computer, from a folder down: a pattern "
    "with * and ? (for example *.pdf or invoice*). Hidden and ignored files are skipped unless include_hidden is true, "
    "which asks the person first; so does starting from a hidden folder. Up to 100 matches."
)
GREP_DESCRIPTION = (
    "Search the text of the files in the folder the person opened on their computer, from a folder down, and give each "
    "matching line with its file and line number. Hidden and ignored files are skipped unless include_hidden is true, "
    "which asks the person first; so does starting from a hidden folder. Binary files are skipped. A regular "
    "expression that takes too long is stopped. Up to 100 matching lines."
)
READ_DESCRIPTION = (
    "Read a text file of the folder the person opened on their computer: its lines, numbered, up to 40,000 characters "
    "from start_line (the answer says where to continue). A file a listing, a search or a read has named may be read at "
    "once; a hidden or ignored file asks the person first. Reads count against the folder's budget: when it is spent, "
    "say so to the person."
)


def folder_tools() -> list[Tool]:
    return [
        Tool(
            "folder_list",
            LIST_DESCRIPTION,
            _runs_in_the_browser,
            _describe_list,
            parameters={"path": PATH_PARAMETER},
            taints=True,
            remote=_remote("folder_list"),
        ),
        Tool(
            "folder_find",
            FIND_DESCRIPTION,
            _runs_in_the_browser,
            _search("Find"),
            parameters={
                "pattern": {"type": "string", "description": "A name pattern with * and ? (for example *.pdf)."},
                "path": PATH_PARAMETER,
                "include_hidden": INCLUDE_HIDDEN_PARAMETER,
            },
            required=("pattern",),
            taints=True,
            remote=_remote("folder_find"),
        ),
        Tool(
            "folder_grep",
            GREP_DESCRIPTION,
            _runs_in_the_browser,
            _search("Search"),
            parameters={
                "pattern": {"type": "string", "description": "The text to look for (plain text unless regex is true)."},
                "path": PATH_PARAMETER,
                "regex": {"type": "boolean", "description": "The pattern is a regular expression (JavaScript syntax)."},
                "include_hidden": INCLUDE_HIDDEN_PARAMETER,
            },
            required=("pattern",),
            taints=True,
            remote=_remote("folder_grep"),
        ),
        Tool(
            "folder_read",
            READ_DESCRIPTION,
            _runs_in_the_browser,
            _describe_read,
            parameters={
                "path": {"type": "string", "description": "The file, with / between names."},
                "start_line": {"type": "integer", "description": "The first line to read (from 1)."},
            },
            required=("path",),
            taints=True,
            remote=_remote("folder_read"),
        ),
    ]
