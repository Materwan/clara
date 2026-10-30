"""Conversations and their messages.

Every client shares one list of conversations; each conversation remembers which client started it (a label only).

Messages form a tree: each message's `parent_id` is the one before it, and `active` marks the path the conversation
follows. Asking a question again (or editing it) starts a sibling of the old question; the old one and what followed
it leave the path but stay, as another *version* you can switch back to.

A conversation's older messages can be replaced by a summary: `summary_upto` is the last message it stands for, and
the prompt replays only what comes after.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from ..db import Database
from ..mirror.clock import bump
from ..timeutil import iso, now_iso, utcnow
from .facts import HOOK, PROJECT, SCHEDULE, fts_query, scope_name

MAX_TITLE = 100
PREVIEW = 300
ANY = "any"
NONE = "none"


@dataclass(frozen=True)
class MemoryScope:
    """A memory a conversation shares with others besides the global one (memory/facts.py)."""

    kind: str  # "project", or "automation" (the schedule or hook whose run the conversation is)
    scope: str  # as facts store it: project:3, schedule:2, hook:5
    name: str


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    titled_by: str
    client: str
    kind: str
    pinned: bool
    project_id: int | None
    model: str
    incognito: bool
    local_only: bool
    vault_open: bool
    summary: str
    summary_upto: int
    context_tokens: int
    created_at: str
    updated_at: str
    packs: tuple[str, ...] = ("web",)
    archived_at: str | None = None  # archived: it can be read, and takes no new message
    archived_by: str = ""  # who archived it: 'you', 'discord' (its idle week there), 'group' (its history switched off)
    auto_level: int | None = (
        None  # its own auto level (gate/levels.py); None: it follows its automation, project or the default
    )

    def active_packs(self) -> list[str]:
        """The packs switched on, the vault's switch included (it is stored as `vault_open`)."""
        return [*self.packs, *(["second_brain"] if self.vault_open and "second_brain" not in self.packs else [])]

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "titled_by": self.titled_by,
            "client": self.client,
            "kind": self.kind,
            "pinned": self.pinned,
            "project": self.project_id,
            "model": self.model,
            "incognito": self.incognito,
            "local_only": self.local_only,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summarised": bool(self.summary),
            "packs": self.active_packs(),
            "archived": self.archived_at is not None,
            "archived_by": self.archived_by,
            "auto_level": self.auto_level,
        }


@dataclass(frozen=True)
class StoredMessage:
    id: int
    parent_id: int | None
    role: str
    content: str
    prefix: str
    tool_calls: list | None
    tool_name: str | None
    thinking: str | None
    client: str
    turn_id: str | None
    created_at: str


@dataclass(frozen=True)
class TurnRow:
    """A message of a turn about to be stored: what followed the question."""

    role: str
    content: str
    tool_calls: list | None = None
    tool_name: str | None = None
    thinking: str | None = None


def _packs(text: str) -> tuple[str, ...]:
    try:
        found = json.loads(text or "[]")
    except ValueError:
        return ("web",)
    return tuple(str(item) for item in found) if isinstance(found, list) else ("web",)


def _conversation(row) -> Conversation:
    return Conversation(
        row["id"],
        row["title"],
        row["titled_by"],
        row["client"],
        row["kind"],
        bool(row["pinned"]),
        row["project_id"],
        row["model"],
        bool(row["incognito"]),
        bool(row["local_only"]),
        bool(row["vault_open"]),
        row["summary"],
        row["summary_upto"],
        row["context_tokens"],
        row["created_at"],
        row["updated_at"],
        _packs(row["packs"]),
        row["archived_at"],
        row["archived_by"],
        row["auto_level"],
    )


def _message(row) -> StoredMessage:
    return StoredMessage(
        row["id"],
        row["parent_id"],
        row["role"],
        row["content"],
        row["prefix"],
        json.loads(row["tool_calls"]) if row["tool_calls"] else None,
        row["tool_name"],
        row["thinking"],
        row["client"],
        row["turn_id"],
        row["created_at"],
    )


def new_id() -> str:
    return "c" + secrets.token_hex(8)


class ConversationError(ValueError):
    pass


