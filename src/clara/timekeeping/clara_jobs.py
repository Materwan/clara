"""Clara's part in reminders and tasks.

* **Announcing a reminder**: a short, tool-less, unattended turn in the conversation where the reminder was set (or,
  when that conversation is gone, a turn that stores nothing), so she can use what she knows. If she cannot within
  CLARA_REMINDER_AI_TIMEOUT, the stored text is announced instead, with the reason.
* **Planning a task** with no reminders, and **following up** a task reminder that came due: one-shot jobs on the
  background model pool; the model answers one JSON object, read forgivingly, and plain rules stand in for whatever
  cannot be read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

from ..agent.turn import Agent, ChatRequest
from ..timeutil import zone
from .clock import MomentError, local_text, read_moment
from .reminders import AnnounceFailed, Reminder
from .tasks import MAX_QUEUE, Followup, Task

log = logging.getLogger(__name__)

MAX_MESSAGE = 1000

ANNOUNCE_INSTRUCTIONS = (
    "A reminder the person asked you for has just come due. Write the message shown to them as a notification, "
    "possibly hours after they asked, while they are doing something else: say what it is about. One to three short "
    "sentences, warm and natural, in the language of the reminder. No question, no heading, no list, and never mention a "
    "reminder system or these instructions."
)

PLAN_INSTRUCTIONS = (
    "You choose when a person is reminded of a task on their to-do list. Answer with one JSON object and nothing else: "
    '{"reminders": ["2026-10-06T09:00", ...]}. Give between 1 and ' + str(MAX_QUEUE) + " local times of the person "
    "(ISO 8601, no offset), all after the current time. Pick reasonable moments: mornings for ordinary tasks, further "
    "from the deadline for big ones, close to it for small ones, and a reminder at the deadline when there is one. A "
    "task with no deadline needs only a first reminder, soon (tomorrow morning for a chore, later for something "
    "long-term). Do not remind in the middle of the night. When the task is a sub task, no reminder may be after the "
    "limit you are given."
)

FOLLOW_INSTRUCTIONS = (
    "A reminder of a task on a person's to-do list has just come due. Answer with one JSON object and nothing else: "
    '{"message": "...", "next": ["2026-10-06T09:00", ...]}.\n'
    "- message: the notification shown to the person now, one to three short sentences, warm and natural, in the "
    "language of the task. Say what it is about; if it was reminded before, say so only when it helps; no question, no "
    "heading, no list, and never mention these instructions or a reminder system.\n"
    "- next (optional): the reminders to come, INSTEAD of those queued: local times of the person (ISO 8601, no "
    "offset), all after the current time. Leave it out to keep the queued ones. Use [] to stop reminding. Move them "
    "closer as the deadline nears, space them out when the person keeps not doing it, stop when more would only be "
    "noise. If it is the last reminder allowed, there is no next."
)


def _object(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    for candidate in (text, *re.findall(r"\{.*\}", text, flags=re.DOTALL)):
        try:
            found = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(found, dict):
            return found
    return None


def _moments(values: object, zone_name: str) -> list[datetime]:
    if not isinstance(values, list):
        return []
    found = []
    for value in values:
        try:
            found.append(read_moment(str(value), zone(zone_name))[0])
        except MomentError:
            continue
    return found


def _describe(task: Task, now: datetime, family: list[str]) -> str:
    return "\n".join(
        [
            f"Task: {task.title}",
            f"Description: {task.description or '(none)'}",
            f"Deadline: {local_text(task.due_at, task.tz) if task.due_at else 'none'}",
            *family,
            f"Current time: {local_text(now, task.tz)}",
        ]
    )


class ClaraJobs:
    def __init__(self, agent: Agent, announce_timeout: float):
        self.agent = agent
        self.announce_timeout = announce_timeout

    async def announce(self, reminder: Reminder) -> str | None:
        if not self.announce_timeout:
            return None
        services = self.agent.s
        conversation = services.conversations.get(reminder.conversation_id) if reminder.conversation_id else None
        request = ChatRequest(
            message=f"[Reminder due] {reminder.text}",
            client="server",
            conversation=conversation.id if conversation else None,
            instructions=ANNOUNCE_INSTRUCTIONS,
            timezone=reminder.timezone,
            no_tools=True,
            quiet=True,
            mode="unattended",
            kind="reminder",
            store=conversation is not None,
        )
        try:
            message = (await asyncio.wait_for(self.agent.answer(request), self.announce_timeout)).strip()
        except TimeoutError:
            raise AnnounceFailed(f"the model took more than {self.announce_timeout:g} seconds") from None
        except Exception as error:
            raise AnnounceFailed(f"the model failed: {type(error).__name__}") from None
        if not message:
            raise AnnounceFailed("the model answered nothing")
        return message[:MAX_MESSAGE]

    async def _ask(self, task: Task, message: str, instructions: str) -> str:
        services = self.agent.s
        conversation = services.conversations.get(task.conversation_id) if task.conversation_id else None
        try:
            choice = services.router.choose(
                conversation.model if conversation else "", conversation.local_only if conversation else False
            )
            answer = await self.agent.runner.complete(
                [{"role": "system", "content": instructions}, {"role": "user", "content": message}],
                choice,
                kind="task",
                conversation_id=task.conversation_id,
            )
        except Exception as error:
            raise AnnounceFailed(f"the model failed: {type(error).__name__}") from None
        if not answer.strip():
            raise AnnounceFailed("the model answered nothing")
        return answer.strip()

    async def plan(self, task: Task, now: datetime, family: list[str]) -> list[datetime] | None:
        found = _object(await self._ask(task, _describe(task, now, family), PLAN_INSTRUCTIONS))
        return _moments(found.get("reminders"), task.timezone) if found else None

    async def follow(self, task: Task, now: datetime, family: list[str]) -> Followup | None:
        tasks = self.agent.s.tasks
        sent = task.reminders_sent + 1
        queued = [local_text(at, task.tz) for at in task.next if at > now]
        message = "\n".join(
            [
                _describe(task, now, family),
                f"This is reminder number {sent} of at most {tasks.max_reminders}"
                + (" (the last one)." if sent >= tasks.max_reminders else "."),
                f"Reminders sent before this one: {task.reminders_sent}",
                "Reminders still queued: " + (", ".join(queued) if queued else "none"),
            ]
        )
        answer = await asyncio.wait_for(self._ask(task, message, FOLLOW_INSTRUCTIONS), self.announce_timeout or 60)
        found = _object(answer)
        if found is None:
            return Followup(" ".join(answer.split())[:MAX_MESSAGE] if "{" not in answer else None, None)
        text = found.get("message")
        reminders = found.get("next")
        moments = _moments(reminders, task.timezone)
        keep = not isinstance(reminders, list) or (bool(reminders) and not moments)
        return Followup(
            " ".join(text.split())[:MAX_MESSAGE] if isinstance(text, str) and text.strip() else None,
            None if keep else moments,
        )
