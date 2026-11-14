"""Automations: do something when a trigger holds (a mail arrives, a push happens, a time comes, a window of time is on). Only
clients reach these routes; the model has tools for them only in a chat with the Automations pack on, and each change asks you
first (packs/automations.py).

    GET /v1/automations                  the automations, and the kinds of event there are
    POST /v1/automations                 make one       PATCH /v1/automations/{id}    change it (`enabled`: pause, resume)
    GET /v1/automations/{id}             one, with its latest runs       DELETE /v1/automations/{id}
    POST /v1/automations/{id}/run        run it now        POST /v1/automations/{id}/close   close the run that waits for you
    POST /v1/automations/{id}/test       whether its trigger holds for an event (a stored one, a sample, the latest) or at a
                                         moment, branch by branch and why; nothing is done
    GET /v1/automations/{id}/runs        its runs          GET /v1/trigger-events    the events stored

The memory of an automation is the scope `automation:<id>` of /v1/memory; its mode is PUT /v1/automations/{id}/mode.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..automations import AutomationError, SourceEvent
from ..automations.service import MAX_ATTEMPTS
from ..llm import ProviderError, split_ref
from ..memory.retention import KEEP_FIELDS
from ..timekeeping.clock import MomentError, read_moment
from .common import ApiError, services
from .retention import KeepLimits

router = APIRouter(prefix="/v1", tags=["automations"])

CatchUp = Literal["all", "latest", "digest", "skip"]
Overlap = Literal["skip", "replace", "queue"]


def _problem(error: AutomationError) -> ApiError:
    return ApiError(404 if str(error).startswith("No automation") else 422, str(error))


def _model(model: str) -> str:
    try:
        split_ref(model) if model.strip() else None
    except ProviderError as error:
        raise ApiError(422, str(error)) from None
    return model.strip()


class AutomationDocument(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, max_length=150_000, description="Leave out to keep the stored text")


class NewAutomation(KeepLimits):
    name: str = Field(min_length=1, max_length=100)
    when: dict | list = Field(
        description='The trigger: a tree of {"type": "or"|"and", "of": [...]} over {"type": "event", "kind", "filter"}, '
        '{"type": "cron", "expr", "as"?}, {"type": "once", "at"}, {"type": "window", "from", "to", "days"?}. A single node is '
        "a tree of one branch."
    )
    timezone: str | None = Field(
        default=None, max_length=64, description="The clock of its cron times, dates and windows"
    )
    action: Literal["run", "notify"] = "run"
    prompt: str = Field(default="", max_length=20_000)
    documents: list[AutomationDocument] = Field(default_factory=list, max_length=20)
    packs: list[str] = Field(default_factory=list, max_length=20, description="The tool packs its runs get")
    tools: list[str] = Field(default_factory=list, max_length=40, description="The only tools its runs may use")
    project: int | Literal["none"] = "none"
    model: str = Field(default="", max_length=200, description="'': the default model")
    local_only: bool = False
    per_run: bool | None = Field(default=None, description="A new conversation for each run (null: by its trigger)")
    catch_up: CatchUp | None = Field(
        default=None, description="Runs missed or events found late (null: by its trigger)"
    )
    overlap: Overlap | None = Field(
        default=None, description="A run that comes due while the last goes on (null: by its trigger)"
    )
    max_per_hour: int = Field(default=20, ge=1, le=600)
    max_age_hours: int = Field(default=0, ge=0, le=8760)
    include_self: bool = False
    rerun_interrupted: bool | None = Field(default=None, description="Repeat a run a stop cut (null: by its trigger)")
    attempts: int = Field(default=MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS)
    announce: bool = True
    in_history: bool = Field(default=False, description="Show in history: and on Discord a forum thread for each run")
    enabled: bool = True


class AutomationChange(KeepLimits):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=100)
    when: dict | list | None = None
    timezone: str | None = Field(default=None, max_length=64)
    action: Literal["run", "notify"] | None = None
    prompt: str | None = Field(default=None, max_length=20_000)
    documents: list[AutomationDocument] | None = Field(default=None, max_length=20)
    packs: list[str] | None = Field(default=None, max_length=20)
    tools: list[str] | None = Field(default=None, max_length=40)
    project: int | Literal["none"] | None = None
    model: str | None = Field(default=None, max_length=200)
    local_only: bool | None = None
    per_run: bool | None = None
    catch_up: CatchUp | None = None
    overlap: Overlap | None = None
    max_per_hour: int | None = Field(default=None, ge=1, le=600)
    max_age_hours: int | None = Field(default=None, ge=0, le=8760)
    include_self: bool | None = None
    rerun_interrupted: bool | None = None
    attempts: int | None = Field(default=None, ge=1, le=MAX_ATTEMPTS)
    announce: bool | None = None
    in_history: bool | None = None
    enabled: bool | None = None


@router.get("/automations")
def automations(request: Request) -> dict:
    service = services(request).automations
    return {"automations": [service.describe(item) for item in service.listing()], "kinds": service.kinds_listing()}


@router.post("/automations", status_code=201)
def add_automation(body: NewAutomation, request: Request) -> dict:
    service = services(request).automations
    fields = body.model_dump(exclude={"name", "when", "project"})
    fields["model"] = _model(body.model)
    try:
        return service.describe(service.create(body.name, body.when, project=body.project, **fields))
    except AutomationError as error:
        raise _problem(error) from None


@router.get("/automations/{automation_id}")
def get_automation(automation_id: int, request: Request) -> dict:
    service = services(request).automations
    found = service.find(automation_id)
    if found is None:
        raise ApiError(404, f"No automation {automation_id}.")
    return {**service.describe(found), "history": service.runs(automation_id)}


@router.patch("/automations/{automation_id}")
async def change_automation(
    automation_id: int, body: AutomationChange, request: Request
) -> dict:  # async: it wakes the server's loop
    s = services(request)
    fields = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None or key in KEEP_FIELDS  # null: follow the global limits again
    }
    if "model" in fields:
        fields["model"] = _model(fields["model"])
    before = s.automations.find(automation_id)
    try:
        changed = s.automations.update(automation_id, **fields)
    except AutomationError as error:
        raise _problem(error) from None
    if before is not None and changed.in_history != before.in_history:
        s.mirror.group_switched(f"automation:{automation_id}", changed.in_history)
    return s.automations.describe(changed)


@router.delete("/automations/{automation_id}")
async def delete_automation(
    automation_id: int, request: Request
) -> dict:  # async: it cancels a task of the server's loop
    if not services(request).automations.delete(automation_id):
        raise ApiError(404, f"No automation {automation_id}.")
    return {"deleted": True}


@router.post("/automations/{automation_id}/run")
async def run_automation(
    automation_id: int, request: Request
) -> dict:  # async: starting a run needs the server's event loop
    service = services(request).automations
    try:
        return service.describe(service.run_now(automation_id))
    except AutomationError as error:
        raise ApiError(404 if str(error).startswith("No automation") else 409, str(error)) from None


@router.post("/automations/{automation_id}/close")
async def close_automation(automation_id: int, request: Request) -> dict:
    """Close the run that waits for your answer, from what Clara has (she finishes it in the background)."""
    service = services(request).automations
    if service.find(automation_id) is None:
        raise ApiError(404, f"No automation {automation_id}.")
    if not service.close_waiting(automation_id):
        raise ApiError(409, "No run of it is waiting for an answer (or it is being closed).")
    return service.describe(service.get(automation_id))


class Probe(BaseModel):
    event: int | dict | None = Field(
        default=None,
        description="A stored event's id, or a sample {kind?, data: {...}, title?, actor?, self_caused?, occurred_at?}; "
        "left out: the latest stored event of a kind the trigger waits for (or, if it waits for none, the moment `at`)",
    )
    at: str | None = Field(
        default=None,
        max_length=40,
        description="The moment to judge at, local to the automation (2026-11-13T19:30) or with an offset: the event's "
        "time is used when left out; with no event, an instant at this moment",
    )


@router.post("/automations/{automation_id}/test")
def test_automation(automation_id: int, body: Probe, request: Request) -> dict:
    service = services(request).automations
    try:
        found = service.get(automation_id)
    except AutomationError as error:
        raise _problem(error) from None
    at = None
    if body.at:
        try:
            at = read_moment(body.at, service.tz(found))[0]
        except MomentError as error:
            raise ApiError(422, str(error)) from None
    event: SourceEvent | None
    if isinstance(body.event, dict):
        sample = body.event
        kind = str(sample.get("kind") or (found.kinds[0] if found.kinds else ""))
        if service.kind(kind) is None:
            raise ApiError(422, f"{kind!r} is not a kind of event.")
        event = SourceEvent(
            source=kind
            if kind.startswith("custom.")
            else kind.split(".")[0],  # a source's events are stored with its kind as source
            kind=kind,
            key="sample",
            occurred_at=str(sample.get("occurred_at", "")),
            actor=str(sample.get("actor", "")),
            self_caused=bool(sample.get("self_caused")),
            title=str(sample.get("title", "A sample event")),
            data=dict(sample.get("data") or {}),
        )
    elif isinstance(body.event, int):
        event = service.event(body.event)
        if event is None:
            raise ApiError(404, f"No event {body.event}.")
    elif at is not None or not found.kinds:
        event = None  # an instant: what would happen at that moment
    else:
        latest = [item for kind in found.kinds for item in service.recent_events(1, kind)]
        if not latest:
            raise ApiError(404, "No event of its kinds was stored yet: give a sample event.")
        event = max(latest, key=lambda item: item.id)
    return {"event": getattr(event, "id", None), **service.test(found, event, at)}


@router.get("/automations/{automation_id}/runs")
def automation_runs(automation_id: int, request: Request, limit: int = Query(20, ge=1, le=200)) -> dict:
    service = services(request).automations
    if service.find(automation_id) is None:
        raise ApiError(404, f"No automation {automation_id}.")
    return {"runs": service.runs(automation_id, limit)}


@router.get("/trigger-events")
def trigger_events(
    request: Request, limit: int = Query(20, ge=1, le=200), kind: str = Query("", max_length=50)
) -> dict:
    return {"events": [event.describe() for event in services(request).automations.recent_events(limit, kind)]}
