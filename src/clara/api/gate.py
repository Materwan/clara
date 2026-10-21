"""Rules, approval requests and the audit log. Only clients reach these routes: the model has no tool for them."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..gate import ANSWERS, CLASSES, AlreadyAnswered, ApprovalError, NoRuleError, RuleError, Situation
from ..tools.base import ToolContext, ToolError, TurnState
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1", tags=["gate"])

Effect = Literal["allow", "ask", "refuse"]
SituationName = Literal["", "interactive", "unattended", "tainted"]
Level = Literal["", "read", "write", "destructive"]


@router.get("/rules")
def rules(request: Request) -> dict:
    s = services(request)
    return {
        "rules": [rule.describe() for rule in s.rules.list()],
        "defaults": [
            {"class": name, "interactive": chat, "unattended": alone, "description": what}
            for name, (chat, alone, what) in CLASSES.items()
        ],
        "tainted_rule": s.runtime.get("tainted_rule"),
        "tools": sorted(s.toolbox.names),
    }


class NewRule(BaseModel):
    effect: Effect
    tool: str = Field(default="", max_length=100)
    target: str = Field(default="", max_length=400)
    situation: SituationName = ""
    level: Level = ""
    note: str = Field(default="", max_length=200)


@router.post("/rules", status_code=201)
def add_rule(body: NewRule, request: Request) -> dict:
    try:
        return (
            services(request)
            .rules.add(
                body.effect,
                tool=body.tool,
                target=body.target,
                situation=body.situation,
                level=body.level,
                note=body.note,
            )
            .describe()
        )
    except RuleError as error:
        raise ApiError(422, str(error)) from None


class RuleChange(BaseModel):
    effect: Effect | None = None
    tool: str | None = Field(default=None, max_length=100)
    target: str | None = Field(default=None, max_length=400)
    situation: SituationName | None = None
    level: Level | None = None
    note: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


@router.patch("/rules/{rule_id}")
def change_rule(rule_id: int, body: RuleChange, request: Request) -> dict:
    try:
        return services(request).rules.update(rule_id, **body.model_dump(exclude_unset=True)).describe()
    except RuleError as error:
        raise ApiError(404 if "No rule" in str(error) else 422, str(error)) from None


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request) -> dict:
    if not services(request).rules.delete(rule_id):
        raise ApiError(404, f"No rule {rule_id}.")
    return {"deleted": True}


class TaintedRule(BaseModel):
    on: bool


@router.put("/rules/tainted")
def tainted_rule(body: TaintedRule, request: Request) -> dict:
    return {"tainted_rule": services(request).runtime.set("tainted_rule", body.on)}


class RuleTest(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)
    situation: Literal["interactive", "unattended"] = "interactive"
    tainted: bool = False
    local_only: bool = False


@router.post("/rules/test")
def test_rule(body: RuleTest, request: Request) -> dict:
    """The test box: which rule (or default) would decide this call, without running it."""
    s = services(request)
    if s.toolbox.get(body.tool) is None:
        raise ApiError(404, f"No tool {body.tool}: {', '.join(sorted(s.toolbox.names))}.")
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
    explained = s.gate.explain(action, Situation(body.situation, body.tainted, body.local_only))
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


@router.get("/approvals")
def approvals(
    request: Request,
    status: Literal["pending", "approved", "denied", "expired", "done", "failed", ""] = "",
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    found = services(request).approvals.list(status or None, limit)
    who = caller(request)
    return {"approvals": [{**item.describe(), "may_answer": who.token.may_answer(item.level)} for item in found]}


class Decision(BaseModel):
    answer: Literal[ANSWERS]  # type: ignore[valid-type]


@router.post("/approvals/{approval_id}/decide")
async def decide(approval_id: int, body: Decision, request: Request) -> dict:
    try:
        approval = await services(request).approvals.decide(approval_id, body.answer, caller(request).token)
    except AlreadyAnswered as error:
        raise ApiError(409, str(error)) from None
    except NoRuleError as error:
        raise ApiError(400, str(error)) from None
    except ApprovalError as error:
        raise ApiError(403 if "may not answer" in str(error) else 404, str(error)) from None
    return approval.describe()


@router.get("/audit")
def audit(request: Request, limit: int = Query(200, ge=1, le=1000), before: int | None = None) -> dict:
    return {"entries": services(request).audit.recent(limit, before)}
