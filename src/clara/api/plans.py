"""The plan Clara keeps for a conversation (packs/plan.py), read only: the owner steers it by telling Clara what to change.

GET /v1/conversations/{id}/plan      the plan: `{conversation, title, steps: [{n, text, status, note, outside}], updated_at}`; 404 when there is none
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .common import ApiError, services

router = APIRouter(prefix="/v1/conversations", tags=["plan"])


@router.get("/{conversation_id}/plan")
def show(conversation_id: str, request: Request) -> dict:
    s = services(request)
    if s.conversations.get(conversation_id) is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    plan = s.plans.get(conversation_id)
    if plan is None:
        raise ApiError(404, "No plan in this conversation.")
    return plan.describe()
