"""Where models run.

    <id>      an Ollama host you own (CLARA_OLLAMA_HOSTS: this machine, your PC on the tailnet)   local
    cloud     ollama.com, with the key `ollama_api_key`                                              hosted
    gemini    Google Gemini, OpenAI-compatible API, key `gemini_api_key`                             hosted
    deepseek  DeepSeek, OpenAI-compatible API, key `deepseek_api_key`                               hosted
    mistral   Mistral, OpenAI-compatible API, key `mistral_api_key`                                 hosted
    opencode-go  OpenCode Go and Zen's free models, OpenAI-compatible API, key `opencode_go_api_key`  hosted

A model is named `provider:model` (`local:qwen3:8b`, `gemini:gemini-2.5-flash`). A provider is *usable* when you
enabled it (runtime setting `providers_enabled`; local hosts are enabled at first) and its key, if it needs one, is in
the secrets store. Keys are read from the store when a backend is built; setting a key drops the old backends.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .. import __version__
from ..runtime import RuntimeSettings
from ..secrets import SecretsStore
from ..settings import Settings
from .ollama import OllamaBackend
from .openai import OpenAIBackend, OpenAIFlavor, SecondList
from .types import Backend, Capabilities

CHECK_TIMEOUT = 8.0
CAPABILITIES_TIMEOUT = 30.0


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # ollama or openai
    host: str
    local: bool  # a machine you own: a local model never falls back to a hosted one
    key_secret: str | None  # the secret holding its key (None: needs none)
    context: int  # the window of a model that does not say its own
    flavor: OpenAIFlavor | None = None
    free_suffix: str = ""  # the models named so are free: offered in the pickers until you hide them, listed last

    def describe(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "host": self.host,
            "local": self.local,
            "needs_key": self.key_secret is not None,
        }


def make_ref(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def split_ref(ref: str) -> tuple[str, str]:
    provider, separator, model = (ref or "").strip().partition(":")
    if not separator or not provider or not model.strip():
        raise ProviderError(
            f"{ref!r} is not a model: write it provider:model, e.g. local:qwen3:8b."
        )
    return provider, model.strip()


def build_providers(settings: Settings) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for host in settings.ollama_hosts:
        providers[host.id] = Provider(
            host.id,
            f"Ollama ({host.id})",
            "ollama",
            host.url,
            True,
            None,
            settings.ollama_context,
        )
    context = settings.hosted_context
    providers["cloud"] = Provider(
        "cloud",
        "Ollama cloud",
        "ollama",
        settings.cloud_host,
        False,
        "ollama_api_key",
        context["cloud"],
    )
    providers["gemini"] = Provider(
        "gemini",
        "Google Gemini",
        "openai",
        settings.gemini_host,
        False,
        "gemini_api_key",
        context["gemini"],
        OpenAIFlavor(
            signatures=True,
            native_models="https://generativelanguage.googleapis.com/v1beta/models",
        ),
    )
    providers["deepseek"] = Provider(
        "deepseek",
        "DeepSeek",
        "openai",
        settings.deepseek_host,
        False,
        "deepseek_api_key",
        context["deepseek"],
        OpenAIFlavor(
            reasoning_back=True,
            extra_body=(
                ()
                if settings.deepseek_thinking
                else (("thinking", {"type": "disabled"}),)
            ),
        ),
    )
    providers["mistral"] = Provider(
        "mistral",
        "Mistral",
        "openai",
        settings.mistral_host,
        False,
        "mistral_api_key",
        context["mistral"],
        OpenAIFlavor(stream_usage=False, tool_names=True),
    )
    go = settings.opencode_go_host.rstrip("/")
    zen = settings.opencode_zen_host.rstrip("/")
    providers["opencode-go"] = Provider(
        "opencode-go",
        "OpenCode Go",
        "openai",
        f"{go}/openai/v1",
        False,
        "opencode_go_api_key",
        context["opencode-go"],
        OpenAIFlavor(
            reasoning_back=True,
            models_url=f"{go}/v1/models",
            headers=(("User-Agent", f"clara/{__version__}"),),
            session_header="x-opencode-session",
            second=SecondList(f"{zen}/models", zen, "-free"),
        ),
        free_suffix="-free",
    )
    return providers


BackendFactory = Callable[[Provider, str, str | None, int | None], Backend]


def default_factory(
    transport: httpx.AsyncBaseTransport | None = None,
) -> BackendFactory:
    def make(
        provider: Provider, model: str, key: str | None, num_ctx: int | None
    ) -> Backend:
        if provider.kind == "ollama":
            return OllamaBackend(
                model,
                provider.host,
                key,
                num_ctx if provider.local else None,
                provider.label,
                transport,
            )
        return OpenAIBackend(
            model, provider.host, key, provider.flavor, provider.label, transport
        )

    return make


class Providers:
    def __init__(
        self,
        settings: Settings,
        secrets: SecretsStore,
        runtime: RuntimeSettings,
        factory: BackendFactory | None = None,
    ):
        self.settings = settings
        self.secrets = secrets
        self.runtime = runtime
        self.all = build_providers(settings)
        self._factory = factory or default_factory()
        self._backends: dict[tuple[str, str, int | None], Backend] = {}
        self._retired: list[Backend] = []

    # ------------------------------------------------------------------
    # Which providers may be used
    # ------------------------------------------------------------------
    def get(self, provider_id: str) -> Provider:
        provider = self.all.get(provider_id)
        if provider is None:
            raise ProviderError(
                f"Unknown provider {provider_id!r}: {', '.join(self.all)}."
            )
        return provider

    def enabled_ids(self) -> list[str]:
        stored = self.runtime.get("providers_enabled")
        if stored is None:
            return [provider.id for provider in self.all.values() if provider.local]
        return [provider_id for provider_id in stored if provider_id in self.all]

    def has_key(self, provider: Provider) -> bool:
        return provider.key_secret is None or self.secrets.has(provider.key_secret)

    def usable(self, provider_id: str) -> bool:
        provider = self.all.get(provider_id)
        return (
            provider is not None
            and provider_id in self.enabled_ids()
            and self.has_key(provider)
        )

    def why_not(self, provider_id: str) -> str:
        provider = self.get(provider_id)
        if provider_id not in self.enabled_ids():
            return f"{provider.label} is not enabled (console: /provider enable {provider_id})."
        if not self.has_key(provider):
            return f"{provider.label} needs its key: clara secrets set {provider.key_secret}."
        return ""

    def set_enabled(self, provider_id: str, enabled: bool) -> list[str]:
        provider = self.get(provider_id)
        current = set(self.enabled_ids())
        if enabled:
            if not self.has_key(provider):
                raise ProviderError(
                    f"{provider.label} needs its key first: clara secrets set {provider.key_secret}."
                )
            current.add(provider_id)
        else:
            current.discard(provider_id)
        return self.runtime.set("providers_enabled", sorted(current))

    def status(self) -> list[dict]:
        enabled = set(self.enabled_ids())
        return [
            {
                **provider.describe(),
                "enabled": provider.id in enabled,
                "key_set": self.has_key(provider),
                "usable": self.usable(provider.id),
            }
            for provider in self.all.values()
        ]

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------
    def backend(self, ref: str, num_ctx: int | None = None) -> Backend:
        provider_id, model = split_ref(ref)
        provider = self.get(provider_id)
        if not self.usable(provider_id):
            raise ProviderError(self.why_not(provider_id))
        slot = (provider_id, model, num_ctx if provider.local else None)
        backend = self._backends.get(slot)
        if backend is None:
            key = self.secrets.get(provider.key_secret) if provider.key_secret else None
            backend = self._backends[slot] = self._factory(
                provider, model, key, num_ctx
            )
        return backend

    def forget_backends(self, provider_id: str | None = None) -> None:
        """Drop the backends of a provider (its key changed): the next call builds them again."""
        for slot in [
            slot
            for slot in self._backends
            if provider_id is None or slot[0] == provider_id
        ]:
            self._retired.append(self._backends.pop(slot))

    def _lister(self, provider_id: str) -> Backend:
        """A backend of the provider for listing its models (the model name does not matter)."""
        return self.backend(make_ref(provider_id, "_list"))

    async def list_models(self, provider_id: str) -> list[str]:
        return await asyncio.wait_for(
            self._lister(provider_id).list_models(), CHECK_TIMEOUT
        )

    async def sizes(self, provider_id: str) -> dict[str, str]:
        backend = self._lister(provider_id)
        method = getattr(backend, "model_sizes", None)
        return await asyncio.wait_for(method(), CHECK_TIMEOUT) if method else {}

    async def capabilities(
        self, provider_id: str, names: list[str]
    ) -> dict[str, Capabilities]:
        backend = self._lister(provider_id)
        method = getattr(backend, "model_capabilities", None)
        return (
            await asyncio.wait_for(method(names), CAPABILITIES_TIMEOUT)
            if method
            else {}
        )

    async def check(self, provider_id: str) -> str | None:
        """None when the provider answers and accepts its key, else a short reason."""
        if not self.usable(provider_id):
            return self.why_not(provider_id)
        try:
            await asyncio.wait_for(self._lister(provider_id).verify(), CHECK_TIMEOUT)
        except TimeoutError:
            return "no answer"
        except Exception as error:  # every failure is a reason to show, never a crash
            return f"{type(error).__name__}: {str(error)[:200]}"
        return None

    async def aclose(self) -> None:
        self.forget_backends()
        retired, self._retired = self._retired, []
        for backend in retired:
            try:
                await backend.aclose()
            except Exception:  # closing is best effort: the process is ending  # noqa: S110
                pass
