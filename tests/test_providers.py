"""OpenCode Go: its address, its model list, and what its gateway insists on (a session header, a User-Agent)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from clara import __version__
from clara.db import migrate
from clara.db.migrate import migrations
from clara.llm import opencode
from clara.llm.catalog import Catalog
from clara.llm.openai import _error_code
from clara.llm.opencode_models import MODELS
from clara.llm.providers import ProviderError, Providers, build_providers, default_factory
from clara.llm.retry import key_refused
from clara.llm.types import PROTOCOLS, LlmError
from clara.settings import load_settings

BASE = "https://opencode.ai/inference/go"
GO_LISTED = ["glm-5.3", "kimi-k3", "claude-haiku-5-5", "muse-spark-1.3-contributor", "beam-free"]


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


def lists(go=GO_LISTED):
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"object": "list", "data": [{"id": name} for name in go]})

    return asked, handler


async def test_the_models_are_those_of_go_alone_asked_with_the_key(settings):
    asked, handler = lists()
    backend = make_backend(settings, handler)
    assert await backend.list_models() == sorted(GO_LISTED)  # `beam-free` is a free model, and Go lists it itself
    assert [str(request.url) for request in asked] == [f"{BASE}/v1/models"]
    assert asked[0].headers["authorization"] == "Bearer go-key"
    await backend.aclose()


async def test_a_model_is_asked_at_go_without_reading_any_list_first(settings):
    asked, handler = lists()

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    for model in ("kimi-k3", "beam-free"):
        backend = make_backend(settings, answer, model)
        [c async for c in backend.stream([{"role": "user", "content": "hi"}], None, session="conv-1")]
        assert str(asked[-1].url) == f"{BASE}/openai/v1/chat/completions"
        assert asked[-1].headers["x-opencode-session"] == "conv-1"
        await backend.aclose()
    assert len(asked) == 2  # the two chats, no model list in between


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


FREE = "opencode-go:longcat-2.5-preview-free"
PAID = "opencode-go:kimi-k3"


async def opencode_catalogue(services, llm, names=("longcat-2.5-preview-free", "kimi-k3")):
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
    llm.models["opencode-go"] = ["longcat-2.5-preview-free", "kimi-k3", "beam-free", "glm-5.3"]
    services.secrets.set("opencode_go_api_key", "go-key")
    services.providers.set_enabled("opencode-go", True)
    await services.catalog.listing(force=True)
    services.catalog.set_shown(["opencode-go:kimi-k3", "opencode-go:glm-5.3", "local:fake"], True)
    assert [info.ref for info in services.catalog.shown()] == [
        "local:fake", "opencode-go:glm-5.3", "opencode-go:kimi-k3",  # as before, by name
        "opencode-go:beam-free", "opencode-go:longcat-2.5-preview-free",  # then the free ones, by name
    ]


# ----------------------------------------------------------------------------------------------------------------
# What goes wrong with OpenCode, and what Clara says and does about it
# ----------------------------------------------------------------------------------------------------------------
FREE_TIER = {"type": "error", "error": {"type": "FreeTierError", "message":
             "OpenCode's free tier can only be used from within OpenCode. Download it at https://opencode.ai and keep building."}}
BAD_CREDENTIAL = {"error": {"type": "server_error", "message": "Upstream request failed: Invalid credential"}}
BEAM_IMAGE = {"error": {"type": "server_error", "message": "Upstream request failed: [invalid_value] Beam supports "
              "text input only. This conversation contains an image. Remove the image from the conversation and try again."}}


async def refusal(settings, status: int, body, model: str = "kimi-k3") -> LlmError:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    backend = make_backend(settings, handler, model)
    with pytest.raises(LlmError) as raised:
        [c async for c in backend.stream([{"role": "user", "content": "hi"}], None)]
    await backend.aclose()
    return raised.value


async def test_the_free_tier_refusal_is_not_taken_for_a_bad_key(settings):
    error = await refusal(settings, 403, FREE_TIER, "beam-free")
    assert (error.status, error.code) == (403, "FreeTierError")
    assert str(error).startswith("OpenCode Go answered HTTP 403: OpenCode's free tier can only be used")
    assert "API key" not in str(error) and not key_refused(error)


@pytest.mark.parametrize("status", [401, 403])
async def test_a_key_that_is_refused_is_still_called_so(settings, status):
    error = await refusal(settings, status, BAD_CREDENTIAL)
    assert str(error) == f"OpenCode Go refused the API key (HTTP {status}): Upstream request failed: Invalid credential"
    assert key_refused(error)


async def test_a_picture_sent_to_a_text_only_model_comes_back_with_its_status_and_code(settings):
    error = await refusal(settings, 400, BEAM_IMAGE, "beam-free")
    assert (error.status, error.code) == (400, "invalid_value")
    assert str(error) == ("OpenCode Go answered HTTP 400: Upstream request failed: [invalid_value] Beam supports text "
                          "input only. This conversation contains an image. Remove the image from the conversation and try again.")
    assert not key_refused(error)


async def test_a_gateway_without_a_session_or_a_model_is_told_as_it_says_it(settings):
    missing = {"type": "error", "error": {"type": "MissingSessionID", "message": "Request is missing x-opencode-session"}}
    error = await refusal(settings, 400, missing)
    assert (error.status, error.code) == (400, "MissingSessionID") and "missing x-opencode-session" in str(error)
    gone = await refusal(settings, 400, {"error": {"type": "server_error", "message": "Upstream request failed: Model is unavailable."}})
    assert (gone.status, gone.code) == (400, "server_error") and str(gone).endswith("Model is unavailable.")


async def test_a_busy_gateway_says_when_to_come_back(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"type": "rate_limit", "message": "slow down"}}, headers={"retry-after": "7"})

    backend = make_backend(settings, handler)
    with pytest.raises(LlmError) as raised:
        [c async for c in backend.stream([{"role": "user", "content": "hi"}], None)]
    assert (raised.value.status, raised.value.code, raised.value.retry_after) == (429, "rate_limit", 7.0)
    await backend.aclose()


async def test_an_error_in_the_middle_of_a_stream_has_its_name_too(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        sse = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: {"error":{"code":429,"type":"overloaded","message":"busy"}}\n\n'
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    backend = make_backend(settings, handler)
    with pytest.raises(LlmError) as raised:
        [c async for c in backend.stream([{"role": "user", "content": "hi"}], None)]
    assert (raised.value.status, raised.value.code) == (429, "overloaded")
    await backend.aclose()


@pytest.mark.parametrize(("body", "name"), [
    ({"error": {"code": "3051", "type": "invalid_request_error", "message": "x"}}, "3051"),  # Mistral
    ([{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "x"}}], "PERMISSION_DENIED"),  # Gemini
    (BEAM_IMAGE, "invalid_value"),
    (FREE_TIER, "FreeTierError"),
    ({"error": {"type": "a sentence, not a name", "message": "x"}}, None),  # only a plain token may be logged
    ({"error": {"message": "Upstream request failed: [see what you wrote here] x"}}, None),
    ({"error": "just words"}, None),
])
def test_the_name_of_an_error_is_a_plain_token_or_nothing(body, name):
    assert _error_code(httpx.Response(400, json=body)) == name
    assert _error_code(httpx.Response(502, text="<html>Bad gateway</html>")) is None


async def test_a_free_tier_403_hands_over_to_the_next_model_and_raises_no_key_alert(client, services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    services.runtime.set("fallbacks", ["pc:backup"])
    llm.by_model["local:fake"] = [LlmError("free tier", 403, code="FreeTierError")]
    llm.by_model["pc:backup"] = ["From the PC."]
    response = await client.post("/v1/chat", json={"message": "hi"})
    assert response.json()["reply"] == "From the PC."
    assert "provider_key:local" not in services.alerts.active


async def test_a_refused_key_hands_over_and_raises_the_key_alert(client, services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    services.runtime.set("fallbacks", ["pc:backup"])
    llm.by_model["local:fake"] = [LlmError("refused", 403)]
    llm.by_model["pc:backup"] = ["From the PC."]
    response = await client.post("/v1/chat", json={"message": "hi"})
    assert response.json()["reply"] == "From the PC."
    assert "provider_key:local" in services.alerts.active


SECRET_TEXT = "you wrote: my passport number"


async def test_a_failed_call_is_logged_with_its_status_and_code_and_never_its_text(client, services, llm):
    seen = []
    services.traffic.sink = lambda kind, fields: seen.append((kind, fields))
    llm.by_model["local:fake"] = [LlmError(f"Upstream request failed: [invalid_value] {SECRET_TEXT}", 400, code="invalid_value")]
    await client.post("/v1/chat", json={"message": "hi"})
    failed = [fields for kind, fields in seen if kind == "model" and fields["outcome"] == "error"]
    assert [(f["error"], f["status"], f["code"]) for f in failed] == [("LlmError", 400, "invalid_value")]
    assert SECRET_TEXT not in json.dumps(seen)
    services.traffic.flush()
    assert not (services.traffic.directory / "debug-content.jsonl").exists()
    written = "".join(path.read_text() for path in services.traffic.directory.glob("traffic-*.jsonl"))
    assert '"code": "invalid_value"' in written and SECRET_TEXT not in written


async def test_a_call_that_works_has_no_failure_fields(client, services, llm):
    seen = []
    services.traffic.sink = lambda kind, fields: seen.append((kind, fields))
    llm.replies = ["Fine."]
    await client.post("/v1/chat", json={"message": "hi"})
    ok = [fields for kind, fields in seen if kind == "model" and fields["use"] == "chat"]
    assert ok and ok[0]["outcome"] == "ok" and not {"status", "code", "error"} & ok[0].keys()


async def test_the_text_of_a_failure_goes_to_the_debug_log_only_while_the_switch_is_on(client, services, llm):
    services.runtime.set("debug_until", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    llm.by_model["local:fake"] = [LlmError(f"Upstream request failed: [invalid_value] {SECRET_TEXT}", 400, code="invalid_value")]
    await client.post("/v1/chat", json={"message": "hi"})
    services.traffic.flush()
    debug = (services.traffic.directory / "debug-content.jsonl").read_text()
    assert SECRET_TEXT in debug and '"kind": "llm_error"' in debug and '"status": 400' in debug
    assert SECRET_TEXT not in "".join(path.read_text() for path in services.traffic.directory.glob("traffic-*.jsonl"))


def test_the_table_knows_what_the_lists_of_opencode_do_not_say():
    beam = opencode.known("beam-free")
    assert beam.fit().works is True
    assert (beam.capabilities.vision, beam.capabilities.tools) == (False, True)  # text only, which a picture would break
    assert opencode.known("kimi-k3").capabilities.vision is True
    fit = opencode.known("claude-haiku-5-5").fit()
    assert fit.works is False and fit.why == "it speaks Anthropic Messages, which Clara does not"
    assert opencode.known("muse-spark-1.2-contributor").fit().works is False
    assert opencode.known("brand-new-model") is None


def test_every_protocol_of_the_table_has_a_name():
    assert {protocol for protocol, *_ in MODELS.values()} <= set(PROTOCOLS)


def test_a_provider_fits_what_it_lists(settings):
    providers = build_providers(settings)
    go = providers["opencode-go"]
    assert go.fit("kimi-k3").works is True and go.fit("gpt-6-luna").works is False
    assert go.fit("brand-new-model").works is None and "no record" in go.fit("brand-new-model").why
    assert providers["deepseek"].fit("anything").works is True and providers["local"].fit("anything").works is True


async def test_the_capabilities_of_opencode_models_come_from_the_table_without_a_question(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"nothing should be asked: {request.url}")

    backend = make_backend(settings, handler)
    found = await backend.model_capabilities(["beam-free", "kimi-k3", "brand-new-model"])
    assert sorted(found) == ["beam-free", "kimi-k3"]
    assert found["beam-free"].vision is False and found["kimi-k3"].vision is True
    await backend.aclose()


async def test_the_catalogue_learns_that_a_model_reads_text_only(settings, services):
    _, handler = lists(go=["beam-free", "kimi-k3"])
    providers = Providers(settings, services.secrets, services.runtime, default_factory(httpx.MockTransport(handler)))
    services.secrets.set("opencode_go_api_key", "go-key")
    services.runtime.set("providers_enabled", ["local", "opencode-go"])
    catalog = Catalog(services.db, providers)
    await catalog.listing(force=True)
    assert catalog.capabilities("opencode-go:beam-free").vision is False  # so a picture is refused before it is sent
    assert catalog.capabilities("opencode-go:kimi-k3").vision is True
    await providers.aclose()


NAMES = ("longcat-2.5-preview-free", "kimi-k3", "claude-haiku-5-5", "brand-new-model")
BROKEN = "opencode-go:claude-haiku-5-5"


async def test_a_model_that_does_not_work_is_listed_with_its_reason_and_never_shown(services, llm):
    await opencode_catalogue(services, llm, names=NAMES)
    fits = {info.ref: info.fit for info in await services.catalog.listing()}
    assert fits[BROKEN].works is False and "Anthropic Messages" in fits[BROKEN].why
    assert fits[PAID].works is True and fits["opencode-go:brand-new-model"].works is None
    services.catalog._upsert(BROKEN, shown=1, chosen=1)  # shown on an earlier day, before the table knew
    assert [i.ref for i in services.catalog.shown() if i.provider == "opencode-go"] == [FREE]
    assert next(i for i in await services.catalog.listing() if i.ref == BROKEN).shown is False


async def test_a_model_that_does_not_work_cannot_be_shown_but_can_be_hidden(services, llm):
    await opencode_catalogue(services, llm, names=NAMES)
    with pytest.raises(ProviderError, match="cannot be shown in the pickers: it speaks Anthropic Messages"):
        services.catalog.set_shown([PAID, BROKEN], True)
    assert PAID not in [i.ref for i in services.catalog.shown()]  # nothing was changed
    services.catalog.set_shown([BROKEN], False)
    services.catalog.set_shown(["opencode-go:brand-new-model"], True)  # unknown: allowed, the service will answer


async def test_the_models_api_gives_the_fit_of_each_model_and_refuses_to_show_one_that_fails(client, services, llm):
    await opencode_catalogue(services, llm, names=NAMES)
    body = (await client.get("/v1/admin/models")).json()
    fits = {model["ref"]: model["fit"] for model in body["models"] if model["provider"] == "opencode-go"}
    assert fits[BROKEN]["works"] is False and "Anthropic Messages" in fits[BROKEN]["why"]
    assert fits[PAID] == {"works": True, "why": ""} and fits["opencode-go:brand-new-model"]["works"] is None
    assert next(model["fit"] for model in body["models"] if model["ref"] == "local:fake") == {"works": True, "why": ""}
    refused = await client.patch("/v1/admin/models", json={"refs": [BROKEN], "shown": True})
    assert refused.status_code == 422 and "Anthropic Messages" in refused.text


def refresh_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "refresh_opencode_models.py"
    spec = importlib.util.spec_from_file_location("refresh_opencode_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_table_is_made_of_the_models_of_go_alone():
    def model(pictures=False, npm=None):
        found = {"modalities": {"input": ["text", "image"] if pictures else ["text"]}, "tool_call": True, "reasoning": False}
        return {**found, "provider": {"npm": npm}} if npm else found

    api = {
        "opencode-go": {"npm": "@ai-sdk/openai-compatible", "models": {"a": model(True), "b": model(npm="@ai-sdk/anthropic"),
                                                                    "c": model(npm="@ai-sdk/openai"),
                                                                    "d-free": model(npm="@ai-sdk/google")}},
        "opencode": {"npm": "@ai-sdk/openai-compatible", "models": {
            "a": model(npm="@ai-sdk/google"),  # Zen's entry for a Go model: not used, it may speak another API there
            "z-free": model(True)}},  # a model of Zen that Go does not have: Clara does not use Zen
    }
    script = refresh_script()
    found = script.table(api)
    assert found == {"a": ("chat", True, True, False), "b": ("messages", False, True, False),
                     "c": ("responses", False, True, False), "d-free": ("google", False, True, False)}
    namespace: dict = {}
    exec(script.render(found), namespace)  # noqa: S102 - the generated file, read back as Python
    assert namespace["MODELS"] == found
