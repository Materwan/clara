"""Tools the model can call, and the one door they all go through.

A tool is a function `(context, **arguments) -> str` (or `async def`) plus a `describe(context, arguments) -> Action`
that says, before anything runs, what the call would touch. `Toolbox.call` asks the gate about that action:

    allow   it runs, and the audit log says how it ended
    refuse  it does not run; the model is told, and told not to work around it
    ask     an approval request is made; the turn either waits for your answer (a chat) or goes on without it
            (unattended), and the model is told it was NOT done

A tool that was not offered in this turn is not run, whatever the model asks. Whatever goes wrong inside a tool comes
back as text for the model to read, never as a crash of the turn.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ..gate.actions import Action, Situation
from ..gate.engine import Audit, Gate, args_digest

if TYPE_CHECKING:
    from ..gate.approvals import Approval, Approvals
    from ..gate.grants import Grants
    from ..knowledge.attachments import Image
    from ..services import Services

log = logging.getLogger(__name__)


class ToolError(ValueError):
    """A problem the model should read and can fix (a bad argument, a missing file)."""


@dataclass
class TurnState:
    """What every tool call of one turn shares."""

    turn_id: str
    conversation_id: str
    client: str
    mode: str = "interactive"  # or unattended
    waits_for_approvals: bool = False  # the turn holds the requests it asks for until they are answered
    timezone: str = "UTC"
    project_id: int | None = None
    incognito: bool = False
    local_only: bool = False
    tainted: bool = False
    read_chars: int = 20_000
    trusted_urls: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    pictures: list[Image] = field(default_factory=list)
    files_written: list[dict] = field(default_factory=list)  # workspace notes changed this turn (chat cards)
    stop_reason: str = ""  # who cut the turn short, for the note stored with what it had said ("" : the client left)

    @property
    def situation(self) -> Situation:
        return Situation(self.mode, self.tainted, self.local_only)


@dataclass
class ToolContext:
    services: Services
    turn: TurnState
    events: list[dict] = field(default_factory=list)  # for the client, sent after the call's result (a form, a card)


Describe = Callable[[ToolContext, dict], Action]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    function: Callable[..., str | Awaitable[str]]
    describe: Describe
    parameters: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    parallel: bool = False  # only reads: several calls of such tools in one round run at the same time
    taints: bool = False  # it brings untrusted content into the turn (a web page, an issue, a fetched file)

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.parameters, "required": list(self.required)},
            },
        }


def fixed(
    klass: str, level: str, target: str | Callable[[ToolContext, dict], str], summary: str | Callable[[dict], str]
) -> Describe:
    """A describe function for tools whose class and level do not depend on their arguments."""

    def describe(context: ToolContext, arguments: dict) -> Action:
        where = target(context, arguments) if callable(target) else target
        said = summary(arguments) if callable(summary) else summary
        return Action("", level, klass, (where,) if where else (), said)

    return describe


@dataclass(frozen=True)
class CallResult:
    text: str
    approval: Approval | None = None  # an approval request was made: the action did NOT run
    ran: bool = False
    grant: int = 0  # an Approve all next grant allowed it (its number)


REFUSED = (
    "Refused: {reason}. Do not try to do this another way; if you need it, tell the person what and why, and they "
    "can change their rules."
)


class Toolbox:
    def __init__(self, tools: list[Tool], gate: Gate, audit: Audit):
        self._tools = {tool.name: tool for tool in tools}
        self.gate = gate
        self.audit = audit
        self.approvals: Approvals | None = None  # set once built (it runs approved calls through this toolbox)
        self.grants: Grants | None = None  # the Approve all next buttons that are on

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"A tool named {tool.name} exists already.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def parallel(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.parallel

    def schemas(self, names: set[str] | frozenset[str]) -> list[dict]:
        return [tool.schema for name, tool in self._tools.items() if name in names]

    def action(self, name: str, context: ToolContext, arguments: dict) -> Action:
        """What a call would do, named after its tool whatever the describe function said."""
        return replace(self._tools[name].describe(context, arguments), tool=name)

    async def call(
        self, name: str, context: ToolContext, arguments: dict, offered: frozenset[str] | set[str]
    ) -> CallResult:
        """One call of the model: described, decided by the gate, then run, refused, or held for your answer."""
        tool = self._tools.get(name)
        if tool is None or name not in offered:
            return CallResult(f"Unknown tool: {name}.")
        if not isinstance(arguments, dict):
            return CallResult("Error: the arguments must be an object.")
        try:
            action = self.action(name, context, arguments)
        except (ToolError, ValueError, TypeError) as error:
            return CallResult(f"Error: {error}")
        situation = context.turn.situation
        decision = self.gate.decide(action, situation)
        digest = args_digest(arguments)
        common = {"conversation_id": context.turn.conversation_id, "turn_id": context.turn.turn_id}
        if decision.effect == "refuse":
            self.audit.record(action, situation, digest, decision, "floor" if decision.floor else "refused", **common)
            return CallResult(
                REFUSED.format(
                    reason=decision.reason if decision.floor else f"your rules refuse this ({decision.reason})"
                )
            )
        granted = None
        detail = ""
        if (
            decision.effect == "ask"
            and self.grants is not None
            and not self.gate.tainted_raise(action, situation)
            and not self.gate.secret_raise(action)
        ):
            granted = self.grants.cover(action, situation, context.turn.conversation_id, context.turn.client)
            if granted is not None:
                decision, detail = self.grants.decision(granted), self.grants.detail(granted)
        if decision.effect == "ask" and granted is None:
            if self.approvals is None:
                self.audit.record(
                    action, situation, digest, decision, "refused", detail="no approval service", **common
                )
                return CallResult(REFUSED.format(reason="it needs your approval and approvals are not available"))
            approval = self.approvals.create(action, situation, context, arguments, decision)
            self.audit.record(action, situation, digest, decision, "asked", approval_id=approval.id, **common)
            return CallResult(self.approvals.waiting_text(approval), approval=approval)
        text, ok = await self.run(tool, context, arguments)
        self.audit.record(action, situation, digest, decision, "ran" if ok else "failed", detail=detail, **common)
        if ok and tool.taints:
            context.turn.tainted = True
        return CallResult(text, ran=True, grant=granted.id if granted else 0)

    async def execute(self, name: str, context: ToolContext, arguments: dict) -> tuple[str, bool]:
        """Run a call you approved, exactly as it was asked. The floor is checked again (it never bends)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: the tool {name} no longer exists.", False
        try:
            action = self.action(name, context, arguments)
        except (ToolError, ValueError, TypeError) as error:
            return f"Error: {error}", False
        decision = self.gate.decide(action, context.turn.situation, count_hit=False)
        if decision.floor:
            return REFUSED.format(reason=decision.reason), False
        text, ok = await self.run(tool, context, arguments)
        if ok and tool.taints:
            context.turn.tainted = True
        return text, ok

    @staticmethod
    async def run(tool: Tool, context: ToolContext, arguments: dict) -> tuple[str, bool]:
        try:
            if inspect.iscoroutinefunction(tool.function):
                return await tool.function(context, **arguments), True
            result = tool.function(context, **arguments)
            if inspect.isawaitable(result):
                result = await result
            return str(result), True
        except (ToolError, ValueError, TypeError) as error:
            return f"Error: {error}", False
        except Exception as error:  # a crash is text for the model, and a line in the server log
            log.exception("tool %s crashed", tool.name)
            return f"Error: the tool failed ({type(error).__name__}).", False


# ----------------------------------------------------------------------
# Small helpers for tools
# ----------------------------------------------------------------------
def whole_number(value: Any, problem: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(problem) from None


def optional_number(value: Any, name: str) -> int | None:
    return None if value is None or value == "" else whole_number(value, f"{name} must be a whole number.")


def flag(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def texts(value: Any, problem: str = "expected a list of texts") -> list[str]:
    """The model may send a list, a single text or a comma-separated one."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ToolError(problem)
