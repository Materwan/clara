"""What Clara puts beside a conversation in the web chat: the side panel, the QCMs and the questions she waits on.

GET  /v1/conversations/{id}/panel                  the files and vault notes Clara wrote in it, and its QCMs with their progress
POST /v1/conversations/{id}/qcm/{ref}/answer       answer a question of a QCM (returns the message to send Clara)
POST /v1/conversations/{id}/qcm/{ref}/skip         skip one (no message, unless it was the last one left)
POST /v1/conversations/{id}/qcm/{ref}/retry        start the QCM again, as a new attempt
POST /v1/conversations/{id}/questions/{ask_id}     answer (or dismiss) the questions `ask_questions` is waiting on
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..connectors.asks import AskError, NotWaiting
from ..connectors.forms import CLIENTS as FORM_CLIENTS
from ..connectors.forms import forms_in, with_answers
from ..connectors.quizzes import QuizError
from ..services import Services
from ..workspace.store import WorkspaceError
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1/conversations", tags=["forms"])


def conversation_forms(s: Services, conversation_id: str) -> list[dict]:
    """The QCMs Clara made in the conversation (a form she sent again is one), each with `created_at` and, for the
    ones made before the panel, the `answers` it was given in one message."""
    shown = s.conversations.read_back(conversation_id, calls=True)
    found: dict[str, dict] = {}
    for index, message in enumerate(shown):
        if message["role"] != "assistant" or not message.get("tool_calls"):
            continue
        forms = forms_in([{"function": call} for call in message["tool_calls"]])
        if forms:
            later = [item["content"] for item in shown[index + 1 :] if item["role"] == "user"]
            for form in with_answers(forms, later):
                found.setdefault(form["ref"], {**form, "created_at": message["created_at"]})
    return list(found.values())


def _form(request: Request, conversation_id: str, ref: str) -> dict:
    s = services(request)
    if s.conversations.get(conversation_id) is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    for form in conversation_forms(s, conversation_id):
        if form["ref"] == ref:
            return form
    raise ApiError(404, f"No QCM {ref} in this conversation.")


def _shown(s: Services, conversation_id: str, form: dict) -> dict:
    legacy = form.get("answers")
    return {
        **{key: value for key, value in form.items() if key != "answers"},
        "progress": s.quizzes.progress(conversation_id, form, legacy),
    }


@router.get("/{conversation_id}/panel")
async def panel(conversation_id: str, request: Request) -> dict:
    s = services(request)
    if s.conversations.get(conversation_id) is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    files: list[dict] = []
    if s.workspace is not None:
        try:
            files = await s.workspace.conversation_files(conversation_id)
        except (WorkspaceError, TimeoutError):  # the history cannot be read right now: the QCMs are still there
            files = []
    notes = await s.vault.conversation_notes(conversation_id) if s.vault is not None else []
    return {
        "files": files,
        "notes": notes,
        "forms": [_shown(s, conversation_id, form) for form in conversation_forms(s, conversation_id)],
    }


class QuizAnswer(BaseModel):
    question: int = Field(ge=1, le=100, description="The question's number, from 1")
    answer: Any = Field(description="A list of option numbers (from 0), or the text of a free question")


class QuizSkip(BaseModel):
    question: int = Field(ge=1, le=100, description="The question's number, from 1")


@router.post("/{conversation_id}/qcm/{ref}/answer")
def qcm_answer(conversation_id: str, ref: str, body: QuizAnswer, request: Request) -> dict:
    form = _form(request, conversation_id, ref)
    try:
        done = services(request).quizzes.answer(conversation_id, form, body.question, body.answer)
    except QuizError as error:
        raise ApiError(409, str(error)) from None
    except ValueError as error:
        raise ApiError(422, str(error)) from None
    return done


@router.post("/{conversation_id}/qcm/{ref}/skip")
def qcm_skip(conversation_id: str, ref: str, body: QuizSkip, request: Request) -> dict:
    form = _form(request, conversation_id, ref)
    try:
        done = services(request).quizzes.skip(conversation_id, form, body.question)
    except QuizError as error:
        raise ApiError(409, str(error)) from None
    return done


@router.post("/{conversation_id}/qcm/{ref}/retry")
def qcm_retry(conversation_id: str, ref: str, request: Request) -> dict:
    form = _form(request, conversation_id, ref)
    return {"progress": services(request).quizzes.retry(conversation_id, form)}


class QuestionReply(BaseModel):
    answers: list[dict] | None = Field(
        default=None, max_length=10, description="One per question: {options: [numbers from 0], other: text}"
    )
    declined: bool = Field(default=False, description="true: dismiss the questions without answering")


@router.post("/{conversation_id}/questions/{ask_id}")
def reply_to_questions(conversation_id: str, ask_id: str, body: QuestionReply, request: Request) -> dict:
    if caller(request).client not in FORM_CLIENTS:
        raise ApiError(403, "Only the web site answers Clara's questions.")
    if not body.declined and body.answers is None:
        raise ApiError(422, "Give the answers, or declined: true.")
    try:
        services(request).questions.reply(ask_id, conversation_id, body.answers, declined=body.declined)
    except NotWaiting as error:
        raise ApiError(409, str(error)) from None
    except AskError as error:
        raise ApiError(422, str(error)) from None
    return {"ok": True}
