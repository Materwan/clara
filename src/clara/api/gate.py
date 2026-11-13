"""Approval requests, Approve all next and the audit log. Only clients reach these routes: the model has no tool for them."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ..gate import ANSWERS, AlreadyAnswered, ApprovalError, GrantError
from ..gate.actions import NOT_PUBLISHED
from ..gate.approvals import Approval
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1", tags=["gate"])


def _seen_by(item: Approval, client: str) -> bool:
    """What a client may see of a request: a file of your computer is the web chat's alone, and a local-only request is
    not Discord's (Discord is outside the machine, and its #approval would post the text of the call)."""
    if item.action_class in NOT_PUBLISHED:
        return client == "web"
    return not (item.local_only and client == "discord")


@router.get("/approvals")
def approvals(
    request: Request,
    status: Literal["pending", "approved", "denied", "expired", "done", "failed", ""] = "",
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    found = services(request).approvals.list(status or None, limit)
    who = caller(request)
    return {
        "approvals": [
            {**item.describe(), "may_answer": who.token.may_answer(item.level)}
            for item in found
            if _seen_by(item, who.token.client)
        ]
    }


class Decision(BaseModel):
    answer: Literal[ANSWERS]  # type: ignore[valid-type]


@router.post("/approvals/{approval_id}/decide")
async def decide(approval_id: int, body: Decision, request: Request) -> dict:
    s = services(request)
    who = caller(request)
    found = s.approvals.get(approval_id)
    # a request of these classes names a file of your computer: only the web chat shows it and answers it
    if found is not None and not _seen_by(found, who.token.client):
        raise ApiError(404, f"No request {approval_id}.")
    try:
        approval = await s.approvals.decide(approval_id, body.answer, who.token)
    except AlreadyAnswered as error:
        raise ApiError(409, str(error)) from None
    except ApprovalError as error:
        message = str(error)
        raise ApiError(
            403 if "may not answer" in message else 422 if "cannot be allowed" in message else 404, message
        ) from None
    return approval.describe()


@router.post("/approvals/{approval_id}/grant")
async def grant(approval_id: int, request: Request) -> dict:
    """Approve all next: allow this request, and the next ones of its tool in its conversation that would ask, for a while."""
    s = services(request)
    who = caller(request)
    approval = s.approvals.get(approval_id)
    if approval is None:
        raise ApiError(404, f"No request {approval_id}.")
    if approval.status != "pending":
        raise ApiError(409, f"Request #{approval_id} is already {approval.status}.")
    if not who.token.may_answer(approval.level):
        raise ApiError(403, f"The token {who.name} may not answer {approval.level} requests.")
    try:
        made = s.grants.arm(approval, who.token)
    except GrantError as error:
        raise ApiError(422, str(error)) from None
    try:
        settled = await s.approvals.decide(approval_id, "allow_once", who.token)
    except ApprovalError as error:
        s.grants.cancel(made, "the request could not be allowed")
        raise ApiError(409 if isinstance(error, AlreadyAnswered) else 403, str(error)) from None
    return {"approval": settled.describe(), "grants": s.grants.describe(made.conversation_id)}


@router.get("/conversations/{conversation_id}/grants")
def grants(conversation_id: str, request: Request) -> dict:
    """What Approve all next is on in this conversation, and what ended since the page last asked."""
    return services(request).grants.state(conversation_id)


@router.delete("/conversations/{conversation_id}/grants")
def stop_grants(conversation_id: str, request: Request) -> dict:
    return {"stopped": services(request).grants.stop(conversation_id)}


@router.get("/audit")
def audit(request: Request, limit: int = Query(200, ge=1, le=1000), before: int | None = None) -> dict:
    return {"entries": services(request).audit.recent(limit, before)}
