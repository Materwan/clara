"""Services that speak the OpenAI chat API: Gemini, DeepSeek, Mistral, OpenCode Go (`{host}/chat/completions`,
`{host}/models`)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .types import Capabilities, LlmChunk, LlmError, ToolCall

log = logging.getLogger(__name__)

# Gemini refuses a function call sent back without its signature; this stands in for the signature of a call made by
# another provider (or before signatures were kept), as Google's documentation allows.
SKIP_SIGNATURE = "skip_thought_signature_validator"
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 600.0
GEMINI_NOT_CHAT = ("tts", "image", "embedding", "transcribe", "audio", "live", "bidi")


@dataclass(frozen=True)
class SecondList:
    """Models of another address, added to the provider's own: OpenCode Zen's free models next to those of Go."""

    models_url: str  # read without the key
    host: str  # where those models are asked (`{host}/chat/completions`)
    suffix: str  # keep only the models named so


@dataclass(frozen=True)
class OpenAIFlavor:
    """What sets one OpenAI-compatible service apart from the others."""

    stream_usage: bool = True  # ask for the token counts at the end of a stream
    reasoning_back: bool = False  # send the reasoning back with the tool calls (DeepSeek requires it)
    signatures: bool = False  # send each tool call's thought signature back (Gemini requires it)
    tool_names: bool = False  # name the tool on a tool result (Mistral)
    extra_body: tuple[tuple[str, Any], ...] = ()
    native_models: str = ""  # the provider's own model list, when the OpenAI one has only names (Gemini)
    models_url: str = ""  # where the models are listed, when it is not `{host}/models` (OpenCode Go)
    headers: tuple[tuple[str, str], ...] = ()  # sent with every request (OpenCode wants a User-Agent of its own)
    session_header: str = ""  # the header naming the conversation, when the service insists on one (OpenCode)
    second: SecondList | None = None


def google_record(model_id: str, model: dict) -> dict:
    chat = (
        model_id.startswith("gemini-")
        and "generateContent" in (model.get("supportedGenerationMethods") or [])
        and not any(word in model_id for word in GEMINI_NOT_CHAT)
    )
    return {
        "thinking": bool(model.get("thinking")),
        "tools": True if chat else None,
        "vision": True if chat else None,
        "context": model.get("inputTokenLimit"),
    }


def cached_tokens(usage: dict) -> int:
    details = usage.get("prompt_tokens_details")
    found = details.get("cached_tokens") if isinstance(details, dict) else None
    return int(found or usage.get("prompt_cache_hit_tokens") or 0)


def call_id(number: int) -> str:
    """Tool call ids, made up when the messages are sent: Mistral wants exactly 9 letters or digits."""
    return f"c{number:08d}"


def _arguments(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        log.warning("a tool call came with arguments that are not JSON (%d characters)", len(text))
        return {}
    return value if isinstance(value, dict) else {}


def _error_detail(response: httpx.Response) -> str:
    try:
        error: Any = response.json()
    except ValueError:
        return " ".join(response.text.split())[:300] or response.reason_phrase
    if isinstance(error, list) and error:
        error = error[0]
    if isinstance(error, dict):
        error = error.get("error", error)
    if isinstance(error, dict):
        error = error.get("message") or error.get("detail") or error
    return " ".join(str(error).split())[:300]


class OpenAIBackend:
    def __init__(self, model: str, host: str, api_key: str | None, flavor: OpenAIFlavor | None = None,
                 label: str = "The provider", transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.host = host.rstrip("/")
        self._api_key = api_key
        self._flavor = flavor or OpenAIFlavor()
        self.label = label
        self._transport = transport
        self._session = uuid.uuid4().hex  # for a request that belongs to no conversation
        self._own: set[str] | None = None  # the models of the main list, to tell which address a model belongs to
        self._http = httpx.AsyncClient(
            headers={**dict(self._flavor.headers), **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _refused(self, response: httpx.Response) -> LlmError:
        await response.aread()
        detail = _error_detail(response)
        status = response.status_code
        if status in (401, 403):
            return LlmError(f"{self.label} refused the API key (HTTP {status}): {detail}", status)
        try:
            retry_after = float(response.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        return LlmError(f"{self.label} answered HTTP {status}: {detail}", status, retry_after)

    def convert(self, messages: list[dict]) -> list[dict]:
        """The messages as the OpenAI API takes them: tool calls get ids, each result the id of its call."""
        flavor = self._flavor
        converted: list[dict] = []
        number = 0
        waiting: list[str] = []
        for message in messages:
            role = message["role"]
            if role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
                calls = message.get("tool_calls") or []
                if calls:
                    waiting = []
                    entry["tool_calls"] = []
                    for call in calls:
                        number += 1
                        function = call.get("function") or {}
                        arguments = function.get("arguments") or {}
                        item: dict[str, Any] = {
                            "id": call_id(number),
                            "type": "function",
                            "function": {
                                "name": function.get("name", ""),
                                "arguments": arguments if isinstance(arguments, str)
                                else json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                        if flavor.signatures:
                            item["extra_content"] = call.get("extra_content") or {
                                "google": {"thought_signature": SKIP_SIGNATURE}
                            }
                        entry["tool_calls"].append(item)
                        waiting.append(item["id"])
                    if not entry["content"]:
                        entry["content"] = None
                    if flavor.reasoning_back:
                        entry["reasoning_content"] = message.get("thinking") or ""
                converted.append(entry)
            elif role == "tool":
                number += 1
                entry = {
                    "role": "tool",
                    "tool_call_id": waiting.pop(0) if waiting else call_id(number),
                    "content": message.get("content") or "",
                }
                if flavor.tool_names and message.get("tool_name"):
                    entry["name"] = message["tool_name"]
                converted.append(entry)
            elif role == "user" and message.get("images"):
                content: list[dict[str, Any]] = [{"type": "text", "text": message.get("content") or ""}]
                for picture in message["images"]:
                    content.append({"type": "image_url",
                                    "image_url": {"url": f"data:{picture['mime']};base64,{picture['data']}"}})
                converted.append({"role": role, "content": content})
            else:
                converted.append({"role": role, "content": message.get("content") or ""})
        return converted

    def body(self, messages: list[dict], tools: list[dict] | None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": self.convert(messages), "stream": True}
        if tools:
            body["tools"] = tools
        if self._flavor.stream_usage:
            body["stream_options"] = {"include_usage": True}
        body.update(dict(self._flavor.extra_body))
        return body

    async def stream(self, messages: list[dict], tools: list[dict] | None,
                     session: str | None = None) -> AsyncIterator[LlmChunk]:
        calls: dict[int, dict[str, Any]] = {}
        counted = (0, 0, 0)
        named = {self._flavor.session_header: session or self._session} if self._flavor.session_header else None
        request = self._http.stream("POST", f"{await self._host_of_model()}/chat/completions",
                                    json=self.body(messages, tools), headers=named)
        async with request as response:
            if response.is_error:
                raise await self._refused(response)
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    part = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(part, dict):
                    continue
                if part.get("error"):
                    error = part["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    message = error.get("message", error) if isinstance(error, dict) else error
                    raise LlmError(f"{self.label}: {message}", code if isinstance(code, int) else None)
                if usage := part.get("usage"):
                    # Gemini repeats its running count on every chunk, others give it once: pass on the increase
                    said = (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                            cached_tokens(usage))
                    now = tuple(max(old, new) for old, new in zip(counted, said, strict=True))
                    if now != counted:
                        yield LlmChunk(prompt_tokens=now[0] - counted[0], completion_tokens=now[1] - counted[1],
                                       cached_tokens=now[2] - counted[2])
                        counted = now
                chunk = LlmChunk()
                for choice in part.get("choices") or []:
                    delta = choice.get("delta") or {}
                    chunk.text += delta.get("content") or ""
                    chunk.thinking += delta.get("reasoning_content") or delta.get("reasoning") or ""
                    for piece in delta.get("tool_calls") or []:
                        index = int(piece.get("index", len(calls)))
                        slot = calls.setdefault(index, {"name": "", "arguments": "", "extra": None})
                        function = piece.get("function") or {}
                        slot["name"] += function.get("name") or ""
                        arguments = function.get("arguments") or ""
                        slot["arguments"] += arguments if isinstance(arguments, str) else json.dumps(arguments)
                        if piece.get("extra_content"):
                            slot["extra"] = piece["extra_content"]
                if chunk.text or chunk.thinking:
                    yield chunk
        if calls:
            yield LlmChunk(tool_calls=[
                ToolCall(slot["name"], _arguments(slot["arguments"]), slot["extra"])
                for _, slot in sorted(calls.items()) if slot["name"]
            ])

    async def _names(self, url: str, keyed: bool = True) -> set[str]:
        request = self._http.build_request("GET", url, timeout=CONNECT_TIMEOUT)
        if not keyed:
            request.headers.pop("authorization", None)  # a list that is open to all does not get the key
        response = await self._http.send(request)
        if response.is_error:
            raise await self._refused(response)
        models = response.json().get("data") or []
        names = {str(model.get("id", "")).removeprefix("models/") for model in models if isinstance(model, dict)}
        return {name for name in names if name}

    async def list_models(self) -> list[str]:
        second = self._flavor.second
        own_url = self._flavor.models_url or f"{self.host}/models"
        if second is None:
            self._own = await self._names(own_url)
            return sorted(self._own)
        own, more = await asyncio.gather(self._names(own_url), self._names(second.models_url, keyed=False),
                                         return_exceptions=True)
        if isinstance(own, BaseException):
            raise own
        self._own = own
        if isinstance(more, BaseException):  # the second list is a bonus: the provider works without it
            log.warning("%s: its second model list could not be read (%s)", self.label, more)
            more = set()
        return sorted(own | {name for name in more if name.endswith(second.suffix)})

    async def _host_of_model(self) -> str:
        """The address to ask: the main one, unless the model is known only to the second list."""
        second = self._flavor.second
        if second is None:
            return self.host
        try:
            if self._own is None:
                self._own = await self._names(self._flavor.models_url or f"{self.host}/models")
        except Exception as error:  # cannot tell: the main address is the one the key was made for
            log.warning("%s: its model list could not be read (%s), asking the main address", self.label, error)
            return self.host
        return self.host if self.model in self._own else second.host.rstrip("/")

    async def model_capabilities(self, names: list[str]) -> dict[str, Capabilities]:
        if not self._flavor.native_models or not self._api_key:
            return {}
        found: dict[str, Capabilities] = {}
        page = ""
        async with httpx.AsyncClient(timeout=CONNECT_TIMEOUT, transport=self._transport) as http:
            while True:
                response = await http.get(
                    self._flavor.native_models,
                    params={"pageSize": 1000, **({"pageToken": page} if page else {})},
                    headers={"x-goog-api-key": self._api_key},
                )
                if response.is_error:
                    raise await self._refused(response)
                data = response.json()
                for model in data.get("models") or []:
                    model_id = str(model.get("name", "")).removeprefix("models/")
                    if model_id:
                        found[model_id] = Capabilities.from_record(google_record(model_id, model))
                page = str(data.get("nextPageToken") or "")
                if not page:
                    break
        return {name: found[name] for name in names if name in found}

    async def verify(self) -> None:
        await self.list_models()  # each of these services wants the key to list its models
