"""Modes: what each conversation may use, and what happens to each tool. See gate/modes.py and DECISIONS.md, « Modes ».

GET    /v1/modes                           the default mode, the modes you made, and what a page needs to edit them (tools, classes, packs)
POST   /v1/modes                           {name, packs?, verdicts?}: make a mode
GET    /v1/modes/{name}                    one mode, with what uses it and the verdict of every tool and class (`default` too)
PUT    /v1/modes/{name}                    {packs?, verdicts?}: replace its packs and/or all its lines
DELETE /v1/modes/{name}                    ?conversations=&automation_conversations=&automations=&projects=: the numbers of what
                                           uses it, as GET gave them (409 with the current ones when they are not); all of it
                                           then moves to the default mode
PUT    /v1/modes/{name}/verdicts           {tool?, class?, target?, verdict, tainted?}: one line, added or replaced
DELETE /v1/modes/{name}/verdicts           ?tool=&class=&target=: remove that line
POST   /v1/modes/test                      {tool, arguments, situation, tainted, local_only, mode?}: what would decide a call
GET    /v1/conversations/{id}/mode         the mode it runs in, and where that comes from
PUT    /v1/conversations/{id}/mode         {mode}: its own mode (null: follow)
PUT    /v1/projects/{id}/mode              {mode}: the mode of the conversations that follow the project (null: the default)
PUT    /v1/schedules/{id}/mode             {mode}: the mode of its runs (null: its project's, else the default)
PUT    /v1/triggers/{id}/mode              the same for a hook

The default mode is a file the server reads at start: no route changes it. Only a token that may answer every request (approval
scope `all`: the web site, a terminal, the console) makes, changes, deletes or sets a mode: a mode lets calls run without a card, so
it is an answer given in advance. A script (`none`) and the Discord bot (`safe`) cannot.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..gate import Action, Line, Mode, ModeError, Situation
from ..gate.actions import CLASSES
from ..gate.modes import MAX_NAME, MissingRow, NeedsConfirmation
from ..tools.base import ToolContext, ToolError, TurnState
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1", tags=["modes"])

Verdict = Literal["approve", "ask", "refuse"]


def settled_by_you(request: Request) -> None:
    """Who may change modes: a token that may answer every request. A mode lets calls run without a card, so changing one is
    answering in advance: a script (scope `none`) and the Discord bot (`safe`) never may."""
    if caller(request).token.approvals != "all":
        raise ApiError(403, "This token may not change modes: they decide what runs without your answer.")


def _failed(error: Exception) -> ApiError:
    return ApiError(404 if isinstance(error, MissingRow) else 422, str(error))


def settable(request: Request, name: str | None) -> str | None:
    """The canonical name of a mode a client starts a conversation with (None: follow), if the token may set one at all."""
    if name is None:
        return None
    settled_by_you(request)
    try:
        return services(request).modes.check_mode(name)
    except ModeError as error:
        raise _failed(error) from None


# ----------------------------------------------------------------------
# What a page shows
# ----------------------------------------------------------------------
def _effective(request: Request, mode: Mode) -> list[dict]:
    """The verdict of every tool and class under this mode (its own line, else the default mode's): what the page lists."""
    s = services(request)
    rows = []
    for name in sorted(s.toolbox.names):
        tool = s.toolbox.get(name)
        for klass in tool.classes:
            named = None if mode is s.modes.default else mode.name
            found, line, verdict = s.modes.verdict(
                Action(name, "read", klass, (), ""), Situation("interactive", mode_name=named)
            )
            rows.append(
                {
                    "tool": name,
                    "class": klass,
                    "verdict": verdict,
                    "tainted": line.tainted if line else "refuse",
                    "line": line.describe() if line else None,
                    "from": "default" if found is s.modes.default and mode is not s.modes.default else "mode",
                }
            )
    return rows


def _described(request: Request, mode: Mode, *, effective: bool = False) -> dict:
    s = services(request)
    out = mode.describe()
    if mode is s.modes.default:
        out |= {
            "default": True,
            "file": str(s.settings.default_mode_file),
            "from_file": "default_mode_file" not in s.alerts.active,
        }
    else:
        out |= {"default": False, "uses": s.modes.uses(mode.name)}
    if effective:
        out["effective"] = _effective(request, mode)
    return out


def _catalog(request: Request) -> dict:
    from ..packs.registry import BY_ID, PACKS

    s = services(request)
    pack_of = {tool: pack.id for pack in PACKS for tool in pack.tools}
    tools = []
    for name in sorted(s.toolbox.names):
        tool = s.toolbox.get(name)
        tools.append(
            {
                "name": name,
                "description": " ".join(tool.description.split())[:200],
                "classes": list(tool.classes),
                "pack": pack_of.get(name),
            }
        )
    return {
        "tools": tools,
        "classes": [{"class": name, "description": what} for name, (_, _, what) in CLASSES.items()],
        "packs": [{"id": pack.id, "label": pack.label, "where": pack.where} for pack in PACKS if pack.id in BY_ID],
    }


@router.get("/modes")
def modes(request: Request) -> dict:
    s = services(request)
    return {
        "default": _described(request, s.modes.default),
        "modes": [_described(request, s.modes.require(name)) for name in s.modes.names()],
        **_catalog(request),
    }


@router.get("/modes/{name}")
def one(name: str, request: Request) -> dict:
    try:
        return _described(request, services(request).modes.require(name), effective=True)
    except ModeError as error:
        raise _failed(error) from None


# ----------------------------------------------------------------------
# Making and changing
# ----------------------------------------------------------------------
class LineBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool: str = Field(default="", max_length=100, description="A tool name or glob ('': every tool of the class)")
    klass: str = Field(
        default="", alias="class", max_length=40, description="An action class ('': every class of the tool)"
    )
    target: str = Field(
        default="", max_length=400, description="A target glob: an exception for one mail address, folder, domain"
    )
    verdict: Verdict
    tainted: Verdict | None = Field(
        default=None, description="The verdict once the conversation read untrusted content"
    )

    def line(self) -> Line:
        return Line(self.verdict, self.tool, self.klass, self.target, self.tainted or "")


def _lines(items: list[LineBody]) -> list[Line]:
    try:
        return [item.line() for item in items]
    except ModeError as error:
        raise _failed(error) from None


class NewMode(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    packs: list[str] = Field(default_factory=list, max_length=30)
    verdicts: list[LineBody] = Field(default_factory=list, max_length=500)


@router.post("/modes", status_code=201)
def create(body: NewMode, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        made = s.modes.create(body.name, body.packs, _lines(body.verdicts))
    except ModeError as error:
        raise _failed(error) from None
    s.modes.log("mode", f"{caller(request).name} made the mode {made.name}")
    return _described(request, made, effective=True)


class ModeChange(BaseModel):
    packs: list[str] | None = Field(default=None, max_length=30)
    verdicts: list[LineBody] | None = Field(default=None, max_length=500)


@router.put("/modes/{name}")
def change(name: str, body: ModeChange, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        made = s.modes.update(name, body.packs, _lines(body.verdicts) if body.verdicts is not None else None)
    except ModeError as error:
        raise _failed(error) from None
    s.modes.log("mode", f"{caller(request).name} changed the mode {made.name}")
    return _described(request, made, effective=True)


@router.put("/modes/{name}/verdicts")
def set_line(name: str, body: LineBody, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        line = body.line()
        made = s.modes.set_line(name, line)
    except ModeError as error:
        raise _failed(error) from None
    s.modes.log("mode", f"{caller(request).name} set {line.text()} in the mode {made.name}")
    return _described(request, made, effective=True)


@router.delete("/modes/{name}/verdicts")
def remove_line(
    name: str,
    request: Request,
    tool: str = Query(default="", max_length=100),
    klass: str = Query(default="", alias="class", max_length=40),
    target: str = Query(default="", max_length=400),
) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        made = s.modes.remove_line(name, tool, klass, target)
    except ModeError as error:
        raise _failed(error) from None
    s.modes.log(
        "mode",
        f"{caller(request).name} removed a line of the mode {made.name} ({tool or '*'} {klass or '*'} {target or '*'})",
    )
    return _described(request, made, effective=True)


@router.delete("/modes/{name}")
def delete(
    name: str,
    request: Request,
    conversations: int | None = Query(default=None, ge=0),
    automation_conversations: int | None = Query(default=None, ge=0),
    automations: int | None = Query(default=None, ge=0),
    projects: int | None = Query(default=None, ge=0),
) -> dict:
    settled_by_you(request)
    s = services(request)
    given = {
        "conversations": conversations,
        "automation_conversations": automation_conversations,
        "automations": automations,
        "projects": projects,
    }
    try:
        moved = s.modes.delete(name, {key: value for key, value in given.items() if value is not None})
    except NeedsConfirmation as error:
        raise ApiError(409, str(error), "needs_confirmation") from None
    except ModeError as error:
        raise _failed(error) from None
    s.modes.log("mode", f"{caller(request).name} deleted the mode {name}: {moved}")
    return {"deleted": True, "moved": moved}


# ----------------------------------------------------------------------
# The tester
# ----------------------------------------------------------------------
class ModeTest(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)
    situation: Literal["interactive", "unattended"] = "interactive"
    tainted: bool = False
    local_only: bool = False
    mode: str | None = Field(default=None, description="A mode's name (default: the default mode)")


@router.post("/modes/test")
def test(body: ModeTest, request: Request) -> dict:
    """The test box: which line would decide this call, and the verdicts it gives, without running it."""
    s = services(request)
    if s.toolbox.get(body.tool) is None:
        raise ApiError(404, f"No tool {body.tool}: {', '.join(sorted(s.toolbox.names))}.")
    try:
        mode_name = None if (body.mode or "").lower() == "default" else s.modes.check_mode(body.mode)
    except ModeError as error:
        raise _failed(error) from None
    state = TurnState(
        turn_id="test",
        conversation_id="",
        client=caller(request).client,
        mode=body.situation,
        timezone=s.settings.timezone,
        tainted=body.tainted,
        local_only=body.local_only,
    )
    try:
        action = s.toolbox.action(body.tool, ToolContext(s, state), body.arguments)
    except (ToolError, ValueError, TypeError) as error:
        raise ApiError(422, f"These arguments do not describe a call: {error}") from None
    explained = s.gate.explain(action, Situation(body.situation, body.tainted, body.local_only, mode_name))
    return {
        "action": {
            "tool": action.tool,
            "level": action.level,
            "class": action.klass,
            "targets": list(action.targets),
            "summary": action.summary,
            "secrets": list(action.secret_kinds),
        },
        **explained,
    }


# ----------------------------------------------------------------------
# The mode of a conversation, a project, a schedule and a hook
# ----------------------------------------------------------------------
class StoredMode(BaseModel):
    mode: str | None = Field(description="A mode's name; null: follow", max_length=MAX_NAME)


def _conversation(request: Request, conversation_id: str) -> dict:
    s = services(request)
    conversation = s.conversations.get(conversation_id)
    if conversation is None:
        raise ApiError(404, f"No conversation {conversation_id}.")
    name, source = s.modes.where(conversation_id)
    follows, follows_source = s.modes.where(conversation_id, own=False)
    return {
        "own": conversation.mode,
        "mode": name,
        "source": source,
        "follows": {"mode": follows, "source": follows_source},
        "packs": list(s.modes.require(name).packs),
    }


@router.get("/conversations/{conversation_id}/mode")
def conversation_mode(conversation_id: str, request: Request) -> dict:
    return _conversation(request, conversation_id)


@router.put("/conversations/{conversation_id}/mode")
def set_conversation_mode(conversation_id: str, body: StoredMode, request: Request) -> dict:
    settled_by_you(request)
    s = services(request)
    _conversation(request, conversation_id)
    try:
        conversation = s.conversations.update(conversation_id, mode=s.modes.check_mode(body.mode))
    except ModeError as error:
        raise _failed(error) from None
    if s.packs is not None:
        s.packs.with_mode(conversation)  # the packs of the new mode are on at once, added to the ones that were
    now = _conversation(request, conversation_id)
    s.modes.log(
        "conversation", f"{caller(request).name} set the mode to {now['mode']} ({now['source']})", conversation_id
    )
    return now


def _set(request: Request, table: str, row_id: int, name: str | None, what: str) -> dict:
    settled_by_you(request)
    s = services(request)
    try:
        s.modes.set_stored(table, row_id, name)
        shown = s.modes.check_mode(name)
    except ModeError as error:
        raise _failed(error) from None
    s.modes.log(what, f"{caller(request).name} set the mode of {what} {row_id} to {shown or 'follow'}")
    return {"mode": shown}


@router.put("/projects/{project_id}/mode")
def set_project_mode(project_id: int, body: StoredMode, request: Request) -> dict:
    return _set(request, "projects", project_id, body.mode, "project")


@router.put("/schedules/{schedule_id}/mode")
def set_schedule_mode(schedule_id: int, body: StoredMode, request: Request) -> dict:
    return _set(request, "schedules", schedule_id, body.mode, "schedule")


@router.put("/triggers/{trigger_id}/mode")
def set_trigger_mode(trigger_id: int, body: StoredMode, request: Request) -> dict:
    return _set(request, "triggers", trigger_id, body.mode, "hook")
