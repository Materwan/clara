"""OpenCode Go: its address, its model list, and what its gateway insists on (a session header, a User-Agent)."""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from clara import __version__
from clara.db import migrate
from clara.db.migrate import migrations
from clara.llm.providers import ProviderError, Providers, build_providers, default_factory
from clara.settings import load_settings

BASE = "https://opencode.ai/inference/go"
ZEN = "https://opencode.ai/zen/v1"
GO_LISTED = ["glm-5.3", "kimi-k3", "claude-haiku-5-5", "muse-spark-1.3-contributor", "beam-free"]
ZEN_LISTED = ["glm-5.3-flash-free", "beam-free", "muse-spark-1.3-contributor-free", "claude-opus-5-5", "gpt-6-sol"]


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


def lists(go=GO_LISTED, zen=ZEN_LISTED, zen_status=200):
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if str(request.url) == f"{ZEN}/models":
            return httpx.Response(zen_status, json={"object": "list", "data": [{"id": name} for name in zen]})
        return httpx.Response(200, json={"object": "list", "data": [{"id": name} for name in go]})

    return asked, handler


async def test_the_models_are_those_of_go_and_the_free_ones_of_zen(settings):
    asked, handler = lists()
    backend = make_backend(settings, handler)
    assert await backend.list_models() == sorted({
        *GO_LISTED, "glm-5.3-flash-free", "muse-spark-1.3-contributor-free",
    })  # Go's list whole, Zen's `-free` models added once, none of Zen's paid ones
    assert sorted(str(request.url) for request in asked) == [f"{BASE}/v1/models", f"{ZEN}/models"]
    await backend.aclose()


async def test_the_key_goes_to_go_and_not_to_the_open_zen_list(settings):
    asked, handler = lists()
    backend = make_backend(settings, handler)
    await backend.list_models()
    by_url = {str(request.url): request for request in asked}
    assert by_url[f"{BASE}/v1/models"].headers["authorization"] == "Bearer go-key"
    assert "authorization" not in by_url[f"{ZEN}/models"].headers
    await backend.aclose()


async def test_go_models_still_list_when_zen_does_not_answer(settings):
    _, handler = lists(zen_status=503)
    backend = make_backend(settings, handler)
    assert await backend.list_models() == sorted(GO_LISTED)
    await backend.aclose()


async def test_a_model_is_asked_at_the_address_of_the_list_it_came_from(settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return lists()[1](request)
        seen.append(request)
        return httpx.Response(200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    for model, address in [("kimi-k3", f"{BASE}/openai/v1"), ("beam-free", f"{BASE}/openai/v1"),  # in both: Go
                           ("glm-5.3-flash-free", ZEN)]:
        backend = make_backend(settings, handler, model)
        [c async for c in backend.stream([{"role": "user", "content": "hi"}], None, session="conv-1")]
        assert str(seen[-1].url) == f"{address}/chat/completions"
        assert seen[-1].headers["x-opencode-session"] == "conv-1"
        await backend.aclose()


async def test_the_main_address_is_asked_when_the_go_list_cannot_be_read(settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(500)
        seen.append(request)
        return httpx.Response(200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    backend = make_backend(settings, handler, "glm-5.3-flash-free")
    [c async for c in backend.stream([{"role": "user", "content": "hi"}], None)]
    assert str(seen[0].url) == f"{BASE}/openai/v1/chat/completions"
    await backend.aclose()


async def test_the_gateway_gets_a_user_agent_of_its_own_and_the_conversation_as_session(settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return lists()[1](request)
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


FREE = "opencode-go:glm-5.3-flash-free"
PAID = "opencode-go:kimi-k3"


async def opencode_catalogue(services, llm, names=("glm-5.3-flash-free", "kimi-k3")):
    llm.models["opencode-go"] = list(names)
    services.secrets.set("opencode_go_api_key", "go-key")
    services.providers.set_enabled("opencode-go", True)
    await services.catalog.listing(force=True)
    return [info.ref for info in services.catalog.shown() if info.provider == "opencode-go"]


async def test_the_free_models_of_opencode_are_in_the_pickers_without_being_chosen(services, llm):
    assert await opencode_catalogue(services, llm) == [FREE]  # kimi-k3 stays out until you show it
    info = next(i for i in await services.catalog.listing() if i.ref == FREE)
    assert info.shown


async def test_hiding_a_free_model_is_kept_and_showing_another_is_too(services, llm):
    await opencode_catalogue(services, llm)
    services.catalog.set_shown([FREE], False)
    services.catalog.set_shown([PAID], True)
    await services.catalog.listing(force=True)
    assert [i.ref for i in services.catalog.shown() if i.provider == "opencode-go"] == [PAID]
    services.catalog.set_shown([FREE], True)  # and back
    assert sorted(i.ref for i in services.catalog.shown() if i.provider == "opencode-go") == sorted([FREE, PAID])


async def test_a_free_model_that_is_no_longer_listed_leaves_the_pickers(services, llm):
    await opencode_catalogue(services, llm)
    llm.models["opencode-go"] = ["kimi-k3"]
    await services.catalog.listing(force=True)
    assert [i.ref for i in services.catalog.shown() if i.provider == "opencode-go"] == []


async def test_other_providers_show_nothing_by_default(services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    llm.models["pc"] = ["thing-free", "other"]
    await services.catalog.listing(force=True)
    assert not [i for i in services.catalog.shown() if i.provider == "pc"]


def test_the_models_already_shown_count_as_chosen_after_the_migration(tmp_path):
    path = tmp_path / "clara.db"
    with sqlite3.connect(path) as old:
        old.executescript(migrations()[0].sql)
        old.execute("PRAGMA user_version = 1")
        old.execute("INSERT INTO models (ref, shown) VALUES ('local:a', 1), ('opencode-go:b-free', 0)")
    migrate(path, tmp_path / "backups")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT ref, shown, chosen FROM models ORDER BY ref").fetchall() == [
            ("local:a", 1, 1), ("opencode-go:b-free", 0, 0)]


async def test_the_free_models_come_last_in_the_pickers(services, llm):
    llm.models["opencode-go"] = ["glm-5.3-flash-free", "kimi-k3", "beam-free", "glm-5.3"]
    services.secrets.set("opencode_go_api_key", "go-key")
    services.providers.set_enabled("opencode-go", True)
    await services.catalog.listing(force=True)
    services.catalog.set_shown(["opencode-go:kimi-k3", "opencode-go:glm-5.3", "local:fake"], True)
    assert [info.ref for info in services.catalog.shown()] == [
        "local:fake", "opencode-go:glm-5.3", "opencode-go:kimi-k3",  # as before, by name
        "opencode-go:beam-free", "opencode-go:glm-5.3-flash-free",  # then the free ones, by name
    ]
