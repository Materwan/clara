"""What a model stream is made of, whatever the provider.

Messages are kept in one shape everywhere (Ollama's): tool results named by `tool_name`, tool calls without ids and
with their arguments as a dict, pictures as `images: [{name, mime, data (base64)}]`. The OpenAI-compatible backend
translates. What a provider insists on getting back with its tool calls travels with them: the reasoning
(`thinking` on the assistant message: DeepSeek refuses a call sent back without it) and a call's `extra_content`
(Gemini's thought signatures).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    extra: dict[str, Any] | None = None  # what the provider wants back with this call (Gemini's signature)


@dataclass
class LlmChunk:
    """A piece of a streamed answer: text, reasoning, tool calls, or token counts (each an increase)."""

    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    notice: str = ""  # not from the model: the agent moves on to another model (fallback)
    switched: str = ""  # with a notice: the provider:model asked now


class LlmError(Exception):
    """The provider refused the request. `status` is the HTTP status when there is one; `code` is the provider's own
    short name for the failure (`FreeTierError`, `invalid_value`), a plain token that cannot carry what you wrote, which
    is why it may be logged where the message may not."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.code = code


@dataclass(frozen=True)
class Capabilities:
    """What a model can do, as far as its provider says (None: it does not say)."""

    thinking: bool | None = None
    tools: bool | None = None
    vision: bool | None = None
    context: int | None = None

    @classmethod
    def from_record(cls, record: dict) -> Capabilities:
        def flag(key: str) -> bool | None:
            value = record.get(key)
            return value if isinstance(value, bool) else None

        context = record.get("context")
        known = isinstance(context, int) and not isinstance(context, bool) and context > 0
        return cls(flag("thinking"), flag("tools"), flag("vision"), context if known else None)

    @classmethod
    def from_json(cls, text: str | None) -> Capabilities:
        try:
            record = json.loads(text or "{}")
        except ValueError:
            return cls()
        return cls.from_record(record) if isinstance(record, dict) else cls()

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    def describe(self) -> dict:
        return asdict(self)


CHAT = "chat"  # OpenAI's Chat Completions: the only protocol of a hosted model that Clara speaks
PROTOCOLS = {CHAT: "Chat Completions", "responses": "OpenAI Responses", "messages": "Anthropic Messages",
             "google": "Google Gemini"}


@dataclass(frozen=True)
class Fit:
    """Whether Clara can use a model (`works`: None when nothing is known of it) and, if not, why."""

    works: bool | None = True
    why: str = ""

    def describe(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Known:
    """What a service's own lists say of a model, before it is asked: the API it speaks and what it can do."""

    protocol: str
    capabilities: Capabilities

    def fit(self) -> Fit:
        if self.protocol == CHAT:
            return Fit(True)
        return Fit(False, f"it speaks {PROTOCOLS.get(self.protocol, self.protocol)}, which Clara does not")


class Backend(Protocol):
    """One model at one provider."""

    model: str

    def stream(self, messages: list[dict], tools: list[dict] | None,
               session: str | None = None) -> AsyncIterator[LlmChunk]:
        """`session` names the conversation, for the providers that route on it (OpenCode)."""
        ...

    async def list_models(self) -> list[str]: ...

    async def verify(self) -> None:
        """Raise when the provider cannot be used (unreachable, key refused)."""
        ...

    async def aclose(self) -> None: ...