class Conversations:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    def create(
        self,
        client: str,
        *,
        kind: str = "chat",
        title: str = "",
        project_id: int | None = None,
        model: str = "",
        incognito: bool = False,
        local_only: bool = False,
        conversation_id: str | None = None,
        packs: list[str] | None = None,
        auto_level: int | None = None,
    ) -> Conversation:
        now = now_iso()
        conversation_id = conversation_id or new_id()
        self.db.execute(
            "INSERT INTO conversations (id, title, titled_by, client, kind, project_id, model, incognito, local_only,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                title[:MAX_TITLE],
                "you" if title else "",
                client,
                kind,
                project_id,
                model,
                int(incognito),
                int(local_only),
                now,
                now,
            ),
        )
        if packs is None and project_id is not None:  # the special tools the project starts its conversations with
            row = self.db.one("SELECT packs FROM projects WHERE id = ?", (project_id,))
            packs = list(_packs(row["packs"])) if row is not None else None
        if packs is not None:
            self.update(conversation_id, packs=packs)
        if auto_level is not None:
            self.update(conversation_id, auto_level=auto_level)
        created = self.get(conversation_id)
        assert created is not None
        return created

    def get(self, conversation_id: str) -> Conversation | None:
        row = self.db.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return _conversation(row) if row else None

    def memory_scopes(self, conversation_id: str) -> dict[str, MemoryScope]:
        """The project and the automation whose memory this conversation shares, by kind (none of them: an empty dict)."""
        row = self.db.one(
            "SELECT c.project_id, (SELECT p.name FROM projects p WHERE p.id = c.project_id) AS project_name,"
            f" {self._SCHEDULE} AS schedule_id, (SELECT s.name FROM schedules s WHERE s.id = {self._SCHEDULE}) AS schedule_name,"
            f" {self._HOOK} AS hook_id, (SELECT t.name FROM triggers t WHERE t.id = {self._HOOK}) AS hook_name"
            " FROM conversations c WHERE c.id = ?",
            (conversation_id,),
        )
        found: dict[str, MemoryScope] = {}
        if row is None:
            return found
        if row["project_id"] is not None:
            found["project"] = MemoryScope("project", scope_name(PROJECT, row["project_id"]), row["project_name"])
        if row["schedule_id"] is not None:
            found["automation"] = MemoryScope(
                "automation", scope_name(SCHEDULE, row["schedule_id"]), row["schedule_name"]
            )
        elif row["hook_id"] is not None:
            found["automation"] = MemoryScope("automation", scope_name(HOOK, row["hook_id"]), row["hook_name"])
        return found

    def require(self, conversation_id: str) -> Conversation:
        conversation = self.get(conversation_id)
        if conversation is None:
            raise ConversationError(f"No conversation {conversation_id}.")
        return conversation

    # The schedule a conversation belongs to: the one that owns it, or the one whose run made it (one per run).
    _SCHEDULE = (
        "COALESCE((SELECT s.id FROM schedules s WHERE s.conversation_id = c.id),"
        " (SELECT r.schedule_id FROM schedule_runs r WHERE r.conversation_id = c.id ORDER BY r.id DESC LIMIT 1))"
    )
    # The hook a conversation belongs to: the one whose run made it.
    _HOOK = "(SELECT r.trigger_id FROM trigger_runs r WHERE r.conversation_id = c.id ORDER BY r.id DESC LIMIT 1)"
    # Whether the group of a conversation shows it in the history: its schedule's say, else its hook's, else its project's,
    # else yes. A schedule's own conversation whose schedule is gone has no group to switch on, so it stays out.
    _SHOWN = (
        f"COALESCE((SELECT s.in_history FROM schedules s WHERE s.id = {_SCHEDULE}),"
        f" (SELECT t.in_history FROM triggers t WHERE t.id = {_HOOK}),"
        " CASE WHEN c.kind = 'schedule' THEN 0 END, (SELECT p.in_history FROM projects p WHERE p.id = c.project_id), 1)"
    )

    def listing(
        self,
        q: str = "",
        project: str = ANY,
        kind: str = "chat",
        limit: int = 50,
        offset: int = 0,
        history: bool = False,
        hide_clients: tuple[str, ...] = (),
        archived: bool | None = False,
    ) -> list[dict]:
        """Pinned first, then the most recently active. `q` searches titles, summaries and messages. Each one names its
        `group` (its schedule, else its hook, else its project, else null). `kind` ANY lists chats and scheduled ones together; `history`
        leaves out those whose group is not shown in the history, the pinned ones apart, and so does `hide_clients` for
        the conversations the listed clients started."""
        where = []
        params: list = []
        if archived is not None:  # False: the ones not archived (the default); True: only the archived ones
            where.append("c.archived_at IS NOT NULL" if archived else "c.archived_at IS NULL")
        if kind != ANY:
            where.append("c.kind = ?")
            params.append(kind)
        if history:
            where.append(f"(c.pinned = 1 OR {self._SHOWN} = 1)")
        if hide_clients:
            where.append(f"(c.pinned = 1 OR c.client NOT IN ({', '.join('?' * len(hide_clients))}))")
            params += hide_clients
        if project == NONE:
            where.append("c.project_id IS NULL")
        elif project != ANY:
            where.append("c.project_id = ?")
            params.append(int(project))
        if q.strip():
            match = fts_query(q)
            like = f"%{q.strip()}%"
            where.append(
                "(c.title LIKE ? OR c.summary LIKE ?"
                + (
                    " OR c.id IN (SELECT m.conversation_id FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
                    " WHERE messages_fts MATCH ?))"
                    if match
                    else ")"
                )
            )
            params += [like, like] + ([match] if match else [])
        rows = self.db.all(
            "SELECT c.*, (SELECT substr(m.content, 1, ?) FROM messages m WHERE m.conversation_id = c.id"
            " AND m.role = 'user' AND m.active = 1 ORDER BY m.id LIMIT 1) AS preview,"
            f" {self._SCHEDULE} AS schedule_id, (SELECT s.name FROM schedules s WHERE s.id = {self._SCHEDULE}) AS schedule_name,"
            f" {self._HOOK} AS hook_id, (SELECT t.name FROM triggers t WHERE t.id = {self._HOOK}) AS hook_name,"
            " (SELECT p.name FROM projects p WHERE p.id = c.project_id) AS project_name"
            f" FROM conversations c WHERE {' AND '.join(where) or '1'} ORDER BY c.pinned DESC, c.updated_at DESC LIMIT ? OFFSET ?",
            (PREVIEW, *params, limit, offset),
        )
        return [
            {**_conversation(row).describe(), "preview": row["preview"] or "", "group": self._group(row)}
            for row in rows
        ]

    @staticmethod
    def _group(row) -> dict | None:
        if row["schedule_id"] is not None:
            return {"kind": "schedule", "id": row["schedule_id"], "name": row["schedule_name"]}
        if row["hook_id"] is not None:
            return {"kind": "hook", "id": row["hook_id"], "name": row["hook_name"]}
        if row["project_id"] is not None:
            return {"kind": "project", "id": row["project_id"], "name": row["project_name"]}
        return None

    def update(self, conversation_id: str, **fields) -> Conversation:
        allowed = {
            "title",
            "pinned",
            "project_id",
            "model",
            "local_only",
            "incognito",
            "vault_open",
            "packs",
            "auto_level",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ConversationError(f"Unknown fields: {', '.join(sorted(unknown))}")
        current = self.require(conversation_id)
        values: dict = {}
        for name, value in fields.items():
            if name == "title":
                title = " ".join(str(value or "").split())[:MAX_TITLE]
                values["title"] = title
                values["titled_by"] = "you" if title else ""
            elif name in ("pinned", "local_only", "vault_open"):
                values[name] = int(bool(value))
            elif name == "packs":  # the vault's switch lives in vault_open
                values["packs"] = json.dumps([str(item) for item in value if item != "second_brain"])
                values["vault_open"] = int("second_brain" in value)
            elif name == "incognito":
                if bool(value) != current.incognito and self.message_count(conversation_id):
                    raise ConversationError("Incognito is chosen when a conversation starts, not afterwards.")
                values[name] = int(bool(value))
            else:
                values[name] = value
        if values:
            assignments = ", ".join(f"{name} = ?" for name in values)
            with self.db.transaction() as tx:
                tx.execute(f"UPDATE conversations SET {assignments} WHERE id = ?", (*values.values(), conversation_id))
                bump(tx, conversation_id)
        return self.require(conversation_id)

    def title_if_untitled(self, conversation_id: str, title: str) -> str:
        """Clara's title, unless the conversation got one meanwhile (yours is never replaced)."""
        title = " ".join(title.split())[:MAX_TITLE]
        with self.db.transaction() as tx:
            changed = tx.execute(
                "UPDATE conversations SET title = ?, titled_by = 'clara' WHERE id = ? AND title = ''",
                (title, conversation_id),
            ).rowcount
            if changed:
                bump(tx, conversation_id)
        return self.require(conversation_id).title

    def touch(self, conversation_id: str) -> None:
        self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), conversation_id))

    def delete(self, conversation_id: str) -> bool:
        with self.db.transaction() as tx:
            self._trash(tx, "id = ?", (conversation_id,))
            return tx.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount > 0

    @staticmethod
    def _trash(tx, where: str, params: tuple = ()) -> None:
        """The Discord threads of the conversations that `where` picks (on the conversations table) are to be removed."""
        tx.execute(
            "INSERT OR IGNORE INTO discord_trash (thread_id) SELECT thread_id FROM discord_threads"
            f" WHERE thread_id <> '' AND conversation_id IN (SELECT id FROM conversations WHERE {where})",
            params,
        )

    def delete_where(self, where: str, params: tuple = ()) -> int:
        """Delete the conversations `where` picks (on the conversations table): what a Discord forum's deletion asks for."""
        with self.db.transaction() as tx:
            self._trash(tx, where, params)
            return tx.execute(f"DELETE FROM conversations WHERE {where}", params).rowcount

    def sweep_incognito(self, hours: int) -> int:
        """Delete incognito conversations idle for longer than `hours`."""
        limit = iso(utcnow() - timedelta(hours=hours))
        with self.db.transaction() as tx:
            self._trash(tx, "incognito = 1 AND updated_at <= ?", (limit,))
            return tx.execute("DELETE FROM conversations WHERE incognito = 1 AND updated_at <= ?", (limit,)).rowcount

    # ------------------------------------------------------------------
    # Summary state
    # ------------------------------------------------------------------
    def set_summary(self, conversation_id: str, summary: str, upto_id: int, context_tokens: int) -> None:
        self.db.execute(
            "UPDATE conversations SET summary = ?, summary_upto = ?, context_tokens = ? WHERE id = ?",
            (summary, upto_id, context_tokens, conversation_id),
        )

    def set_context_tokens(self, conversation_id: str, tokens: int) -> None:
        self.db.execute("UPDATE conversations SET context_tokens = ? WHERE id = ?", (tokens, conversation_id))

    def purge_summarised(self, conversation_id: str, upto_id: int) -> int:
        """Delete the messages a summary stands for (CLARA_PURGE_SUMMARISED)."""
        return self.db.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id <= ?", (conversation_id, upto_id)
        ).rowcount

    # ------------------------------------------------------------------
    # Messages on the path
    # ------------------------------------------------------------------
    _COLUMNS = "SELECT * FROM messages"

    def history(self, conversation_id: str, turns: int, after_id: int = 0) -> list[StoredMessage]:
        """The last `turns` turns of the path after `after_id`, oldest first."""
        start = self.db.one(
            "SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' AND id > ? AND active = 1"
            " ORDER BY id DESC LIMIT 1 OFFSET ?",
            (conversation_id, after_id, max(turns, 1) - 1),
        )
        rows = self.db.all(
            f"{self._COLUMNS} WHERE conversation_id = ? AND id >= ? AND id > ? AND active = 1 ORDER BY id",
            (conversation_id, start["id"] if start else after_id + 1, after_id),
        )
        return [_message(row) for row in rows]

    def messages_after(self, conversation_id: str, after_id: int = 0) -> list[StoredMessage]:
        rows = self.db.all(
            f"{self._COLUMNS} WHERE conversation_id = ? AND id > ? AND active = 1 ORDER BY id",
            (conversation_id, after_id),
        )
        return [_message(row) for row in rows]

    def turns_after(self, conversation_id: str, after_id: int = 0) -> int:
        return int(
            self.db.value(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user' AND id > ? AND active = 1",
                (conversation_id, after_id),
                0,
            )
        )

    def message_count(self, conversation_id: str, after_id: int = 0) -> int:
        return int(
            self.db.value(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND id > ? AND active = 1",
                (conversation_id, after_id),
                0,
            )
        )

    def first_exchange(self, conversation_id: str) -> tuple[str, str]:
        first = (
            "SELECT content FROM messages WHERE conversation_id = ? AND role = ? AND content != '' AND active = 1"
            " ORDER BY id LIMIT 1"
        )
        question = self.db.one(first, (conversation_id, "user"))
        answer = self.db.one(first, (conversation_id, "assistant"))
        return (question[0] if question else "", answer[0] if answer else "")

    def add_turn(
        self,
        conversation_id: str,
        question: str,
        rows: list[TurnRow],
        *,
        prefix: str = "",
        client: str = "",
        turn_id: str | None = None,
        ref: str = "",
    ) -> int:
        """Store a question and what followed it after the end of the path. Returns the question's id. `ref`: the id of
        the question's message on Discord, when it came from there."""
        now = now_iso()
        with self.db.transaction() as tx:
            parent = tx.execute(
                "SELECT MAX(id) FROM messages WHERE conversation_id = ? AND active = 1", (conversation_id,)
            ).fetchone()[0]
            question_id = parent = tx.execute(
                "INSERT INTO messages (conversation_id, parent_id, role, content, prefix, client, turn_id, ref, created_at)"
                " VALUES (?, ?, 'user', ?, ?, ?, ?, ?, ?)",
                (conversation_id, parent, question, prefix, client, turn_id, ref, now),
            ).lastrowid
            for row in rows:
                parent = tx.execute(
                    "INSERT INTO messages (conversation_id, parent_id, role, content, tool_calls, tool_name, thinking,"
                    " turn_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        conversation_id,
                        parent,
                        row.role,
                        row.content,
                        json.dumps(row.tool_calls, ensure_ascii=False) if row.tool_calls else None,
                        row.tool_name,
                        row.thinking or None,
                        turn_id,
                        now,
                    ),
                ).lastrowid
            tx.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            bump(tx, conversation_id)
        return int(question_id or 0)

    # ------------------------------------------------------------------
    # Versions: a question asked again, or edited
    # ------------------------------------------------------------------
    def question(self, conversation_id: str, message_id: int) -> StoredMessage | None:
        row = self.db.one(
            f"{self._COLUMNS} WHERE conversation_id = ? AND id = ? AND active = 1 AND role = 'user'",
            (conversation_id, message_id),
        )
        return _message(row) if row else None

    def tools_after(self, conversation_id: str, message_id: int) -> list[str]:
        rows = self.db.all(
            "SELECT tool_name FROM messages WHERE conversation_id = ? AND active = 1 AND id >= ? AND role = 'tool' ORDER BY id",
            (conversation_id, message_id),
        )
        return [row[0] for row in rows if row[0]]

    def detach_from(self, conversation_id: str, message_id: int) -> list[int]:
        """Take this message and everything after it off the path; returns their ids, for `reattach`."""
        with self.db.transaction() as tx:
            ids = [
                row[0]
                for row in tx.execute(
                    "SELECT id FROM messages WHERE conversation_id = ? AND active = 1 AND id >= ? ORDER BY id",
                    (conversation_id, message_id),
                ).fetchall()
            ]
            tx.executemany("UPDATE messages SET active = 0 WHERE id = ?", [(i,) for i in ids])
        return ids

    def reattach(self, ids: list[int]) -> None:
        with self.db.transaction() as tx:
            tx.executemany("UPDATE messages SET active = 1 WHERE id = ?", [(i,) for i in ids])

    def versions_of(self, conversation_id: str, message_ids: list[int]) -> dict[int, list[int]]:
        """For each question, the ids of all its versions (oldest first), for questions with more than one."""
        found: dict[int, list[int]] = {}
        for message_id in message_ids:
            siblings = [
                row[0]
                for row in self.db.all(
                    "SELECT s.id FROM messages m JOIN messages s ON s.conversation_id = m.conversation_id"
                    " AND s.parent_id IS m.parent_id AND s.role = 'user' WHERE m.id = ? AND m.conversation_id = ? ORDER BY s.id",
                    (message_id, conversation_id),
                )
            ]
            if len(siblings) > 1:
                found[message_id] = siblings
        return found

    def switch_version(self, conversation_id: str, message_id: int) -> None:
        """Make another version of a question the one the conversation follows, with what followed it then."""
        conversation = self.require(conversation_id)
        with self.db.transaction() as tx:
            target = tx.execute(
                "SELECT id, parent_id FROM messages WHERE id = ? AND conversation_id = ? AND role = 'user'",
                (message_id, conversation_id),
            ).fetchone()
            if target is None:
                raise ConversationError("No such question in this conversation.")
            current = tx.execute(
                "SELECT id FROM messages WHERE conversation_id = ? AND parent_id IS ? AND role = 'user' AND active = 1",
                (conversation_id, target["parent_id"]),
            ).fetchone()
            if current is not None and current["id"] == message_id:
                return
            if min(message_id, current["id"] if current else message_id) <= conversation.summary_upto:
                raise ConversationError("This part of the conversation was summarised: it cannot go another way.")
            if current is not None:
                tx.execute(
                    "UPDATE messages SET active = 0 WHERE conversation_id = ? AND active = 1 AND id >= ?",
                    (conversation_id, current["id"]),
                )
            node: int | None = message_id
            while node is not None:
                tx.execute("UPDATE messages SET active = 1 WHERE id = ?", (node,))
                node = tx.execute(
                    "SELECT MAX(id) FROM messages WHERE conversation_id = ? AND parent_id = ?", (conversation_id, node)
                ).fetchone()[0]
            bump(tx, conversation_id)

    def heal_versions(self) -> int:
        """A server that stopped while a question was asked again left the old version off the path and the new one
        not stored: put the newest version of that question back (called at start, before anything runs)."""
        rows = self.db.all(
            "SELECT m.conversation_id, m.parent_id, MAX(m.id) AS newest FROM messages m WHERE m.active = 0 AND m.role = 'user'"
            " AND NOT EXISTS (SELECT 1 FROM messages a WHERE a.conversation_id = m.conversation_id AND a.active = 1"
            " AND a.parent_id IS m.parent_id)"
            " AND (m.parent_id IS NULL OR EXISTS (SELECT 1 FROM messages p WHERE p.id = m.parent_id AND p.active = 1))"
            " GROUP BY m.conversation_id, m.parent_id"
        )
        for row in rows:
            node: int | None = row["newest"]
            while node is not None:
                self.db.execute("UPDATE messages SET active = 1 WHERE id = ?", (node,))
                node = self.db.value(
                    "SELECT MAX(id) FROM messages WHERE conversation_id = ? AND parent_id = ?",
                    (row["conversation_id"], node),
                )
        return len(rows)

    # ------------------------------------------------------------------
    # Reading back, and searching
    # ------------------------------------------------------------------
    def read_back(self, conversation_id: str, calls: bool = False) -> list[dict]:
        """The path as a client shows it: questions and answers (with `calls`, the tools called too), and the
        versions of each question that has several."""
        rows = self.db.all(f"{self._COLUMNS} WHERE conversation_id = ? AND active = 1 ORDER BY id", (conversation_id,))
        messages = [_message(row) for row in rows]
        versions = self.versions_of(conversation_id, [m.id for m in messages if m.role == "user"])
        shown: list[dict] = []
        for message in messages:
            if message.role == "tool" and not calls:
                continue
            entry: dict = {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }
            if message.role == "user":
                entry["client"] = message.client
                if message.id in versions:
                    entry["versions"] = versions[message.id]
                    entry["version"] = versions[message.id].index(message.id) + 1
            if message.role == "assistant" and message.tool_calls:
                if not calls and not message.content:
                    continue
                if calls:
                    entry["tool_calls"] = [call.get("function", {}) for call in message.tool_calls]
            if message.role == "tool":
                entry["tool_name"] = message.tool_name
            shown.append(entry)
        return shown

    def search(self, query: str, limit: int = 10, exclude: str | None = None) -> list[dict]:
        """Messages of earlier conversations that match, newest first (incognito conversations are never searched)."""
        match = fts_query(query)
        if not match:
            return []
        rows = self.db.all(
            "SELECT m.id, m.conversation_id, m.role, m.created_at, c.title,"
            " snippet(messages_fts, 0, '«', '»', '…', 24) AS excerpt"
            " FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid JOIN conversations c ON c.id = m.conversation_id"
            " WHERE messages_fts MATCH ? AND c.incognito = 0 AND c.id IS NOT ? ORDER BY bm25(messages_fts), m.id DESC LIMIT ?",
            (match, exclude, limit),
        )
        return [dict(row) for row in rows]
