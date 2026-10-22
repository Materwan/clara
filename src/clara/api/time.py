"""Reminders, tasks and scheduled prompts."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..llm import ProviderError, split_ref
from ..timekeeping import NO_DUE, NO_PARENT, ReminderError, ScheduleError, TaskError
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


# ----------------------------------------------------------------------
# Scheduled prompts
# ----------------------------------------------------------------------
class ScheduleDocument(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, max_length=150_000, description="Leave out to keep the stored text")


class NewSchedule(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(default="", max_length=20_000)
    documents: list[ScheduleDocument] = Field(default_factory=list, max_length=20)
    at: str = Field(min_length=1, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)
    repeat: Literal["", "daily", "weekly", "monthly"] = ""
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    project: int | None = None
    local_only: bool = False
    packs: list[str] = Field(default_factory=lambda: ["web"], max_length=20, description="The tool packs its runs get")
    per_run: bool = Field(default=False, description="A new conversation for each run")
    missed: Literal["skip", "latest", "all"] = "skip"
    overlap: Literal["skip", "replace", "queue"] = "skip"
    model: str = Field(default="", max_length=200, description="'': the default model")
    attempts: int = Field(default=3, description="Tries of a run when the model is busy or unreachable: 1 to 3")
    deliver: Literal["notice", "web", "discord", "both"] = Field(
        default="notice",
        description="Where a run's result goes: notice (a short notification everywhere), web, discord (the whole answer in #general, to answer there) or both",
    )


def _model(model: str) -> str:
    try:
        split_ref(model) if model.strip() else None
    except ProviderError as error:
        raise ApiError(422, str(error)) from None
    return model.strip()


@router.get("/schedules")
def schedules(request: Request) -> dict:
    service = services(request).schedules
    return {"schedules": [service.describe(item) for item in service.listing()]}


@router.post("/schedules", status_code=201)
def add_schedule(body: NewSchedule, request: Request) -> dict:
    service = services(request).schedules
    try:
        schedule = service.create(
            body.name,
            body.prompt,
            body.at,
            documents=[d.model_dump() for d in body.documents],
            timezone=body.timezone,
            repeat=body.repeat,
            weekdays=body.weekdays,
            project_id=body.project,
            local_only=body.local_only,
            packs=body.packs,
            per_run=body.per_run,
            missed=body.missed,
            overlap=body.overlap,
            model=_model(body.model),
            attempts=body.attempts,
            deliver=body.deliver,
        )
    except ScheduleError as error:
        raise ApiError(422, str(error)) from None
    return service.describe(schedule)


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: int, request: Request) -> dict:
    service = services(request).schedules
    schedule = service.find(schedule_id)
    if schedule is None:
        raise ApiError(404, f"No scheduled task {schedule_id}.")
    return {**service.describe(schedule), "history": service.runs(schedule_id)}


class ScheduleChange(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, max_length=100)
    prompt: str | None = Field(default=None, max_length=20_000)
    documents: list[ScheduleDocument] | None = Field(default=None, max_length=20)
    at: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)
    repeat: Literal["", "daily", "weekly", "monthly"] | None = None
    weekdays: list[int] | None = Field(default=None, max_length=7)
    project: int | None = None
    local_only: bool | None = None
    enabled: bool | None = None
    packs: list[str] | None = Field(default=None, max_length=20)
    per_run: bool | None = None
    missed: Literal["skip", "latest", "all"] | None = None
    overlap: Literal["skip", "replace", "queue"] | None = None
    model: str | None = Field(default=None, max_length=200)
    attempts: int | None = None
    in_history: bool | None = None
    deliver: Literal["notice", "web", "discord", "both"] | None = None


@router.patch("/schedules/{schedule_id}")
def change_schedule(schedule_id: int, body: ScheduleChange, request: Request) -> dict:
    service = services(request).schedules
    fields = {key: value for key, value in body.model_dump(exclude_unset=True).items() if value is not None}
    if "project" in fields:
        fields["project_id"] = fields.pop("project") or None
    if "model" in fields:
        fields["model"] = _model(fields["model"])
    try:
        changed = service.update(schedule_id, **fields)
    except ScheduleError as error:
        raise ApiError(404 if "No scheduled" in str(error) else 422, str(error)) from None
    if "in_history" in fields:
        services(request).mirror.group_switched(f"schedule:{schedule_id}", fields["in_history"])
    return service.describe(changed)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule(
    schedule_id: int, request: Request
) -> dict:  # async: starting a run needs the server's event loop
    service = services(request).schedules
    try:
        return service.describe(service.run_now(schedule_id))
    except ScheduleError as error:
        raise ApiError(409, str(error)) from None


@router.post("/schedules/{schedule_id}/close")
async def close_schedule(schedule_id: int, request: Request) -> dict:
    """Close the run that waits for your answer, from what Clara has (she finishes it in the background)."""
    service = services(request).schedules
    if service.find(schedule_id) is None:
        raise ApiError(404, f"No scheduled task {schedule_id}.")
    if not service.close_waiting(schedule_id):
        raise ApiError(409, "No run of it is waiting for an answer (or it is being closed).")
    return service.describe(service.get(schedule_id))


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, request: Request) -> dict:
    if not services(request).schedules.delete(schedule_id):
        raise ApiError(404, f"No scheduled task {schedule_id}.")
    return {"deleted": True}
