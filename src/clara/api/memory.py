"""Memory: the facts Clara keeps, in the global memory and in the memory of each project, schedule and hook. You can list,
add, edit, confirm, pin and delete them."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..memory.facts import MAX_CATEGORY, MAX_FACT_LENGTH, FactError
from ..timekeeping.clock import MomentError, read_moment, zone_of
from ..timeutil import iso
from .common import ApiError, services

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def _expiry(request: Request, text: str | None) -> str | None:
    if not text:
        return None
    try:
        _, tz = zone_of(None, services(request).settings.timezone)
        return iso(read_moment(text, tz)[0])
    except MomentError as error:
        raise ApiError(422, f"expires_at: {error}") from None


def _scope(request: Request, scope: str) -> str:
    """The scope, which must be the global memory (empty) or a project, schedule or hook that exists, as it is stored."""
    try:
        return services(request).facts.check_scope(scope)
    except FactError as error:
        raise ApiError(404 if str(error).startswith("No ") else 422, str(error)) from None


@router.get("/facts")
def listing(
    request: Request,
    state: Literal["live", "unconfirmed", "expired", "superseded", "all"] = "live",
    q: str = Query("", max_length=200),
    scope: str = Query("", max_length=40, description="'' (global), project:<id>, schedule:<id> or hook:<id>"),
) -> dict:
    facts = services(request).facts
    scope = _scope(request, scope)
    found = (
        facts.search(q, 200, include_old=state != "live", scopes=(scope,)) if q.strip() else facts.listing(state, scope)
    )
    return {
        "facts": [fact.describe() for fact in found],
        "live": facts.count_live(scope),
        "waiting": len(facts.listing("unconfirmed", scope)),
    }


@router.get("/scopes")
def scopes(request: Request) -> dict:
    """The projects, schedules and hooks that keep a memory: its live facts, how many wait for your confirmation, and every
    fact it keeps (`total`), which is what deleting it removes."""
    return {"scopes": services(request).facts.scopes()}


class NewFact(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_FACT_LENGTH)
    category: str = Field(default="", max_length=MAX_CATEGORY)
    pinned: bool = False
    expires_at: str | None = Field(default=None, max_length=40)
    replaces: int | None = None
    scope: str = Field(default="", max_length=40, description="'' (global), project:<id>, schedule:<id> or hook:<id>")


@router.post("/facts", status_code=201)
def add(body: NewFact, request: Request) -> dict:
    scope = _scope(request, body.scope)
    try:
        fact, new = services(request).facts.add(
            body.text,
            "you",
            category=body.category,
            pinned=body.pinned,
            expires_at=_expiry(request, body.expires_at),
            replaces=body.replaces,
            scope=scope,
        )
    except FactError as error:
        raise ApiError(422, str(error)) from None
    return {**fact.describe(), "new": new}


class FactChange(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_FACT_LENGTH)
    category: str | None = Field(default=None, max_length=MAX_CATEGORY)
    pinned: bool | None = None
    expires_at: str | None = Field(default=None, max_length=40, description="'' removes the expiry")


@router.patch("/facts/{fact_id}")
def change(fact_id: int, body: FactChange, request: Request) -> dict:
    expires: str | None | bool = False
    if body.expires_at is not None:
        expires = _expiry(request, body.expires_at) if body.expires_at else None
    try:
        return (
            services(request)
            .facts.update(fact_id, text=body.text, category=body.category, pinned=body.pinned, expires_at=expires)
            .describe()
        )
    except FactError as error:
        raise ApiError(404 if "No fact" in str(error) else 422, str(error)) from None


@router.post("/facts/{fact_id}/confirm")
def confirm(fact_id: int, request: Request) -> dict:
    try:
        return services(request).facts.confirm(fact_id).describe()
    except FactError as error:
        raise ApiError(404, str(error)) from None


@router.delete("/facts/{fact_id}")
def forget(fact_id: int, request: Request) -> dict:
    if not services(request).facts.forget(fact_id):
        raise ApiError(404, f"No fact {fact_id}.")
    return {"deleted": True}
