"""Discord tool: `discord_say`, a message in #general that the person can answer there.

`notify` is a ping (it needs no answer, and lands in #notification); this one is a conversation: the conversation Clara is
in becomes the current one on Discord, so the person's reply goes to it, with everything she knows of it.
"""

from __future__ import annotations

from ..events import delivery
from ..events.store import MAX_SAID
from .base import Tool, ToolContext, ToolError, TurnState, fixed

SAY_PER_TURN = 3


def can_say(state: TurnState) -> bool:
    """Offered where it makes sense: not from Discord itself, not in an incognito conversation (it is deleted), not in a
    local-only one (Discord is outside the machine)."""
    return state.client != "discord" and not state.incognito and not state.local_only


def _say(context: ToolContext, text: str, title: str = "") -> str:
    s = context.services
    turn = context.turn
    sent = turn.counts.get("discord_say", 0)
    if sent >= SAY_PER_TURN:
        raise ToolError(f"At most {SAY_PER_TURN} messages per answer.")
    text = str(text).strip()
    if not text:
        raise ToolError("The message is empty.")
    if len(text) > MAX_SAID:
        raise ToolError(f"At most {MAX_SAID:,} characters: shorten it.")
    if not delivery.discord_connected(s):
        raise ToolError("The Discord bot is not connected: tell the person here instead.")
    conversation = s.conversations.get(turn.conversation_id)
    named = " ".join(str(title or "").split())[:100]
    delivery.say(
        s.events,
        named or (conversation.title if conversation and conversation.title else "") or "Clara",
        text,
        conversation=turn.conversation_id,
        source="clara",
    )
    turn.counts["discord_say"] = sent + 1
    return "Sent to Discord's #general. This conversation is now the current one there: the person's reply comes back to it."


def discord_tools() -> list[Tool]:
    return [
        Tool(
            name="discord_say",
            description=(
                "Say something to the person on Discord, in the #general channel, as a message they can answer: this "
                "conversation goes on there, with their reply. Use it when they ask to get something on Discord (a "
                "result, a summary, a question), or when you want them to continue there, on their phone. For a ping "
                "that needs no answer use notify; never for an ordinary answer on the client they write from."
            ),
            function=_say,
            describe=fixed("discord", "write", "discord", lambda a: f"Say on Discord: {str(a.get('text', ''))[:100]}"),
            parameters={
                "text": {"type": "string", "description": "The whole message, as the person reads it."},
                "title": {"type": "string", "description": "Optional short title (default: the conversation's)."},
            },
            required=("text",),
        )
    ]
