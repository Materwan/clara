"""Token estimates, made locally so they work when a provider reports nothing (or less than it read, when cached)."""

from __future__ import annotations

import json
import math

CHARS_PER_TOKEN = 3.5  # a rough average for mixed prose and code
IMAGE_TOKENS = 1_200  # what a picture counts in a prompt (providers do not say beforehand)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def estimate_stored(content: str, prefix: str = "", tool_calls: list | None = None) -> int:
    calls = json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else ""
    return estimate_tokens(content) + estimate_tokens(prefix) + estimate_tokens(calls)


def estimate_prompt_tokens(messages: list[dict], schemas: list[dict] | None = None) -> int:
    """What a prompt weighs: the text of its messages, their tool calls, their pictures and the tool schemas."""
    total = 0
    for message in messages:
        total += estimate_tokens(message.get("content") or "")
        if message.get("tool_calls"):
            total += estimate_tokens(json.dumps(message["tool_calls"], ensure_ascii=False, default=str))
        total += IMAGE_TOKENS * len(message.get("images") or ())
    if schemas:
        total += estimate_tokens(json.dumps(schemas, ensure_ascii=False, default=str))
    return total
