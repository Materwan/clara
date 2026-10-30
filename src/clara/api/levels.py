"""Auto levels: the ladder, the thresholds of every tool, the defaults, and the level of a conversation, a project, a schedule
or a hook. See gate/levels.py and DECISIONS.md, « Auto levels ».

GET    /v1/levels                          the ladder, the defaults and every tool's thresholds
POST   /v1/levels                          {name, before?}: add a level just below `before` (default: just below the last)
PATCH  /v1/levels/{position}               {name}: rename a level between the first and the last
DELETE /v1/levels/{position}               remove one; what was set to it moves to the level below
PUT    /v1/levels/defaults                 {chat?, unattended?}: the level a conversation has when nothing else says
PUT    /v1/levels/tools/{tool}             {ask_from, approve_from, class?}: a tool's thresholds, for one of its action classes
                                           or (no class) for all of them
DELETE /v1/levels/tools/{tool}             back to the default of the tool's action class (?class=: for that class only)
GET    /v1/conversations/{id}/level        the level it runs at, and where that comes from
PUT    /v1/conversations/{id}/level        {level}: its own level (null: follow)
PUT    /v1/projects/{id}/level             {level}: the level of the conversations that follow the project (null: the default)
PUT    /v1/schedules/{id}/level            {level}: the level of its runs (null: its project's, else the unattended default)
PUT    /v1/triggers/{id}/level             the same for a hook
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..gate import LevelError
from ..gate.levels import MAX_NAME, MissingRow
from ..memory.conversations import ConversationError
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1", tags=["levels"])


def settled_by_you(request: Request) -> None:
    """Who may change levels: a token that may answer every request. A level lets calls run without a card, so changing it is
    answering in advance: a script (scope `none`) never may, and the Discord bot (`safe`) may only lower a conversation's
    level, never raise it (see `may_set`)."""
    if caller(request).token.approvals != "all":
        raise ApiError(403, "This token may not change the auto levels: they decide what runs without your answer.")


def may_set(request: Request, conversation_id: str | None, level: int | None, mode: str = "interactive") -> None:
    """A token that may not answer everything (the Discord bot) may only set a conversation's level at or below the one it
    would have otherwise (what it has now, or what it would follow); a script token may not set one at all."""
    approvals = caller(request).token.approvals
    if approvals == "all":
        return
    if approvals == "none":
        raise ApiError(403, "This token may not set an auto level.")
    s = services(request)
    current = s.levels.where(conversation_id, mode)[0] if conversation_id else s.levels.default_level(mode)
    new = (
        level
        if level is not None
        else (s.levels.where(conversation_id, mode, own=False)[0] if conversation_id else s.levels.default_level(mode))
    )
    if new > current:
        raise ApiError(
            403, "This token may lower an auto level, not raise it: raise it from the web site or the console."
        )


def _state(request: Request) -> dict:
    s = services(request)
    levels = s.levels
    tools = []
    for name in sorted(s.toolbox.names):
        tool = s.toolbox.get(name)
        description = " ".join(tool.description.split())[:200]
        for klass in tool.classes:  # one row for each class: a script with network and one without are set apart
            default = levels.default_thresholds(klass)
            ask, approve = levels.thresholds(name, klass)
            tools.append(
                {
                    "name": name,
                    "description": description,
                    "class": klass,
                    "classes": list(tool.classes),
                    "ask_from": ask,
                    "approve_from": approve,
                    "default_ask_from": default[0],
                    "default_approve_from": default[1],
                    "changed": (ask, approve) != default,
                }
            )
    return {
        "levels": [level.describe() for level in levels.ladder()],
        "top": levels.top,
        "defaults": {"chat": levels.default_level("interactive"), "unattended": levels.default_level("unattended")},
        "classes": levels.class_defaults(),
        "tools": tools,
    }


def _failed(error: Exception, status: int = 422) -> ApiError:
    return ApiError(status, str(error))


@router.get("/levels")
def levels(request: Request) -> dict:
    return _state(request)


class NewLevel(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    before: int | None = Field(default=None, ge=1, le=9)


@router.post("/levels", status_code=201)
def add_level(body: NewLevel, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        added = s.levels.add(body.name, body.before)
    except LevelError as error:
        raise _failed(error) from None
    s.levels.log("ladder", f"{caller(request).name} added level {added.position} ({added.name})")
    return _state(request)


class LevelName(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)


@router.patch("/levels/{position}")
def rename_level(position: int, body: LevelName, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        s.levels.rename(position, body.name)
    except LevelError as error:
        raise _failed(error) from None
    s.levels.log("ladder", f"{caller(request).name} renamed level {position} to {body.name}")
    return _state(request)


@router.delete("/levels/{position}")
def remove_level(position: int, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    name = s.levels.name(position)
    try:
        s.levels.remove(position)
    except LevelError as error:
        raise _failed(error) from None
    s.levels.log("ladder", f"{caller(request).name} removed level {position} ({name})")
    return _state(request)


class Defaults(BaseModel):
    chat: int | None = Field(default=None, ge=0, le=9)
    unattended: int | None = Field(default=None, ge=0, le=9)


@router.put("/levels/defaults")
def set_defaults(body: Defaults, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    wanted = [
        (knob, value)
        for knob, value in (("auto_level_default", body.chat), ("auto_level_unattended", body.unattended))
        if value is not None
    ]
    try:
        for _, value in wanted:  # both are checked before either is stored
            s.levels.check_level(value)
    except LevelError as error:
        raise _failed(error) from None
    for knob, value in wanted:
        s.runtime.set(knob, value)
        s.levels.log("default", f"{caller(request).name} set {knob} to {s.levels.label(value)}")
    return _state(request)


class Thresholds(BaseModel):
    ask_from: int = Field(ge=1, le=9)
    approve_from: int = Field(ge=1, le=9)
    klass: str | None = Field(
        default=None, alias="class", description="One of the tool's action classes; none: all of them"
    )


def _classes_of(request: Request, tool: str, klass: str | None) -> tuple[str, ...]:
    """The action classes of a tool, checking that it exists and has the class asked for."""
    found = services(request).toolbox.get(tool)
    if found is None:
        raise ApiError(404, f"No tool {tool}.")
    if klass is not None and klass not in found.classes:
        raise ApiError(422, f"{tool} has no action class {klass} (it has {', '.join(found.classes)}).")
    return found.classes


@router.put("/levels/tools/{tool}")
def set_tool(tool: str, body: Thresholds, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    classes = _classes_of(request, tool, body.klass)
    pair = (body.ask_from, body.approve_from)
    try:
        if body.klass is not None:
            if pair == s.levels.default_thresholds(body.klass):
                s.levels.reset_tool(tool, body.klass)  # the same as its class: nothing to remember
            else:
                s.levels.set_tool(tool, *pair, body.klass)
        else:
            s.levels.set_tool(tool, *pair)
            if classes and all(s.levels.default_thresholds(klass) == pair for klass in classes):
                s.levels.reset_tool(tool)
    except LevelError as error:
        raise _failed(error) from None
    where = f"{tool} ({body.klass})" if body.klass else tool
    s.levels.log(
        "tool", f"{caller(request).name} set {where} to ask from {body.ask_from}, run from {body.approve_from}"
    )
    return _state(request)


@router.delete("/levels/tools/{tool}")
def reset_tool(tool: str, request: Request, klass: str | None = Query(default=None, alias="class")) -> dict:
    settled_by_you(request)
    s = services(request)
    if s.toolbox.get(tool) is not None:  # a setting left by a tool that is not there (a pack switched off) can still go
        _classes_of(request, tool, klass)
    s.levels.reset_tool(tool, klass)
    where = f"{tool} ({klass})" if klass else tool
    s.levels.log("tool", f"{caller(request).name} reset {where} to the default of its class")
    return _state(request)


class StoredLevel(BaseModel):
    level: int | None = Field(description="A level from 0 to the last one; null: follow", ge=0, le=9)


def _described(request: Request, conversation_id: str) -> dict:
    s = services(request)
    conversation = s.conversations.get(conversation_id)
    if conversation is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    mode = s.levels.mode_of(conversation_id)
    level, source = s.levels.where(conversation_id, mode)
    return {
        "own": conversation.auto_level,
        "level": level,
        "name": s.levels.name(level),
        "source": source,
        "top": s.levels.top,
    }


@router.get("/conversations/{conversation_id}/level")
def conversation_level(conversation_id: str, request: Request) -> dict:
    return _described(request, conversation_id)


@router.put("/conversations/{conversation_id}/level")
def set_conversation_level(conversation_id: str, body: StoredLevel, request: Request) -> dict:
    s = services(request)
    _described(request, conversation_id)
    try:
        level = s.levels.check_level(body.level)
    except LevelError as error:
        raise _failed(error) from None
    may_set(request, conversation_id, level, s.levels.mode_of(conversation_id))
    try:
        s.conversations.update(conversation_id, auto_level=level)
    except (LevelError, ConversationError) as error:
        raise _failed(error) from None
    now = _described(request, conversation_id)
    s.levels.log(
        "conversation", f"{caller(request).name} set the level to {now['level']} ({now['source']})", conversation_id
    )
    return now


def _set(request: Request, table: str, row_id: int, level: int | None, what: str) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        s.levels.set_stored(table, row_id, level)
    except LevelError as error:
        raise ApiError(404 if isinstance(error, MissingRow) else 422, str(error)) from None
    shown = "follow" if level is None else s.levels.label(level)
    s.levels.log(what, f"{caller(request).name} set the level of {what} {row_id} to {shown}")
    return {"level": level, "name": s.levels.name(level) if level is not None else None}


@router.put("/projects/{project_id}/level")
def set_project_level(project_id: int, body: StoredLevel, request: Request) -> dict:
    return _set(request, "projects", project_id, body.level, "project")


@router.put("/schedules/{schedule_id}/level")
def set_schedule_level(schedule_id: int, body: StoredLevel, request: Request) -> dict:
    return _set(request, "schedules", schedule_id, body.level, "schedule")


@router.put("/triggers/{trigger_id}/level")
def set_trigger_level(trigger_id: int, body: StoredLevel, request: Request) -> dict:
    return _set(request, "triggers", trigger_id, body.level, "hook")
