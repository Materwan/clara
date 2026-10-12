"""Which failures of a model pass (and so move on to the next fallback model; see test_models.py)."""

import httpx
import pytest
from conftest import FakeBackend

from clara.agent import Agent, ChatRequest
from clara.llm import LlmChunk, LlmError
from clara.prompt import SystemPrompt
from clara.retry import retryable
from clara.tools import default_toolbox


class Flaky(FakeBackend):
    """Fails with `errors` one after the other (before saying anything), then answers; `after_text` says a word first."""

    def __init__(self, *errors, after_text: bool = False):
        super().__init__(model="flaky")
        self.errors = list(errors)
        self.after_text = after_text
        self.attempts = 0

    async def stream(self, messages, tools):
        self.attempts += 1
        if self.errors:
            if self.after_text:
                yield LlmChunk(text="Hel")
            raise self.errors.pop(0)
        yield LlmChunk(text="Hello")
        yield LlmChunk(prompt_tokens=5, completion_tokens=1)


def make_agent(memory, tmp_path, backend, **options) -> Agent:
    return Agent(memory, backend, default_toolbox(), SystemPrompt(tmp_path / "none.md"), **options)


async def events_of(agent):
    return [event async for event in agent.turn(ChatRequest("cli", "erwan", "Erwan", "hi"))]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_busy_statuses_are_retried(status):
    assert retryable(LlmError("busy", status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, None])
def test_the_other_refusals_are_not(status):
    assert not retryable(LlmError("no", status))


def test_a_connection_that_failed_is_retried_but_a_silent_model_is_not():
    assert retryable(httpx.ConnectError("down"))
    assert retryable(httpx.RemoteProtocolError("cut"))
    assert retryable(ConnectionError("refused"))
    assert not retryable(httpx.ReadTimeout("silent"))
    assert not retryable(ValueError("a bug"))


async def test_a_busy_model_with_no_fallback_fails_at_once(memory, tmp_path):
    backend = Flaky(LlmError("high demand", 503))
    with pytest.raises(LlmError, match="high demand"):
        await events_of(make_agent(memory, tmp_path, backend))
    assert backend.attempts == 1
