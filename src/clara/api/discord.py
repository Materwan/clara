"""The Discord mirror, for the Discord bot (its own token, client `discord`) and for the web site's Admin page.

GET  /v1/discord/sync?since=N        the places, the conversations changed after N, the threads to remove (bot)
POST /v1/discord/report              what the bot made and how the threads are (bot)
GET  /v1/discord/live                the answers being written in the mirrored conversations, as they come (bot, SSE)
POST /v1/discord/event               what Discord changed: an archived thread, a message edited, a thread or forum deleted (bot)
POST /v1/admin/discord/resume        start the mirror again after it stopped at a limit (web site)
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..agent.turn import ChatRequest
from ..mirror.service import INCOGNITO, MAIN
from ..tools.discord_tools import scope_of
from .common import ApiError, caller, event_stream, refuse_when_stopping, services, sse, with_keepalive

router = APIRouter(prefix="/v1", tags=["discord"])


def _bot(request: Request) -> None:
    if caller(request).client != "discord":
        raise ApiError(403, "Only the Discord bot reads the mirror.")


@router.get("/discord/sync")
def sync(request: Request, since: int = Query(0, ge=0)) -> dict:
    _bot(request)
    return services(request).mirror.sync(since)


class Report(BaseModel):
    places: list[dict] = Field(default_factory=list, max_length=100)
    dropped: list[str] = Field(default_factory=list, max_length=100)
    threads: list[dict] = Field(default_factory=list, max_length=500)
    posts: list[dict] = Field(default_factory=list, max_length=2000)
    cleared: list[str] = Field(default_factory=list, max_length=500)
    stopped: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=500)


@router.post("/discord/report")
def report(body: Report, request: Request) -> dict:
    _bot(request)
    payload = body.model_dump(exclude_unset=True)
    services(request).mirror.report(payload)
    return {"ok": True}


@router.get("/discord/live")
async def live(request: Request):
    _bot(request)
    events = services(request).mirror.live.listen()

    async def lines():
        async for event in with_keepalive(events):
            yield ": keepalive\n\n" if event is None else sse(event)

    return event_stream(lines())


class DiscordEvent(BaseModel):
    kind: Literal["thread_archived", "thread_deleted", "forum_deleted", "edit"]
    thread: str = Field(default="", max_length=40)
    archived: bool = False
    place: str = Field(default="", max_length=60)
    message: str = Field(default="", max_length=40)
    text: str = Field(default="", max_length=2_000)


@router.post("/discord/event")
async def event(body: DiscordEvent, request: Request) -> dict:
    _bot(request)
    s = services(request)
    if body.kind == "thread_archived":
        return {"conversation": s.mirror.thread_archived(body.thread, body.archived)}
    if body.kind == "thread_deleted":
        conversation = s.mirror.conversation_of_thread(body.thread)
        if conversation is None:
            return {"asked": None}
        title = s.conversations.require(conversation).title or "without a title"
        approval = s.approvals.ask_outside(
            "discord_forget",
            {"kind": "conversation", "target": conversation, "summary": f"Delete the conversation “{title}”"},
            f"conversation:{conversation}",
            f"Delete the conversation “{title}”: its thread was deleted on Discord.",
            conversation_id=conversation,
        )
        return {"asked": approval.id}
    if body.kind == "forum_deleted":
        ask = _forum_deleted(s, body.place)
        return {"asked": ask.id if ask else None}
    return await _edited(request, body)


def _forum_deleted(s, place: str):
    """A forum was deleted on Discord: you are asked whether to delete what it held, with its conversations (a project, a
    schedule or a hook goes too). None for a place this server does not know."""
    if place == MAIN:
        kind, target, name = "loose", "", "the conversations outside any project, schedule or hook"
    elif place == INCOGNITO:
        kind, target, name = "incognito", "", "the incognito conversations"
    elif ":" in place and place.split(":", 1)[0] in ("project", "schedule", "hook"):
        kind, target = place.split(":", 1)
        name = {"project": "the project", "schedule": "the schedule", "hook": "the hook"}[kind] + f" {target}"
        if kind == "project" and s.projects.get(int(target)) is not None:
            name = f"the project “{s.projects.get(int(target)).name}”"
    else:
        return None
    where, params = scope_of(kind, target)
    count = s.db.value(f"SELECT COUNT(*) FROM conversations WHERE {where}", params, 0)
    remembered = s.facts.count_live(f"{kind}:{target}") if kind in ("project", "schedule", "hook") else 0
    what = f"{count} conversations" + (f" and its {remembered} remembered facts" if remembered else "")
    summary = f"Delete {name} and its {what}: their forum was deleted on Discord."
    return s.approvals.ask_outside(
        "discord_forget",
        {"kind": kind, "target": target, "summary": summary},
        f"{kind}:{target}",
        summary,
    )


async def _edited(request: Request, body: DiscordEvent) -> dict:
    """A message of yours in a mirrored thread was edited on Discord: when it is your last question, Clara answers it
    again, as the web site does when a question is edited. Only the last one can be changed."""
    refuse_when_stopping(request)
    s = services(request)
    found = s.mirror.conversation_of_ref(body.message)
    if found is None:
        raise ApiError(409, "Only your last question can be changed here.")
    conversation, question = found
    if s.agent.answering(conversation):
        raise ApiError(409, "Clara is answering in this conversation: wait for the end of the answer.")
    if s.mirror.is_archived(conversation):
        raise ApiError(409, "This conversation is archived: unarchive it to change it.")
    s.agent.start_turn(
        ChatRequest(
            message=body.text,
            client="discord",
            conversation=conversation,
            fork_of=question,
            ref=body.message,
            wait_for_approvals=False,
        )
    )
    return {"conversation": conversation}


@router.post("/admin/discord/resume")
def resume(request: Request) -> dict:
    services(request).mirror.resume()
    return {"ok": True}
