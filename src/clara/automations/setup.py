"""Adds the automations to a server: the runs they make, what is told to the model, and the limits a run's later turns keep."""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import TYPE_CHECKING

from ..agent.reader import ReaderError
from ..agent.turn import ChatRequest, ServerStopping
from ..events import delivery
from ..llm.retry import retryable
from ..timeutil import zone
from .events import StoredEvent
from .message import CLOSING, DIGEST_SHOWN, instructions_of, message_of, shown_of
from .service import Automation, Stopping

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)


async def gists_of(s: Services, automation: Automation, events: list[StoredEvent], conversation_id: str) -> dict | None:
    """The reader's gist of each mail event a run is told of (event id -> Gist). None: the reader is off, or no mail is
    told of, and the events are shown as they are. If it fails, an empty answer: the run is told the mails' text was
    withheld."""
    if not s.reader.on:
        return None
    mails = {str(event.id): event for event in events[:DIGEST_SHOWN] if event.kind == "mail.received"}
    if not mails:
        return None
    try:
        found = await s.reader.gists(
            "emails",
            {
                key: f"From: {event.data.get('from', '')}. Subject: {event.data.get('subject', '')}. "
                f"Preview: {event.data.get('snippet', '')}"
                for key, event in mails.items()
            },
            conversation_id=conversation_id,
            local_only=automation.local_only,
        )
    except ReaderError as error:
        log.warning(
            "automation %s: the reader could not read the mails (%s): their text is withheld", automation.id, error
        )
        return {}
    return {int(key): gist for key, gist in found.items()}


def _packs(s: Services, automation: Automation, only_tools: tuple[str, ...]) -> list[str]:
    """The packs a run's conversation has: the automation's own, and the ones its tools need (the allow-list is the switch, as in
    a chat's "+")."""
    wanted = automation.tools or (only_tools if not automation.packs else ())
    needed = s.packs.starting_with(wanted) if s.packs is not None and wanted else []
    return list(dict.fromkeys([*automation.packs, *needed]))


async def run_automation(
    s: Services, automation: Automation, run_id: int, due: datetime, events: list[StoredEvent]
) -> str:
    """One run: a turn in a conversation (its own for each run, or the automation's shared one), unattended, with the tools its
    trigger gives it, tainted when what started it is. The run remembers its conversation, which is where your answer comes back
    when it asks something. A run is tried again when the model was busy or unreachable and the attempt had stored nothing."""
    zone_name = automation.timezone or s.settings.timezone
    only_tools = s.automations.tools_for(automation, events)
    packs = _packs(s, automation, only_tools)
    if automation.per_run:
        if events:
            title = (
                f"{automation.name} — {events[0].title}"
                if len(events) == 1
                else f"{automation.name} — {len(events)} events"
            )
        else:
            title = f"{automation.name} — {due.astimezone(zone(zone_name)).date().isoformat()}"
        conversation_id = s.conversations.create(
            "server",
            title=title,
            project_id=automation.project_id,
            local_only=automation.local_only,
            model=automation.model,
            packs=packs,
        ).id
    else:
        conversation_id = automation.conversation_id
        if conversation_id is None or s.conversations.get(conversation_id) is None:
            conversation_id = s.conversations.create(
                "server",
                kind="schedule",  # the stored name of the conversation an automation shares between its runs
                title=automation.name,
                project_id=automation.project_id,
                local_only=automation.local_only,
            ).id
            s.db.execute("UPDATE automations SET conversation_id = ? WHERE id = ?", (conversation_id, automation.id))
        s.conversations.update(conversation_id, packs=packs, model=automation.model)
    s.db.execute("UPDATE automation_runs SET conversation_id = ? WHERE id = ?", (conversation_id, run_id))
    if automation.in_history:
        delivery.started(
            s,
            f"Automation: {automation.name}",
            conversation=conversation_id,
            payload={"automation": automation.id, "run": run_id},
        )
    number = int(
        s.db.value(
            "SELECT COUNT(*) FROM automation_runs WHERE automation_id = ? AND status NOT IN ('queued', 'skipped', 'missed', 'expired')",
            (automation.id,),
            1,
        )
    )
    request = ChatRequest(
        message=message_of(automation, events, await gists_of(s, automation, events, conversation_id)),
        client="server",
        conversation=conversation_id,
        instructions=instructions_of(automation, events),
        prefix=f"[Automation “{automation.name}”, run {number}]",
        shown=shown_of(automation, due, events, zone_name),
        timezone=zone_name,
        mode="unattended",
        kind="hook" if events else "schedule",  # the kinds the usage figures have always had
        quiet=True,
        model=automation.model or None,
        tainted=s.automations.is_tainted(events),
        only_tools=only_tools,
    )
    attempt = 1
    while True:
        before = s.conversations.message_count(conversation_id)
        try:
            return await s.agent.answer(request)
        except ServerStopping:
            raise Stopping() from None
        except Exception as error:
            # A turn that used a tool or wrote text stores what it did: only a run that stored nothing is tried again.
            done = s.conversations.message_count(conversation_id) != before
            if done or attempt >= automation.attempts or not retryable(error):
                raise
            log.warning(
                "automation %s: attempt %d of %d failed (%s), trying again",
                automation.id,
                attempt,
                automation.attempts,
                error,
            )
            await s.automations.pause_before_retry(attempt)
            attempt += 1


async def close_run(s: Services, automation: Automation, run) -> None:
    """A run that waits for an answer nobody gave: Clara finishes it from what she has (in its own conversation)."""
    try:
        await s.agent.answer(
            ChatRequest(
                message=CLOSING,
                client="server",
                conversation=run["conversation_id"],
                timezone=automation.timezone or s.settings.timezone,
                mode="unattended",
                kind="schedule",
                quiet=True,
                model=automation.model or None,
            )
        )
    except ServerStopping:
        raise Stopping() from None


def limits_of(s: Services, conversation_id: str) -> dict:
    """What an automation run's conversation keeps for any later turn of Clara's in it (the one that follows an approval): the
    run was started by what started it, and had only its tools; her next turn there has the same."""
    row = s.db.one(
        "SELECT * FROM automation_runs WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (conversation_id,)
    )
    automation = s.automations.find(row["automation_id"]) if row else None
    if automation is None:
        return {}
    events = s.automations.events_of(row)
    found: dict = {}
    if s.automations.is_tainted(events):
        found["tainted"] = True
    if tools := s.automations.tools_for(automation, events):
        found["only_tools"] = tools
    return found


def install(s: Services) -> None:
    from ..ops.upkeep import late_alert

    automations = s.automations
    automations.runner = lambda automation, run_id, due, events: run_automation(s, automation, run_id, due, events)
    automations.closer = lambda automation, run: close_run(s, automation, run)
    automations.check_packs = lambda ids: s.packs.check(ids, "automation") if s.packs is not None else list(ids)
    hours, minutes = s.settings.waiting_remind_at.split(":")
    automations.remind_at = time(int(hours), int(minutes))
    automations.waiting = s.approvals.pending_count
    automations.late = late_alert(s)
    automations.late_after = float(s.settings.late_alert_seconds)
    automations.alert = lambda automation, text: s.alerts.raise_(
        f"automation:{automation.id}", f"Automation “{automation.name}” is falling behind", text
    )
    s.agent.restrictions.append(lambda conversation_id: limits_of(s, conversation_id))
