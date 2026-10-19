"""Conversations: one list shared by every client."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..connectors.forms import forms_in, with_answers
from ..memory.conversations import ANY, ConversationError
from .common import ApiError, caller, failure, refuse_when_stopping, services

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

ConversationId = Field(pattern=r"^c[0-9a-f]{16}$")


def _get(request: Request, conversation_id: str):
    conversation = services(request).conversations.get(conversation_id)
    if conversation is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    return conversation


@router.get("")
def listing(request: Request, q: str = Query("", max_length=200), project: str = Query(ANY, pattern=r"^(any|none|\d+)$"),
            kind: Literal["chat", "schedule", "any"] = "chat", history: bool = False,
            limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    return {"conversations": services(request).conversations.listing(q, project, kind, limit, offset, history)}


class NewConversation(BaseModel):
    title: str = Field(default="", max_length=100)
    project: int | None = None
    model: str = Field(default="", max_length=200)
    incognito: bool = False
    local_only: bool = False
    packs: list[str] | None = Field(default=None, max_length=20, description="The tool packs switched on (default: web)")


def _packs(s, packs: list[str]) -> list[str]:
    try:
        return s.packs.check(packs, "chat")
    except ValueError as error:
        raise ApiError(422, str(error)) from None


@router.post("", status_code=201)
def create(body: NewConversation, request: Request) -> dict:
    s = services(request)
    if body.project is not None and s.projects.get(body.project) is None:
        raise ApiError(404, f"No project {body.project}.")
    conversation = s.conversations.create(caller(request).client, title=body.title, project_id=body.project,
                                          model=body.model, incognito=body.incognito, local_only=body.local_only,
                                          packs=_packs(s, body.packs) if body.packs is not None else None)
    return conversation.describe()


@router.get("/{conversation_id}")
def get(conversation_id: str, request: Request) -> dict:
    conversation = _get(request, conversation_id)
    s = services(request)
    return {**conversation.describe(), "context": s.agent.context_info(conversation_id),
            "answering": s.agent.answering(conversation_id),
            "files": [item.describe() for item in s.conversation_files.of(conversation_id)],
            "approvals": [item.describe() for item in s.approvals.waiting(conversation_id)]}


class ConversationChange(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    pinned: bool | None = None
    project: int | None = Field(default=None, description="0: out of any project")
    model: str | None = Field(default=None, max_length=200)
    local_only: bool | None = None
    packs: list[str] | None = Field(default=None, max_length=20, description="The tool packs switched on, all of them")


@router.patch("/{conversation_id}")
def change(conversation_id: str, body: ConversationChange, request: Request) -> dict:
    _get(request, conversation_id)
    s = services(request)
    fields: dict = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.pinned is not None:
        fields["pinned"] = body.pinned
    if body.project is not None:
        if body.project and s.projects.get(body.project) is None:
            raise ApiError(404, f"No project {body.project}.")
        fields["project_id"] = body.project or None
    if body.model is not None:
        fields["model"] = body.model.strip()
    if body.local_only is not None:
        fields["local_only"] = body.local_only
    if body.packs is not None:
        fields["packs"] = _packs(s, body.packs)
    try:
        return s.conversations.update(conversation_id, **fields).describe()
    except ConversationError as error:
        raise ApiError(409, str(error)) from None


@router.delete("/{conversation_id}")
def delete(conversation_id: str, request: Request) -> dict:
    s = services(request)
    if s.agent.answering(conversation_id):
        raise ApiError(409, "Clara is answering in this conversation: wait for the end of the answer.")
    if not s.conversations.delete(conversation_id):
        raise ApiError(404, f"No conversation {conversation_id}.")
    return {"deleted": True}


@router.get("/{conversation_id}/messages")
def messages(conversation_id: str, request: Request, calls: bool = False) -> dict:
    _get(request, conversation_id)
    s = services(request)
    shown = s.conversations.read_back(conversation_id, calls=True)
    for index, message in enumerate(shown):
        if message["role"] == "assistant" and message.get("tool_calls"):
            forms = forms_in([{"function": call} for call in message["tool_calls"]])
            if forms:
                later = [item["content"] for item in shown[index + 1:] if item["role"] == "user"]
                message["forms"] = with_answers(forms, later)
    if not calls:
        shown = [{key: value for key, value in message.items() if key != "tool_calls"} for message in shown
                 if message["role"] != "tool" and (message["content"] or message.get("forms"))]
    return {"conversation": conversation_id, "messages": shown}


class Compact(BaseModel):
    focus: str = Field(default="", max_length=500)


@router.post("/{conversation_id}/compact")
async def compact(conversation_id: str, body: Compact, request: Request) -> dict:
    refuse_when_stopping(request)
    _get(request, conversation_id)
    try:
        before, after = await services(request).agent.compact(conversation_id, body.focus)
    except Exception as error:
        raise failure(error, "compaction") from None
    return {"before": round(before, 1), "after": round(after, 1)}


@router.post("/{conversation_id}/title")
async def title(conversation_id: str, request: Request) -> dict:
    _get(request, conversation_id)
    try:
        return {"title": await services(request).agent.title(conversation_id)}
    except Exception as error:
        raise failure(error, "title") from None


class Version(BaseModel):
    message_id: int


@router.post("/{conversation_id}/version")
def version(conversation_id: str, body: Version, request: Request) -> dict:
    _get(request, conversation_id)
    s = services(request)
    if s.agent.answering(conversation_id):
        raise ApiError(409, "Clara is answering in this conversation.")
    try:
        s.conversations.switch_version(conversation_id, body.message_id)
    except ConversationError as error:
        raise ApiError(409, str(error)) from None
    return {"ok": True}


@router.get("/{conversation_id}/files/{file_id}")
def file(conversation_id: str, file_id: int, request: Request) -> Response:
    saved = services(request).conversation_files.numbered(conversation_id, file_id)
    if saved is None:
        raise ApiError(404, "No such file in this conversation.")
    media = saved.mime or ("text/plain; charset=utf-8" if saved.kind == "text" else "application/octet-stream")
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in saved.name)[:100] or "file"
    return Response(saved.data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{safe}"'})
