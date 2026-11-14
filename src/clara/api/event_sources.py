"""Event sources: the address a program calls to tell Clara that something happened, and how you manage them. See
automations/sources.py and DECISIONS.md, « Event sources (2026-11-13) ».

    POST /v1/events/{source_id}                the call of a program: an event, proved by the source's secret
                                               (`Authorization: Bearer <secret>`), NOT by an API token. The only route an
                                               outsider reaches: the middleware lets this one shape of path through, and the
                                               handler checks the secret. 404 for an address nobody was given (nothing is
                                               looked up or counted), 401 with no detail for a wrong secret, 413 above 64 KB,
                                               415 unless application/json, 422 for what is not declared, 429 over a limit
    GET /v1/event-sources                      the sources: name, kind, fields, proof, trusted, address, counts (never a secret)
    POST /v1/event-sources                     {name, fields?, trusted?}: make one; its address and secret come back once
    PATCH /v1/event-sources/{source}           {trusted}: the only thing that changes (a name is the slug of the kind)
    DELETE /v1/event-sources/{source}          revoke it: its record, its secret and its waiting-for-nobody events go
    POST /v1/event-sources/{source}/rotate     a new secret, returned once; the old one stops working
    POST /v1/event-sources/{source}/fire       an event from this machine, proved by your API token: stored and handled like
                                               the public route's (`clara event fire`, the web page's Test);
                                               for a TRUSTED source it takes scope `all`, as its run is not tainted
    GET /v1/event-sources/{source}/events      the latest events of a source

`{source}` is the id, the name or the kind. Making, trusting, rotating and revoking need a token that may answer every request
(scope `all`), as a mode does: a source lets a program start Clara by itself. The model has no tool for any of it.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect

from ..automations.sources import TRUSTED_FIRE, Source, SourceError
from .common import ApiError, caller, refuse_when_stopping, services
from .modes import settled_by_you

router = APIRouter(prefix="/v1", tags=["event sources"])

BY_YOU = "This token may not make or change event sources: they let a program start Clara by itself."
NO_STORE = {"Cache-Control": "no-store"}  # an answer that carries a secret is not kept anywhere


def _failed(error: SourceError) -> ApiError:
    headers: dict[str, str] = {}
    if error.retry_after:
        headers["Retry-After"] = str(error.retry_after)
    if error.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return ApiError(error.status, str(error), headers=headers or None)


def _source(request: Request, ref: str) -> Source:
    found = services(request).event_sources.find(ref)
    if found is None:
        raise ApiError(404, f"No event source {ref[:60]!r}.")
    return found


async def _store(request: Request, source: Source) -> JSONResponse:
    """Store one event (from the public route or from `fire`) and answer: 202 and its id, or 200 when it was stored before."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise ApiError(415, "Send the event as application/json.")
    try:
        body = await request.body()
    except ClientDisconnect:  # the body went past its limit (the middleware has answered 413) or the sender left
        raise ApiError(413, "The body is too big, or it was cut short.") from None
    try:
        found, duplicate = services(request).event_sources.accept(source, body)
    except SourceError as error:
        raise _failed(error) from None
    return JSONResponse({"duplicate": True} if duplicate else {"id": found}, 200 if duplicate else 202)


# ----------------------------------------------------------------------
# The call of a program
# ----------------------------------------------------------------------
@router.post("/events/{source_id}", status_code=202)
async def receive(source_id: str, request: Request) -> JSONResponse:
    """An event from a source. A body is `{"key"?, "title"?, "occurred_at"?, "actor"?, "data": {the declared fields}}`; the answer
    is 202 `{"id"}`, or 200 `{"duplicate": true}` for an event with the same key."""
    sources = services(request).event_sources
    source = sources.by_id.get(source_id)
    if source is None:
        raise ApiError(404, "Not found.")  # the whole cost of an address nobody was given: no lookup, no count, no log
    try:
        sources.admit(source, request.headers.get("authorization"))
    except SourceError as error:
        raise _failed(error) from None
    refuse_when_stopping(request)
    return await _store(request, source)


# ----------------------------------------------------------------------
# What you manage
# ----------------------------------------------------------------------
@router.get("/event-sources")
def listing(request: Request) -> dict:
    return {"sources": services(request).event_sources.listing()}


class NewSource(BaseModel):
    name: str = Field(
        min_length=1, max_length=60, description="Its kind is custom.<slug of the name>; it cannot be renamed"
    )
    fields: dict = Field(
        default_factory=dict, description='What an event carries: {"state": "text", "open": "bool"}, 12 at most'
    )
    trusted: bool = Field(
        default=False, description="A run its events start is not tainted (a program whose output you write)"
    )


@router.post("/event-sources", status_code=201)
async def make(body: NewSource, request: Request) -> JSONResponse:
    settled_by_you(request, BY_YOU)
    registry = services(request).event_sources
    try:
        source, secret = registry.create(body.name, body.fields, trusted=body.trusted, by=caller(request).name)
    except SourceError as error:
        raise _failed(error) from None
    return JSONResponse(
        {"source": registry.describe(source), "address": source.address, "secret": secret}, 201, headers=NO_STORE
    )


class SourceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trusted: bool


@router.patch("/event-sources/{source}")
async def change(source: str, body: SourceChange, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    registry = services(request).event_sources
    found = _source(request, source)
    try:
        return registry.describe(registry.set_trusted(found, body.trusted, caller(request).name))
    except SourceError as error:
        raise _failed(error) from None


@router.delete("/event-sources/{source}")
async def revoke(source: str, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    services(request).event_sources.revoke(_source(request, source), caller(request).name)
    return {"revoked": True}


@router.post("/event-sources/{source}/rotate")
async def rotate(source: str, request: Request) -> JSONResponse:
    settled_by_you(request, BY_YOU)
    registry = services(request).event_sources
    found = _source(request, source)
    secret = registry.rotate(found, caller(request).name)
    return JSONResponse(
        {"source": registry.describe(found), "address": found.address, "secret": secret}, headers=NO_STORE
    )


@router.post("/event-sources/{source}/fire", status_code=202)
async def fire(source: str, request: Request) -> JSONResponse:
    """An event from this machine: your API token is the proof, the body is the public route's, and it is stored and handled the
    same way (the source's own flag decides whether the run is tainted). Any token may fire an event of a source that is not
    trusted; one of a trusted source takes a token that may answer every request (scope `all`)."""
    found = _source(request, source)
    if found.trusted:  # its run is not tainted: only a token that may answer every request starts one this way
        settled_by_you(request, TRUSTED_FIRE)
    refuse_when_stopping(request)
    return await _store(request, found)


@router.get("/event-sources/{source}/events")
def latest(source: str, request: Request, limit: int = Query(20, ge=1, le=200)) -> dict:
    found = _source(request, source)
    return {"events": [event.describe() for event in services(request).automations.recent_events(limit, found.kind)]}
