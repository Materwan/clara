"""Tool calls a model wrote as text.

LongCat sometimes puts its call in `content` in its own markup instead of `tool_calls`:
`<longcat_tool_call>local_read <longcat_arg_key>path</longcat_arg_key> <longcat_arg_value>a.py</longcat_arg_value>`.
`TextCalls` keeps that markup from being shown while the text streams, then reads the calls out of it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .types import ToolCall

OPEN = re.compile(r"<\s*longcat_tool_call\s*>")
CLOSE = re.compile(r"<\s*/\s*longcat_tool_call\s*>")
NAME = re.compile(r"\s*([\w.-]+)")
ARG = re.compile(
    r"<\s*longcat_arg_key\s*>(.*?)<\s*/\s*longcat_arg_key\s*>\s*"
    r"<\s*longcat_arg_value\s*>(.*?)<\s*/\s*longcat_arg_value\s*>",
    re.S,
)
HOLD = 32  # a tail this short, from a `<`, may be the start of the marker: wait for more before showing it


class TextCalls:
    def __init__(self, tools: list[dict]):
        self.types: dict[str, dict[str, Any]] = {}
        for tool in tools:
            function = tool.get("function") or {}
            properties = (function.get("parameters") or {}).get("properties") or {}
            self.types[function.get("name", "")] = {key: (spec or {}).get("type") for key, spec in properties.items()}
        self.held = ""
        self.caught: str | None = None  # from the marker on, once there is one

    def feed(self, text: str) -> str:
        """The part of the stream that is plain text, now."""
        if self.caught is not None:
            self.caught += text
            return ""
        buffer = self.held + text
        found = OPEN.search(buffer)
        if found:
            self.held, self.caught = "", buffer[found.start() :]
            return buffer[: found.start()]
        cut = buffer.rfind("<")
        if cut >= 0 and len(buffer) - cut < HOLD and ">" not in buffer[cut:]:
            self.held = buffer[cut:]
            return buffer[:cut]
        self.held = ""
        return buffer

    def finish(self) -> tuple[str, list[ToolCall]]:
        """The text still held, and the calls read from the markup. Markup that holds no call is text after all."""
        rest, caught = self.held, self.caught or ""
        self.held, self.caught = "", None
        calls = [call for part in OPEN.split(caught)[1:] if (call := self._call(part))]
        return (rest if calls else rest + caught), calls

    def _call(self, part: str) -> ToolCall | None:
        named = NAME.match(part)
        if not named or named.group(1) not in self.types:
            return None
        name = named.group(1)
        arguments: dict[str, Any] = {}
        for key, value in ARG.findall(CLOSE.split(part)[0]):
            key, kind = key.strip(), self.types[name].get(key.strip())
            if kind not in (None, "string"):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            arguments[key] = value
        return ToolCall(name, arguments)
