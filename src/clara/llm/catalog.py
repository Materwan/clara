"""The model catalogue: what each usable provider offers, what each model can do, and which ones the pickers show
(free models last).

Provider answers are cached for a minute. Sizes and capabilities are kept in the `models` table, so they are known
when a provider is down. The window of a model is what its provider says (capped, for a local host, at
CLARA_OLLAMA_CONTEXT, which is also what Clara asks Ollama to use), else the provider's default.

A model is shown in the pickers when you showed it (`/models show`), or, if you never chose either way, when it is
free (OpenCode's `-free` models) and its provider lists it now. Hiding such a model is a choice too. The pickers list
the free models after the others. A model that does not work (OpenCode lists models of APIs Clara does not speak, see
`opencode.py`) is never shown, and cannot be: its `fit` says why.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..db import Database
from ..timeutil import now_iso
from .providers import ProviderError, Providers, make_ref, split_ref
from .types import Capabilities, Fit

CACHE_SECONDS = 60.0
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmbt])\b", re.IGNORECASE)
_UNITS = {"k": 1e-6, "m": 1e-3, "b": 1.0, "t": 1e3}


def parse_size(text: str | None) -> float | None:
    match = _SIZE.match(text or "")
    if match is None:
        return None
    size = float(match.group(1)) * _UNITS[match.group(2).lower()]
    return size if size > 0 else None


@dataclass(frozen=True)
class ModelInfo:
    ref: str
    provider: str
    name: str
    local: bool
    shown: bool
    listed: bool
    size_b: float | None
    capabilities: Capabilities
    window: int
    fit: Fit = Fit()  # whether Clara can use it: a model that does not work is never shown in the pickers

    def describe(self) -> dict:
        return {
            "ref": self.ref,
            "provider": self.provider,
            "name": self.name,
            "local": self.local,
            "shown": self.shown,
            "listed": self.listed,
            "size_b": self.size_b,
            "capabilities": self.capabilities.describe(),
            "window": self.window,
            "fit": self.fit.describe(),
        }


class Catalog:
    def __init__(self, db: Database, providers: Providers, clock: Callable[[], float] = time.monotonic):
        self.db = db
        self.providers = providers
        self._clock = clock
        self._listed: dict[str, list[str]] = {}
        self.problems: dict[str, str] = {}
        self._fresh_until = 0.0
        self._lock = asyncio.Lock()

    def _row(self, ref: str):
        return self.db.one("SELECT * FROM models WHERE ref = ?", (ref,))

    def _upsert(self, ref: str, **fields) -> None:
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{name} = excluded.{name}" for name in fields)
        self.db.execute(
            f"INSERT INTO models (ref, {columns}) VALUES (?, {marks}) ON CONFLICT (ref) DO UPDATE SET {updates}",
            (ref, *fields.values()),
        )

    async def refresh(self, force: bool = False) -> None:
        async with self._lock:
            if not force and self._clock() < self._fresh_until:
                return
            usable = [pid for pid in self.providers.all if self.providers.usable(pid)]

            async def ask(provider_id: str) -> None:
                try:
                    names = await self.providers.list_models(provider_id)
                except Exception as error:  # a provider that cannot be listed is reported, not fatal
                    self.problems[provider_id] = f"{type(error).__name__}: {str(error)[:200]}"
                    return
                self.problems.pop(provider_id, None)
                self._listed[provider_id] = names
                try:
                    sizes = await self.providers.sizes(provider_id)
                except Exception:
                    sizes = {}
                try:
                    found = await self.providers.capabilities(provider_id, names)
                except Exception:
                    found = {}
                now = now_iso()
                for name in names:
                    fields: dict = {"listed_at": now}
                    size = parse_size(sizes.get(name))
                    if size is not None:
                        fields["size_b"] = size
                    if name in found:
                        fields["capabilities"] = found[name].to_json()
                    self._upsert(make_ref(provider_id, name), **fields)

            await asyncio.gather(*(ask(provider_id) for provider_id in usable))
            for provider_id in list(self._listed):
                if provider_id not in usable:
                    self._listed.pop(provider_id)
            self._fresh_until = self._clock() + CACHE_SECONDS

    def capabilities(self, ref: str) -> Capabilities:
        row = self._row(ref)
        return Capabilities.from_json(row["capabilities"]) if row else Capabilities()

    def window(self, ref: str) -> int:
        try:
            provider = self.providers.get(split_ref(ref)[0])
        except ProviderError:
            return 32_768
        said = self.capabilities(ref).context
        if provider.local:
            return min(said, provider.context) if said else provider.context
        return said or provider.context

    def _is_free(self, provider_id: str, name: str) -> bool:
        provider = self.providers.all.get(provider_id)
        return bool(provider and provider.free_suffix and name.endswith(provider.free_suffix))

    def fit(self, ref: str) -> Fit:
        provider_id, name = split_ref(ref)
        provider = self.providers.all.get(provider_id)
        return provider.fit(name) if provider else Fit()

    def _is_shown(self, ref: str, listed: bool) -> bool:
        if self.fit(ref).works is False:
            return False
        row = self._row(ref)
        if row is not None and (row["shown"] or row["chosen"]):
            return bool(row["shown"])
        provider_id, name = split_ref(ref)
        return listed and self._is_free(provider_id, name)

    def info(self, ref: str, listed: bool = True) -> ModelInfo:
        provider_id, name = split_ref(ref)
        row = self._row(ref)
        provider = self.providers.get(provider_id)
        return ModelInfo(
            ref,
            provider_id,
            name,
            provider.local,
            self._is_shown(ref, listed),
            listed,
            row["size_b"] if row else None,
            Capabilities.from_json(row["capabilities"]) if row else Capabilities(),
            self.window(ref),
            provider.fit(name),
        )

    async def listing(self, force: bool = False) -> list[ModelInfo]:
        """Every model of every usable provider, plus the shown ones whose provider did not answer."""
        await self.refresh(force)
        refs: dict[str, bool] = {make_ref(p, n): True for p, names in self._listed.items() for n in names}
        for row in self.db.all("SELECT ref FROM models WHERE shown = 1"):
            refs.setdefault(row["ref"], False)
        found = []
        for ref, listed in refs.items():
            try:
                if self.providers.usable(split_ref(ref)[0]):
                    found.append(self.info(ref, listed))
            except ProviderError:
                continue
        return sorted(found, key=lambda info: (not info.local, info.provider, info.name))

    def shown(self) -> list[ModelInfo]:
        """The models the pickers offer (shown, of a usable provider)."""
        refs = {row["ref"] for row in self.db.all("SELECT ref FROM models WHERE shown = 1")}
        refs |= {
            make_ref(p, n)
            for p, names in self._listed.items()
            for n in names
            if self._is_free(p, n) and self._is_shown(make_ref(p, n), True)
        }
        found = []
        for ref in refs:
            try:
                if self.providers.usable(split_ref(ref)[0]) and (info := self.info(ref)).shown:
                    found.append(info)
            except ProviderError:
                continue
        return sorted(found, key=lambda info: (self._is_free(info.provider, info.name), info.ref))  # free ones last

    def set_shown(self, refs: list[str], shown: bool) -> None:
        for ref in refs:
            self.providers.get(split_ref(ref)[0])
            if shown and (fit := self.fit(ref)).works is False:
                raise ProviderError(f"{ref} cannot be shown in the pickers: {fit.why}.")
        for ref in refs:
            provider_id, name = split_ref(ref)
            self._upsert(make_ref(provider_id, name), shown=int(shown), chosen=1)
