"""Tool packs: the special tools a conversation or a schedule can be given."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request

from .common import services

router = APIRouter(prefix="/v1/packs", tags=["packs"])


@router.get("")
def packs(request: Request, where: Literal["chat", "schedule"] | None = None) -> dict:
    """The packs (for a chat or for a schedule; all by default), with whether each can be used right now and why not."""
    return {"packs": services(request).packs.listing(where)}
