"""Files sent in a conversation, kept with it so Clara can read them again later (`read_conversation_file`).

A picture is kept as sent. A document is kept as sent when it came as a file, or as its text when the client wrote it
in the message (`<document>` blocks, as the web site does). Each file has a number, and a document the digest of its
text as the message shows it: the history finds the file of each `<document>` block by its text (never by its name,
which two files can share), and a file already kept is not kept twice. The newest 20 of a conversation are kept.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..db import Database
from ..timeutil import now_iso
from .attachments import Attached, block_name, document_digest, documents_in, image_type

MAX_FILES = 20
_COLUMNS = "id, name, mime, kind, size, client, created_at, digest"


@dataclass(frozen=True)
class SavedFile:
    id: int
    name: str
    mime: str
    kind: str  # picture, document (as sent) or text (the document's text, as the client wrote it)
    size: int
    client: str
    created_at: str
    digest: str
    data: bytes = field(default=b"", repr=False)

    def describe(self) -> dict:
        return {"id": self.id, "name": self.name, "mime": self.mime, "kind": self.kind, "size": self.size,
                "client": self.client, "created_at": self.created_at}


def _saved(row, with_data: bool = False) -> SavedFile:
    return SavedFile(row["id"], row["name"], row["mime"], row["kind"], row["size"], row["client"], row["created_at"],
                     row["digest"], bytes(row["data"]) if with_data else b"")


class ConversationFiles:
    def __init__(self, db: Database, max_files: int = MAX_FILES):
        self.db = db
        self.max_files = max_files

    def save_message(self, conversation_id: str, client: str, files: Sequence[Attached], message: str) -> None:
        """Keep what a message carries: its files, and the documents the client wrote in it."""
        blocks = documents_in(message)
        digests: dict[str, list[str]] = {}
        for name, body in blocks:
            digests.setdefault(name, []).append(document_digest(body))
        rows: list[tuple[str, str, str, bytes, str]] = []
        sent: set[str] = set()
        for file in files:
            mime = image_type(file.data) or ""
            shown = block_name(file.name)
            sent.add(shown)
            waiting = None if mime else digests.get(shown)
            digest = waiting.pop(0) if waiting else hashlib.sha256(file.data).hexdigest()
            rows.append((file.name, mime, "picture" if mime else "document", file.data, digest))
        for name, body in blocks:
            if name in sent:
                continue
            rows.append((name, "", "text", body.encode(), document_digest(body)))
        self._insert(conversation_id, client, rows)

    def _insert(self, conversation_id: str, client: str, rows: Sequence[tuple[str, str, str, bytes, str]]) -> None:
        if not rows:
            return
        now = now_iso()
        with self.db.transaction() as tx:
            for name, mime, kind, data, digest in rows:
                tx.execute(
                    "INSERT OR IGNORE INTO conversation_files (conversation_id, name, kind, mime, digest, size, data,"
                    " client, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (conversation_id, name, kind, mime, digest, len(data), data, client, now),
                )
            tx.execute(
                "DELETE FROM conversation_files WHERE conversation_id = ? AND id NOT IN ("
                " SELECT id FROM conversation_files WHERE conversation_id = ? ORDER BY id DESC LIMIT ?)",
                (conversation_id, conversation_id, self.max_files),
            )

    def of(self, conversation_id: str) -> list[SavedFile]:
        rows = self.db.all(
            f"SELECT {_COLUMNS} FROM conversation_files WHERE conversation_id = ? ORDER BY id", (conversation_id,)
        )
        return [_saved(row) for row in rows]

    def numbered(self, conversation_id: str, number: int) -> SavedFile | None:
        row = self.db.one(
            f"SELECT {_COLUMNS}, data FROM conversation_files WHERE conversation_id = ? AND id = ?",
            (conversation_id, number),
        )
        return _saved(row, True) if row else None

    def named(self, conversation_id: str, name: str) -> SavedFile | None:
        row = self.db.one(
            f"SELECT {_COLUMNS}, data FROM conversation_files WHERE conversation_id = ? AND name = ? COLLATE NOCASE"
            " ORDER BY id DESC LIMIT 1",
            (conversation_id, name),
        )
        return _saved(row, True) if row else None

    def readable(self, conversation_id: str) -> dict[str, int]:
        """The digest of each kept document's text -> its number: those blocks can be shortened in the history."""
        rows = self.db.all(
            "SELECT id, digest FROM conversation_files WHERE conversation_id = ? AND kind != 'picture'", (conversation_id,)
        )
        return {row["digest"]: row["id"] for row in rows}
