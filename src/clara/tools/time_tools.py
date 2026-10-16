"""Time tools: reminders, notifications, tasks."""

from __future__ import annotations

from typing import Any

from ..events import TARGETS
from ..timekeeping import DONE, NO_DUE, OPEN, STATUSES, ReminderError, TaskError
from ..timeutil import REPEATS
from .base import Tool, ToolContext, ToolError, fixed, texts, whole_number

NOTIFY_PER_TURN = 3
TARGETS_HELP = f"Clients to show it on ({', '.join(TARGETS)}); omit for every client."
TASK_DESCRIPTION_HELP = "What to do beyond the title: where, with whom, the steps. Omit only when the title says it all."


def _targets(value: Any) -> list[str]:
    return [target.lower() for target in texts(value, "targets must be a list of client names")]


def _remind(context: ToolContext, text: str, when: str, repeat: str = "", targets: Any = None) -> str:
    try:
        reminder = context.services.reminders.create(
            str(text), str(when), repeat=str(repeat or ""), timezone=context.turn.timezone, targets=_targets(targets),
            conversation_id=None if context.turn.incognito else context.turn.conversation_id, client=context.turn.client,
        )
    except ReminderError as error:
        raise ToolError(str(error)) from None
    again = f", then {reminder.repeat}" if reminder.repeat else ""
    return f"Reminder {reminder.id} set: {reminder.line()}{again}."


def _list_reminders(context: ToolContext) -> str:
    found = [reminder for reminder in context.services.reminders.upcoming() if not reminder.system]
    return "\n".join(reminder.line() for reminder in found) if found else "No reminder set."


def _cancel_reminder(context: ToolContext, reminder_id: Any) -> str:
    number = whole_number(reminder_id, "reminder_id must be the number shown in brackets.")
    reminder = context.services.reminders.get(number)
    if reminder is None or reminder.system:
        return "No such reminder."
    return "Cancelled." if context.services.reminders.cancel(number) else "No such reminder."


def _notify(context: ToolContext, text: str, title: str = "", targets: Any = None) -> str:
    sent = context.turn.counts.get("notify", 0)
    if sent >= NOTIFY_PER_TURN:
        raise ToolError(f"At most {NOTIFY_PER_TURN} notifications per answer.")
    text = str(text).strip()
    if not text:
        raise ToolError("The notification is empty.")
    try:
        event = context.services.events.publish(
            "notification", str(title or "Clara")[:100], text[:2000], targets=_targets(targets), source="clara",
            conversation_id=context.turn.conversation_id,
        )
    except ValueError as error:
        raise ToolError(str(error)) from None
    context.turn.counts["notify"] = sent + 1
    where = ", ".join(event.targets) or "every client"
    return f"Notification {event.id} sent to {where}."


def _task_number(value: Any) -> int:
    return whole_number(value, "task_id must be the number shown in brackets.")


async def _add_task(context: ToolContext, title: str, description: str = "", due: str = "", reminders: Any = None,
                    targets: Any = None, parent_id: Any = None) -> str:
    tasks = context.services.tasks
    try:
        task = await tasks.create(
            str(title), str(description or ""), str(due) if due else None, texts(reminders),
            timezone=context.turn.timezone, targets=_targets(targets),
            parent_id=None if parent_id in (None, "", 0) else _task_number(parent_id),
            conversation_id=None if context.turn.incognito else context.turn.conversation_id,
        )
    except TaskError as error:
        raise ToolError(str(error)) from None
    return f"{'Sub task' if task.parent_id else 'Task'} added.\n{tasks.detail(task)}"


def _list_tasks(context: ToolContext, task_id: Any = None, status: str = OPEN) -> str:
    tasks = context.services.tasks
    try:
        if task_id not in (None, ""):
            return tasks.detail(tasks.get(_task_number(task_id)))
        found = tasks.listing(None if status == "all" else (status or OPEN))
    except TaskError as error:
        raise ToolError(str(error)) from None
    if not found:
        return "No task." if status == "all" else f"No {status or OPEN} task."
    return "\n".join(tasks.line(task) for task in found)


async def _update_task(context: ToolContext, task_id: Any, title: Any = None, description: Any = None, due: Any = NO_DUE,
                       reminders: Any = None, status: Any = None, parent_id: Any = None) -> str:
    tasks = context.services.tasks
    number = _task_number(task_id)
    if status not in (None, "", *STATUSES):
        raise ToolError("status must be open or done.")
    try:
        current = tasks.get(number)
        times = None if reminders is None else texts(reminders)
        if status == DONE and times:
            raise ToolError("A task that is done is not reminded: reopen it to set reminders.")
        settling = status in STATUSES and (status == OPEN) != (current.status == OPEN)
        edits = any(value is not None for value in (title, description)) or (times is not None and not settling)
        if edits or due is not NO_DUE:
            tasks.update(number, title=None if title is None else str(title),
                         description=None if description is None else str(description),
                         due=due if due is NO_DUE or not due else str(due), reminders=None if settling else times,
                         timezone=None)
        if settling and status == DONE:
            tasks.complete(number)
        elif settling:
            await tasks.reopen(number, times)
        if parent_id not in (None, ""):
            under = whole_number(parent_id, "parent_id must be the number shown in brackets, or 0.")
            tasks.move(number, under or None)
        return f"Task updated.\n{tasks.detail(tasks.get(number))}"
    except TaskError as error:
        raise ToolError(str(error)) from None


