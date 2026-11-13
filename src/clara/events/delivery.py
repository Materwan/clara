"""What an automation's run tells you when it ends (automations/service.py).

Every run sends the short notification to every client, Discord's `#notification` included (a local-only automation: the web
only). When the automation has **Show in history** ticked, its runs live in a forum thread on Discord (their conversation, in the
automation's forum): the run is told in `#notification` when it starts (with the thread's link), asks its questions in the thread,
and its whole answer is posted there when it ends well, for you to answer. A run that failed is only told by the notification: a
failure is not a conversation. Discord is outside the machine, so a local-only conversation is never mirrored there.

(Until schema version 32 a `deliver` setting chose between these; its four values are gone: `discord` and `both` became « Show in
history », all four became the notification.)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .store import MAX_SAID, Events

if TYPE_CHECKING:
    from ..services import Services

DISCORD_SILENCE = 300.0  # seconds without the bot's heartbeat before it is called disconnected
SAID = "said"  # the stage of an `ask` event that is a message for #general, with no run waiting
STARTED_SOURCE = "automations"  # the source of the notice that a run that lives in a forum thread has started


def forum_mark(shown: bool) -> dict:
    """What the payload of an `ask` event gains when its run lives in a forum thread (nothing otherwise)."""
    return {"forum": True} if shown else {}


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
