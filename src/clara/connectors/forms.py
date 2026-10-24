"""QCM: a form of questions Clara asks through the `qcm` tool, answered one question at a time in the web chat's side panel.

A question is `single` (one option), `multiple` (any number of options) or `text` (a free answer). Choice
questions may name their correct options, which makes the clients grade them; a question may carry a hint
(shown on request) and an explanation, a `text` question a reference answer (shown once answered). The QCM is not
blocking: the turn ends with the form shown, and each answered question comes back as a message of its own
(`answer_message`), which Clara corrects at once. A question can be skipped (nothing is sent). When every question is
answered or skipped, one more line (`final_line`) tells Clara the score; an attempt that is only skipped to its end
sends `finish_message` instead. The progress is kept by `Quizzes` (connectors/quizzes.py).

Conversations made before the panel hold the form's answers in one message (`[QCM answers ref]`): `with_answers`
still reads them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MAX_QUESTIONS = 20
MIN_OPTIONS = 2
MAX_OPTIONS = 10
MAX_TITLE = 120
MAX_QUESTION = 500
MAX_OPTION = 200
MAX_NOTE = 500  # an explanation, a hint, or the reference answer of a text question
MAX_TEXT_ANSWER = 4_000  # what you may type for a text question
SINGLE, MULTIPLE, TEXT = "single", "multiple", "text"
TYPES = (SINGLE, MULTIPLE, TEXT)
CLIENTS = ("web",)  # the clients that can show a form

NO_ANSWER = "(no answer)"
ANSWER_MARK = "[QCM answer {ref} {number}/{count}]"
FINISH_MARK = "[QCM finished {ref}]"
_MARK_RE = re.compile(r"^\[QCM answers ([0-9a-f]{8})\]")  # the one message of the forms made before the panel
LETTERS = "ABCDEFGHIJ"


def _line(value: Any, name: str, limit: int, required: bool = True) -> str:
    """One line of text: the whitespace folded, at most `limit` characters."""
    if value is None or isinstance(value, (dict, list)):
        value = ""
    text = " ".join(str(value).split())
    if not text and required:
        raise ValueError(f"{name} is empty.")
    if len(text) > limit:
        raise ValueError(f"{name} is too long ({len(text)} characters, at most {limit}).")
    return text


def _note(value: Any, name: str) -> str:
    """A short text that may have several lines."""
    text = "" if value is None or isinstance(value, (dict, list)) else str(value).strip()
    if len(text) > MAX_NOTE:
        raise ValueError(f"{name} is too long ({len(text)} characters, at most {MAX_NOTE}).")
    return text


def _list(value: Any, name: str) -> list:
    if isinstance(value, str):  # some models send a JSON text instead of a list
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError(f"{name} must be a list.") from None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list.")
    return list(value)


def _correct(value: Any, count: int, kind: str, where: str) -> list[int]:
    items = _list(value, f"{where}: correct")
    chosen: list[int] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, str)) or not str(item).strip().lstrip("-").isdigit():
            raise ValueError(f"{where}: correct holds the numbers of the options, the first being 0.")
        number = int(item)
        if not 0 <= number < count:
            raise ValueError(f"{where}: correct {number} is not an option (0 to {count - 1}).")
        if number not in chosen:
            chosen.append(number)
    if kind == SINGLE and len(chosen) != 1:
        raise ValueError(f"{where}: a single question has exactly one correct option.")
    if kind == MULTIPLE and not chosen:
        raise ValueError(f"{where}: a multiple question needs at least one correct option (or leave correct out).")
    return sorted(chosen)


def _question(raw: Any, number: int) -> dict:
    where = f"Question {number}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object with text, type and options.")
    kind = str(raw.get("type") or SINGLE).strip().lower()
    if kind not in TYPES:
        raise ValueError(f"{where}: type must be one of {', '.join(TYPES)}.")
    question = {
        "text": _line(raw.get("text"), f"{where}: text", MAX_QUESTION),
        "type": kind,
        "options": [],
        "correct": None,
        "explanation": _note(raw.get("explanation"), f"{where}: explanation"),
        "hint": _note(raw.get("hint"), f"{where}: hint"),
        "answer": "",
    }
    if kind == TEXT:
        if raw.get("options"):
            raise ValueError(f"{where}: a text question has no options.")
        question["answer"] = _note(raw.get("answer"), f"{where}: answer")
        return question
    options = [
        _line(option, f"{where}: an option", MAX_OPTION) for option in _list(raw.get("options"), f"{where}: options")
    ]
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"{where}: {MIN_OPTIONS} to {MAX_OPTIONS} options are needed, not {len(options)}.")
    if len({option.casefold() for option in options}) != len(options):
        raise ValueError(f"{where}: the options must differ.")
    question["options"] = options
    if raw.get("correct") not in (None, "", []):
        question["correct"] = _correct(raw["correct"], len(options), kind, where)
    return question


def reference(title: str, questions: list[dict]) -> str:
    """A short id of a form, the same each time it is built from the same questions: the answers carry it."""
    material = json.dumps([title, [[q["text"], q["type"], q["options"]] for q in questions]], ensure_ascii=False)
    return hashlib.sha1(material.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def build_form(arguments: dict[str, Any]) -> dict:
    """The form the model asked for, checked and cleaned (ValueError says what to fix), as sent to the clients:
    `{"ref", "title", "graded", "questions": [{"text", "type", "options", "correct", "explanation", "hint", "answer"}]}`."""
    questions = [_question(raw, number) for number, raw in enumerate(_list(arguments.get("questions"), "questions"), 1)]
    if not questions:
        raise ValueError("A QCM needs at least one question.")
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"A QCM holds at most {MAX_QUESTIONS} questions, not {len(questions)}.")
    title = _line(arguments.get("title"), "title", MAX_TITLE, required=False)
    return {
        "ref": reference(title, questions),
        "title": title,
        # graded: every choice question names its correct options, so the clients can score the QCM
        "graded": any(q["type"] != TEXT for q in questions)
        and all(q["correct"] is not None for q in questions if q["type"] != TEXT),
        "questions": questions,
    }


def forms_in(tool_calls: list[dict] | None) -> list[dict]:
    """The forms of the `qcm` calls stored with a message (a call that was refused leaves none)."""
    forms = []
    for call in tool_calls or []:
        function = call.get("function") or {}
        if function.get("name") != "qcm":
            continue
        arguments = function.get("arguments")
        try:
            forms.append(build_form(arguments if isinstance(arguments, dict) else {}))
        except ValueError:
            pass
    return forms


# ---- the answers -------------------------------------------------------------------------------------
# An entry of a form's progress is None (not touched), or {"status": "answered" | "skipped", "answer": ...} where the
# answer is the list of the chosen option numbers (choice questions) or the typed text.


def _token(question: dict, index: int) -> str:
    return f"{LETTERS[index]}. {question['options'][index]}"


def check_answer(question: dict, answer: Any) -> list[int] | str:
    """The answer to one question, checked and cleaned (ValueError says what is wrong)."""
    if question["type"] == TEXT:
        text = "" if answer is None or isinstance(answer, (dict, list)) else str(answer).strip()
        if not text:
            raise ValueError("Write an answer, or skip the question.")
        if len(text) > MAX_TEXT_ANSWER:
            raise ValueError(f"The answer is too long ({len(text)} characters, at most {MAX_TEXT_ANSWER}).")
        return text
    if not isinstance(answer, (list, tuple)) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in answer
    ):
        raise ValueError("The answer is the list of the numbers of the chosen options.")
    chosen = sorted(set(answer))
    if not chosen:
        raise ValueError("Choose an option, or skip the question.")
    if chosen[0] < 0 or chosen[-1] >= len(question["options"]):
        raise ValueError(f"The options are numbered 0 to {len(question['options']) - 1}.")
    if question["type"] == SINGLE and len(chosen) != 1:
        raise ValueError("This question takes one option.")
    return chosen


def given_text(question: dict, answer: Any) -> str:
    """What was answered, as a line: "B. Canberra; C. Perth", or the typed text."""
    if question["type"] == TEXT:
        return str(answer or "").strip()
    return "; ".join(_token(question, index) for index in sorted(answer or []))


def is_right(question: dict, answer: Any) -> bool | None:
    """Does the answer match the key? None: the question has no key (a free text, or a choice without `correct`)."""
    if question["type"] == TEXT or question["correct"] is None:
        return None
    return sorted(answer or []) == question["correct"]


def summary(form: dict, entries: list) -> dict:
    """Where a form stands: `{"total", "answered", "skipped": [numbers], "open": [numbers], "right", "asked"}`
    (numbers from 1; `right` of `asked` over the answered questions that have a key)."""
    found: dict = {"total": len(form["questions"]), "answered": 0, "skipped": [], "open": [], "right": 0, "asked": 0}
    for number, question in enumerate(form["questions"], 1):
        entry = entries[number - 1] if number <= len(entries) else None
        if entry is None:
            found["open"].append(number)
        elif entry["status"] == "skipped":
            found["skipped"].append(number)
        else:
            found["answered"] += 1
            verdict = is_right(question, entry["answer"])
            if verdict is not None:
                found["asked"] += 1
                found["right"] += verdict
    return found


def final_line(found: dict) -> str:
    """The end of an attempt, as Clara is told it: "Final: 9 of 10 answered, score 7/9, skipped: 4"."""
    line = f"Final: {found['answered']} of {found['total']} answered"
    if found["asked"]:
        line += f", score {found['right']}/{found['asked']}"
    if found["skipped"]:
        line += ", skipped: " + ", ".join(str(number) for number in found["skipped"])
    return line + "."


def _head(mark: str, form: dict, **fields) -> str:
    return mark.format(ref=form["ref"], **fields) + (f" {form['title']}" if form["title"] else "")


def answer_message(form: dict, number: int, answer: Any, final: dict | None = None) -> str:
    """The message that sends one answer to Clara (`number` from 1); `final`, when it was the last question left,
    adds the end of the attempt."""
    question = form["questions"][number - 1]
    lines = [
        _head(ANSWER_MARK, form, number=number, count=len(form["questions"])),
        f"Question: {question['text']}",
        f"Answer: {given_text(question, answer) or NO_ANSWER}",
    ]
    verdict = is_right(question, answer)
    if verdict is not None:
        lines.append(f"Marked by the key: {'right' if verdict else 'wrong'}")
    if final is not None:
        lines += ["", "That was the last question. " + final_line(final)]
    return "\n".join(lines)


def finish_message(form: dict, final: dict) -> str:
    """The message sent when the last question left was skipped: no answer to correct, only the end of the attempt."""
    return "\n".join([_head(FINISH_MARK, form), final_line(final)])


# ---- the forms made before the panel: all the answers in one message ----------------------------------------


def answered_ref(message: str) -> str | None:
    """The form a user message answers, if it is the one message of answers the forms of before the panel sent."""
    found = _MARK_RE.match(message)
    return found.group(1) if found else None


def parse_answers(form: dict, message: str) -> list | None:
    """The answers of such a message, one per question (None: the message is not for this form)."""
    if answered_ref(message) != form["ref"]:
        return None
    count = len(form["questions"])
    position = message.find("\n\n1. ")
    answers: list = []
    for number, question in enumerate(form["questions"], 1):
        if position < 0:
            answers.append("" if question["type"] == TEXT else [])
            continue
        end = message.find(f"\n\n{number + 1}. ", position + 1) if number < count else -1
        block = message[position + 2 : end if end >= 0 else len(message)]
        position = end
        _, _, given = block.partition("\nAnswer: ")
        given = "" if given == NO_ANSWER else given
        if question["type"] == TEXT:
            answers.append(given)
        else:
            answers.append(
                [index for index in range(len(question["options"])) if _chosen(_token(question, index), given)]
            )
    return answers


def _chosen(token: str, given: str) -> bool:
    """Is `token` one of the "; "-separated choices of `given`?"""
    return given == token or given.startswith(token + "; ") or given.endswith("; " + token) or f"; {token}; " in given


def with_answers(forms: list[dict], later: list[str]) -> list[dict]:
    """The forms, each with the `answers` the user gave in one of the messages `later` (None: not answered)."""
    done = []
    for form in forms:
        answers = next((found for message in later if (found := parse_answers(form, message)) is not None), None)
        done.append({**form, "answers": answers})
    return done
