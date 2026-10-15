"""Providers and models: which providers are enabled, the catalogue, the default model, fallbacks, side-job model."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..llm import NoModel, ProviderError, split_ref
from .common import ApiError, services

router = APIRouter(prefix="/v1", tags=["models"])


def _check_ref(request: Request, ref: str) -> str:
    s = services(request)
    try:
        provider_id, _ = split_ref(ref)
        s.providers.get(provider_id)
    except ProviderError as error:
        raise ApiError(422, str(error)) from None
    if not s.providers.usable(provider_id):
        raise ApiError(422, s.providers.why_not(provider_id))
    return ref.strip()


@router.get("/models")
def pickers(request: Request) -> dict:
    """What a model picker shows: the models you chose to show, and the default."""
    s = services(request)
    return {"models": [info.describe() for info in s.catalog.shown()], "default": s.runtime.get("default_model"),
            "upkeep": s.runtime.get("upkeep_model"), "fallbacks": s.runtime.get("fallbacks")}


@router.get("/admin/providers")
def providers(request: Request) -> dict:
    s = services(request)
    return {"providers": s.providers.status(), "problems": s.catalog.problems}


class Enabled(BaseModel):
    enabled: bool


@router.put("/admin/providers/{provider_id}")
def enable(provider_id: str, body: Enabled, request: Request) -> dict:
    s = services(request)
    try:
        enabled = s.providers.set_enabled(provider_id, body.enabled)
    except ProviderError as error:
        raise ApiError(422, str(error)) from None
    s.providers.forget_backends(provider_id)
    return {"enabled": enabled}


@router.post("/admin/providers/{provider_id}/check")
async def check(provider_id: str, request: Request) -> dict:
    s = services(request)
    try:
        s.providers.get(provider_id)
    except ProviderError as error:
        raise ApiError(404, str(error)) from None
    problem = await s.providers.check(provider_id)
    return {"provider": provider_id, "ok": problem is None, "problem": problem}


@router.get("/admin/models")
async def catalogue(request: Request, refresh: bool = False) -> dict:
    s = services(request)
    found = await s.catalog.listing(force=refresh)
    return {"models": [info.describe() for info in found], "problems": s.catalog.problems,
            "default": s.runtime.get("default_model"), "upkeep": s.runtime.get("upkeep_model"),
            "fallbacks": s.runtime.get("fallbacks")}


class Shown(BaseModel):
    refs: list[str] = Field(min_length=1, max_length=500)
    shown: bool


@router.patch("/admin/models")
def show(body: Shown, request: Request) -> dict:
    try:
        services(request).catalog.set_shown(body.refs, body.shown)
    except ProviderError as error:
        raise ApiError(422, str(error)) from None
    return {"ok": True}


class Ref(BaseModel):
    ref: str = Field(default="", max_length=200)


@router.put("/admin/models/default")
def set_default(body: Ref, request: Request) -> dict:
    if not body.ref:
        raise ApiError(422, "Give the default model, as provider:model.")
    ref = _check_ref(request, body.ref)
    services(request).runtime.set("default_model", ref)
    return {"default": ref}


@router.put("/admin/models/upkeep")
def set_upkeep(body: Ref, request: Request) -> dict:
    ref = _check_ref(request, body.ref) if body.ref else ""
    services(request).runtime.set("upkeep_model", ref)
    return {"upkeep": ref}


class Fallbacks(BaseModel):
    refs: list[str] = Field(max_length=10)


@router.put("/admin/models/fallbacks")
def set_fallbacks(body: Fallbacks, request: Request) -> dict:
    refs: list[str] = []
    for ref in body.refs:
        checked = _check_ref(request, ref)
        if checked not in refs:
            refs.append(checked)
    services(request).runtime.set("fallbacks", refs)
    try:
        chosen = services(request).router.choose()
        kept = [option.ref for option in chosen.fallbacks]
    except NoModel:
        kept = []
    return {"fallbacks": refs, "used_for_default": kept}
