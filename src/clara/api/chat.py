"""Chat: one question, streamed as server-sent events or answered as one JSON object.

POST /v1/chat/stream                  the events of the turn, as they come (`keep_running`: it goes on if you leave)
POST /v1/chat                         the `done` event only (approvals are not waited for: they are pushed)
GET /v1/conversations/{id}/run        follow the answer a conversation is giving, from its first event
DELETE /v1/conversations/{id}/run     stop that answer (what was said is kept, marked interrupted)
POST /v1/turns/{id}/tool-results      a client's answers to `tool_requests` (only the token that opened the turn)
POST /v1/documents/extract            the text of a PDF or Word file, for a client that puts documents in messages
"""

from __future__ import annotations

import base64
import binascii
import contextlib
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..agent.runs import AlreadyRunning, Run
from ..agent.turn import ChatRequest
from ..knowledge import ingest
from ..knowledge.attachments import MAX_ATTACHMENTS, Attached, FileError, prepare
from ..knowledge.ingest import IngestError
from .common import (
    ApiError,
    caller,
    error_event,
    event_stream,
    failure,
    refuse_when_stopping,
    services,
    sse,
    with_keepalive,
)

router = APIRouter(prefix="/v1", tags=["chat"])

MAX_MESSAGE = 2_000_000
MAX_ATTACHMENT_CHARS = 42_000_000


