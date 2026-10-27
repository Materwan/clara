"""« Allow for this conversation »: one approval card lets an action class (`FOR_A_CONVERSATION`) through again for one target
in one conversation. It writes no rule, and it lasts as long as the conversation: the row goes when the conversation does.
"""

from __future__ import annotations

from ..db import Database
from ..timeutil import now_iso


class Allowances:
    def __init__(self, db: Database):
        self.db = db

    def add(self, conversation_id: str, tool: str, target: str, approval_id: int) -> None:
        self.db.execute(
            "INSERT INTO conversation_allowances (conversation_id, tool, target, approval_id, granted_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (conversation_id, tool, target) DO NOTHING",
            (conversation_id, tool, target, approval_id, now_iso()),
        )

    def covers(self, conversation_id: str, tool: str, target: str) -> bool:
        if not conversation_id:
            return False
        row = self.db.one(
            "SELECT 1 FROM conversation_allowances WHERE conversation_id = ? AND tool = ? AND target = ?",
            (conversation_id, tool, target),
        )
        return row is not None
