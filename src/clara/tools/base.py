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

from ..gate.actions import FOR_A_CONVERSATION, Action, Decision, Situation
from ..gate.engine import Audit, Gate, args_digest

if TYPE_CHECKING:
    from ..gate.allowances import Allowances
    from ..gate.approvals import Approval, Approvals
    from ..gate.grants import Grants
    from ..knowledge.attachments import Image
    from ..services import Services
    from .folder_tools import FolderGrant

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
    can_ask: bool = False  # the turn can stop to ask the person questions (ask_questions) and wait for the answers
    timezone: str = "UTC"
    project_id: int | None = None
    incognito: bool = False
    local_only: bool = False
    tainted: bool = False
    level: int | None = None  # the auto level of the conversation, read again at each call (Toolbox.call)
    read_chars: int = 20_000
    trusted_urls: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    pictures: list[Image] = field(default_factory=list)
    files_written: list[dict] = field(default_factory=list)  # workspace notes changed this turn (chat cards)
    stop_reason: str = ""  # who cut the turn short, for the note stored with what it had said ("" : the client left)
    folder: FolderGrant | None = None  # the folder open on the person's computer in this web chat (folder tools)

    @property
    def situation(self) -> Situation:
        return Situation(self.mode, self.tainted, self.local_only, self.level)


@dataclass
class ToolContext:
    services: Services
    turn: TurnState
    events: list[dict] = field(default_factory=list)  # for the client, sent after the call's result (a form, a card)
    asks: list = field(default_factory=list)  # questions the call put to the person: the turn waits for their answers


Describe = Callable[[ToolContext, dict], Action]


@dataclass(frozen=True)
class Remote:
    """A tool the person's browser runs. The gate decides as for any tool; then the call goes to the page, and its answer
    comes back to `after`. `before` gives the text to answer with instead (why the call must not be sent), or None.
    `after` returns what the model reads; it raises ToolError when the answer is an error."""

    before: Callable[[ToolContext, dict], str | None]
    after: Callable[[ToolContext, dict, str], str]


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
    remote: Remote | None = None  # run by the person's browser, not here (`function` is then never called)

    @property
    def classes(self) -> tuple[str, ...]:
        """The action classes the tool's calls can have (what its describe function declared; () if it declared none)."""
        return tuple(getattr(self.describe, "classes", ()))

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


def acts_as(*classes: str) -> Callable[[Callable], Callable]:
    """Name the action classes a describe function can answer with, for the Auto mode page (a tool's default thresholds
    are those of its class). The gate itself only ever looks at the class of the call."""

    def mark(function: Callable) -> Callable:
        function.classes = classes  # type: ignore[attr-defined]
        return function

    return mark


def fixed(
    klass: str, level: str, target: str | Callable[[ToolContext, dict], str], summary: str | Callable[[dict], str]
) -> Describe:
    """A describe function for tools whose class and level do not depend on their arguments."""

    def describe(context: ToolContext, arguments: dict) -> Action:
        where = target(context, arguments) if callable(target) else target
        said = summary(arguments) if callable(summary) else summary
        return Action("", level, klass, (where,) if where else (), said)

    describe.classes = (klass,)  # type: ignore[attr-defined]
    return describe


@dataclass(frozen=True)
class Dispatch:
    """A call the page runs: what it was (for the audit), and the call to settle when the page's answer is in."""

    tool: Tool
    context: ToolContext
    arguments: dict
    action: Action
    decision: Decision
    digest: str
    situation: Situation
    detail: str = ""


@dataclass(frozen=True)
class CallResult:
    text: str
    approval: Approval | None = None  # an approval request was made: the action did NOT run
    ran: bool = False
    grant: int = 0  # an Approve all next grant allowed it (its number)
    dispatch: Dispatch | None = None  # allowed, and run by the person's browser: the turn sends it and settles it


REFUSED = (
    "Refused: {reason}. Do not try to do this another way; if you need it, tell the person what and why, and they "
    "can change their rules."
)
REFUSED_BY_LEVEL = (
    "Refused: {reason}. Do not try to do this another way; if you need it, tell the person what and why, and they "
    "can raise the auto level of this conversation."
)


