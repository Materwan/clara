"""Voice dictation: audio in, text out (src/clara/dictation/), and into a conversation, per client.

POST /v1/dictation/transcribe   The body is the audio itself, any format the decoder reads (webm or ogg with opus, m4a, wav...),
                                sent as `audio/*` or `application/octet-stream`. `?language=fr` overrides the setting (`auto`
                                detects it). The answer is `{"text": "...", "seconds": 13.7}`. Nothing goes into a conversation
                                and the audio is never kept. One dictation at a time: a second one gets a 429 at once.
POST /v1/dictation              The same audio and options, plus `?client=<web|island|phone|cli|discord>` (anything else: 422) and
                                `&conversation=<id>`. The text goes into the conversation: that one, else the client's current
                                dictation conversation, else a new one made for the client. The conversation is tainted from then
                                on. With the client's automatic sending ON the text is sent as a message at once, in a Run that goes
                                on without the client (follow it with GET /v1/conversations/{id}/run): `{"text", "seconds",
                                "conversation", "sent": true, "running": true}`. OFF (the default): `{..., "sent": false}`, the client
                                puts the text in its box. Silence: `text: ""`, `sent: false`, nothing is made or sent. It never
                                answers an approval: those go through their own routes.
GET  /v1/dictation/clients      the five clients' settings: `{client, conversation, auto_send, mode, packs}`
GET  /v1/dictation/clients/{client}
PUT  /v1/dictation/clients/{client}   {auto_send?, mode?, packs?}: only a token that may answer every request (scope `all`)
POST /v1/dictation/clients/{client}/conversation   a new dictation conversation, now the client's current: `{conversation, title}`
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from ..agent.runs import AlreadyRunning
from ..dictation import LANGUAGE, DictationError, Transcript
from ..dictation.clients import SOURCE, Client
from ..gate import ModeError
from ..packs.registry import PackError
from .chat import ChatBody, chat_request
from .common import ApiError, caller, error_event, failure, refuse_when_stopping, services
from .modes import settled_by_you

router = APIRouter(prefix="/v1/dictation", tags=["dictation"])

BUSY = "Clara is already answering in this conversation: follow it, or stop it."


async def _gone(request: Request) -> None:
    """Returns when the client has left: once the body is read, the server's next message on the request is its disconnection."""
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _transcript(request: Request, language: str | None) -> Transcript:
    kind = request.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
    if not (kind.startswith("audio/") or kind == "application/octet-stream"):
        raise ApiError(
            415, "Send the audio as the body, with the type audio/* or application/octet-stream.", "bad_media_type"
        )
    if language is not None:
        language = "" if language.lower() == "auto" else language.lower()
        if language and not LANGUAGE.fullmatch(language):
            raise ApiError(422, "language: two or three letters such as fr or en, or auto.", "bad_language")
    try:
        return await services(request).dictation.transcribe(request.stream(), language, lambda: _gone(request))
    except ClientDisconnect:
        raise ApiError(499, "The client left while sending the audio.", "client_gone") from None
    except DictationError as error:
        raise ApiError(
            error.status, str(error), error.code, {"Retry-After": "5"} if error.status == 429 else None
        ) from None


@router.post("/transcribe")
async def transcribe(request: Request, language: str | None = Query(default=None, max_length=8)) -> dict:
    found = await _transcript(request, language)
    return {"text": found.text, "seconds": found.seconds}


@router.post("")
async def dictate(
    request: Request,
    client: Client,
    conversation: str | None = Query(default=None, pattern=r"^c[0-9a-f]{16}$"),
    language: str | None = Query(default=None, max_length=8),
) -> dict:
    refuse_when_stopping(request)
    s = services(request)
    settings = s.dictation.clients.get(client)
    target = conversation
    if (
        conversation is not None
    ):  # refused before the audio is transcribed: the words are not lost on a mistake of address
        if s.conversations.get(conversation) is None:
            raise ApiError(404, f"No conversation {conversation}.")
        if s.mirror.is_archived(conversation):
            raise ApiError(409, "This conversation is archived: unarchive it to write in it.")
    else:
        target = s.dictation.clients.current(client)
    if settings["auto_send"] and target is not None and s.agent.runs.of(target) is not None:
        raise ApiError(409, BUSY)

    found = await _transcript(request, language)
    text = found.text.strip()
    answer = {"text": text, "seconds": found.seconds, "conversation": target, "sent": False}
    if not text:
        return answer  # silence: nothing is made, tainted or sent
    if target is None:
        target = s.dictation.clients.make_conversation(client).id  # titled, tainted and current
    else:
        s.conversations.taint(target, SOURCE)  # for good: nothing removes a row of `conversation_taint`
    answer["conversation"] = target
    if not settings["auto_send"]:
        return answer

    # the request is a typed message's, with the client's name as its label
    chat = dataclasses.replace(
        chat_request(ChatBody(message=text, conversation=target, keep_running=True), request, wait=True), client=client
    )
    try:
        plan = s.agent.plan(chat)  # what fails before the run is a plain HTTP error
    except Exception as error:
        raise failure(error, "chat") from None
    try:
        s.agent.runs.start(chat, plan, lambda error: error_event(error, "chat"))
    except AlreadyRunning:
        raise ApiError(409, BUSY) from None
    return {**answer, "sent": True, "running": True}


# ----------------------------------------------------------------------
# What each client keeps
# ----------------------------------------------------------------------
@router.get("/clients")
def clients(request: Request) -> dict:
    return {"clients": services(request).dictation.clients.listing()}


@router.get("/clients/{client}")
def one(client: Client, request: Request) -> dict:
    return services(request).dictation.clients.get(client)


class ClientChange(BaseModel):
    auto_send: bool | None = None
    mode: str | None = Field(default=None, max_length=30, description="A mode's name; null: the default mode")
    packs: list[str] | None = Field(
        default=None, max_length=30, description="The packs of a new dictation conversation"
    )


@router.put("/clients/{client}")
def change(client: Client, body: ClientChange, request: Request) -> dict:
    settled_by_you(
        request,
        "This token may not change dictation settings: automatic sending is an answer given in advance, and the mode "
        "and packs decide what runs without your answer.",
    )
    s = services(request)
    try:
        mode = s.modes.check_mode(body.mode)
        packs = s.packs.check(body.packs, "chat") if body.packs is not None else None
    except (ModeError, PackError) as error:
        raise ApiError(422, str(error)) from None
    done = s.dictation.clients.change(
        client, auto_send=body.auto_send, mode=mode, packs=packs, set_mode="mode" in body.model_fields_set
    )
    s.modes.log("dictation", f"{caller(request).name} changed the dictation settings of {client}: {done}")
    return done


@router.post("/clients/{client}/conversation", status_code=201)
def new_conversation(client: Client, request: Request) -> dict:
    made = services(request).dictation.clients.make_conversation(client)
    return {"conversation": made.id, "title": made.title}
