"""The commands you can type in a chat, in one list (docs/DECISIONS.md, « Command display »).

GET /v1/commands?project=<id>|none   every command of a conversation in that project (`none`: outside any project), one row each:
                                     `name`, `kind` (`skill` or `builtin`), `argument_hint`, `description`; a skill row also says
                                     where it is kept (`scope` `global` or `projects`, and its `projects`), as the Skills page does

The web box reads this list to complete a command, to draw it and to show what it expects. A command added later gets its row here.
A skill stored under a built-in's name is left out: the built-in is the one that runs (`goal` is reserved, so none can be added now).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..agent.goals import SYNTAX as GOAL_SYNTAX
from .common import services

router = APIRouter(prefix="/v1", tags=["commands"])

BUILTIN = [
    {
        "name": "goal",
        "kind": "builtin",
        "argument_hint": GOAL_SYNTAX,
        "description": "Keep working until a condition is met",
    },
]


@router.get("/commands")
def listing(
    request: Request,
    project: str = Query(
        pattern=r"^(none|\d+)$", description="A project's id, or `none`: a conversation outside any project"
    ),
) -> dict:
    taken = {row["name"] for row in BUILTIN}
    skills = services(request).skills.listing(project=None if project == "none" else int(project))
    rows = [
        {
            "name": s["name"],
            "kind": "skill",
            "argument_hint": s["argument_hint"],
            "description": s["description"],
            "scope": s["scope"],
            "projects": s["projects"],
        }
        for s in skills
        if s["name"] not in taken
    ]
    return {"commands": sorted(BUILTIN + rows, key=lambda row: row["name"])}
