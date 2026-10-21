"""One model round: slots, timeouts, fallbacks, the time budget, and the usage log.

* **Slots.** Your chats get their own model slots (CLARA_CHAT_SLOTS); titles, summaries, reminders, task follow-ups,
  scheduled runs and approval follow-ups draw from a smaller background pool (CLARA_BACKGROUND_SLOTS), so they can
  never starve a chat. A slot is held only while the model works, never while a tool or a client runs.
* **Timeouts.** A model that says nothing for CLARA_FIRST_TOKEN_TIMEOUT, or pauses for CLARA_IDLE_TIMEOUT, is given
  up on. On top of that, each answer has a total budget of model time (CLARA_ANSWER_BUDGET, 15 minutes), so a chain of
  slow fallbacks cannot run for an hour.
* **Fallbacks.** A model that fails *before it said anything* (busy, unreachable, gone, silent past the first-token
  timeout, key refused, payment required) hands over to the next of the choice's fallbacks; a refused key or a 402 also raises an alert.
* **Usage.** Every call, failed ones too, is a `usage_log` row (tokens only) and a metadata line of the traffic log. A
  failed call's line adds the HTTP status and the provider's short code for the failure (`400 invalid_value`), never
  its text: that goes to the debug log, while the debug switch is on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ..llm import Choice, LlmChunk, Providers
from ..llm.retry import PAYMENT_STATUS, key_refused, moves_on, status_of
from ..llm.types import LlmError
from ..ops.alerts import Alerts
from ..ops.traffic import TrafficLog
from ..ops.usage import UsageLog
from .estimate import CHARS_PER_TOKEN, estimate_prompt_tokens

log = logging.getLogger(__name__)

_END = object()


class ModelTimeout(Exception):
    """The model stopped answering, or the answer's time budget is spent."""


class ModelSilent(ModelTimeout):
    """The model said nothing at all in time: as it gave nothing yet, the next model may answer instead."""


@dataclass
class Budget:
    """The model time an answer may still spend."""

    seconds: float
    spent: float = 0.0

    @property
    def left(self) -> float:
        return self.seconds - self.spent


@dataclass
class RoundResult:
    text_parts: list[str] = field(default_factory=list)
    thinking_parts: list[str] = field(default_factory=list)
    calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0
    used: Choice | None = None  # the model that answered (a fallback, maybe)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def thinking(self) -> str:
        return "".join(self.thinking_parts)


async def with_idle_timeout(stream: AsyncIterator[LlmChunk], first: float, idle: float) -> AsyncIterator[LlmChunk]:
    iterator = stream.__aiter__()
    wait = first
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), wait)
            except StopAsyncIteration:
                return
            except TimeoutError:
                if wait == first:
                    raise ModelSilent(f"The model took more than {wait:g} seconds to start answering.") from None
                raise ModelTimeout(
                    f"The model took more than {wait:g} seconds between two pieces of its answer."
                ) from None
            wait = idle
            yield chunk
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()


