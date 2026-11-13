"""Reminders, tasks and booked calls (automations are in api/automations.py)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..timekeeping import NO_DUE, NO_PARENT, ReminderError, TaskError
from ..timekeeping.deferred import MAX_PENDING as MAX_PENDING_CALLS
from ..timekeeping.tasks import MAX_DESCRIPTION, MAX_TITLE
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1", tags=["time"])


# ----------------------------------------------------------------------
# Reminders
# ----------------------------------------------------------------------
class NewReminder(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    at: str = Field(
        min_length=1, max_length=40, description="Local 2026-10-05T09:00 (in `timezone`), or with an offset"
    )
    repeat: Literal["", "daily", "weekly", "monthly"] = ""
    timezone: str | None = Field(default=None, max_length=64)
    targets: list[str] = Field(default_factory=list, max_length=5)
    conversation: str | None = Field(default=None, pattern=r"^c[0-9a-f]{16}$")


@router.get("/reminders")
def reminders(request: Request) -> dict:
    return {"reminders": [r.describe() for r in services(request).reminders.upcoming()]}


@router.post("/reminders", status_code=201)
def add_reminder(body: NewReminder, request: Request) -> dict:
    try:
        reminder = services(request).reminders.create(
            body.text,
            body.at,
            repeat=body.repeat,
            timezone=body.timezone,
            targets=body.targets,
            conversation_id=body.conversation,
            client=caller(request).client,
        )
    except ReminderError as error:
        raise ApiError(422, str(error)) from None
    return reminder.describe()


@router.delete("/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int, request: Request) -> dict:
    if not services(request).reminders.cancel(reminder_id):
        raise ApiError(404, f"No reminder {reminder_id}.")
    return {"deleted": True}


# ----------------------------------------------------------------------
# Booked calls (a tool call Clara booked in a chat to run later; only a chat books one, so there is no POST)
# ----------------------------------------------------------------------
@router.get("/deferred")
def deferred(request: Request) -> dict:
    service = services(request).deferred
    # what a local-only call holds stays off Discord and scripts, as its notices do
    private = caller(request).token.client in ("discord", "script")
    return {
        "calls": [call.describe(private=private) for call in service.listing()],
        "max_pending": MAX_PENDING_CALLS,
    }


@router.delete("/deferred/{deferred_id}")
def cancel_deferred(deferred_id: int, request: Request) -> dict:
    service = services(request).deferred
    call = service.get(deferred_id)
    if call is None:
        raise ApiError(404, f"No booked call {deferred_id}.")
    if not service.cancel(deferred_id):
        held = f" Answer request #{call.request_id} instead." if call.status == "held" and call.request_id else ""
        raise ApiError(
            409, f"The call {deferred_id} is {call.status}: only one that waits for its date can be cancelled.{held}"
        )
    return {"cancelled": True}


# ----------------------------------------------------------------------
# Tasks
# ----------------------------------------------------------------------
class NewTask(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    due: str | None = Field(default=None, max_length=40)
    reminders: list[str] = Field(default_factory=list, max_length=10)
    timezone: str | None = Field(default=None, max_length=64)
    targets: list[str] = Field(default_factory=list, max_length=5)
    parent_id: int | None = None


@router.get("/tasks")
def tasks(request: Request, status: Literal["open", "done", "all"] = "open") -> dict:
    service = services(request).tasks
    found = service.listing(None if status == "all" else status)
    return {"tasks": [service.describe(task) for task in found], "max_reminders": service.max_reminders}


@router.post("/tasks", status_code=201)
async def add_task(body: NewTask, request: Request) -> dict:
    service = services(request).tasks
    try:
        task = await service.create(
            body.title,
            body.description,
            body.due,
            body.reminders,
            timezone=body.timezone,
            targets=body.targets,
            parent_id=body.parent_id,
        )
    except TaskError as error:
        raise ApiError(422, str(error)) from None
    return service.describe(task)


@router.get("/tasks/{task_id}")
def get_task(task_id: int, request: Request) -> dict:
    service = services(request).tasks
    task = service.find(task_id)
    if task is None:
        raise ApiError(404, f"No task {task_id}.")
    return {**service.describe(task), "children": [service.describe(kid) for kid in service.children(task_id)]}


class TaskChange(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    due: str | None = Field(default=None, max_length=40, description="'' removes the deadline")
    reminders: list[str] | None = Field(default=None, max_length=10)
    targets: list[str] | None = Field(default=None, max_length=5)
    status: Literal["open", "done"] | None = None
    parent_id: int | None = Field(default=None, description="0: a main task")
    before_id: int | None = Field(default=None, description="0: last among its siblings")


@router.patch("/tasks/{task_id}")
async def change_task(task_id: int, body: TaskChange, request: Request) -> dict:
    service = services(request).tasks
    given = body.model_fields_set
    try:
        task = service.get(task_id)
        if given & {"title", "description", "due", "reminders", "targets"} and not (
            body.status == "open" and task.status == "done"
        ):
            service.update(
                task_id,
                title=body.title,
                description=body.description,
                due=body.due if "due" in given else NO_DUE,
                reminders=body.reminders,
                targets=body.targets,
            )
        if body.status == "done":
            service.complete(task_id)
        elif body.status == "open" and task.status == "done":
            await service.reopen(task_id, body.reminders)
        if "parent_id" in given or "before_id" in given:
            service.move(
                task_id,
                (body.parent_id or None) if "parent_id" in given else NO_PARENT,
                (body.before_id or None) if "before_id" in given else NO_PARENT,
            )
        return service.describe(service.get(task_id))
    except TaskError as error:
        raise ApiError(404 if "No task" in str(error) else 422, str(error)) from None


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, request: Request) -> dict:
    if not services(request).tasks.delete(task_id):
        raise ApiError(404, f"No task {task_id}.")
    return {"deleted": True}
