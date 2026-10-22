"""Adds the hooks to a server: the service, the run a trigger makes, and the limits a run's later turns keep."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..agent.turn import ChatRequest, ServerStopping
from ..llm.retry import retryable
from ..timekeeping.schedules import Stopping
from .events import StoredEvent
from .message import INSTRUCTIONS, message_of
from .service import Trigger, Triggers

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)


async def run_trigger(s: Services, trigger: Trigger, run_id: int, events: list[StoredEvent]) -> str:
    """One run: a turn in a conversation of its own (listed under the hook, and in its project if it has one), unattended,
    tainted from its first word, with only the tools the hook allows. Like a scheduled run, it is tried again when the
    model was busy or unreachable and the attempt had stored nothing."""
    title = f"{trigger.name} — {events[0].title}" if len(events) == 1 else f"{trigger.name} — {len(events)} events"
    conversation = s.conversations.create(
        "server", title=title, project_id=trigger.project_id, local_only=trigger.local_only, model=trigger.model
    )
    s.db.execute("UPDATE trigger_runs SET conversation_id = ? WHERE id = ?", (conversation.id, run_id))
    request = ChatRequest(
        message=message_of(trigger.name, trigger.prompt, events),
        client="server",
        conversation=conversation.id,
        instructions=INSTRUCTIONS,
        prefix=f"[Hook “{trigger.name}”, run {run_id}]",
        timezone=s.settings.timezone,
        mode="unattended",
        kind="hook",
        quiet=True,
        model=trigger.model or None,
        tainted=True,
        only_tools=trigger.effective_tools,
    )
    attempt = 1
    while True:
        before = s.conversations.message_count(conversation.id)
        try:
            return await s.agent.answer(request)
        except ServerStopping:
            raise Stopping() from None
        except Exception as error:
            done = s.conversations.message_count(conversation.id) != before
            if done or attempt >= trigger.attempts or not retryable(error):
                raise
            log.warning(
                "hook %s: attempt %d of %d failed (%s), trying again", trigger.id, attempt, trigger.attempts, error
            )
            await s.triggers.pause_before_retry(attempt)
            attempt += 1


def limits_of(s: Services, conversation_id: str) -> dict:
    """What a hook run's conversation keeps for any later turn of Clara's in it (the one that follows an approval): the run
    was started by untrusted content and had only its tools; her next turn there has the same."""
    row = s.db.one("SELECT trigger_id FROM trigger_runs WHERE conversation_id = ?", (conversation_id,))
    trigger = s.triggers.find(row["trigger_id"]) if row else None
    return {"tainted": True, "only_tools": trigger.effective_tools} if trigger is not None else {}


def install(s: Services) -> None:
    s.triggers = Triggers(s.db, s.events, s.projects, lambda: s.toolbox.names)
    s.triggers.runner = lambda trigger, run_id, events: run_trigger(s, trigger, run_id, events)
    s.triggers.alert = lambda trigger, text: s.alerts.raise_(
        f"trigger:{trigger.id}", f"Hook “{trigger.name}” is falling behind", text
    )
    s.agent.restrictions.append(lambda conversation_id: limits_of(s, conversation_id))