class ModelRunner:
    def __init__(
        self,
        providers: Providers,
        usage: UsageLog,
        traffic: TrafficLog,
        alerts: Alerts,
        *,
        chat_slots: int,
        background_slots: int,
        first_token_timeout: float,
        idle_timeout: float,
    ):
        self.providers = providers
        self.usage = usage
        self.traffic = traffic
        self.alerts = alerts
        self.slots = {"chat": asyncio.Semaphore(chat_slots), "background": asyncio.Semaphore(background_slots)}
        self.first_token_timeout = first_token_timeout
        self.idle_timeout = idle_timeout

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        choice: Choice,
        *,
        pool: str,
        kind: str,
        client: str,
        conversation_id: str | None,
        budget: Budget | None = None,
    ) -> AsyncIterator[LlmChunk]:
        """The chunks of one round. A task reads the model while holding a slot and passes chunks through a queue: a
        client that reads slowly cannot keep a slot busy. A `notice` chunk says a fallback is asked."""
        queue: asyncio.Queue = asyncio.Queue()
        options = [choice, *choice.fallbacks]
        semaphore = self.slots[pool]

        async def produce() -> None:
            for index, option in enumerate(options):
                started = False
                begun = time.monotonic()
                counts = [0, 0, 0]
                text_size = 0
                outcome = "ok"
                failure: Exception | None = None
                try:
                    async with semaphore:
                        backend = self.providers.backend(option.ref, option.window)
                        self.traffic.content(
                            "llm_request",
                            provider=option.provider.id,
                            model=option.model,
                            messages=_without_pictures(messages),
                            tools=[t["function"]["name"] for t in tools or ()],
                        )
                        first = self.first_token_timeout
                        idle = self.idle_timeout
                        if budget is not None:
                            if budget.left <= 0:
                                raise ModelTimeout("This answer has used up its time budget (CLARA_ANSWER_BUDGET).")
                            first, idle = min(first, budget.left), min(idle, budget.left)
                        async for chunk in with_idle_timeout(
                            backend.stream(messages, tools, session=conversation_id), first, idle
                        ):
                            started = started or bool(chunk.text or chunk.thinking or chunk.tool_calls)
                            counts[0] += chunk.prompt_tokens
                            counts[1] += chunk.completion_tokens
                            counts[2] += chunk.cached_tokens
                            text_size += len(chunk.text)
                            queue.put_nowait(chunk)
                            if budget is not None and budget.spent + (time.monotonic() - begun) > budget.seconds:
                                raise ModelTimeout("This answer has used up its time budget (CLARA_ANSWER_BUDGET).")
                    self.providers_ok(option)
                    queue.put_nowait(_END)
                    return
                except asyncio.CancelledError:
                    outcome = "interrupted"
                    raise
                except Exception as error:
                    outcome = "error"
                    failure = error
                    self._alert_for(option, error)
                    self.traffic.content(
                        "llm_error",
                        provider=option.provider.id,
                        model=option.model,
                        status=status_of(error),
                        error=str(error),
                    )
                    hand_over = moves_on(error, fallback=index > 0) or isinstance(error, ModelSilent)
                    if started or index + 1 == len(options) or not hand_over:
                        queue.put_nowait(error)
                        return
                    following = options[index + 1]
                    log.warning("the model %s failed (%s), asking %s", option.ref, type(error).__name__, following.ref)
                    queue.put_nowait(
                        LlmChunk(notice=f"{option.ref} failed, trying {following.ref}…", switched=following.ref)
                    )
                finally:
                    seconds = time.monotonic() - begun
                    if budget is not None:
                        budget.spent += seconds
                    estimated = not (counts[0] or counts[1])
                    prompt = counts[0] or (estimate_prompt_tokens(messages, tools) if outcome == "ok" else 0)
                    completion = counts[1] or (math.ceil(text_size / CHARS_PER_TOKEN) if outcome == "ok" else 0)
                    self.usage.record(
                        kind=kind,
                        client=client,
                        conversation_id=conversation_id,
                        provider=option.provider.id,
                        model=option.model,
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        cached_tokens=counts[2],
                        duration_ms=int(seconds * 1000),
                        outcome=outcome,
                        estimated=estimated and outcome == "ok",
                    )
                    self.traffic.record(
                        "model",
                        provider=option.provider.id,
                        model=option.model,
                        use=kind,
                        client=client,
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        cached_tokens=counts[2],
                        ms=int(seconds * 1000),
                        outcome=outcome,
                        **_failure_fields(failure),
                    )

        async def guarded() -> None:
            """Whatever goes wrong in the producer reaches the consumer: it must never wait for ever."""
            try:
                await produce()
            except asyncio.CancelledError:
                raise
            except BaseException as error:  # handed over, not swallowed
                queue.put_nowait(error)

        producer = asyncio.ensure_future(guarded())
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            producer.cancel()
            with contextlib.suppress(BaseException):
                await producer

    def providers_ok(self, option: Choice) -> None:
        self.alerts.clear(f"provider_key:{option.provider.id}")
        self.alerts.clear(f"provider_payment:{option.provider.id}")

    def _alert_for(self, option: Choice, error: BaseException) -> None:
        status = status_of(error)
        if key_refused(error):
            self.alerts.raise_(
                f"provider_key:{option.provider.id}",
                f"{option.provider.label} refused its key",
                f"HTTP {status} while calling {option.ref}. Check the key: clara secrets set "
                f"{option.provider.key_secret}.",
            )
        elif status == PAYMENT_STATUS:
            self.alerts.raise_(
                f"provider_payment:{option.provider.id}",
                f"{option.provider.label} wants payment",
                f"HTTP 402 while calling {option.ref}: the balance may be spent.",
            )

    async def complete(
        self, messages: list[dict], choice: Choice, *, kind: str, conversation_id: str | None, client: str = "server"
    ) -> str:
        """A side job's whole text (a title, a summary, a plan): background pool, no tools."""
        parts: list[str] = []
        async with contextlib.aclosing(
            self.stream(
                messages, None, choice, pool="background", kind=kind, client=client, conversation_id=conversation_id
            )
        ) as chunks:
            async for chunk in chunks:
                parts.append(chunk.text)
        return "".join(parts)


def _failure_fields(error: Exception | None) -> dict:
    """What the traffic log keeps of a failed call: what kind of failure, the HTTP status and the provider's short
    code for it. Never the message, which can quote what you wrote."""
    if error is None:
        return {}
    fields: dict = {"error": type(error).__name__}
    if (status := status_of(error)) is not None:
        fields["status"] = status
    if isinstance(error, LlmError) and error.code:
        fields["code"] = error.code
    return fields


def _without_pictures(messages: list[dict]) -> list[dict]:
    return [
        {**message, "images": [f"{image.get('name', '')} ({image.get('mime', '')})" for image in message["images"]]}
        if message.get("images")
        else message
        for message in messages
    ]
