"""A scripted model provider for tests: each call takes the next reply from a list (or answers "ok")."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from clara.llm.providers import Provider
from clara.llm.types import Capabilities, LlmChunk, LlmError, ToolCall


@dataclass(frozen=True)
class Hang:
    """A model that says nothing for this long before its reply."""

    seconds: float
    then: str = "late"


Reply = str | list[LlmChunk] | Exception | Hang | Callable[[list[dict], list[dict] | None], "Reply"]


def calls(*items: tuple[str, dict]) -> list[LlmChunk]:
    """A reply that calls these tools."""
    return [LlmChunk(tool_calls=[ToolCall(name, arguments) for name, arguments in items])]


@dataclass
class FakeBackend:
    model: str
    owner: FakeLLM
    provider: str

    async def stream(self, messages: list[dict], tools: list[dict] | None) -> AsyncIterator[LlmChunk]:
        self.owner.seen.append({"model": f"{self.provider}:{self.model}", "messages": messages, "tools": tools})
        reply = self.owner.next_reply(f"{self.provider}:{self.model}")
        while callable(reply):
            reply = reply(messages, tools)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Hang):
            await asyncio.sleep(reply.seconds)
            reply = reply.then
        if self.owner.delay:
            await asyncio.sleep(self.owner.delay)
        if isinstance(reply, str):
            for start in range(0, len(reply), 5):
                yield LlmChunk(text=reply[start:start + 5])
            yield LlmChunk(prompt_tokens=100, completion_tokens=max(1, len(reply) // 4))
            return
        for chunk in reply:
            yield chunk
        yield LlmChunk(prompt_tokens=100, completion_tokens=10)

    async def list_models(self) -> list[str]:
        return list(self.owner.models.get(self.provider, ["fake"]))

    async def model_capabilities(self, names: list[str]) -> dict[str, Capabilities]:
        return {name: Capabilities(thinking=False, tools=True, vision=True, context=32_768) for name in names}

    async def verify(self) -> None:
        if self.provider in self.owner.refused:
            raise LlmError("refused", 401)

    async def aclose(self) -> None:
        return None


@dataclass
class FakeLLM:
    replies: list[Reply] = field(default_factory=list)
    by_model: dict[str, list[Reply]] = field(default_factory=dict)
    seen: list[dict] = field(default_factory=list)
    models: dict[str, list[str]] = field(default_factory=dict)
    refused: set[str] = field(default_factory=set)
    delay: float = 0.0

    def next_reply(self, ref: str) -> Reply:
        queue = self.by_model.get(ref)
        if queue:
            return queue.pop(0)
        if self.replies:
            return self.replies.pop(0)
        return "ok"

    def factory(self, provider: Provider, model: str, key: str | None, num_ctx: int | None) -> FakeBackend:
        return FakeBackend(model, self, provider.id)
