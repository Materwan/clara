"""Shrinking a long conversation: the transcript and the summary request.

When a conversation fills the model's window, its older messages are replaced by a summary the model writes. The
messages stay in the database (unless CLARA_PURGE_SUMMARISED); the summary stands for them from then on. Nothing is
dropped silently: a transcript too long for one request is summarised in several chunks, each continuing the summary
of the one before.
"""

from __future__ import annotations

import json

from .estimate import CHARS_PER_TOKEN

MAX_TRANSCRIPT_CHARS = 60_000
MIN_TRANSCRIPT_CHARS = 4_000
MESSAGE_CHARS = 1_500
TOOL_RESULT_CHARS = 300
TOOL_ARGUMENT_CHARS = 200

COMPACT_PROMPT = (
    "You are summarising a conversation between a person and their AI assistant, Clara, so that Clara can continue "
    "the work without the original messages.\n"
    "Write a factual summary (at most 800 words) that keeps: the person's goals, instructions and preferences; "
    "decisions taken; files that were read, created or changed; commands that were run and what they showed; errors "
    "and how they were solved; what remains to be done.\n"
    "Copy identifiers exactly as written, never paraphrase them: file paths, commands, names, ids, numbers, URLs, "
    "error messages.\n"
    "Write it in the language the person speaks. No commentary, no greeting."
)


def transcript_budget(window: int) -> int:
    """How many characters of transcript one summary request may carry, for a model window."""
    return max(MIN_TRANSCRIPT_CHARS, min(MAX_TRANSCRIPT_CHARS, int(window * CHARS_PER_TOKEN * 0.4)))


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " […]"


def _call_text(call: dict) -> str:
    function = call.get("function") or {}
    arguments = json.dumps(function.get("arguments") or {}, ensure_ascii=False, default=str)
    return f"{function.get('name', '?')}({_clip(arguments, TOOL_ARGUMENT_CHARS)})"


def message_line(role: str, content: str, tool_calls: list | None = None, tool_name: str | None = None) -> str:
    """One message as a line of transcript ("" when it says nothing)."""
    if role == "tool":
        return f"[{tool_name or 'tool'} result] {_clip(content, TOOL_RESULT_CHARS)}"
    if role == "assistant":
        said = _clip(content, MESSAGE_CHARS)
        calls = [_call_text(call) for call in tool_calls or []]
        if calls:
            said = (said + " " if said else "") + f"[called: {', '.join(calls)}]"
        return f"Clara: {said}" if said else ""
    return f"Person: {_clip(content, MESSAGE_CHARS)}"


def build_transcript(messages: list) -> str:
    """Stored messages (anything with role, content, tool_calls, tool_name) as plain text for the summariser."""
    return "\n".join(
        line for line in (message_line(m.role, m.content, m.tool_calls, m.tool_name) for m in messages) if line
    )


def chunk_messages(messages: list, budget: int = MAX_TRANSCRIPT_CHARS) -> list[list]:
    """The messages in consecutive groups whose transcripts each fit in `budget` characters."""
    chunks: list[list] = []
    current: list = []
    used = 0
    for message in messages:
        size = len(message_line(message.role, message.content, message.tool_calls, message.tool_name))
        size = size + 1 if size else 0
        if current and used + size > budget:
            chunks.append(current)
            current, used = [], 0
        current.append(message)
        used += size
    if current:
        chunks.append(current)
    return chunks


def summary_request(transcript: str, previous_summary: str = "", focus: str = "") -> list[dict]:
    request = ""
    if previous_summary:
        request += f"Summary of the conversation before these messages:\n{previous_summary}\n\n"
    request += f"Conversation:\n{transcript}"
    if focus:
        request += f"\n\nPay particular attention to: {focus}"
    return [{"role": "system", "content": COMPACT_PROMPT}, {"role": "user", "content": request}]
