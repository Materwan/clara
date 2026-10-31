"""What the automatic deletion of automation conversations would delete, before a limit is saved (memory/retention.py).

GET /v1/retention/preview       without `kind`: the global limits as given (what is left out stays as it is stored);
                                with `kind` (schedule or hook) and `id`: that automation's own limits as given (what is
                                left out follows the global setting). Nothing is deleted.
    The answer: `would_delete`, the conversations the next pass would delete with these limits; `more`, how many of them
    the limits now saved would not delete; and the same two numbers for each automation concerned.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..memory.retention import KEEP_AGE, KEEP_LAST, MAX_DAYS, MAX_HOURS, MAX_LAST, Limits, RetentionError, check_own
from .common import ApiError, services

router = APIRouter(prefix="/v1", tags=["retention"])


class KeepLimits(BaseModel):
    """An automation's own limits on the conversations of its runs: the base of what creates or changes a schedule or a
    hook, so that the bounds and the words are written once. Null follows the global setting."""

    keep_days: int | None = Field(default=None, ge=0, le=MAX_DAYS, description=KEEP_AGE)
    keep_hours: int | None = Field(default=None, ge=0, le=MAX_HOURS, description=KEEP_AGE)
    keep_last: int | None = Field(default=None, ge=0, le=MAX_LAST, description=KEEP_LAST)


@router.get("/retention/preview")
def preview(
    request: Request,
    kind: Literal["schedule", "hook"] | None = None,
    id: int | None = None,
    keep_days: int | None = Query(default=None, ge=0, le=MAX_DAYS),
    keep_hours: int | None = Query(default=None, ge=0, le=MAX_HOURS),
    keep_last: int | None = Query(default=None, ge=0, le=MAX_LAST),
) -> dict:
    retention = services(request).retention
    if kind is None:
        stored = retention.general()
        return retention.preview(
            general=Limits(
                stored.days if keep_days is None else keep_days,
                stored.hours if keep_hours is None else keep_hours,
                stored.last if keep_last is None else keep_last,
            )
        )
    if id is None:
        raise ApiError(422, "Give the id of the schedule or the hook.")
    if kind == "hook" and services(request).triggers is None:
        raise ApiError(404, "Hooks are not available.")
    if not retention.exists(kind, id):
        raise ApiError(404, f"No {kind} {id}.")
    try:
        own = check_own(keep_days, keep_hours, keep_last)
    except RetentionError as error:
        raise ApiError(422, str(error)) from None
    return retention.preview(only=(kind, id), own=own)
