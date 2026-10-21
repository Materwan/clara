"""Things you change while Clara runs, kept in the database (`settings` table) as JSON.

These are not `.env` settings: there is one source for each value, and the console and the Admin page change it
live. Unknown keys are refused, so a typo cannot create a setting nobody reads.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .db import Database
from .events.routing import check_routes
from .timeutil import now_iso


@dataclass(frozen=True)
class Knob:
    default: Any
    check: Callable[[Any], Any]
    description: str


def _ref_or_empty(value: Any) -> str:
    value = str(value or "").strip()
    if value and ":" not in value:
        raise ValueError("a model is written provider:model")
    return value


def _refs(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("a list of provider:model names")
    return [_ref_or_empty(item) for item in value if item.strip()]


def _names(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("a list of names")
    return sorted({item.strip() for item in value if item.strip()})


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("true or false")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("a text")
    return value


KNOBS: dict[str, Knob] = {
    "default_model": Knob("", _ref_or_empty, "The model that answers when a conversation has none of its own."),
    "upkeep_model": Knob("", _ref_or_empty, "The model for titles and summaries ('': the default model)."),
    "fallbacks": Knob([], _refs, "Models asked in turn when the one answering is busy or unreachable."),
    "providers_enabled": Knob(None, _names, "Providers that may be used (local Ollama hosts are enabled at first)."),
    "tainted_rule": Knob(True, _bool, "After untrusted content, raise outside-world writes and networked runs to ask."),
    "debug_until": Knob("", _text, "Full prompts and answers go to the debug log until this instant ('': off)."),
    "sandbox_image": Knob("", _text, "The sandbox image pinned by the last release ('': CLARA_SANDBOX_IMAGE)."),
    "hidden_tools": Knob({}, lambda value: {str(k): _names(v) for k, v in dict(value).items()},
                         "Tools not offered to some clients: {client: [tool, ...]}."),
    "discord_routes": Knob({}, check_routes, "Where Discord posts each kind of message: {category: notification|general|alerts|off}; "
                           "a category not named keeps its default."),
    "mail_notify": Knob(False, _bool, "Notify you of new mail in the inbox of a connected Gmail mailbox."),
    "mail_notify_senders": Knob([], _names, "Only for senders whose name or address contains one of these words ([]: all)."),
}


class RuntimeSettings:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> Any:
        knob = KNOBS[key]
        row = self.db.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return knob.default
        try:
            return json.loads(row["value"])
        except ValueError:
            return knob.default

    def set(self, key: str, value: Any) -> Any:
        knob = KNOBS.get(key)
        if knob is None:
            raise KeyError(f"Unknown setting {key!r}: {', '.join(KNOBS)}.")
        try:
            value = knob.check(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{key}: {error}") from None
        self.db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), now_iso()),
        )
        return value

    def reset(self, key: str) -> None:
        if key not in KNOBS:
            raise KeyError(key)
        self.db.execute("DELETE FROM settings WHERE key = ?", (key,))

    def describe(self) -> list[dict]:
        return [{"key": key, "value": self.get(key), "description": knob.description} for key, knob in KNOBS.items()]
