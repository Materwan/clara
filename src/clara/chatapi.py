"""The routes of a turn of conversation (see agent.py), and the checks every chat request goes through (the built-in
Discord bot runs them too, discord_bot/local.py).

    POST   /v1/chat                     JSON answer
    POST   /v1/chat/stream              Server-Sent Events: token / tool / done / error
    POST   /v1/turns/{id}/tool-results  a client's answer to a `tool_requests` event
"""

from __future__ import annotations

import base64
import dataclasses
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from .agent import Agent, ChatRequest, ClientToolTimeout, ModelTimeout, PromptTooLarge, ServerStopping
from .apicommon import (
    Body,
    ExternalId,
    Surface,
    event_stream,
    log,
    model_failure,
    own_project_id,
    refuse_when_stopping,
    sse,
    sse_lines,
)
from .attachments import MAX_ATTACHMENTS, MAX_NAME, Attached, FileError, Prepared, prepare
from .auth import Client, require_account, require_conversation, require_space
from .limits import UsageLimitReached
from .memory import Member, Memory, Person
from .settings import Settings

router = APIRouter()

MAX_ROSTER = 500


class RosterEntry(Body):
    user_id: ExternalId
    name: str = Field(default="", max_length=80)
    # what Clara writes to mention this person where "@name" is not enough (`<@123>` on Discord): given back to her
    # as it is. A single word, no spaces.
    mention: str = Field(default="", max_length=64, pattern=r"^\S*$")


MAX_ATTACHMENT_CHARS = 40_000_000  # a file in base64 (a 30 MB document is about 40 million)


class AttachmentBody(Body):
    """A file a message carries (attachments.py): its name, a MIME type (a hint only), its bytes in base64."""

    name: str = Field(min_length=1, max_length=MAX_NAME)
    mime: str = Field(default="", max_length=100)
    data: str = Field(min_length=1, max_length=MAX_ATTACHMENT_CHARS)

    @field_validator("data")
    @classmethod
    def _base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except ValueError:
            raise ValueError("the file is not base64") from None
        return value


class ChatBody(Body):
    surface: Surface
    user_id: ExternalId
    user_name: str | None = Field(default=None, max_length=80)
    message: str = Field(default="", max_length=200_000)  # may be empty when files are attached
    attachments: list[AttachmentBody] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    conversation: str | None = Field(default=None, min_length=1, max_length=200)
    # What a client can add to a turn (see agent.py):
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=200)  # tools it runs itself
    instructions: str = Field(default="", max_length=100_000)  # added to the system prompt
    prefix: str = Field(default="", max_length=50_000)  # put before the message, never summarised
    ephemeral: bool = False  # one-shot job: no persona, no memory, nothing stored
    timezone: str | None = Field(default=None, max_length=64)  # IANA name, e.g. "Europe/Paris"
    quiet: bool = False  # never notify the person about this turn (the client shows the answer anyway)
    # A group space (a Discord server, see README "Discord"): its id, its members, who the message is about
    space: str | None = Field(default=None, min_length=1, max_length=200)
    roster: list[RosterEntry] = Field(default_factory=list, max_length=MAX_ROSTER)
    focus: list[ExternalId] = Field(default_factory=list, max_length=20)
    mode: Literal["answer", "observe", "maybe"] = "answer"
    # The project a new conversation is part of (one that exists stays in its own, see PATCH /v1/conversations)
    project: int | None = Field(default=None, ge=1)
    # Ask a question again: the id of a question of the conversation (GET .../messages). With a `message`, that is the
    # question edited; without, the same question. The old version stays, as another version of the question
    # (POST .../version goes back to it). `model` (provider:model) answers this turn alone with that model.
    fork_of: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def _says_something(self) -> ChatBody:
        if not self.message.strip() and not self.attachments and self.fork_of is None:
            raise ValueError("Write a message, or attach a file.")
        if self.fork_of is not None and (not self.conversation or self.ephemeral or self.mode != "answer" or self.tools):
            raise ValueError("A question is asked again in its own conversation, as a plain answer.")
        return self

    def to_request(self, roster: tuple[Person, ...] = (), focus: tuple[Person, ...] = (), mode: str = "") -> ChatRequest:
        return ChatRequest(
            surface=self.surface, user_id=self.user_id, user_name=self.user_name, message=self.message,
            conversation=self.conversation, tools=tuple(self.tools), instructions=self.instructions,
            prefix=self.prefix, ephemeral=self.ephemeral, timezone=self.timezone,
            quiet=self.quiet or self.mode != "answer", space=self.space, roster=roster, focus=focus,
            mode=mode or self.mode, fork_of=self.fork_of, model=self.model,
        )


