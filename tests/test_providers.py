"""OpenCode Go: its address, its model list, and what its gateway insists on (a session header, a User-Agent)."""

from __future__ import annotations

import json

import httpx
import pytest

from clara import __version__
from clara.llm.providers import ProviderError, Providers, build_providers, default_factory
from clara.settings import load_settings

BASE = "https://opencode.ai/inference/go"
LISTED = ["glm-5.3", "kimi-k3", "deepseek-v4-pro", "mimo-v2.5-pro", "hy3", "claude-haiku-5-5", "gpt-6-luna",
          "grok-4.7", "gemini-4-argon-contributor", "minimax-m3", "qwen3.8-max", "omen-alpha"]


def go_provider(settings):
    return build_providers(settings)["opencode-go"]


def make_backend(settings, handler, model="kimi-k3"):
    provider = go_provider(settings)
    return default_factory(httpx.MockTransport(handler))(provider, model, "go-key", None)


def test_opencode_go_is_a_hosted_provider_with_its_own_key_and_window(settings, environ):
    provider = go_provider(settings)
    assert (provider.label, provider.local, provider.key_secret) == ("OpenCode Go", False, "opencode_go_api_key")
    assert provider.host == f"{BASE}/openai/v1" and provider.context == 131_072
    own = load_settings({**environ, "CLARA_OPENCODE_GO_CONTEXT": "65536", "CLARA_OPENCODE_GO_HOST": f"{BASE}/"},
                        use_file=False)
    assert go_provider(own).context == 65_536 and go_provider(own).host == f"{BASE}/openai/v1"


async def test_only_the_models_that_speak_chat_completions_are_listed(settings):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"object": "list", "data": [{"id": name} for name in LISTED]})

    backend = make_backend(settings, handler)
    assert await backend.list_models() == ["deepseek-v4-pro", "glm-5.3", "hy3", "kimi-k3", "mimo-v2.5-pro"]
    assert asked == [f"{BASE}/v1/models"]
    await backend.aclose()


async def test_the_gateway_gets_a_user_agent_of_its_own_and_the_conversation_as_session(settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        sse = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    backend = make_backend(settings, handler)
    chunks = [c async for c in backend.stream([{"role": "user", "content": "hello"}], None, session="conv-1")]
    assert "".join(c.text for c in chunks) == "Hi"
    request = seen[0]
    assert str(request.url) == f"{BASE}/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer go-key"
    assert request.headers["user-agent"] == f"clara/{__version__}"
    assert request.headers["x-opencode-session"] == "conv-1"
    assert json.loads(request.content)["model"] == "kimi-k3"

    [c async for c in backend.stream([{"role": "user", "content": "again"}], None)]  # no conversation: a steady stand-in
    [c async for c in backend.stream([{"role": "user", "content": "and again"}], None)]
    assert seen[1].headers["x-opencode-session"] == seen[2].headers["x-opencode-session"] != "conv-1"
    await backend.aclose()


async def test_other_providers_are_not_sent_the_session_header(settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    provider = build_providers(settings)["deepseek"]
    backend = default_factory(httpx.MockTransport(handler))(provider, "deepseek-chat", "k", None)
    [c async for c in backend.stream([{"role": "user", "content": "hi"}], None, session="conv-1")]
    assert "x-opencode-session" not in seen[0].headers
    await backend.aclose()


def test_opencode_go_needs_its_key_before_it_can_be_enabled(services):
    providers: Providers = services.providers
    assert not providers.usable("opencode-go") and "not enabled" in providers.why_not("opencode-go")
    with pytest.raises(ProviderError, match="opencode_go_api_key"):
        providers.set_enabled("opencode-go", True)
    services.secrets.set("opencode_go_api_key", "go-key")
    providers.set_enabled("opencode-go", True)
    assert providers.usable("opencode-go")


async def test_a_chat_passes_its_conversation_to_the_backend_as_the_session(client, llm):
    llm.replies = ["Hello."]
    response = await client.post("/v1/chat", json={"message": "hi"})
    conversation = response.json()["conversation"]
    assert [call["session"] for call in llm.seen if call["tools"]][-1] == conversation
