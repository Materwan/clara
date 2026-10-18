"""Services that speak the OpenAI chat API: Gemini, DeepSeek, Mistral, OpenCode Go (`{host}/chat/completions`,
`{host}/models`)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .retry import KEY_STATUSES, NOT_THE_KEY
from .types import Capabilities, Known, LlmChunk, LlmError, ToolCall

log = logging.getLogger(__name__)

# Gemini refuses a function call sent back without its signature; this stands in for the signature of a call made by
# another provider (or before signatures were kept), as Google's documentation allows.
SKIP_SIGNATURE = "skip_thought_signature_validator"
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 600.0
GEMINI_NOT_CHAT = ("tts", "image", "embedding", "transcribe", "audio", "live", "bidi")
CODE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}")  # what may pass for an error's name: a token, never a sentence
UPSTREAM_CODE = re.compile(r"Upstream request failed:\s*\[([A-Za-z0-9][\w.-]{0,63})\]")  # OpenCode: "...: [invalid_value] ..."


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
    known: Callable[[str], Known | None] | None = None  # what the service's lists say of a model: no question asked (OpenCode)


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


def _error_body(response: httpx.Response) -> Any:
    """The error of an answer, out of its wrappers (a list of one, an `error` key); ValueError when it is not JSON."""
    error: Any = response.json()
    if isinstance(error, list) and error:
        error = error[0]
    if isinstance(error, dict):
        error = error.get("error", error)
    return error


def _error_detail(response: httpx.Response) -> str:
    try:
        error = _error_body(response)
    except ValueError:
        return " ".join(response.text.split())[:300] or response.reason_phrase
    if isinstance(error, dict):
        error = error.get("message") or error.get("detail") or error
    return " ".join(str(error).split())[:300]


def _error_name(error: Any) -> str | None:
    """The provider's short name for a failure: its `code`, the `[code]` OpenCode puts in front of a message, Google's
    `status`, or its `type`. Only a plain token counts, so the name is safe to log where the message is not."""
    if not isinstance(error, dict):
        return None
    found = UPSTREAM_CODE.match(str(error.get("message") or ""))
    for candidate in (error.get("code"), found.group(1) if found else None, error.get("status"), error.get("type")):
        if isinstance(candidate, str) and CODE.fullmatch(candidate):
            return candidate
    return None


def _error_code(response: httpx.Response) -> str | None:
    try:
        return _error_name(_error_body(response))
    except ValueError:
        return None


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
        code = _error_code(response)
        if status in KEY_STATUSES and code not in NOT_THE_KEY:
            return LlmError(f"{self.label} refused the API key (HTTP {status}): {detail}", status, code=code)
        try:
            retry_after = float(response.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        return LlmError(f"{self.label} answered HTTP {status}: {detail}", status, retry_after, code)

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
        request = self._http.stream("POST", f"{self.host}/chat/completions",
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
                    raise LlmError(f"{self.label}: {message}", code if isinstance(code, int) else None,
                                   code=_error_name(error))
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

    async def list_models(self) -> list[str]:
        response = await self._http.get(self._flavor.models_url or f"{self.host}/models", timeout=CONNECT_TIMEOUT)
        if response.is_error:
            raise await self._refused(response)
        models = response.json().get("data") or []
        names = {str(model.get("id", "")).removeprefix("models/") for model in models if isinstance(model, dict)}
        return sorted(name for name in names if name)

    async def model_capabilities(self, names: list[str]) -> dict[str, Capabilities]:
        if self._flavor.known is not None:
            return {name: found.capabilities for name in names if (found := self._flavor.known(name))}
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
