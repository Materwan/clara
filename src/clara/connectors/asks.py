"""Questions Clara asks while she works (`ask_questions`), answered in the web chat in place of the box you write in.

Unlike a QCM (connectors/forms.py), this one blocks: the turn shows the questions and waits (`CLARA_APPROVAL_WAIT`) for
your answers, which come back as the result of the call, so Clara goes on in the same answer. A question has a short
`header`, the question, 2 to 4 options (a `label` and an optional `description`) and `multi_select`; the client adds
a last choice, "Other", where you write anything. You may also dismiss the questions: Clara is told you declined.

The questions waiting are kept in memory only: they belong to a turn that is running, and a page that is reloaded while
it waits finds them again in the events of the run (`GET /v1/conversations/{id}/run`).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

MAX_QUESTIONS = 4
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_HEADER = 12
MAX_QUESTION = 300
MAX_LABEL = 80
MAX_DESCRIPTION = 200
MAX_OTHER = 2_000  # what you may type in "Other"
MAX_ASKS_PER_TURN = 5

DECLINED = (
    "The person dismissed these questions without answering. Do not ask them again: go on with your best guess and "
    "say what you assumed, or tell them what you need."
)
TIMED_OUT = (
    "The person did not answer in time and the questions were withdrawn. Go on without, or say what you need and end "
    "your turn."
)
ANSWERED = "The person answered your questions:"


class AskError(ValueError):
    """The questions or the answers are not valid (the message says what to fix)."""


class NotWaiting(AskError):
    """Nobody is waiting for these answers any more (they were answered, withdrawn, or the answer ended)."""


def _text(value: Any, name: str, limit: int, required: bool = True) -> str:
    if value is None or isinstance(value, (dict, list)):
        value = ""
    text = " ".join(str(value).split())
    if not text and required:
        raise AskError(f"{name} is empty.")
    if len(text) > limit:
        raise AskError(f"{name} is too long ({len(text)} characters, at most {limit}).")
    return text


def _items(value: Any, name: str) -> list:
    if isinstance(value, str):  # some models send a JSON text instead of a list
        try:
            value = json.loads(value)
        except ValueError:
            raise AskError(f"{name} must be a list.") from None
    if not isinstance(value, (list, tuple)):
        raise AskError(f"{name} must be a list.")
    return list(value)


def _flag(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _option(raw: Any, where: str) -> dict:
    if isinstance(raw, dict):
        return {
            "label": _text(raw.get("label"), f"{where}: a label", MAX_LABEL),
            "description": _text(raw.get("description"), f"{where}: a description", MAX_DESCRIPTION, required=False),
        }
    return {"label": _text(raw, f"{where}: an option", MAX_LABEL), "description": ""}


def _question(raw: Any, number: int) -> dict:
    where = f"Question {number}"
    if not isinstance(raw, dict):
        raise AskError(f"{where} must be an object with question, header and options.")
    options = [_option(option, where) for option in _items(raw.get("options"), f"{where}: options")]
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise AskError(f"{where}: {MIN_OPTIONS} to {MAX_OPTIONS} options are needed, not {len(options)}.")
    if len({option["label"].casefold() for option in options}) != len(options):
        raise AskError(f"{where}: the options must differ.")
    multiple = raw["multi_select"] if "multi_select" in raw else raw.get("multiSelect")
    return {
        "question": _text(raw.get("question"), f"{where}: question", MAX_QUESTION),
        "header": _text(raw.get("header"), f"{where}: header", MAX_HEADER, required=False),
        "multi_select": _flag(multiple),
        "options": options,
    }


def build_ask(arguments: dict[str, Any]) -> dict:
    """The questions Clara asked, checked and cleaned (AskError says what to fix):
    `{"questions": [{"question", "header", "multi_select", "options": [{"label", "description"}]}]}`."""
    questions = [
        _question(raw, number) for number, raw in enumerate(_items(arguments.get("questions"), "questions"), 1)
    ]
    if not questions:
        raise AskError("Ask at least one question.")
    if len(questions) > MAX_QUESTIONS:
        raise AskError(f"At most {MAX_QUESTIONS} questions in one call, not {len(questions)}.")
    return {"questions": questions}


def check_replies(ask: dict, replies: Any) -> list[dict]:
    """Your answers, one per question: `{"options": [numbers from 0], "other": "text"}`. A question that takes one
    option takes either an option or an "Other" text; one that takes several takes any of them together."""
    items = _items(replies, "answers")
    if len(items) != len(ask["questions"]):
        raise AskError(f"Answer all {len(ask['questions'])} questions.")
    cleaned = []
    for number, (question, reply) in enumerate(zip(ask["questions"], items), 1):
        if not isinstance(reply, dict):
            raise AskError(f"Answer {number} must be an object with options and other.")
        chosen = reply.get("options") or []
        if not isinstance(chosen, list) or any(isinstance(n, bool) or not isinstance(n, int) for n in chosen):
            raise AskError(f"Answer {number}: options is the list of the numbers of the chosen options.")
        chosen = sorted(set(chosen))
        if chosen and (chosen[0] < 0 or chosen[-1] >= len(question["options"])):
            raise AskError(f"Answer {number}: the options are numbered 0 to {len(question['options']) - 1}.")
        other = reply.get("other")
        other = "" if other is None else str(other).strip()
        if len(other) > MAX_OTHER:
            raise AskError(f"Answer {number}: the text is too long ({len(other)} characters, at most {MAX_OTHER}).")
        if not chosen and not other:
            raise AskError(f"Answer {number}: choose an option or write your own.")
        if not question["multi_select"] and len(chosen) + bool(other) != 1:
            raise AskError(f"Answer {number}: this question takes one answer.")
        cleaned.append({"options": chosen, "other": other})
    return cleaned


def format_replies(ask: dict, replies: list[dict]) -> str:
    """The answers as Clara reads them (the result of her call)."""
    lines = [ANSWERED]
    for number, (question, reply) in enumerate(zip(ask["questions"], replies), 1):
        given = [question["options"][index]["label"] for index in reply["options"]]
        if reply["other"]:
            given.append(f"Other: {reply['other']}")
        header = f"[{question['header']}] " if question["header"] else ""
        lines.append(f"{number}. {header}{question['question']}\n   Answer: {'; '.join(given)}")
    return "\n".join(lines)


# ---- the questions waiting ----------------------------------------------------------------------------------


@dataclass
class Pending:
    id: str
    conversation_id: str
    turn_id: str
    ask: dict
    future: asyncio.Future = field(repr=False)

    def describe(self) -> dict:
        return {"id": self.id, "conversation": self.conversation_id, **self.ask}


class Questions:
    """The `ask_questions` calls whose turn is waiting for your answers."""

    def __init__(self) -> None:
        self._pending: dict[str, Pending] = {}

    def create(self, conversation_id: str, turn_id: str, ask: dict) -> Pending:
        pending = Pending(
            uuid.uuid4().hex[:12], conversation_id, turn_id, ask, asyncio.get_running_loop().create_future()
        )
        self._pending[pending.id] = pending
        return pending

    async def wait(self, pending: Pending, seconds: float) -> dict | None:
        """What you answered (`{"answers": [...]}`, or `{"declined": True}`), or None if nothing came in time."""
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), seconds)
        except TimeoutError:
            return None
        finally:
            self._pending.pop(pending.id, None)
            if not pending.future.done():
                pending.future.cancel()

    def reply(self, ask_id: str, conversation_id: str, answers: Any = None, *, declined: bool = False) -> None:
        pending = self._pending.get(ask_id)
        if pending is None or pending.conversation_id != conversation_id or pending.future.done():
            raise NotWaiting("Clara is not waiting for these answers any more.")
        if declined:
            pending.future.set_result({"declined": True})
        else:
            pending.future.set_result({"answers": check_replies(pending.ask, answers)})

    def all(self) -> list[Pending]:
        """Every question waiting, whichever conversation asked it."""
        return list(self._pending.values())

    def waiting(self, conversation_id: str) -> list[Pending]:
        return [item for item in self._pending.values() if item.conversation_id == conversation_id]

    def release(self, turn_id: str) -> None:
        """The turn ended (or was cut short): its questions are withdrawn."""
        for pending in [item for item in self._pending.values() if item.turn_id == turn_id]:
            self._pending.pop(pending.id, None)
            if not pending.future.done():
                pending.future.cancel()
