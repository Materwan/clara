"""Skills: instructions you add and run from a chat command. See skills/ and DECISIONS.md, « Skills (2026-11-13) ».

POST   /v1/skills/preview            {skill_md | zip_base64, name?}: what would be added, with every problem, storing nothing
POST   /v1/skills                    {skill_md | zip_base64, name?, scope?, replace?}: add one (read again, never trusted from a preview)
GET    /v1/skills                    ?project=<id>|none: the skills of a conversation in that project (the global ones and the ones
                                     reserved for it); with no `project`, every skill with the `projects` it is reserved for (the page)
GET    /v1/skills/{name}             one skill: its SKILL.md and the list of its other files (never their bytes); `?project=` as above
GET    /v1/skills/{name}/status      ?conversation=<id>: the packs it needs, each on or off in that conversation (the chips of the menu)
DELETE /v1/skills/{name}             remove it

A command in a chat (`/name arguments`) is expanded by the chat route (`api/chat.py`), so every client gets it. Only a token that
may answer every request (approval scope `all`: the web site, a terminal, the console) previews, adds, replaces or removes a skill:
the text of a skill steers Clara like your own message. A script (`none`) and the Discord bot (`safe`) cannot, and the model has no
tool for skills.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..packs.registry import BY_ID, DEFAULT
from ..skills import MAX_SKILLS, Parsed, SkillError, read_skill
from ..skills.parse import MAX_PROJECTS, MAX_ZIP
from .common import ApiError, caller, services
from .modes import settled_by_you

router = APIRouter(prefix="/v1", tags=["skills"])

BY_YOU = "This token may not add, replace or remove skills: their text steers Clara like your own message."
ProjectFilter = Query(
    default=None, pattern=r"^(none|\d+)$", description="A project's id, or `none`: a conversation outside any project"
)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_md: str | None = Field(default=None, max_length=2_000_000, description="The text of a SKILL.md")
    zip_base64: str | None = Field(
        default=None, max_length=(MAX_ZIP * 4) // 3 + 8, description="A .zip of the skill's folder, in base64"
    )
    name: str | None = Field(default=None, max_length=80, description="The name, when the frontmatter has none")


class ProjectScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projects: list[int] = Field(min_length=1, max_length=MAX_PROJECTS)


class NewSkill(Source):
    scope: Literal["global"] | ProjectScope = Field(
        default="global", description='"global", or {"projects": [ids]}: it does not exist in any other conversation'
    )
    replace: bool = Field(
        default=False, description="Replace the skill of this name (a skill never changes: it is added again)"
    )


def _problem(error: SkillError) -> ApiError:
    return ApiError(error.status, str(error))


def _project(value: str | None) -> tuple[bool, int | None]:
    """(everything, project) of a `project` query: absent is every skill, `none` a conversation outside any project."""
    return (True, None) if value is None else (False, None if value == "none" else int(value))


def _preview(request: Request, parsed: Parsed) -> dict:
    skills = services(request).skills
    replaces = skills.exists(parsed.name)
    errors = list(parsed.errors)
    if parsed.name and not replaces and skills.count() >= MAX_SKILLS:
        errors.append(f"{MAX_SKILLS} skills are held: remove one before adding another.")
    return {
        "name": parsed.name,
        "description": parsed.description,
        "argument_hint": parsed.argument_hint,
        "packs": [
            {"id": pack, "label": BY_ID[pack].label if pack in BY_ID else pack, "known": pack in BY_ID}
            for pack in parsed.packs
        ],
        "disable_model_invocation": parsed.disable_model_invocation,
        "ignored": parsed.ignored,
        "files": [
            {"path": item.path, "size": item.size, "kind": item.kind, "note": item.note, "text": item.text}
            for item in parsed.files
        ],
        "size": parsed.size,
        "warnings": parsed.warnings,
        "errors": errors,
        "can_add": not errors,
        "skill_md_text": parsed.skill_md,
        "would_replace": replaces,
        "skills_held": skills.count(),
        "skills_max": MAX_SKILLS,
    }


@router.post("/skills/preview")
def preview(body: Source, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    return _preview(request, read_skill(skill_md=body.skill_md, zip_base64=body.zip_base64, name=body.name))


@router.post("/skills", status_code=201)
def add(body: NewSkill, request: Request, response: Response) -> dict:
    settled_by_you(request, BY_YOU)
    skills = services(request).skills
    parsed = read_skill(skill_md=body.skill_md, zip_base64=body.zip_base64, name=body.name)
    replaces = skills.exists(parsed.name)
    try:
        found = skills.add(
            parsed,
            scope="global" if body.scope == "global" else "projects",
            projects=[] if body.scope == "global" else body.scope.projects,
            replace=body.replace,
            by=caller(request).name,
        )
    except SkillError as error:
        raise _problem(error) from None
    if replaces:
        response.status_code = 200
    return {**found, "replaced": replaces, "warnings": parsed.warnings, "ignored": parsed.ignored}


@router.get("/skills")
def listing(request: Request, project: str | None = ProjectFilter) -> dict:
    everything, found = _project(project)
    return {"skills": services(request).skills.listing(everything=everything, project=found)}


def _skill(request: Request, name: str, project: str | None) -> dict:
    everything, found = _project(project)
    skill = services(request).skills.get(name, project=found, everything=everything)
    if skill is None:
        raise ApiError(404, f"No skill {name[:60]!r}.")
    return skill


@router.get("/skills/{name}")
def one(name: str, request: Request, project: str | None = ProjectFilter) -> dict:
    return _skill(request, name, project)


@router.get("/skills/{name}/status")
def status(
    name: str,
    request: Request,
    conversation: str | None = Query(default=None, pattern=r"^c[0-9a-f]{16}$"),
    project: str | None = ProjectFilter,
) -> dict:
    """The packs a skill needs and whether each is on in a conversation, for the chips beside the command. Without a conversation
    (one not started yet, in `project` or outside any) the packs that a new conversation starts with are on. A skill that is not
    visible to the conversation's project is not found, as it is not in its menu. The packs of the conversation's mode are counted as on: they are added at its
    next turn."""
    s = services(request)
    found = s.conversations.get(conversation) if conversation else None
    if conversation and found is None:
        raise ApiError(404, f"No conversation {conversation}.")
    where = (project or "none") if found is None else str(found.project_id or "none")
    skill = _skill(request, name, where)
    on = set(DEFAULT)
    if found is not None and s.packs is not None:
        mode = s.modes.get(s.modes.resolve(found.id))
        on = set(s.packs.active(found)) | set(mode.packs if mode else ())
    return {
        "name": name,
        "conversation": conversation,
        "packs": [
            {
                "id": pack,
                "label": BY_ID[pack].label,
                "on": pack in on,
                "available": s.packs is None or not s.packs.unavailable(BY_ID[pack]),
                "reason": "" if s.packs is None else s.packs.unavailable(BY_ID[pack]),
            }
            for pack in skill["packs"]
            if pack in BY_ID
        ],
    }


@router.delete("/skills/{name}")
def remove(name: str, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    if not services(request).skills.remove(name, caller(request).name):
        raise ApiError(404, f"No skill {name[:60]!r}.")
    return {"removed": True}