def _delete_task(context: ToolContext, task_id: Any) -> str:
    return "Deleted." if context.services.tasks.delete(_task_number(task_id)) else "No such task."


def time_tools() -> list[Tool]:
    targets = {"type": "array", "items": {"type": "string", "enum": list(TARGETS)}, "description": TARGETS_HELP}
    local = "Local date and time, ISO 8601 without offset: 2026-10-05T09:00."
    return [
        Tool(
            name="remind",
            description="Set a reminder for the person: at that time it is shown to them as a notification.",
            function=_remind,
            describe=fixed("memory", "write", "reminders", lambda a: f"Reminder at {a.get('when')}: {str(a.get('text', ''))[:100]}"),
            parameters={
                "text": {"type": "string", "description": "What to announce, written to make sense on its own."},
                "when": {"type": "string", "description": local},
                "repeat": {"type": "string", "enum": list(REPEATS), "description": "Optional."},
                "targets": targets,
            },
            required=("text", "when"),
        ),
        Tool(
            name="list_reminders",
            description="The person's reminders that have not fired yet, with their ids.",
            function=_list_reminders,
            describe=fixed("read", "read", "reminders", "List reminders"),
            parallel=True,
        ),
        Tool(
            name="cancel_reminder",
            description="Cancel a reminder, by id.",
            function=_cancel_reminder,
            describe=fixed("memory", "write", "reminders", lambda a: f"Cancel reminder {a.get('reminder_id')}"),
            parameters={"reminder_id": {"type": "integer"}},
            required=("reminder_id",),
        ),
        Tool(
            name="notify",
            description=(
                "Notify the person now: it pops up on their clients even outside this conversation (Discord's #notification channel too). "
                "Only when they asked to be told, or when long work of yours is done; never for an ordinary answer."
            ),
            function=_notify,
            describe=fixed("memory", "write", "notify", lambda a: f"Notify: {str(a.get('text', ''))[:100]}"),
            parameters={
                "text": {"type": "string", "description": "One or two sentences."},
                "title": {"type": "string", "description": "Optional short title."},
                "targets": targets,
            },
            required=("text",),
        ),
        Tool(
            name="add_task",
            description=(
                "Add a task to the person's to-do list, with reminders: the times they gave, else sensible ones you "
                "choose (say when). With parent_id it is a sub task."
            ),
            function=_add_task,
            describe=fixed("memory", "write", "tasks", lambda a: f"Add task: {str(a.get('title', ''))[:100]}"),
            parameters={
                "title": {"type": "string", "description": "Short name of the task."},
                "description": {"type": "string", "description": TASK_DESCRIPTION_HELP},
                "due": {"type": "string", "description": "Deadline. " + local},
                "reminders": {"type": "array", "items": {"type": "string"}, "description": local},
                "targets": targets,
                "parent_id": {"type": "integer", "description": "The task it is part of."},
            },
            required=("title",),
        ),
        Tool(
            name="list_tasks",
            description="The person's tasks with reminders sent and the next one; with task_id, one task in full.",
            function=_list_tasks,
            describe=fixed("read", "read", "tasks", "List tasks"),
            parameters={"task_id": {"type": "integer"},
                        "status": {"type": "string", "enum": [*STATUSES, "all"], "description": "Default: open."}},
            parallel=True,
        ),
        Tool(
            name="update_task",
            description=(
                "Change a task; only what you give changes. `reminders` replaces those to come ([] stops them), `due` "
                "the deadline (empty: none), status done ends the reminders, parent_id moves it (0: a main task)."
            ),
            function=_update_task,
            describe=fixed("memory", "write", "tasks", lambda a: f"Update task {a.get('task_id')}"),
            parameters={
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string", "description": TASK_DESCRIPTION_HELP + " Empty: remove it."},
                "due": {"type": "string", "description": local},
                "reminders": {"type": "array", "items": {"type": "string"}, "description": local},
                "status": {"type": "string", "enum": list(STATUSES)},
                "parent_id": {"type": "integer", "description": "Make it a sub task of that task; 0: a main task."},
            },
            required=("task_id",),
        ),
        Tool(
            name="delete_task",
            description="Delete a task and its sub tasks for good (to finish one, set its status to done).",
            function=_delete_task,
            describe=fixed("memory", "write", "tasks", lambda a: f"Delete task {a.get('task_id')}"),
            parameters={"task_id": {"type": "integer"}},
            required=("task_id",),
        ),
    ]
