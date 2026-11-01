"""What a conversation has been shown of the messages and files it names by id.

A request to trash a mail says which one by its id, which tells the owner nothing. The id came to Clara a moment before from
a search, a read or a listing that also gave the subject and the sender (a file's name): this keeps that, to say it in the
request (gate/outside.py). It is a memory of the server process: nothing is stored, a restart forgets it, and an id that
was never seen keeps the plain wording. It is per conversation, so a request never shows what another conversation read.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from ..gate.approvals import SUMMARY_SHOWN
from ..gate.outside import named, shown

if TYPE_CHECKING:
    from ..tools.base import ToolContext

MAX_SEEN = 2_000  # ids kept in all; the oldest is dropped first


class Seen:
    def __init__(self, limit: int = MAX_SEEN):
        self.limit = limit
        self._words: OrderedDict[tuple[str, str, str], str] = OrderedDict()

    def note(self, conversation_id: str | None, kind: str, ref: str, words: str) -> None:
        """Remember that `ref` (a `kind` such as gmail or drive) was shown with `words`. Nothing is kept for a call with no
        conversation: ephemeral requests would share one bucket."""
        if not conversation_id or not ref:
            return
        key = (conversation_id, kind, ref)
        self._words[key] = words
        self._words.move_to_end(key)
        while len(self._words) > self.limit:
            self._words.popitem(last=False)

    def words(self, conversation_id: str | None, kind: str, ref: str) -> str | None:
        return self._words.get((conversation_id or "", kind, ref))


def say(context: ToolContext, kind: str, ref: str, fixed: str, today: str, *, label: str = "") -> str:
    """The summary of a request about one message or file: `fixed`, then what the conversation was shown of it and its id.
    With nothing shown (the id came from elsewhere, or before a restart) `today`, the plain wording. A Drive file Clara
    created has its name in the database, whoever shows it. `label` is the id as the request has always written it, when that
    is not the id itself (`drive:<id>`)."""
    connectors = context.services.connectors
    words = connectors.seen.words(context.turn.conversation_id, kind, ref) if connectors is not None else None
    if words is None and kind == "drive":
        row = context.services.db.one(
            "SELECT name FROM created_files WHERE kind = 'drive' AND locator = ? ORDER BY id DESC LIMIT 1", (ref,)
        )
        words = named(row["name"]) if row else None
    return today if words is None else shown(fixed, words, label or ref[:40], SUMMARY_SHOWN)
