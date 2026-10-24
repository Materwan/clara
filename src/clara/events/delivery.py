"""Where the result of an automation (a schedule's or a hook's run) goes: its `deliver` setting.

* `notice`: a short notification to every client, Discord's `#notification` included. What automations always did.
* `web`: the same short notification to every client but Discord, which hears nothing.
* `discord`: the run's whole answer in Discord, as a message you can answer. Nothing else is sent.
* `both`: that message, and the short notification to the clients that are not Discord.

A run that delivers to Discord lives in **one forum thread** (its conversation, in its automation's forum) and nothing in
`#general`: it is told in `#notification` when the run starts (with the thread's link), asks its questions in the thread,
and you answer there. Choosing `discord` or `both` therefore switches the automation's "Show in history" on (the forum is
made from it).

A run that failed always tells you the way `notice` does: a failure is not a conversation. Discord is outside the machine,
so a local-only automation cannot deliver there.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .store import MAX_SAID, TARGETS, Events

if TYPE_CHECKING:
    from ..services import Services

DELIVERIES = ("notice", "web", "discord", "both")
OTHER_CLIENTS = tuple(client for client in TARGETS if client != "discord")
DISCORD_SILENCE = 300.0  # seconds without the bot's heartbeat before it is called disconnected
SAID = "said"  # the stage of an `ask` event that is a message for #general, with no run waiting
STARTED_SOURCE = "automations"  # the source of the notice that a run that lives in a forum thread has started


def check(deliver: object, *, local_only: bool, conversation: bool = True) -> str:
    """The setting, or ValueError saying what is wrong. `conversation` is False for a notification with no model: there is
    nothing to answer in."""
    if deliver not in DELIVERIES:
        raise ValueError(f"deliver must be one of {', '.join(DELIVERIES)}.")
    if deliver in ("discord", "both"):
        if local_only:
            raise ValueError("Discord is outside the machine: a local-only automation cannot deliver there.")
        if not conversation:
            raise ValueError("A notification has no conversation to answer in: choose notice or web.")
    return str(deliver)


def forum(deliver: str) -> bool:
    """Does a run of this automation live in a forum thread on Discord? It does when its result goes to Discord."""
    return deliver in ("discord", "both")


def forum_mark(deliver: str) -> dict:
    """What the payload of an `ask` event gains when its run lives in a forum thread (nothing otherwise)."""
    return {"forum": True} if forum(deliver) else {}


def says(deliver: str, ok: bool) -> bool:
    """Does the whole answer of this run go to Discord's #general?"""
    return ok and deliver in ("discord", "both")


def notifies(deliver: str, ok: bool) -> bool:
    """Is the short notification of this run's end sent?"""
    return not (ok and deliver == "discord")


def notice_targets(deliver: str, local_only: bool, ok: bool = True) -> tuple[str, ...] | None:
    """The clients the short notification is for; None: every client."""
    if local_only:
        return ("web",)
    return OTHER_CLIENTS if ok and deliver in ("web", "both") else None


def say(
    events: Events, title: str, text: str, *, conversation: str | None, source: str, payload: dict | None = None
) -> None:
    """Put a message in Discord's #general: the conversation becomes the current one there, so your next message goes to it."""
    events.publish(
        "ask",
        title,
        text[:MAX_SAID],
        payload={**(payload or {}), "conversation": conversation, "stage": SAID},
        targets=("discord",),
        source=source,
        conversation_id=conversation,
    )


def started(s: Services, title: str, *, conversation: str, payload: dict) -> None:
    """A run that lives in a forum thread has begun: a notice for Discord, whose bot adds the thread's link (it is the one that
    makes the thread). Only when the mirror shows the conversation: without a thread there is nothing to link to."""
    if s.mirror is None or not s.mirror.wants(conversation):
        return
    s.events.publish(
        "notification",
        title,
        "Started.",
        payload={**payload, "conversation": conversation, "thread": s.mirror.thread_of(conversation)},
        targets=("discord",),
        source=STARTED_SOURCE,
        conversation_id=conversation,
    )


def discord_connected(s: Services) -> bool:
    seen = s.clients.get("discord")
    return seen is not None and time.monotonic() - seen < DISCORD_SILENCE
