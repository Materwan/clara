"""What people send in a conversation, kept with it: a picture or a document that was sent in one message is still
there in the later ones, where Clara can read it again (the `read_conversation_file` tool, see tools.py). A document
the client already turned into text (the web site writes it in the message) is kept as that text (kind "text"). The
newest MAX_FILES of a conversation are kept; the older ones go. Erasing a conversation or a person erases them too
(memory.py).

Each file has a number, and a document the digest of its text as the message shows it: the history finds the file of
each `<document>` block by its text (never by its name, which two files can share) and names it by number, and a
document already kept (a question asked again) is not kept twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .attachments import Attached, block_name, document_digest, documents_in, image_type
from .memory import Memory

MAX_FILES = 20  # the newest files kept for one conversation
_COLUMNS = "f.id, f.name, f.mime, f.kind, f.size, f.created_at, f.digest, p.name AS sender"


@dataclass(frozen=True)
class SavedFile:
    id: int
    name: str
    mime: str  # a picture's MIME type ("" for a document)
    kind: str  # "picture", "document" (the file as sent) or "text" (a document's text, as the client wrote it)
    size: int  # bytes
    sender: str  # who sent it
    created_at: str
    digest: str = ""  # document_digest of the text its `<document>` block shows ("": a picture, or not known)
    data: bytes = field(default=b"", repr=False)  # only from `named` and `numbered`


class ConversationFiles:
    def __init__(self, memory: Memory, max_files: int = MAX_FILES):
        self._memory = memory
        self._max = max_files
        self._digest_texts()

    def save_message(self, conversation: str, person_id: int, files: Sequence[Attached], message: str) -> None:
        """Keep what a message carries: its files, and the documents the client wrote in it (the web site does).
        A document whose text is already kept here (the same question asked again) is not kept twice."""
        blocks = documents_in(message)
        digests: dict[str, list[str]] = {}  # the name of a block -> the digests of the blocks so named, in order
        for name, body in blocks:
            digests.setdefault(name, []).append(document_digest(body))
        kept = self._digests(conversation)
        rows: list[tuple[str, str, str, bytes, str]] = []
        sent: set[str] = set()
        for file in files:
            mime = image_type(file.data) or ""
            shown = block_name(file.name)
            sent.add(shown)
            waiting = None if mime else digests.get(shown)
            digest = waiting.pop(0) if waiting else ""  # its block: the next one with its name
            if digest in kept:
                continue
            if digest:
                kept.add(digest)
            rows.append((file.name, mime, "picture" if mime else "document", file.data, digest))
        for name, body in blocks:
            digest = document_digest(body)
            if name in sent or digest in kept:
                continue
            kept.add(digest)
            rows.append((name, "", "text", body.encode(), digest))
        self._insert(conversation, person_id, rows)

    def save(self, conversation: str, person_id: int, files: Sequence[Attached]) -> None:
        """Keep these files of a conversation (the older ones beyond the newest `max_files` go)."""
        rows = []
        for file in files:
            mime = image_type(file.data) or ""
            rows.append((file.name, mime, "picture" if mime else "document", file.data, ""))
        self._insert(conversation, person_id, rows)

    def save_texts(self, conversation: str, person_id: int, documents: Sequence[tuple[str, str]]) -> None:
        """Keep the text of documents a client wrote in a message, by (name, text)."""
        self._insert(
            conversation, person_id, [(name, "", "text", text.encode(), document_digest(text)) for name, text in documents]
        )

    def _insert(self, conversation: str, person_id: int, rows: Sequence[tuple[str, str, str, bytes, str]]) -> None:
        if not rows:
            return
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._memory.lock, self._memory.database as db:
            for name, mime, kind, data, digest in rows:
                db.execute(
                    "INSERT INTO conversation_files (conversation, person_id, name, mime, kind, size, data, created_at,"
                    " digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (conversation, person_id, name, mime, kind, len(data), data, now, digest),
                )
            db.execute(
                "DELETE FROM conversation_files WHERE conversation = ? AND id NOT IN ("
                " SELECT id FROM conversation_files WHERE conversation = ? ORDER BY id DESC LIMIT ?)",
                (conversation, conversation, self._max),
            )

    def _digests(self, conversation: str) -> set[str]:
        with self._memory.lock:
            rows = self._memory.database.execute(
                "SELECT digest FROM conversation_files WHERE conversation = ? AND digest != ''", (conversation,)
            ).fetchall()
        return {row["digest"] for row in rows}

    def _digest_texts(self) -> None:
        """Give their digest to the texts kept before there were digests (a text is its block's text, so it is
        known; a document as sent is not, short of reading it again: its blocks stay whole in the history)."""
        with self._memory.lock, self._memory.database as db:
            rows = db.execute("SELECT id, data FROM conversation_files WHERE kind = 'text' AND digest = ''").fetchall()
            for row in rows:
                digest = document_digest(bytes(row["data"]).decode("utf-8", "replace"))
                db.execute("UPDATE conversation_files SET digest = ? WHERE id = ?", (digest, row["id"]))

    def of(self, conversation: str) -> list[SavedFile]:
        """The files of a conversation, oldest first (their contents are not read)."""
        with self._memory.lock:
            rows = self._memory.database.execute(
                f"SELECT {_COLUMNS} FROM conversation_files f"
                " JOIN people p ON p.id = f.person_id WHERE f.conversation = ? ORDER BY f.id",
                (conversation,),
            ).fetchall()
        return [self._saved(row) for row in rows]

    def named(self, conversation: str, name: str) -> SavedFile | None:
        """The newest file of a conversation with this name (any case), with its contents."""
        with self._memory.lock:
            row = self._memory.database.execute(
                f"SELECT {_COLUMNS}, f.data FROM conversation_files f"
                " JOIN people p ON p.id = f.person_id WHERE f.conversation = ? AND f.name = ? COLLATE NOCASE"
                " ORDER BY f.id DESC LIMIT 1",
                (conversation, name),
            ).fetchone()
        return None if row is None else replace(self._saved(row), data=bytes(row["data"]))

    def numbered(self, conversation: str, number: int) -> SavedFile | None:
        """The file of a conversation with this number, with its contents (None: not one of this conversation's)."""
        with self._memory.lock:
            row = self._memory.database.execute(
                f"SELECT {_COLUMNS}, f.data FROM conversation_files f"
                " JOIN people p ON p.id = f.person_id WHERE f.conversation = ? AND f.id = ?",
                (conversation, number),
            ).fetchone()
        return None if row is None else replace(self._saved(row), data=bytes(row["data"]))

    @staticmethod
    def _saved(row) -> SavedFile:
        return SavedFile(
            row["id"], row["name"], row["mime"], row["kind"], row["size"], row["sender"], row["created_at"], row["digest"]
        )
