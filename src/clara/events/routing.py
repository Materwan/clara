"""Which Discord channel carries which kind of message.

The Discord bot has four channels (found by id in its own settings). Approval requests always go to `#approval` (the
buttons are there) and the questions of scheduled runs to `#general` (their threads are there). Every other message
belongs to a *category*, and you choose its channel in the web site: `#notification`, `#general`, `#alerts`, or off
(Discord stays silent; the web site and the other clients still get it). `#approval` is not a choice, so whatever is
there is waiting for your answer, and alerts cannot be switched off.

The server works out the channel when it sends an event to the bot (`channel`), so the bot holds no table of its own
beyond the fallback it uses with a server that does not say.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .store import Event

CHANNELS = ("general", "notification", "approval", "alerts")  # the bot's four
CHOICES = ("notification", "general", "alerts")  # where a category may go
OFF = "off"
TEST_SOURCE = "discord-test"  # the event of a test message: it names its channel in its payload


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    about: str
    default: str = "notification"
    can_be_off: bool = True


CATEGORIES = (
    Category("reminders", "Reminders", "A reminder you set has come due."),
    Category("tasks", "Tasks", "The reminders of a task on your to-do list."),
    Category("schedules", "Scheduled tasks", "What a scheduled task did, when it ends."),
    Category("hook_results", "Hook results", "A hook's run ended well (its summary)."),
    Category("hook_failures", "Hook failures", "A hook's run failed, or was cut short."),
    Category("hook_notices", "Hook notices", "A hook that only notifies: a mail matched its conditions."),
    Category("mail", "New mail", "The inbox of a connected mailbox received a mail (the sender and the subject)."),
    Category("clara_notices", "Clara's own notices", "Clara notified you herself, with her notify tool."),
    Category("answers", "Answer ready", "A long answer is finished."),
    Category("notifications", "Other notifications", "Scripts and other clients that notify you."),
    Category("updates", "Updates", "Clara is updating, or has updated."),
    Category(
        "alerts",
        "Alerts",
        "Something needs your attention: the disk, a backup, a connection, a hook falling behind.",
        default="alerts",
        can_be_off=False,
    ),
)
BY_ID = {category.id: category for category in CATEGORIES}


def category_of(kind: str, source: str, payload: Mapping) -> str | None:
    """The category of an event, or None when it is not one of the routed kinds (approvals, questions, server states)."""
    if kind == "reminder":
        return "reminders"
    if kind == "task":
        return "tasks"
    if kind == "alert":
        return "alerts"
    if kind == "update":
        return "updates"
    if kind == "schedule":
        if source == "triggers":
            return "hook_results" if payload.get("status") == "ok" else "hook_failures"
        return "schedules"
    if kind == "notification":
        return {"triggers": "hook_notices", "mail": "mail", "clara": "clara_notices", "server": "answers"}.get(
            source, "notifications"
        )
    return None


def check_routes(value: object) -> dict[str, str]:
    """The setting `discord_routes`: the categories sent somewhere other than their default, {category: channel}."""
    if not isinstance(value, dict):
        raise ValueError("an object {category: channel}")
    found: dict[str, str] = {}
    for key, channel in value.items():
        category = BY_ID.get(key)
        if category is None:
            raise ValueError(f"unknown category {key!r}: {', '.join(BY_ID)}")
        if channel != OFF and channel not in CHOICES:
            raise ValueError(f"{key}: a channel is {', '.join(CHOICES)} or {OFF}")
        if channel == OFF and not category.can_be_off:
            raise ValueError(f"{key} cannot be switched off")
        if channel != category.default:
            found[key] = channel
    return dict(sorted(found.items()))


def channel_of(category: str, routes: Mapping[str, str]) -> str:
    return routes.get(category) or BY_ID[category].default


def route(event: Event, routes: Mapping[str, str]) -> str | None:
    """The channel (or `off`) the bot posts this event in; None when the bot decides by itself (it does for approvals
    and questions)."""
    if event.source == TEST_SOURCE:
        named = event.payload.get("discord_test")
        return named if named in CHANNELS else None
    category = category_of(event.kind, event.source, event.payload)
    return None if category is None else channel_of(category, routes)


def carried(routes: Mapping[str, str]) -> dict[str, list[str]]:
    """What each channel receives now, by label: for the page, and for the text of a test message."""
    found: dict[str, list[str]] = {channel: [] for channel in CHANNELS}
    found["general"].append("your conversation")
    found["approval"].append("approval requests, with buttons")
    for category in CATEGORIES:
        channel = channel_of(category.id, routes)
        if channel in found:
            found[channel].append(category.label.lower())
    found["general"].append("the questions of scheduled runs")
    return found
