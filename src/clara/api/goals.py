"""`/goal`: Clara keeps working on a conversation until a condition is met (agent/goals.py).

GET /v1/conversations/{id}/goal      the goal's state, or `"goal": null`
POST /v1/conversations/{id}/goal     what follows `/goal` as `text`: nothing shows the state, `clear` ends the goal, `resume` or
                                     flags alone (`--turns N`, `--minutes N`) add limits and resume it, anything else starts a goal

A goal that starts or resumes is driven by a kept answer of the conversation: the reply says `running`, and the client follows it
with `GET /v1/conversations/{id}/run` (the same stream as a chat with `keep_running`; `DELETE` on it stops the goal and pauses it).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..agent.goals import GoalError, command
from .common import ApiError, caller, error_event, refuse_when_stopping, services

router = APIRouter(prefix="/v1/conversations", tags=["goals"])


class GoalText(BaseModel):
    text: str = Field(
        default="", max_length=10_000, description="What follows /goal: flags, then the condition, or clear"
    )
    timezone: str | None = Field(default=None, max_length=64)


def _state(request: Request, conversation_id: str) -> dict:
    s = services(request)
    if s.conversations.get(conversation_id) is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    goal = s.goals.get(conversation_id)
    return {"goal": goal.describe() if goal else None, "running": s.agent.runs.of(conversation_id) is not None}


@router.get("/{conversation_id}/goal")
def show(conversation_id: str, request: Request) -> dict:
    return _state(request, conversation_id)


@router.post("/{conversation_id}/goal")
async def run_command(conversation_id: str, body: GoalText, request: Request) -> dict:
    refuse_when_stopping(request)
    who = caller(request)
    s = services(request)
    try:
        return await command(
            s.agent,
            conversation_id,
            body.text,
            client=who.client,
            token_id=who.token.id,
            timezone=body.timezone,
            can_answer_questions=who.may_answer_questions,
            fail=lambda error: error_event(error, "chat"),
        )
    except GoalError as error:
        raise ApiError(error.status, str(error)) from None
