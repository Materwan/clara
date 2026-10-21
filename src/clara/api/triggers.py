"""Hooks: when something happens outside (a mail arrives), Clara does what a trigger says. Only clients reach these routes: the
model has no tool to make, change or read a trigger, so a message cannot create one.

    GET /v1/triggers                   the triggers, and the kinds of event there are
    POST /v1/triggers                  make one      PATCH /v1/triggers/{id}      change it (or turn it off)
    GET /v1/triggers/{id}              one, with its latest runs     DELETE /v1/triggers/{id}
    POST /v1/triggers/{id}/test        what would happen for an event (a stored one, a sample, the latest), done nothing
    GET /v1/triggers/{id}/runs         its runs        GET /v1/trigger-events   the events stored
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..triggers import KINDS, SourceEvent, TriggerError
from ..triggers.service import CATCH_UPS, MAX_ATTEMPTS
from .common import ApiError, services

router = APIRouter(prefix="/v1", tags=["triggers"])

CatchUp = Literal["all", "latest", "digest", "skip"]
assert CATCH_UPS == ("all", "latest", "digest", "skip")


def _service(request: Request):
    found = services(request).triggers
    if found is None:
        raise ApiError(404, "Hooks are not available.")
    return found


def _problem(error: TriggerError) -> ApiError:
    return ApiError(404 if str(error).startswith("No hook") else 422, str(error))


class NewTrigger(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    on: str = Field(min_length=1, max_length=50, description="The kind of event: mail.received")
    filter: dict | None = None
    action: Literal["run", "notify"] = "run"
    prompt: str = Field(default="", max_length=20_000)
    tools: list[str] | None = Field(default=None, max_length=40)
    project: int | Literal["auto", "none"] = "auto"
    model: str = Field(default="", max_length=200)
    local_only: bool = False
    catch_up: CatchUp = "all"
    max_per_hour: int = Field(default=20, ge=1, le=600)
    max_age_hours: int = Field(default=0, ge=0, le=8760)
    include_self: bool = False
    rerun_interrupted: bool = False
    attempts: int = Field(default=MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS)
    announce: bool = True
    enabled: bool = True


class TriggerChange(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    filter: dict | None = None
    action: Literal["run", "notify"] | None = None
    prompt: str | None = Field(default=None, max_length=20_000)
    tools: list[str] | None = Field(default=None, max_length=40)
    project: int | Literal["none"] | None = None
    model: str | None = Field(default=None, max_length=200)
    local_only: bool | None = None
    catch_up: CatchUp | None = None
    max_per_hour: int | None = Field(default=None, ge=1, le=600)
    max_age_hours: int | None = Field(default=None, ge=0, le=8760)
    include_self: bool | None = None
    rerun_interrupted: bool | None = None
    attempts: int | None = Field(default=None, ge=1, le=MAX_ATTEMPTS)
    announce: bool | None = None
    enabled: bool | None = None


@router.get("/triggers")
def triggers(request: Request) -> dict:
    service = _service(request)
    return {"triggers": [service.describe(item) for item in service.listing()],
            "kinds": [{"kind": kind.kind, "label": kind.label, "fields": kind.fields, "read_tools": list(kind.read_tools)}
                      for kind in KINDS.values()]}


@router.post("/triggers", status_code=201)
def add_trigger(body: NewTrigger, request: Request) -> dict:
    service = _service(request)
    fields = body.model_dump(exclude={"name", "on", "project"})
    try:
        return service.describe(service.create(body.name, body.on, project=body.project, **fields))
    except TriggerError as error:
        raise _problem(error) from None


@router.get("/triggers/{trigger_id}")
def get_trigger(trigger_id: int, request: Request) -> dict:
    service = _service(request)
    found = service.find(trigger_id)
    if found is None:
        raise ApiError(404, f"No hook {trigger_id}.")
    return {**service.describe(found), "history": service.runs(trigger_id)}


@router.patch("/triggers/{trigger_id}")
async def change_trigger(trigger_id: int, body: TriggerChange, request: Request) -> dict:  # async: it wakes the server's loop
    service = _service(request)
    try:
        return service.describe(service.update(trigger_id, **body.model_dump(exclude_unset=True)))
    except TriggerError as error:
        raise _problem(error) from None


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(trigger_id: int, request: Request) -> dict:  # async: it cancels a task of the server's loop
    if not _service(request).delete(trigger_id):
        raise ApiError(404, f"No hook {trigger_id}.")
    return {"deleted": True}


class Probe(BaseModel):
    event: int | dict | None = Field(default=None, description="A stored event's id, or a sample {data: {...}, self_caused: false}; "
                                                              "left out: the latest stored event of the trigger's kind")


@router.post("/triggers/{trigger_id}/test")
def test_trigger(trigger_id: int, body: Probe, request: Request) -> dict:
    service = _service(request)
    try:
        found = service.get(trigger_id)
    except TriggerError as error:
        raise _problem(error) from None
    if isinstance(body.event, dict):
        sample = body.event
        event = SourceEvent(source=found.on.split(".")[0], kind=found.on, key="sample", occurred_at="", actor=str(sample.get("actor", "")),
                            self_caused=bool(sample.get("self_caused")), title=str(sample.get("title", "A sample event")),
                            data=dict(sample.get("data") or {}))
    elif isinstance(body.event, int):
        event = service.event(body.event)
        if event is None:
            raise ApiError(404, f"No event {body.event}.")
    else:
        latest = service.recent_events(1, found.on)
        if not latest:
            raise ApiError(404, "No event of this kind was stored yet: give a sample event.")
        event = latest[0]
    return {"event": event.id if hasattr(event, "id") else None, **service.preview(found, event)}


@router.get("/triggers/{trigger_id}/runs")
def trigger_runs(trigger_id: int, request: Request, limit: int = Query(20, ge=1, le=200)) -> dict:
    service = _service(request)
    if service.find(trigger_id) is None:
        raise ApiError(404, f"No hook {trigger_id}.")
    return {"runs": service.runs(trigger_id, limit)}


@router.get("/trigger-events")
def trigger_events(request: Request, limit: int = Query(20, ge=1, le=200), kind: str = Query("", max_length=50)) -> dict:
    return {"events": [event.describe() for event in _service(request).recent_events(limit, kind)]}
