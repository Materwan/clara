"""What each client keeps for dictation: its current dictation conversation, automatic sending, and the approval mode and tool
packs a new dictation conversation starts with (table `dictation_clients`, made on first use). DECISIONS.md, « Dictation goes into
conversations, per client »."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Literal, get_args

from ..packs.registry import DEFAULT as DEFAULT_PACKS
from ..timeutil import now_iso

if TYPE_CHECKING:
    from ..memory.conversations import Conversation
    from ..services import Services

Client = Literal["web", "island", "phone", "cli", "discord"]  # declared names: nothing else may dictate
CLIENTS: tuple[str, ...] = get_args(Client)
SOURCE = "dictation"  # the taint source of a dictation conversation


class Clients:
    def __init__(self, s: Services):
        self.s = s

    def get(self, client: str) -> dict:
        """The client's settings, with the defaults of a client that never dictated (`conversation` None)."""
        row = self.s.db.one("SELECT * FROM dictation_clients WHERE client = ?", (client,))
        mode = row["mode"] if row else None
        try:
            packs = [str(item) for item in json.loads(row["packs"])] if row else []
        except ValueError:
            packs = []
        return {
            "client": client,
            "conversation": row["conversation_id"] if row else None,
            "auto_send": bool(row["auto_send"]) if row else False,
            "mode": mode if mode is not None and self.s.modes.get(mode) is not None else None,
            "packs": packs or list(DEFAULT_PACKS),
        }

    def listing(self) -> list[dict]:
        return [self.get(client) for client in CLIENTS]

    def change(self, client: str, *, auto_send: bool | None, mode: str | None, packs: list[str] | None, set_mode: bool):
        """Store what was given (`mode` None with `set_mode`: follow the default mode). The caller has checked the values."""
        now = now_iso()
        self.s.db.execute("INSERT OR IGNORE INTO dictation_clients (client, updated_at) VALUES (?, ?)", (client, now))
        fields: dict = {}
        if auto_send is not None:
            fields["auto_send"] = int(auto_send)
        if set_mode:
            fields["mode"] = mode
        if packs is not None:
            fields["packs"] = json.dumps(packs)
        assignments = "".join(f"{name} = ?, " for name in fields)
        self.s.db.execute(
            f"UPDATE dictation_clients SET {assignments}updated_at = ? WHERE client = ?",
            (*fields.values(), now, client),
        )
        return self.get(client)

    def current(self, client: str) -> str | None:
        """The client's current dictation conversation, unless it is archived (it takes no new message: a new one is made)."""
        found = self.get(client)["conversation"]
        return None if found is None or self.s.mirror.is_archived(found) else found

    def make_conversation(self, client: str) -> Conversation:
        """A new dictation conversation for the client: a normal conversation with its mode and packs, tainted, and the one
        the next dictations of the client go to. The previous one is left as it is."""
        settings = self.get(client)
        now = datetime.now(self.s.settings.zone())
        made = self.s.conversations.create(
            client,
            title=f"Dictation · {client} · {now.day} {now:%b %H:%M}",
            packs=settings["packs"],
            mode=settings["mode"],
        )
        if self.s.packs is not None and settings["mode"]:
            made = self.s.packs.with_mode(made)  # as `PUT /v1/conversations/{id}/mode` does
        self.s.conversations.taint(made.id, SOURCE)
        self.s.db.execute(
            "INSERT INTO dictation_clients (client, conversation_id, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (client) DO UPDATE SET conversation_id = excluded.conversation_id, updated_at = excluded.updated_at",
            (client, made.id, now_iso()),
        )
        return made
