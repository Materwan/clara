"""The Discord mirror: what the bot must make in Discord, from what Clara holds.

A place is a forum in Discord (inside its own category): one for each project, schedule and hook whose "Show in history"
is on, one for the conversations that belong to none of them (`main`), and one for the incognito ones. Each conversation
that is mirrored is a thread in its place. Local-only conversations are never mirrored.

The bot asks `sync(since)` for what changed since its last revision (`mirror_rev`, from `mirror_clock`), creates or edits
what differs, and tells the result with `report(...)`: the ids Discord gave, the state each thread is in, and what it
could not do. What Discord changes itself (an archived thread, a message edited, a thread deleted) comes back with
`event(...)`. A turn in a mirrored conversation is watched while it runs (`watch`), so the bot can show the answer as it
is written; the stored answer then replaces what was shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..db import Database
from ..events.store import Events
from ..memory.conversations import Conversations
from ..timeutil import now_iso
from . import text
from .clock import bump
from .live import Live

MAIN = "main"
INCOGNITO = "incognito"


class MirrorError(ValueError):
    pass


@dataclass
class _Turn:
    conversation: str
    turn: str
    names: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    done: bool = False


class Watch:
    """One turn, followed while it runs: what the mirror shows of it. Only a turn of a mirrored conversation is shown."""

    def __init__(self, mirror: Mirror, question: str, ref: str, thread: str):
        self.mirror = mirror
        self.question = question  # the question as stored (its documents named, not repeated)
        self.ref = ref
        self.thread = thread  # a Discord thread the question was asked in, for a new conversation
        self.turn: _Turn | None = None

    def see(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "turn" and self.turn is None:
            conversation = event.get("conversation")
            if not conversation or self.mirror.place_of_id(conversation) is None:
                return
            self.turn = _Turn(conversation, str(event.get("id")))
            if self.thread:
                self.mirror.link_thread(conversation, self.thread)
            self.mirror.live.publish(
                {
                    "type": "start",
                    "conversation": conversation,
                    "turn": self.turn.turn,
                    "title": self.mirror.title_of(conversation),
                    "text": self.question,
                    "digest": text.digest(self.question),
                    "ref": self.ref,
                    "place": self.mirror.place_of_id(conversation),
                    "thread": self.mirror.thread_of(conversation),
                }
            )
        elif self.turn is None:
            return
        elif kind == "token":
            self.turn.text.append(event.get("text", ""))
            self.mirror.live.publish(self._event("token", text=event.get("text", "")))
        elif kind == "tool_start":
            self.turn.names.append(str(event.get("name", "")))
            self.mirror.live.publish(self._event("summary", text=text.tools_line(self.turn.names)))
        elif kind == "done":
            self._finish(event)

    def _event(self, kind: str, **fields: Any) -> dict:
        assert self.turn is not None
        return {"type": kind, "conversation": self.turn.conversation, "turn": self.turn.turn, **fields}

    def _finish(self, done: dict) -> None:
        assert self.turn is not None
        if self.turn.done:
            return
        self.turn.done = True
        stored = self.mirror.answer_of(self.turn.conversation, self.turn.turn)
        if stored is None:  # not stored (a question that was not kept): what was written is what there is
            body = text.answer_text(self.turn.names, "".join(self.turn.text))
        else:
            body = stored["text"]
        self.mirror.live.publish(
            self._event(
                "done",
                text=body,
                digest=text.digest(body),
                files=[card.get("path", "") for card in done.get("files", []) if isinstance(card, dict)],
                failed=False,
            )
        )

    def close(self) -> None:
        """The turn ended without its `done` (it failed): the mirror ends what it showed."""
        if self.turn is None or self.turn.done:
            return
        self.turn.done = True
        body = text.answer_text(self.turn.names, "".join(self.turn.text))
        self.mirror.live.publish(self._event("done", text=body, digest=text.digest(body), files=[], failed=True))


class Mirror:
    def __init__(self, db: Database, events: Events):
        self.db = db
        self.events = events
        self.live = Live()

    # ------------------------------------------------------------------
    # Revisions: every change the mirror must see takes the next number
    # ------------------------------------------------------------------
    def _bump_many(self, tx, conversation_ids: list[str]) -> None:
        for conversation_id in conversation_ids:
            bump(tx, conversation_id)

    # ------------------------------------------------------------------
    # Places and groups
    # ------------------------------------------------------------------
    def _groups(self) -> dict[str, dict]:
        """Every project, schedule and hook: its place key, its name and whether its conversations are shown in history."""
        found: dict[str, dict] = {}
        for row in self.db.all("SELECT id, name, in_history FROM projects"):
            found[f"project:{row['id']}"] = {"name": row["name"], "shown": bool(row["in_history"])}
        for row in self.db.all("SELECT id, name, in_history FROM schedules"):
            found[f"schedule:{row['id']}"] = {"name": row["name"], "shown": bool(row["in_history"])}
        for row in self.db.all("SELECT id, name, in_history FROM triggers"):
            found[f"hook:{row['id']}"] = {"name": row["name"], "shown": bool(row["in_history"])}
        return found

    @staticmethod
    def _group_key(row) -> str | None:
        """The place a conversation belongs to, shown or not (None: local-only, which is never on Discord)."""
        if row["local_only"]:
            return None
        if row["incognito"]:
            return INCOGNITO
        if row["schedule_id"] is not None:
            return f"schedule:{row['schedule_id']}"
        if row["hook_id"] is not None:
            return f"hook:{row['hook_id']}"
        if row["project_id"] is not None:
            return f"project:{row['project_id']}"
        return MAIN

    def _place(self, row, groups: dict[str, dict]) -> str | None:
        key = self._group_key(row)
        if key is None:
            return None
        if key in (MAIN, INCOGNITO):
            return key
        return key if groups.get(key, {}).get("shown") else None

    def _conversations(self, where: str = "1 = 1", params: tuple = ()) -> list:
        """The conversations, with the schedule and the hook they belong to (as the history groups them)."""
        return self.db.all(
            f"SELECT c.*, {Conversations._SCHEDULE} AS schedule_id, {Conversations._HOOK} AS hook_id FROM conversations c WHERE {where}",
            params,
        )

    def place_of_id(self, conversation_id: str) -> str | None:
        rows = self._conversations("c.id = ?", (conversation_id,))
        return self._place(rows[0], self._groups()) if rows else None

    def wants(self, conversation_id: str) -> bool:
        return self.place_of_id(conversation_id) is not None

    def group_switched(self, key: str, shown: bool) -> None:
        """A project, schedule or hook got or lost its "Show in history": its conversations are archived in Clara (and
        their threads on Discord) when it is off, and unarchived again when it is on, if the switch did the archiving."""
        with self.db.transaction() as tx:
            changed = []
            for row in self._conversations():
                if row["local_only"] or self._group_key(row) != key:
                    continue
                if not shown and row["archived_at"] is None:
                    tx.execute(
                        "UPDATE conversations SET archived_at = ?, archived_by = 'group' WHERE id = ?",
                        (now_iso(), row["id"]),
                    )
                    changed.append(row["id"])
                elif shown and row["archived_by"] == "group":
                    tx.execute(
                        "UPDATE conversations SET archived_at = NULL, archived_by = '' WHERE id = ?", (row["id"],)
                    )
                    changed.append(row["id"])
            self._bump_many(tx, changed)

    def set_archived(self, conversation_id: str, archived: bool, by: str) -> bool:
        """Archive or unarchive a conversation. True when it changed. `by`: 'you' or 'discord'."""
        with self.db.transaction() as tx:
            row = tx.execute("SELECT archived_at FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                raise MirrorError("No such conversation.")
            if (row["archived_at"] is not None) == archived:
                return False
            if archived:
                tx.execute(
                    "UPDATE conversations SET archived_at = ?, archived_by = ? WHERE id = ?",
                    (now_iso(), by, conversation_id),
                )
            else:
                tx.execute(
                    "UPDATE conversations SET archived_at = NULL, archived_by = '' WHERE id = ?", (conversation_id,)
                )
            bump(tx, conversation_id)
        return True

    def is_archived(self, conversation_id: str) -> bool:
        row = self.db.one("SELECT archived_at FROM conversations WHERE id = ?", (conversation_id,))
        return row is not None and row["archived_at"] is not None

    def link_thread(self, conversation_id: str, thread_id: str) -> None:
        """A Discord thread that a new conversation was started in (a new post in a forum)."""
        place = self.place_of_id(conversation_id) or MAIN
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO discord_threads (conversation_id, place_key, thread_id, archived) VALUES (?, ?, ?, 0)"
                " ON CONFLICT (conversation_id) DO UPDATE SET thread_id = excluded.thread_id,"
                " place_key = excluded.place_key",
                (conversation_id, place, thread_id),
            )

    # ------------------------------------------------------------------
    # What the bot reads
    # ------------------------------------------------------------------
    def sync(self, since: int) -> dict:
        """The places, the conversations changed after `since`, and the threads to remove. Read the clock first: a change
        made while this runs is either in this answer or in the next one."""
        cursor = int(self.db.value("SELECT seq FROM mirror_clock WHERE id = 1", (), 0))
        groups = self._groups()
        wanted = [MAIN, INCOGNITO, *(key for key, group in groups.items() if group["shown"])]
        stored = {row["key"]: row for row in self.db.all("SELECT key, category_id, forum_id FROM discord_places")}
        places = []
        for key in wanted:
            name = {MAIN: "Clara", INCOGNITO: "Clara · incognito"}.get(key) or groups[key]["name"]
            known = stored.get(key)
            places.append(
                {
                    "key": key,
                    "name": name[:100],
                    "category_id": known["category_id"] if known else "",
                    "forum_id": known["forum_id"] if known else "",
                }
            )
        dropped = [
            {"key": row["key"], "category_id": row["category_id"], "forum_id": row["forum_id"]}
            for row in stored.values()
            if row["key"] not in wanted
        ]
        conversations = [self._entry(row, groups) for row in self._conversations("c.mirror_rev > ?", (since,))]
        trash = [row["thread_id"] for row in self.db.all("SELECT thread_id FROM discord_trash")]
        return {
            "cursor": cursor,
            "stopped": self.stopped(),
            "places": places,
            "dropped": dropped,
            "conversations": conversations,
            "trash": trash,
        }

    def stopped(self) -> str | None:
        return self.db.value("SELECT value FROM discord_state WHERE key = 'stopped'")

    def _entry(self, row, groups: dict[str, dict]) -> dict:
        place = self._place(row, groups)
        thread = self.db.one(
            "SELECT thread_id, place_key, archived FROM discord_threads WHERE conversation_id = ?", (row["id"],)
        )
        posted: dict[str, dict] = {}
        for post in self.db.all(
            "SELECT turn_key, side, ids, digest FROM discord_posts WHERE conversation_id = ?", (row["id"],)
        ):
            posted.setdefault(post["turn_key"], {})[post["side"]] = {
                "ids": json.loads(post["ids"]),
                "digest": post["digest"],
            }
        return {
            "id": row["id"],
            "title": text.clip(row["title"] or "", 100) if row["title"] else "Conversation",
            "archived": row["archived_at"] is not None,
            "place": place,
            "thread": (
                {"thread_id": thread["thread_id"], "place": thread["place_key"], "archived": bool(thread["archived"])}
                if thread
                else None
            ),
            "turns": self._turns(row["id"]) if place is not None else [],
            "posted": posted,
        }

    def _turns(self, conversation_id: str) -> list[dict]:
        rows = self.db.all(
            "SELECT id, role, content, turn_id, ref, tool_calls FROM messages"
            " WHERE conversation_id = ? AND active = 1 AND role IN ('user', 'assistant') ORDER BY id",
            (conversation_id,),
        )
        turns: list[dict] = []
        current: dict | None = None
        for row in rows:
            if row["role"] == "user":
                body, _ = text.question_text(row["content"])
                current = {
                    "key": row["turn_id"] or f"q{row['id']}",
                    "user": {"text": body, "ref": row["ref"]},
                    "names": [],
                    "texts": [],
                }
                current["user"]["digest"] = "ref" if row["ref"] else text.digest(body)
                turns.append(current)
            elif current is not None:
                for call in json.loads(row["tool_calls"] or "[]"):
                    name = (call.get("function") or {}).get("name") or ""
                    if name:
                        current["names"].append(name)
                if row["content"].strip():
                    current["texts"].append(row["content"].strip())
        result = []
        for turn in turns:
            reply = None
            if turn["names"] or turn["texts"]:
                body = text.answer_text(turn["names"], "\n\n".join(turn["texts"]))
                reply = {"text": body, "digest": text.digest(body)}
            result.append({"key": turn["key"], "user": turn["user"], "reply": reply})
        return result

    def answer_of(self, conversation_id: str, turn_id: str) -> dict | None:
        """What the mirror writes for one turn, as stored (the same text `sync` gives for it)."""
        for turn in self._turns(conversation_id):
            if turn["key"] == turn_id:
                return turn["reply"]
        return None

    # ------------------------------------------------------------------
    # What the bot reports
    # ------------------------------------------------------------------
    def report(self, body: dict) -> None:
        """The bot's results: places it made, threads and their state, messages it wrote (or removed, with no ids), threads
        it removed, and what stopped it (then you are told, in the alerts channel as well)."""
        stopping = ""
        with self.db.transaction() as tx:
            stamp = now_iso()
            for item in body.get("places", []):
                tx.execute(
                    "INSERT INTO discord_places (key, category_id, forum_id, updated_at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (key) DO UPDATE SET category_id = excluded.category_id,"
                    " forum_id = excluded.forum_id, updated_at = excluded.updated_at",
                    (item["key"], str(item.get("category_id", "")), str(item.get("forum_id", "")), stamp),
                )
            for key in body.get("dropped", []):
                tx.execute("DELETE FROM discord_places WHERE key = ?", (key,))
            for item in body.get("threads", []):
                conversation_id, thread_id = item["conversation"], str(item.get("thread_id", ""))
                previous = tx.execute(
                    "SELECT thread_id FROM discord_threads WHERE conversation_id = ?", (conversation_id,)
                ).fetchone()
                if previous is not None and previous["thread_id"] != thread_id:
                    tx.execute("DELETE FROM discord_posts WHERE conversation_id = ?", (conversation_id,))
                tx.execute(
                    "INSERT INTO discord_threads (conversation_id, place_key, thread_id, archived) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (conversation_id) DO UPDATE SET place_key = excluded.place_key,"
                    " thread_id = excluded.thread_id, archived = excluded.archived",
                    (conversation_id, str(item.get("place", "")), thread_id, int(bool(item.get("archived")))),
                )
            for item in body.get("posts", []):
                ids = [str(value) for value in item.get("ids", [])]
                if not ids:
                    tx.execute(
                        "DELETE FROM discord_posts WHERE conversation_id = ? AND turn_key = ? AND side = ?",
                        (item["conversation"], item["turn"], item["side"]),
                    )
                    continue
                tx.execute(
                    "INSERT INTO discord_posts (conversation_id, turn_key, side, ids, digest) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT (conversation_id, turn_key, side) DO UPDATE SET ids = excluded.ids,"
                    " digest = excluded.digest",
                    (item["conversation"], item["turn"], item["side"], json.dumps(ids), str(item.get("digest", ""))),
                )
            for thread_id in body.get("cleared", []):
                tx.execute("DELETE FROM discord_trash WHERE thread_id = ?", (str(thread_id),))
            if "stopped" in body:
                if body["stopped"]:
                    stopping = str(body["stopped"])[:500]
                    tx.execute(
                        "INSERT INTO discord_state (key, value) VALUES ('stopped', ?)"
                        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                        (stopping,),
                    )
                else:
                    tx.execute("DELETE FROM discord_state WHERE key = 'stopped'")
            if "error" in body:
                if body["error"]:
                    tx.execute(
                        "INSERT INTO discord_state (key, value) VALUES ('error', ?)"
                        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                        (str(body["error"])[:500],),
                    )
                else:
                    tx.execute("DELETE FROM discord_state WHERE key = 'error'")
        if stopping:
            self.events.publish(
                "alert",
                "Discord sync stopped",
                f"{stopping}. Clara sends nothing more to Discord until you resume it.",
                source="server",
            )

    def resume(self) -> None:
        self.report({"stopped": "", "error": ""})

    def status(self) -> dict:
        def count(sql: str) -> int:
            return int(self.db.value(sql, (), 0))

        return {
            "stopped": self.stopped(),
            "error": self.db.value("SELECT value FROM discord_state WHERE key = 'error'"),
            "places": count("SELECT COUNT(*) FROM discord_places WHERE forum_id <> ''"),
            "threads": count("SELECT COUNT(*) FROM discord_threads WHERE thread_id <> ''"),
            "trash": count("SELECT COUNT(*) FROM discord_trash"),
            "archived": count("SELECT COUNT(*) FROM conversations WHERE archived_at IS NOT NULL"),
            "at": now_iso(),
        }

    # ------------------------------------------------------------------
    # Discord changed something
    # ------------------------------------------------------------------
    def thread_archived(self, thread_id: str, archived: bool) -> str | None:
        """A thread was archived or unarchived in Discord: the conversation follows. Returns the conversation id."""
        row = self.db.one("SELECT conversation_id FROM discord_threads WHERE thread_id = ?", (thread_id,))
        if row is None:
            return None
        conversation_id = row["conversation_id"]
        with self.db.transaction() as tx:
            tx.execute("UPDATE discord_threads SET archived = ? WHERE thread_id = ?", (int(archived), thread_id))
        if archived:
            self.set_archived(conversation_id, True, "discord")
        elif self.db.value("SELECT archived_by FROM conversations WHERE id = ?", (conversation_id,)) == "discord":
            self.set_archived(conversation_id, False, "discord")
        return conversation_id

    def title_of(self, conversation_id: str) -> str:
        return self.db.value("SELECT title FROM conversations WHERE id = ?", (conversation_id,), "") or ""

    def thread_of(self, conversation_id: str) -> str:
        """The Discord thread of a conversation, '' when it has none (yet)."""
        return (
            self.db.value("SELECT thread_id FROM discord_threads WHERE conversation_id = ?", (conversation_id,), "")
            or ""
        )

    def conversation_of_thread(self, thread_id: str) -> str | None:
        return self.db.value("SELECT conversation_id FROM discord_threads WHERE thread_id = ?", (thread_id,))

    def conversation_of_ref(self, message_ref: str) -> tuple[str, int] | None:
        """The conversation and the id of the question that came from this Discord message, when it is the last question."""
        row = self.db.one(
            "SELECT m.id, m.conversation_id FROM messages m WHERE m.ref = ? AND m.role = 'user' AND m.active = 1"
            " AND NOT EXISTS (SELECT 1 FROM messages n WHERE n.conversation_id = m.conversation_id AND n.role = 'user'"
            " AND n.active = 1 AND n.id > m.id)",
            (message_ref,),
        )
        return (row["conversation_id"], row["id"]) if row else None

    def watch(self, message: str, ref: str = "", thread: str = "") -> Watch:
        """A turn of a question `message` (as it is stored), followed while it runs."""
        return Watch(self, text.question_text(message)[0], ref, thread)
