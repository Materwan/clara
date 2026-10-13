"""What people send in a conversation, kept with it: a picture or a document that was sent in one message is still
there in the later ones, where Clara can read it again (the `read_conversation_file` tool, see tools.py). A document
the client already turned into text (the web site writes it in the message) is kept as that text (kind "text"). The
newest MAX_FILES of a conversation are kept; the older ones go. Erasing a conversation or a person erases them too
(memory.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .attachments import Attached, image_type
from .memory import Memory

MAX_FILES = 20  # the newest files kept for one conversation


@dataclass(frozen=True)
class SavedFile:
    id: int
    name: str
    mime: str  # a picture's MIME type ("" for a document)
    kind: str  # "picture", "document" (the file as sent) or "text" (a document's text, as the client wrote it)
    size: int  # bytes
    sender: str  # who sent it
    created_at: str
    data: bytes = field(default=b"", repr=False)  # only from `named`


class ConversationFiles:
    def __init__(self, memory: Memory, max_files: int = MAX_FILES):
        self._memory = memory
        self._max = max_files

    def save(self, conversation: str, person_id: int, files: Sequence[Attached]) -> None:
        """Keep these files of a conversation (the older ones beyond the newest `max_files` go)."""
        rows = []
        for file in files:
            mime = image_type(file.data) or ""
            rows.append((file.name, mime, "picture" if mime else "document", file.data))
        self._insert(conversation, person_id, rows)

    def save_texts(self, conversation: str, person_id: int, documents: Sequence[tuple[str, str]]) -> None:
        """Keep the text of documents a client wrote in a message, by (name, text)."""
        self._insert(conversation, person_id, [(name, "", "text", text.encode()) for name, text in documents])

    def _insert(self, conversation: str, person_id: int, rows: Sequence[tuple[str, str, str, bytes]]) -> None:
        if not rows:
            return
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._memory.lock, self._memory.database as db:
            for name, mime, kind, data in rows:
                db.execute(
                    "INSERT INTO conversation_files (conversation, person_id, name, mime, kind, size, data, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (conversation, person_id, name, mime, kind, len(data), data, now),
                )
            db.execute(
                "DELETE FROM conversation_files WHERE conversation = ? AND id NOT IN ("
                " SELECT id FROM conversation_files WHERE conversation = ? ORDER BY id DESC LIMIT ?)",
                (conversation, conversation, self._max),
            )

    def of(self, conversation: str) -> list[SavedFile]:
        """The files of a conversation, oldest first (their contents are not read)."""
        with self._memory.lock:
            rows = self._memory.database.execute(
                "SELECT f.id, f.name, f.mime, f.kind, f.size, f.created_at, p.name AS sender FROM conversation_files f"
                " JOIN people p ON p.id = f.person_id WHERE f.conversation = ? ORDER BY f.id",
                (conversation,),
            ).fetchall()
        return [self._saved(row) for row in rows]

    def named(self, conversation: str, name: str) -> SavedFile | None:
        """The newest file of a conversation with this name (any case), with its contents."""
        with self._memory.lock:
            row = self._memory.database.execute(
                "SELECT f.id, f.name, f.mime, f.kind, f.size, f.created_at, f.data, p.name AS sender FROM conversation_files f"
                " JOIN people p ON p.id = f.person_id WHERE f.conversation = ? AND f.name = ? COLLATE NOCASE"
                " ORDER BY f.id DESC LIMIT 1",
                (conversation, name),
            ).fetchone()
        return None if row is None else replace(self._saved(row), data=bytes(row["data"]))

    @staticmethod
    def _saved(row) -> SavedFile:
        return SavedFile(row["id"], row["name"], row["mime"], row["kind"], row["size"], row["sender"], row["created_at"])