class Attachment(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(max_length=MAX_ATTACHMENT_CHARS, description="The file's bytes, base64")


class ChatBody(BaseModel):
    message: str = Field(default="", max_length=MAX_MESSAGE)
    conversation: str | None = Field(default=None, pattern=r"^c[0-9a-f]{16}$")
    attachments: list[Attachment] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    project: int | None = None
    model: str | None = Field(default=None, max_length=200)
    fork_of: int | None = None
    timezone: str | None = Field(default=None, max_length=64)
    tools: list[dict] = Field(default_factory=list, max_length=64)
    instructions: str = Field(default="", max_length=20_000)
    incognito: bool = False
    local_only: bool = False
    parent: str | None = Field(
        default=None,
        pattern=r"^c[0-9a-f]{16}$",
        description="With a new conversation: the one it is shown in (a sub agent's)",
    )
    ref: str = Field(default="", max_length=40, description="The message's id on Discord, when the bot sends it")
    mirror_thread: str = Field(
        default="", max_length=40, description="A new conversation started in a Discord forum post: its thread"
    )
    keep_running: bool = Field(
        default=False,
        description="The answer goes on if the stream is closed (follow it again with GET /v1/conversations/{id}/run)",
    )


def _files(body: ChatBody) -> tuple[Attached, ...]:
    files = []
    for item in body.attachments:
        try:
            files.append(Attached(item.name, base64.b64decode(item.data, validate=True)))
        except (binascii.Error, ValueError):
            raise ApiError(422, f"{item.name}: the file is not base64.") from None
    return tuple(files)


def chat_request(body: ChatBody, request: Request, wait: bool) -> ChatRequest:
    who = caller(request)
    files = _files(body)
    if not body.message.strip() and not files:
        raise ApiError(422, "The message is empty.")
    try:
        prepared = prepare(body.message, files)
    except FileError as error:
        raise ApiError(422, str(error)) from None
    if body.project is not None and services(request).projects.get(body.project) is None:
        raise ApiError(404, f"No project {body.project}.")
    if body.conversation and services(request).mirror.is_archived(body.conversation):
        raise ApiError(409, "This conversation is archived: unarchive it to write in it.")
    return ChatRequest(
        message=prepared.message,
        client=who.client,
        token_id=who.token.id,
        conversation=body.conversation,
        tools=tuple(body.tools),
        instructions=body.instructions,
        timezone=body.timezone,
        images=prepared.images,
        files=files,
        project=body.project,
        model=body.model,
        fork_of=body.fork_of,
        incognito=body.incognito,
        local_only=body.local_only,
        parent=body.parent,
        wait_for_approvals=wait,
        ref=body.ref,
        mirror_thread=body.mirror_thread,
    )


def _followed(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    async def lines() -> AsyncIterator[str]:
        async with contextlib.aclosing(with_keepalive(events)) as stream:
            async for event in stream:
                yield ": keepalive\n\n" if event is None else sse(event)

    return lines()


@router.post("/chat/stream")
async def chat_stream(body: ChatBody, request: Request):
    refuse_when_stopping(request)
    agent = services(request).agent
    if body.keep_running and body.tools:
        raise ApiError(422, "keep_running cannot carry client tools: they need the client to stay.")
    if body.keep_running and body.conversation and agent.runs.of(body.conversation) is not None:
        raise ApiError(409, "Clara is already answering in this conversation: follow it, or stop it.")
    chat = chat_request(body, request, wait=True)
    try:
        plan = agent.plan(chat)  # what fails before streaming is a plain HTTP error
    except Exception as error:
        raise failure(error, "chat") from None

    if body.keep_running:
        try:
            run = agent.runs.start(chat, plan, lambda error: error_event(error, "chat"))
        except AlreadyRunning:
            raise ApiError(409, "Clara is already answering in this conversation: follow it, or stop it.") from None
        return event_stream(_followed(run.follow()))

    async def lines() -> AsyncIterator[str]:
        events = agent.turn(chat, plan)
        try:
            async with contextlib.aclosing(with_keepalive(events)) as stream:
                async for event in stream:
                    yield ": keepalive\n\n" if event is None else sse(event)
        except Exception as error:  # once streaming, a failure is the last event
            yield sse(error_event(error, "chat"))

    return event_stream(lines())


def _running(request: Request, conversation_id: str) -> Run:
    run = services(request).agent.runs.of(conversation_id)
    if run is None:
        raise ApiError(404, "Clara is not answering in this conversation.")
    return run


@router.get("/conversations/{conversation_id}/run")
async def follow_run(conversation_id: str, request: Request):
    """The answer in progress, from its start: `resume` (your question), what was said so far, then what comes."""
    run = _running(request, conversation_id)
    approvals = services(request).approvals

    async def events() -> AsyncIterator[dict]:
        yield {"type": "resume", "message": run.question, "attachments": run.attachments}
        async with contextlib.aclosing(run.follow()) as followed:
            async for event in followed:
                if event["type"] == "approval":  # answered since it was said: the card shows how it ended
                    current = approvals.get(event["approval"]["id"])
                    if current is not None:
                        event = {**event, "approval": current.describe()}
                yield event

    return event_stream(_followed(events()))


@router.delete("/conversations/{conversation_id}/run")
async def stop_run(conversation_id: str, request: Request) -> dict:
    run = _running(request, conversation_id)
    await services(request).agent.runs.stop(run, "you stopped it")
    return {"ok": True}


@router.post("/chat")
async def chat_once(body: ChatBody, request: Request) -> dict:
    refuse_when_stopping(request)
    agent = services(request).agent
    chat = chat_request(body, request, wait=False)
    done: dict = {}
    approvals: list[dict] = []
    try:
        async with contextlib.aclosing(agent.turn(chat)) as events:
            async for event in events:
                if event["type"] == "tool_requests":
                    raise ApiError(422, "POST /v1/chat cannot carry client tools: use /v1/chat/stream.")
                if event["type"] == "approval":
                    approvals.append(event["approval"])
                if event["type"] == "done":
                    done = event
    except ApiError:
        raise
    except Exception as error:
        raise failure(error, "chat") from None
    return {**done, "approvals": approvals}


class ToolResult(BaseModel):
    id: str = Field(max_length=64)
    content: str = Field(max_length=60_000_000)


class ToolResults(BaseModel):
    results: list[ToolResult] = Field(max_length=64)


@router.post("/turns/{turn_id}/tool-results")
def tool_results(turn_id: str, body: ToolResults, request: Request) -> dict:
    who = caller(request)
    try:
        services(request).agent.submit_results(turn_id, who.token.id, {item.id: item.content for item in body.results})
    except KeyError:
        raise ApiError(404, "No turn of yours is waiting for these results.") from None
    except ValueError as error:
        raise ApiError(422, str(error)) from None
    return {"ok": True}


class Document(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(max_length=MAX_ATTACHMENT_CHARS)


@router.post("/documents/extract")
def extract(body: Document) -> dict:
    try:
        raw = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(422, "The file is not base64.") from None
    if len(raw) > 30_000_000:
        raise ApiError(413, "A document may be 30 MB at most.")
    try:
        found = ingest.extract(body.name, raw)
    except IngestError as error:
        raise ApiError(422, f"{body.name}: {error}") from None
    return {"name": found.path, "kind": found.kind, "text": found.text, "chars": len(found.text)}
