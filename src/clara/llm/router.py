"""Which model answers, and which ones are asked if it fails.

* The conversation's own model, else the default model (runtime setting `default_model`).
* Fallbacks: your ordered list (`fallbacks`), keeping only usable providers, skipping the model itself and models
  with a smaller window (the prompt was fitted to this one). **A local model never falls back to a hosted one.**
* A **local-only** conversation must run on a local host; its fallbacks and side jobs too. If that is impossible
  the turn fails: it never quietly leaves the machine.
* Side jobs (titles, summaries) run on `upkeep_model`, else the conversation's model; for a local-only
  conversation, on a local model only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..runtime import RuntimeSettings
from .catalog import Catalog
from .providers import Provider, ProviderError, Providers, split_ref


class NoModel(Exception):
    """No model can answer: none configured, or none allowed for this conversation."""


@dataclass(frozen=True)
class Choice:
    ref: str
    provider: Provider
    model: str
    window: int
    fallbacks: tuple[Choice, ...] = field(default=(), repr=False)

    @property
    def local(self) -> bool:
        return self.provider.local


class Router:
    def __init__(self, providers: Providers, catalog: Catalog, runtime: RuntimeSettings):
        self.providers = providers
        self.catalog = catalog
        self.runtime = runtime

    def default_ref(self) -> str:
        ref = self.runtime.get("default_model")
        if not ref:
            raise NoModel("No default model is set: choose one on the Models page or with /model in the console.")
        return ref

    def _single(self, ref: str) -> Choice:
        try:
            provider_id, model = split_ref(ref)
            provider = self.providers.get(provider_id)
        except ProviderError as error:
            raise NoModel(str(error)) from None
        if not self.providers.usable(provider_id):
            raise NoModel(f"{ref} cannot be used: {self.providers.why_not(provider_id)}")
        return Choice(ref, provider, model, self.catalog.window(ref))

    def choose(self, model: str = "", local_only: bool = False) -> Choice:
        chosen = self._single(model or self.default_ref())
        if local_only and not chosen.local:
            raise NoModel(
                f"This conversation is local-only, and {chosen.ref} runs at a hosted provider: choose a model of a local "
                "Ollama host for it."
            )
        fallbacks = []
        for ref in self.runtime.get("fallbacks"):
            if ref == chosen.ref:
                continue
            try:
                option = self._single(ref)
            except NoModel:
                continue
            if (chosen.local or local_only) and not option.local:
                continue  # a local model never falls back to a hosted one
            if option.window < chosen.window:
                continue
            fallbacks.append(option)
        return Choice(chosen.ref, chosen.provider, chosen.model, chosen.window, tuple(fallbacks))

    def upkeep(self, conversation_choice: Choice | None, local_only: bool = False) -> Choice:
        """The model of a side job (a title, a summary) for a conversation answered by `conversation_choice`."""
        ref = self.runtime.get("upkeep_model")
        if ref:
            try:
                option = self._single(ref)
                if not (local_only or (conversation_choice and conversation_choice.local)) or option.local:
                    return option
            except NoModel:
                pass
        if conversation_choice is not None:
            return Choice(conversation_choice.ref, conversation_choice.provider, conversation_choice.model,
                          conversation_choice.window)
        return self.choose(local_only=local_only)
