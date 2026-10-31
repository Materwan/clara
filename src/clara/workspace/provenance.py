"""Where the files of the workspace come from.

The workspace is a tree that scripts write into, and a mail attachment or a page can land in it. A file that arrived from
outside must not be a way round the quarantine: Clara reads it back with `ws_read` in another conversation, which has read
nothing untrusted and so is not tainted. Two kinds of mark, kept in the database (never in the workspace, which the host
treats as untrusted):

* `quarantine`: text that came in as it was (an attachment saved from a mail, what a script fetched from the network and the
  log of such a run). Reading it taints the conversation, and the quarantined reader reads it instead of the conversation.
* `tainted`: a file Clara wrote after reading untrusted content. It is her own words, but it may carry what she read on, as
  a fact would: reading it taints the conversation, the text is read as it is.

The person marks a file **trusted** (the web site, the console, the API: never a tool of the model). The digest of the content
they trusted is kept, so a file that changes is untrusted again until they say so.

A mark is kept when its file is deleted or moved away: both are commits, `undo` brings the file back, and a file that came
back without its mark would be a way round the quarantine. A move copies the marks to the new path and leaves them at the
old one. The mark of a path with no file is not listed, and a file written later at that path is marked as the old one was
(the safe side: trust it if it is yours).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..db import Database
from ..timeutil import now_iso
from .paths import PathError, clean
from .store import Workspace, WorkspaceError

KINDS = ("quarantine", "tainted")


@dataclass(frozen=True)
class Mark:
    path: str
    kind: str
    source: str
    conversation_id: str
    marked_at: str
    trusted_digest: str
    trusted_at: str

    def describe(self, trusted: bool) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "source": self.source,
            "conversation": self.conversation_id or None,
            "marked_at": self.marked_at,
            "trusted": trusted,
            "trusted_at": self.trusted_at or None,
        }


def _mark(row) -> Mark:
    return Mark(
        row["path"],
        row["kind"],
        row["source"],
        row["conversation_id"] or "",
        row["marked_at"],
        row["trusted_digest"] or "",
        row["trusted_at"] or "",
    )


class Provenance:
    def __init__(self, db: Database, workspace: Workspace):
        self.db = db
        self.workspace = workspace

    # ------------------------------------------------------------------
    # Marking (what the tools do)
    # ------------------------------------------------------------------
    def mark(self, paths: list[str], source: str, *, kind: str, conversation_id: str = "") -> None:
        """These files are not to be trusted. A file already marked `quarantine` stays so; a mark takes back a trust."""
        assert kind in KINDS
        with self.db.transaction() as tx:
            for path in paths:
                try:
                    name = clean(path)
                except PathError:
                    continue
                if not name:
                    continue
                row = tx.execute("SELECT kind FROM workspace_marks WHERE path = ?", (name,)).fetchone()
                keep = "quarantine" if kind == "quarantine" or (row and row["kind"] == "quarantine") else "tainted"
                tx.execute(
                    "INSERT INTO workspace_marks (path, kind, source, conversation_id, marked_at) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT (path) DO UPDATE SET kind = excluded.kind, source = excluded.source,"
                    " conversation_id = excluded.conversation_id, marked_at = excluded.marked_at,"
                    " trusted_digest = NULL, trusted_at = NULL",
                    (name, keep, source[:200], conversation_id or None, now_iso()),
                )

    def moved(self, old: str, new: str) -> None:
        """A file or folder was moved: its marks are copied to the new path, and the old path keeps them (an undo of the move
        brings the file back there). Where the new path was marked already (a file replaced), the stricter kind stays and
        the trust is dropped: the mark of the replaced file is needed again if the move is undone."""
        old, new = clean(old), clean(new)
        if old == new:
            return
        with self.db.transaction() as tx:
            rows = tx.execute(f"SELECT * FROM workspace_marks WHERE {_IN}", (old, *_inside(old))).fetchall()
            for row in rows:
                target = new + row["path"][len(old) :]
                there = tx.execute("SELECT kind FROM workspace_marks WHERE path = ?", (target,)).fetchone()
                if there is None:
                    tx.execute(
                        "INSERT INTO workspace_marks (path, kind, source, conversation_id, marked_at, trusted_digest, trusted_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            target,
                            row["kind"],
                            row["source"],
                            row["conversation_id"],
                            row["marked_at"],
                            row["trusted_digest"],
                            row["trusted_at"],
                        ),
                    )
                else:
                    kind = "quarantine" if "quarantine" in (row["kind"], there["kind"]) else "tainted"
                    tx.execute(
                        "UPDATE workspace_marks SET kind = ?, source = ?, conversation_id = ?, marked_at = ?,"
                        " trusted_digest = NULL, trusted_at = NULL WHERE path = ?",
                        (kind, row["source"], row["conversation_id"], row["marked_at"], target),
                    )

    # ------------------------------------------------------------------
    # Asking (what the tools read)
    # ------------------------------------------------------------------
    def entry(self, path: str) -> Mark | None:
        try:
            name = clean(path)
        except PathError:
            return None
        row = self.db.one("SELECT * FROM workspace_marks WHERE path = ?", (name,))
        return _mark(row) if row else None

    def _digest(self, path: str) -> str:
        try:
            return hashlib.sha256(self.workspace.read_bytes(path)).hexdigest()
        except (WorkspaceError, PathError, OSError):
            return ""  # unreadable: it cannot match a trusted digest

    def trusted(self, mark: Mark) -> bool:
        """Does the content the person trusted equal the content now?"""
        return bool(mark.trusted_digest) and mark.trusted_digest == self._digest(mark.path)

    def untrusted(self, path: str) -> Mark | None:
        """The mark of a file that is not to be trusted now (None: it is not marked, or the person trusted what it holds)."""
        mark = self.entry(path)
        return None if mark is None or self.trusted(mark) else mark

    def untrusted_lines(self, lines: list[str]) -> dict[int, Mark]:
        """The lines of a search (`path:number: text`) that belong to an untrusted file, by index. A line is matched against
        the marked paths (a path may hold a colon, so it is not cut at the first one): a line that could be a marked file's
        counts as its."""
        marks = [_mark(row) for row in self.db.all("SELECT * FROM workspace_marks")]
        if not marks:
            return {}
        now: dict[str, Mark | None] = {}
        found: dict[int, Mark] = {}
        for index, line in enumerate(lines):
            for mark in marks:
                if line.startswith(mark.path + ":") and re.match(r"\d+: ", line[len(mark.path) + 1 :]):
                    if mark.path not in now:
                        now[mark.path] = None if self.trusted(mark) else mark
                    if now[mark.path] is not None:
                        found[index] = now[mark.path]
                    break
        return found

    # ------------------------------------------------------------------
    # The person's word
    # ------------------------------------------------------------------
    def trust(self, path: str) -> int:
        """The person trusts a marked file as it is now, or every marked file of a folder. How many files."""
        name = clean(path)
        rows = self.db.all(f"SELECT path FROM workspace_marks WHERE {_IN}", (name, *_inside(name)))
        count = 0
        for row in rows:
            digest = self._digest(row["path"])
            if digest:
                self.db.execute(
                    "UPDATE workspace_marks SET trusted_digest = ?, trusted_at = ? WHERE path = ?",
                    (digest, now_iso(), row["path"]),
                )
                count += 1
        return count

    def untrust(self, path: str) -> int:
        """The person takes their trust back."""
        name = clean(path)
        cursor = self.db.execute(
            f"UPDATE workspace_marks SET trusted_digest = NULL, trusted_at = NULL WHERE ({_IN}) AND trusted_digest IS NOT NULL",
            (name, *_inside(name)),
        )
        return cursor.rowcount

    def listing(self) -> list[dict]:
        """The marked files that are in the workspace. The mark of a deleted file is kept (see the module), but not shown."""
        rows = self.db.all("SELECT * FROM workspace_marks ORDER BY marked_at DESC, path")
        return [mark.describe(self.trusted(mark)) for mark in map(_mark, rows) if self.workspace.exists(mark.path)]


# A path, or what is inside it when it is a folder. Not LIKE: SQLite's LIKE ignores the case of ASCII letters, so a folder
# `Mail` would reach the marks of `mail/` (a different folder on this file system).
_IN = "(path = ? OR substr(path, 1, ?) = ?)"


def _inside(folder: str) -> tuple[int, str]:
    """The two values `_IN` takes after the path: the length of the folder's prefix (`folder/`) and the prefix."""
    prefix = folder + "/"
    return len(prefix), prefix