class Toolbox:
    def __init__(self, tools: list[Tool], gate: Gate, audit: Audit):
        self._tools = {tool.name: tool for tool in tools}
        self.gate = gate
        self.audit = audit
        self.approvals: Approvals | None = None  # set once built (it runs approved calls through this toolbox)
        self.grants: Grants | None = None  # the Approve all next buttons that are on
        self.allowances: Allowances | None = None  # « Allow for this conversation » on a card (FOR_A_CONVERSATION)

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

    def is_remote(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.remote is not None

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
        # the level is read at each call: lowering it in the middle of a turn takes effect at the next tool
        context.turn.level = self.gate.levels.resolve(context.turn.conversation_id, context.turn.mode)
        situation = context.turn.situation
        decision = self.gate.decide(action, situation)
        digest = args_digest(arguments)
        common = {"conversation_id": context.turn.conversation_id, "turn_id": context.turn.turn_id}
        if decision.effect == "refuse":
            self.audit.record(action, situation, digest, decision, "floor" if decision.floor else "refused", **common)
            if decision.by_level:
                return CallResult(REFUSED_BY_LEVEL.format(reason=decision.reason))
            return CallResult(
                REFUSED.format(
                    reason=decision.reason if decision.floor else f"your rules refuse this ({decision.reason})"
                )
            )
        granted = None
        detail = ""
        if (
            decision.effect == "ask"
            and self.allowances is not None
            and action.klass in FOR_A_CONVERSATION
            and self.allowances.covers(context.turn.conversation_id, name, action.target)
        ):
            decision, detail = Decision("allow", "allowed for this conversation"), "allowed for this conversation"
        if (
            decision.effect == "ask"
            and self.grants is not None
            and not self.gate.tainted_raise(action, situation)
            and not self.gate.secret_raise(action, situation)
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
        if tool.remote is not None:
            return self._dispatch(tool, context, arguments, action, decision, digest, situation, detail)
        text, ok = await self.run(tool, context, arguments)
        self.audit.record(action, situation, digest, decision, "ran" if ok else "failed", detail=detail, **common)
        if ok and tool.taints:
            context.turn.tainted = True
        return CallResult(text, ran=True, grant=granted.id if granted else 0)

    def dispatch_approved(self, name: str, context: ToolContext, arguments: dict, approval_id: int) -> CallResult:
        """A remote call you allowed on its card: the floor is checked again, then it goes to the page like any other."""
        tool = self._tools[name]
        try:
            action = self.action(name, context, arguments)
        except (ToolError, ValueError, TypeError) as error:
            return CallResult(f"Error: {error}")
        situation = context.turn.situation
        decision = self.gate.decide(action, situation, count_hit=False, approved=True)
        if decision.floor or decision.effect == "refuse":
            return CallResult(REFUSED.format(reason=decision.reason))
        detail = f"allowed on request #{approval_id}"
        return self._dispatch(tool, context, arguments, action, decision, args_digest(arguments), situation, detail)

    def _dispatch(
        self,
        tool: Tool,
        context: ToolContext,
        arguments: dict,
        action: Action,
        decision: Decision,
        digest: str,
        situation: Situation,
        detail: str,
    ) -> CallResult:
        """Allowed and run by the page: the server first says why the call must not be sent, if it must not."""
        common = {"conversation_id": context.turn.conversation_id, "turn_id": context.turn.turn_id}
        try:
            problem = tool.remote.before(context, arguments)
        except (ToolError, ValueError, TypeError) as error:
            problem = f"Error: {error}"
        if problem is not None:
            self.audit.record(action, situation, digest, decision, "failed", detail=problem, **common)
            return CallResult(problem)
        dispatch = Dispatch(tool, context, arguments, action, decision, digest, situation, detail)
        return CallResult("", dispatch=dispatch)

    def settle(self, dispatch: Dispatch, answer: str) -> tuple[str, bool]:
        """The page answered a call the server sent: what the model reads, and whether it worked (audited, and tainting)."""
        tool, context = dispatch.tool, dispatch.context
        common = {"conversation_id": context.turn.conversation_id, "turn_id": context.turn.turn_id}
        try:
            text, ok = tool.remote.after(context, dispatch.arguments, answer), True
        except (ToolError, ValueError, TypeError) as error:
            text, ok = f"Error: {error}", False
        except Exception as error:  # the page's answer is untrusted: a crash is text for the model, and a log line
            log.exception("the answer of tool %s could not be settled", tool.name)
            text, ok = f"Error: the tool failed ({type(error).__name__}).", False
        status = "ran" if ok else "failed"
        self.audit.record(
            dispatch.action,
            dispatch.situation,
            dispatch.digest,
            dispatch.decision,
            status,
            detail=dispatch.detail,
            **common,
        )
        if ok and tool.taints:
            context.turn.tainted = True
        return text, ok

    async def execute(self, name: str, context: ToolContext, arguments: dict) -> tuple[str, bool]:
        """Run a call you approved, exactly as it was asked. The floor is checked again (it never bends)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: the tool {name} no longer exists.", False
        if tool.remote is not None:  # it runs in the person's browser, while the chat is open: nothing to run here
            return "Error: this runs on your computer while the conversation is open: ask again then.", False
        try:
            action = self.action(name, context, arguments)
        except (ToolError, ValueError, TypeError) as error:
            return f"Error: {error}", False
        decision = self.gate.decide(action, context.turn.situation, count_hit=False, approved=True)
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
