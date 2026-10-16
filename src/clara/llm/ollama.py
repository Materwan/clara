"""Ollama, spoken directly over HTTP: a host you own (this machine, your PC on the tailnet) or ollama.com with a key.

`POST /api/chat` streams one JSON object per line; `GET /api/tags` lists the models; `POST /api/show` says what a
model can do and its context length.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .types import Capabilities, LlmChunk, LlmError, ToolCall

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 600.0  # the agent gives up on a silent model sooner (idle timeout)
SHOW_AT_ONCE = 4


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return " ".join(response.text.split())[:300] or response.reason_phrase
    if isinstance(data, dict) and data.get("error"):
        return " ".join(str(data["error"]).split())[:300]
    return response.reason_phrase


def plain_messages(messages: list[dict]) -> list[dict]:
    """The messages as Ollama takes them: the reasoning and what other providers keep with a call left out; pictures
    as base64 strings."""
    plain = []
    for message in messages:
        pictures = message.get("images") or []
        entry = {key: value for key, value in message.items() if key not in ("thinking", "images")}
        if entry.get("tool_calls"):
            entry["tool_calls"] = [{"function": call["function"]} for call in entry["tool_calls"]]
        if pictures:
            entry["images"] = [picture["data"] for picture in pictures]
        plain.append(entry)
    return plain


def record_of(show: dict) -> dict:
    """Ollama's words for a model, as Capabilities.from_record takes them."""
    info = show.get("model_info") or show.get("modelinfo") or {}
    context = next((value for key, value in info.items() if str(key).endswith(".context_length")), None)
    capabilities = show.get("capabilities")
    if not isinstance(capabilities, list):
        return {"context": context}
    said = set(capabilities)
    return {"thinking": "thinking" in said, "tools": "tools" in said, "vision": "vision" in said, "context": context}


class OllamaBackend:
    def __init__(self, model: str, host: str, api_key: str | None = None, num_ctx: int | None = None,
                 label: str = "Ollama", transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.host = host.rstrip("/")
        self.label = label
        self._num_ctx = num_ctx
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=self.host,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def body(self, messages: list[dict], tools: list[dict] | None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": plain_messages(messages), "stream": True}
        if tools:
            body["tools"] = tools
        if self._num_ctx:
            body["options"] = {"num_ctx": self._num_ctx}  # Ollama's own default window is tiny
        return body

    async def stream(self, messages: list[dict], tools: list[dict] | None,
                     session: str | None = None) -> AsyncIterator[LlmChunk]:
        async with self._http.stream("POST", "/api/chat", json=self.body(messages, tools)) as response:
            if response.is_error:
                await response.aread()
                raise LlmError(f"{self.label} answered HTTP {response.status_code}: {_error_text(response)}",
                               response.status_code)
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    part = json.loads(line)
                except ValueError:
                    continue
                if part.get("error"):
                    raise LlmError(f"{self.label}: {part['error']}")
                message = part.get("message") or {}
                calls = [
                    ToolCall(str(call["function"].get("name", "")), dict(call["function"].get("arguments") or {}))
                    for call in message.get("tool_calls") or []
                    if isinstance(call, dict) and isinstance(call.get("function"), dict)
                ]
                chunk = LlmChunk(
                    text=message.get("content") or "",
                    thinking=message.get("thinking") or "",
                    tool_calls=calls,
                    prompt_tokens=int(part.get("prompt_eval_count") or 0) if part.get("done") else 0,
                    completion_tokens=int(part.get("eval_count") or 0) if part.get("done") else 0,
                )
                if chunk.text or chunk.thinking or chunk.tool_calls or chunk.prompt_tokens or chunk.completion_tokens:
                    yield chunk

    async def _tags(self) -> list[dict]:
        response = await self._http.get("/api/tags", timeout=CONNECT_TIMEOUT)
        if response.is_error:
            raise LlmError(f"{self.label} answered HTTP {response.status_code}: {_error_text(response)}",
                           response.status_code)
        models = response.json().get("models") or []
        return [model for model in models if isinstance(model, dict)]

    async def list_models(self) -> list[str]:
        return sorted({str(model.get("model") or model.get("name")) for model in await self._tags()
                       if model.get("model") or model.get("name")})

    async def model_sizes(self) -> dict[str, str]:
        """What the host says of each model's size ("7.6B"), for the models that say it."""
        sizes: dict[str, str] = {}
        for model in await self._tags():
            size = (model.get("details") or {}).get("parameter_size")
            name = model.get("model") or model.get("name")
            if name and size:
                sizes[str(name)] = str(size)
        return sizes

    async def model_capabilities(self, names: list[str]) -> dict[str, Capabilities]:
        slots = asyncio.Semaphore(SHOW_AT_ONCE)

        async def ask(name: str) -> tuple[str, Capabilities] | None:
            async with slots:
                try:
                    response = await self._http.post("/api/show", json={"model": name}, timeout=CONNECT_TIMEOUT * 3)
                    response.raise_for_status()
                    return name, Capabilities.from_record(record_of(response.json()))
                except (httpx.HTTPError, ValueError):
                    return None  # one model that cannot be shown does not hide the others

        answers = await asyncio.gather(*(ask(name) for name in names))
        return dict(answer for answer in answers if answer is not None)

    async def verify(self) -> None:
        if self._api_key:
            # The listing is public on ollama.com and proves nothing about the key; /api/me refuses a bad one
            response = await self._http.post("/api/me", timeout=CONNECT_TIMEOUT)
            if response.status_code in (401, 403):
                raise LlmError(f"{self.label} refused the API key (HTTP {response.status_code}).", response.status_code)
        await self._tags()
