"""Tool packs: the special tools a conversation or a schedule is granted.

A pack is a name for a group of tools. The tools of a pack that is not switched on are not offered to the model at all
(like a tool whose service is off); a pack that is on only makes its tools exist, the rule gate still decides each call.

* In a chat, the "+" menu of the web site (or `/tools` on Discord and in the terminal) switches packs on or off for the
  conversation; the choice is saved with it, so it follows the conversation to every client.
* A schedule lists the packs its runs get; the conversation of a run starts with them.

Two packs are the familiar switches: `web` (web search and fetch, on by default) and `second_brain` (the vault open, its
tools offered), which are stored where they always were. A pack for schedules only (`where="schedule"`) is offered in
the conversation of a scheduled run, never in a plain chat; one for chats only (`where="chat"`) the other way round.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..connectors.mail import PROVIDERS
from ..events.delivery import discord_connected, forum

if TYPE_CHECKING:
    from ..memory.conversations import Conversation
    from ..services import Services

SECOND_BRAIN = "second_brain"


class PackError(ValueError):
    pass


@dataclass(frozen=True)
class Pack:
    id: str
    label: str
    description: str
    tools: frozenset[str]
    where: str = "both"  # chat, schedule or both
    default: bool = False  # on in a new conversation
    icon: str = "tools"
    check: Callable[[Services], str] | None = None  # "": usable, else why it is not


def _vault_needed(s: Services) -> str:
    return "" if s.vault is not None else "No vault is set up (CLARA_VAULT_PATH)."


def _discord_needed(s: Services) -> str:
    return "" if discord_connected(s) else "The Discord bot is not connected."


def _mail_needed(s: Services) -> str:
    connected = s.connectors is not None and any(s.connectors.mail_connected(name) for name in PROVIDERS)
    return "" if connected else "No mailbox is connected (the Connections page connects Gmail)."


PACKS: tuple[Pack, ...] = (
    Pack(
        "web",
        "Web search",
        "Search the web and read pages.",
        frozenset({"web_search", "web_fetch"}),
        default=True,
        icon="globe",
    ),
    Pack(
        SECOND_BRAIN,
        "Second brain",
        "Open your Obsidian vault: search and read notes, write them, and put a block in a day's daily note.",
        frozenset(),
        icon="book",
        check=_vault_needed,
    ),
    Pack(
        "mail",
        "Mail",
        "Your Gmail: search, read and draft; label messages, make and delete labels; mark a message read; move a message "
        "to the Trash. Sending always asks you first.",
        frozenset(
            {
                "mail_search",
                "mail_read",
                "mail_attachment_read",
                "mail_attachment_save",
                "mail_draft",
                "mail_send",
                "mail_labels",
                "mail_label_create",
                "mail_label",
                "mail_trash",
                "mail_mark_read",
                "mail_label_delete",
            }
        ),
        icon="mail",
        check=_mail_needed,
    ),
    Pack(
        "qcm",
        "QCM",
        "Make a quiz that opens in the side panel, one question at a time, and correct each answer. Web chat only.",
        frozenset({"qcm"}),
        where="chat",
        icon="quiz",
    ),
    Pack(
        "day_conversations",
        "Day conversations",
        "Read what was said on a day, with the context of conversations begun earlier, or one conversation in full.",
        frozenset({"day_conversations", "read_conversation"}),
        icon="chat",
    ),
    Pack(
        "day_activity",
        "Day activity",
        "Tasks done and open, reminders fired, scheduled runs and what Clara did on a day.",
        frozenset({"day_activity"}),
        icon="tasks",
    ),
    Pack(
        "files_created",
        "Files created",
        "The files of a day (workspace, vault, Drive, files you sent) as a short list; "
        "read one, or only its diff, when needed.",
        frozenset({"files_created", "read_created_file"}),
        icon="file",
    ),
    Pack(
        "daily_notes",
        "Daily notes",
        "Read a week or a month of daily notes of the vault.",
        frozenset({"daily_notes"}),
        icon="calendar",
        check=_vault_needed,
    ),
    Pack(
        "automations",
        "Automations",
        "Read, create, change and delete your schedules and hooks. Every change asks you first.",
        frozenset({"automation_list", "automation_create", "automation_update", "automation_delete"}),
        where="chat",
        icon="play",
    ),
    Pack(
        "ask_person",
        "Ask you",
        "Ask you a question on the web site and Discord, wait for your answer in the "
        "conversation, then finish the run.",
        frozenset({"ask_person", "finish_run"}),
        where="schedule",
        icon="bell",
    ),
    Pack(
        "discord_thread",
        "Discord thread",
        "Open, write in and remove a Discord thread, whose replies come back to the run.",
        frozenset({"discord_thread"}),
        where="schedule",
        icon="chat",
        check=_discord_needed,
    ),
)
BY_ID = {pack.id: pack for pack in PACKS}
DEFAULT = tuple(pack.id for pack in PACKS if pack.default)


class Packs:
    def __init__(self, services: Services):
        self.s = services

    def unavailable(self, pack: Pack) -> str:
        return pack.check(self.s) if pack.check else ""

    def listing(self, where: str | None = None) -> list[dict]:
        """The packs for a place (`chat` or `schedule`; None: all), with why one cannot be used right now."""
        return [
            {
                "id": pack.id,
                "label": pack.label,
                "description": pack.description,
                "icon": pack.icon,
                "where": pack.where,
                "default": pack.default,
                "tools": sorted(pack.tools),
                "available": not self.unavailable(pack),
                "reason": self.unavailable(pack),
            }
            for pack in PACKS
            if where is None or pack.where in (where, "both")
        ]

    def check(self, ids, where: str) -> list[str]:
        """These pack ids, in the registry's order, without repeats; PackError for one that does not exist or does not
        belong there."""
        wanted = [str(item) for item in (ids or [])]
        unknown = [item for item in wanted if item not in BY_ID]
        if unknown:
            raise PackError(f"No such tool pack: {', '.join(unknown)}.")
        misplaced = [item for item in wanted if BY_ID[item].where not in (where, "both")]
        if misplaced:
            raise PackError(f"Not for {'a chat' if where == 'chat' else 'a schedule'}: {', '.join(misplaced)}.")
        return [pack.id for pack in PACKS if pack.id in wanted]

    @staticmethod
    def parse(text: str) -> list[str]:
        try:
            found = json.loads(text or "[]")
        except ValueError:
            return list(DEFAULT)
        return [item for item in found if item in BY_ID] if isinstance(found, list) else list(DEFAULT)

    @staticmethod
    def starting_with(patterns) -> list[str]:
        """The packs a new conversation starts with: the defaults, and each pack holding a tool that these patterns (names
        or globs, as a hook's allow-list has them) allow. A hook's run gets its packs this way, and only the packs a run
        may have (`both`): the Automations pack is never given to a run, whatever its allow-list names."""
        return [
            pack.id
            for pack in PACKS
            if pack.default
            or (
                pack.where == "both"
                and any(fnmatch.fnmatchcase(name, pattern) for name in pack.tools for pattern in patterns)
            )
        ]

    def active(self, conversation: Conversation | None) -> list[str]:
        if conversation is None:
            return list(DEFAULT)
        stored = [item for item in conversation.packs if item != SECOND_BRAIN]
        return [pack.id for pack in PACKS if pack.id in stored or (pack.id == SECOND_BRAIN and conversation.vault_open)]

    # ------------------------------------------------------------------
    # Scheduled runs
    # ------------------------------------------------------------------
    def run_of(self, conversation_id: str):
        """The scheduled run a conversation belongs to (its row, with the schedule's name), or None."""
        if not conversation_id:
            return None
        return self.s.db.one(
            "SELECT r.*, s.name AS schedule_name, s.deliver FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id"
            " WHERE r.conversation_id = ? ORDER BY r.id DESC LIMIT 1",
            (conversation_id,),
        )

    # ------------------------------------------------------------------
    # What is offered
    # ------------------------------------------------------------------
    def hidden(self, conversation: Conversation | None) -> set[str]:
        """The tools of the packs that are off, unusable now, or for schedules in a conversation that is no run (and the
        chat-only ones in a run's: an automation never manages automations)."""
        active = set(self.active(conversation))
        run = self.run_of(conversation.id) if conversation is not None else None
        hidden: set[str] = set()
        for pack in PACKS:
            if (
                pack.id not in active
                or self.unavailable(pack)
                or (pack.where == "schedule" and run is None)
                or (pack.where == "chat" and run is not None)
            ):
                hidden |= pack.tools
        if run is not None and forum(run["deliver"]):  # its thread is the forum's: none is opened in #general
            hidden |= BY_ID["discord_thread"].tools
        return hidden
