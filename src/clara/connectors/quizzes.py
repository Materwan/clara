"""The progress of the QCMs of a conversation: which question was answered or skipped, in which attempt.

The form is in the message that holds Clara's `qcm` call (connectors/forms.py); this keeps what was done with it. Each
call that settles a question returns the message to send Clara, when there is one: the answer to correct, the end of the
attempt (once), or nothing (a question skipped with others left).
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from ..timeutil import now_iso
from .forms import answer_message, check_answer, finish_message, summary

ANSWERED, SKIPPED = "answered", "skipped"


class QuizError(ValueError):
    """The call does not fit the state of the QCM (the message says why)."""


class Quizzes:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def attempts(self, conversation_id: str, ref: str) -> int:
        return int(
            self.db.value(
                "SELECT MAX(attempt) FROM quiz_attempts WHERE conversation_id = ? AND ref = ?",
                (conversation_id, ref),
                0,
            )
            or 0
        )

    def _entries(self, conversation_id: str, ref: str, attempt: int, count: int) -> list:
        entries: list = [None] * count
        rows = self.db.all(
            "SELECT question, status, answer FROM quiz_answers WHERE conversation_id = ? AND ref = ? AND attempt = ?",
            (conversation_id, ref, attempt),
        )
        for row in rows:
            if 0 <= row["question"] < count:
                entries[row["question"]] = {"status": row["status"], "answer": json.loads(row["answer"])}
        return entries

    def progress(self, conversation_id: str, form: dict, legacy: list | None = None) -> dict:
        """Where the current attempt stands. `legacy`: the answers a form made before the panel got in one message."""
        count = len(form["questions"])
        attempts = self.attempts(conversation_id, form["ref"])
        if attempts == 0 and legacy is not None:
            entries = [
                {"status": ANSWERED, "answer": answer} if answer not in ("", []) else {"status": SKIPPED, "answer": []}
                for answer in legacy
            ]
            entries += [None] * (count - len(entries))
            return self._shape(form, entries, 1, 1, closed=True, legacy=True)
        attempt = max(attempts, 1)
        entries = self._entries(conversation_id, form["ref"], attempt, count)
        closed = bool(
            self.db.value(
                "SELECT closed FROM quiz_attempts WHERE conversation_id = ? AND ref = ? AND attempt = ?",
                (conversation_id, form["ref"], attempt),
                0,
            )
        )
        return self._shape(form, entries, attempt, max(attempts, 1), closed=closed)

    @staticmethod
    def _shape(form: dict, entries: list, attempt: int, attempts: int, *, closed: bool, legacy: bool = False) -> dict:
        found = summary(form, entries)
        return {
            "attempt": attempt,
            "attempts": attempts,
            "entries": entries,
            "summary": found,
            "finished": not found["open"],
            "closed": closed,
            "legacy": legacy,
        }

    # ------------------------------------------------------------------
    # Settling a question
    # ------------------------------------------------------------------
    def answer(self, conversation_id: str, form: dict, number: int, answer: Any) -> dict:
        """Answer question `number` (from 1): `{"progress", "message"}` (the message to send Clara)."""
        question = self._question(form, number)
        cleaned = check_answer(question, answer)
        return self._settle(conversation_id, form, number, ANSWERED, cleaned)

    def skip(self, conversation_id: str, form: dict, number: int) -> dict:
        self._question(form, number)
        return self._settle(conversation_id, form, number, SKIPPED, [])

    @staticmethod
    def _question(form: dict, number: int) -> dict:
        if not 1 <= number <= len(form["questions"]):
            raise ValueError(f"The QCM has questions 1 to {len(form['questions'])}.")
        return form["questions"][number - 1]

    def _settle(self, conversation_id: str, form: dict, number: int, status: str, answer: Any) -> dict:
        ref = form["ref"]
        with self.db.transaction() as connection:
            attempt = self.attempts(conversation_id, ref)
            if attempt == 0:
                attempt = self._begin(conversation_id, ref, 1)
            before = connection.execute(
                "SELECT status FROM quiz_answers WHERE conversation_id = ? AND ref = ? AND attempt = ? AND question = ?",
                (conversation_id, ref, attempt, number - 1),
            ).fetchone()
            if before is not None and before["status"] == ANSWERED:
                raise QuizError(f"Question {number} is answered already: Retry starts again.")
            if before is not None and status == SKIPPED:  # skipped twice: nothing changes, nothing is sent
                return {"progress": self.progress(conversation_id, form), "message": None}
            connection.execute(
                "INSERT OR REPLACE INTO quiz_answers (conversation_id, ref, attempt, question, status, answer,"
                " answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, ref, attempt, number - 1, status, json.dumps(answer, ensure_ascii=False), now_iso()),
            )
            progress = self.progress(conversation_id, form)
            ends = progress["finished"] and not progress["closed"]
            if ends:  # the end of the attempt is told to Clara once
                connection.execute(
                    "UPDATE quiz_attempts SET closed = 1 WHERE conversation_id = ? AND ref = ? AND attempt = ?",
                    (conversation_id, ref, attempt),
                )
                progress = {**progress, "closed": True}
        final = progress["summary"] if ends else None
        if status == ANSWERED:
            message = answer_message(form, number, answer, final)
        else:
            message = finish_message(form, final) if final is not None else None
        return {"progress": progress, "message": message}

    def _begin(self, conversation_id: str, ref: str, attempt: int) -> int:
        self.db.execute(
            "INSERT INTO quiz_attempts (conversation_id, ref, attempt, started_at) VALUES (?, ?, ?, ?)",
            (conversation_id, ref, attempt, now_iso()),
        )
        return attempt

    def retry(self, conversation_id: str, form: dict) -> dict:
        """Start the form again: a new attempt with nothing answered (the earlier ones stay)."""
        ref = form["ref"]
        with self.db.transaction():
            attempts = self.attempts(conversation_id, ref)
            touched = attempts and self.db.value(
                "SELECT COUNT(*) FROM quiz_answers WHERE conversation_id = ? AND ref = ? AND attempt = ?",
                (conversation_id, ref, attempts),
                0,
            )
            if attempts == 0 or touched:
                self._begin(conversation_id, ref, attempts + 1)
        return self.progress(conversation_id, form)
