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


# What a deleted Discord thread or forum asks Clara to delete too, once you approve it. The model is never offered it.
FORGET_KINDS = ("conversation", "loose", "incognito", "project", "schedule", "hook")


def scope_of(kind: str, target: str = "") -> tuple[str, tuple]:
    """The conversations a deletion of this kind covers, as a condition on the conversations table: a conversation; the
    loose ones (the main forum); the incognito ones (their forum); a project, a schedule or a hook with its runs."""
    if kind == "conversation":
        return "id = ?", (target,)
    if kind == "loose":
        return (
            "project_id IS NULL AND incognito = 0 AND local_only = 0"
            " AND id NOT IN (SELECT conversation_id FROM schedule_runs WHERE conversation_id IS NOT NULL)"
            " AND id NOT IN (SELECT conversation_id FROM trigger_runs WHERE conversation_id IS NOT NULL)"
            " AND id NOT IN (SELECT conversation_id FROM schedules WHERE conversation_id IS NOT NULL)",
            (),
        )
    if kind == "incognito":
        return "incognito = 1", ()
    if kind == "project":
        return "project_id = ?", (int(target),)
    if kind == "schedule":
        return (
            "id IN (SELECT conversation_id FROM schedule_runs WHERE schedule_id = ? AND conversation_id IS NOT NULL)"
            " OR id IN (SELECT conversation_id FROM schedules WHERE id = ? AND conversation_id IS NOT NULL)",
            (int(target), int(target)),
        )
    if kind == "hook":
        return (
            "id IN (SELECT conversation_id FROM trigger_runs WHERE trigger_id = ? AND conversation_id IS NOT NULL)",
            (int(target),),
        )
    raise ValueError(f"Unknown kind {kind!r}.")


def _forget(context: ToolContext, kind: str, target: str = "", summary: str = "") -> str:
    """Delete what a deleted Discord thread or forum stood for, with its conversations (`summary` is the request's words)."""
    s = context.services
    if kind not in FORGET_KINDS:
        raise ToolError(f"Unknown kind {kind!r}.")
    if kind == "project" and s.projects.get(int(target)) is None:
        raise ToolError("No such project any more.")
    try:
        if kind == "schedule":
            s.schedules.get(int(target))
        elif kind == "hook":
            s.triggers.get(int(target))
    except Exception:  # a schedule or a hook that is gone: the service says so with its own error
        raise ToolError(f"No such {kind} any more.") from None
    removed = s.conversations.delete_where(*scope_of(kind, target))
    if kind == "project":
        s.projects.delete(int(target))
    elif kind == "schedule":
        s.schedules.delete(int(target))
    elif kind == "hook":
        s.triggers.delete(int(target))
    return f"Deleted {removed} conversations" + (
        f" and the {kind}." if kind in ("project", "schedule", "hook") else "."
    )


def discord_tools() -> list[Tool]:
    return [
        Tool(
            name="discord_forget",
            description="Delete what a deleted Discord thread or forum stood for, once the person approves it.",
            function=_forget,
            describe=fixed(
                "discord_forget", "destructive", lambda a: str(a.get("target", "")), lambda a: str(a.get("summary", ""))
            ),
            parameters={
                "kind": {
                    "type": "string",
                    "enum": ["conversation", "loose", "incognito", "project", "schedule", "hook"],
                },
                "target": {"type": "string", "description": "The id of the conversation, project, schedule or hook."},
                "summary": {"type": "string"},
            },
            required=("kind",),
        ),
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
        ),
    ]