class ToolResult(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    content: str = Field(max_length=2_000_000)


class ToolResultsBody(BaseModel):
    results: list[ToolResult] = Field(max_length=100)


# ----------------------------------------------------------------------
# The checks of a chat request
# ----------------------------------------------------------------------
def members(
    request: Request, surface: str, ids: list[tuple[str, str]], mentions: dict[str, str] | None = None
) -> tuple[Member, ...]:
    """The people behind the accounts `ids` of a surface (id, name shown), once each, leaving out the
    accounts Clara does not know (and, on a login surface, those not signed in): nothing is created. Each comes
    with the other names they go by, and `mentions` (account id -> how to mention them) when the client gave some."""
    settings: Settings = request.app.state.settings
    memory: Memory = request.app.state.memory
    signed_in = request.app.state.users.signed_in_accounts(surface) if surface in settings.login_surfaces else None
    found: dict[int, tuple[Person, str, str]] = {}
    for user_id, name in ids:
        if signed_in is not None and user_id not in signed_in:
            continue
        person = memory.find_person(surface, user_id)
        if person is not None and person.id not in found:
            found[person.id] = (person, " ".join(name.split()) or person.name, (mentions or {}).get(user_id, ""))
    aliases = memory.aliases_of(found)
    return tuple(
        Member(person.id, shown, mention, aliases.get(person.id, ())) for person, shown, mention in found.values()
    )


def conversation_project(request: Request, body: ChatBody) -> int | None:
    """The project of the conversation: the one it is in, or (a new one) the one the message names."""
    memory: Memory = request.app.state.memory
    info = memory.conversation_info(body.conversation or f"{body.surface}:{body.user_id}")
    if info is not None:
        # only for its owner: a conversation shared with others never brings someone's files to another
        person = memory.find_person(body.surface, body.user_id)
        return info.project_id if person is not None and info.person_id == person.id else None
    return own_project_id(request, body.surface, body.user_id, body.project) if body.project is not None else None


def attached_files(body: ChatBody) -> tuple[Attached, ...]:
    """The files of a message as they were sent (the bytes, decoded)."""
    return tuple(Attached(item.name, base64.b64decode(item.data)) for item in body.attachments)


def prepared_message(body: ChatBody) -> Prepared:
    """The message and its files as the model gets them (attachments.py). A file that cannot be used is a 422."""
    try:
        return prepare(body.message, attached_files(body))
    except FileError as error:
        raise HTTPException(422, str(error)) from None


def asker(request: Request, body: ChatBody) -> Person:
    person = request.app.state.memory.find_person(body.surface, body.user_id)
    if person is None:
        raise HTTPException(404, "No such conversation of yours")
    return person


def same_question(request: Request, body: ChatBody, turn: ChatRequest) -> ChatRequest:
    """The turn that asks a question again as it was written (its text and the context put before it)."""
    memory: Memory = request.app.state.memory
    question = memory.question(body.conversation or "", body.fork_of or 0)
    if question is None or question.person_id != asker(request, body).id:
        raise HTTPException(404, "No such message of yours in this conversation")
    return dataclasses.replace(turn, message=question.content, prefix=turn.prefix or question.prefix)


def check_fork(request: Request, body: ChatBody) -> None:
    """A question may be asked again when it is the person's own, on the path of the conversation and after the
    summary; a model may be chosen for the turn when the person may choose it."""
    memory: Memory = request.app.state.memory
    person = asker(request, body)
    if body.fork_of is not None:
        question = memory.question(body.conversation or "", body.fork_of)
        if question is None or question.person_id != person.id:
            raise HTTPException(404, "No such message of yours in this conversation")
        if question.id <= memory.state(body.conversation or "").upto_id:
            raise HTTPException(409, "That part of the conversation was summarised: it cannot be asked again.")
    if body.model:
        try:
            usable = request.app.state.models.is_usable_by(body.model, person.id)
        except Exception:
            usable = False
        if not usable:
            raise HTTPException(422, f"{body.model} is not one of the models you may choose.")


def checked(request: Request, client: str, body: ChatBody) -> ChatRequest:
    """The turn a chat request asks for, once everything in it was checked (else an HTTPException)."""
    refuse_when_stopping(request)
    require_account(request, client, body.surface, body.user_id)
    if body.conversation:
        require_conversation(request, client, body.conversation)
    roster: tuple[Person, ...] = ()
    focus: tuple[Person, ...] = ()
    mode = body.mode
    if body.space is not None:
        require_space(request, client, body.space, body.surface)
        roster = members(
            request, body.surface, [(entry.user_id, entry.name) for entry in body.roster],
            {entry.user_id: entry.mention for entry in body.roster if entry.mention},
        )
        named = {entry.user_id: entry.name for entry in body.roster}
        focus = members(request, body.surface, [(user_id, named.get(user_id, "")) for user_id in body.focus])
    elif body.roster or body.focus:
        raise HTTPException(422, "roster and focus need a space")
    if mode == "maybe" and not request.app.state.memory.chime_allowed(body.space):
        mode = "observe"  # the owner did not let Clara answer what is not for her there
    turn = body.to_request(roster, focus, mode)
    if body.fork_of is not None and not body.message.strip() and not body.attachments:
        turn = same_question(request, body, turn)
    else:
        prepared = prepared_message(body)
        turn = dataclasses.replace(
            turn, message=prepared.message, images=prepared.images, files=attached_files(body)
        )
    if body.fork_of is not None or body.model:
        check_fork(request, body)
    project = None if body.ephemeral else conversation_project(request, body)
    if project is not None:
        turn = dataclasses.replace(turn, project=project)
    try:
        request.app.state.agent.validate(turn)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return turn


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@router.post("/v1/chat")
async def chat(body: ChatBody, client: Client, request: Request) -> dict:
    if body.tools:
        raise HTTPException(422, "Client tools need a stream: use /v1/chat/stream")
    turn = checked(request, client, body)
    agent: Agent = request.app.state.agent
    final: dict | None = None
    try:
        async for event in agent.turn(turn, client):
            final = event
    except Exception as error:
        raise model_failure(error, "chat", client) from None
    return final or {}


@router.post("/v1/chat/stream")
async def chat_stream(body: ChatBody, client: Client, request: Request) -> StreamingResponse:
    turn = checked(request, client, body)
    agent: Agent = request.app.state.agent

    async def lines() -> AsyncIterator[str]:
        try:
            async for line in sse_lines(agent.turn(turn, client)):
                yield line
        except UsageLimitReached as error:
            yield sse({"type": "error", "reason": "usage_limit", "message": str(error)})
        except (ClientToolTimeout, ModelTimeout, PromptTooLarge, ServerStopping) as error:
            yield sse({"type": "error", "message": str(error)})
        except Exception:
            log.exception("chat stream failed (client=%s)", client)
            yield sse({"type": "error", "message": "The language model failed"})

    return event_stream(lines())


@router.post("/v1/turns/{turn_id}/tool-results")
async def tool_results(turn_id: str, body: ToolResultsBody, client: Client, request: Request) -> dict:
    results = {item.id: item.content for item in body.results}
    try:
        request.app.state.agent.submit_results(turn_id, client, results)
    except KeyError:
        raise HTTPException(404, "No turn of yours is waiting for tool results") from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return {"accepted": len(results)}


def install(app: FastAPI) -> None:
    app.include_router(router)
