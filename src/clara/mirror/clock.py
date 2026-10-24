"""The revision clock of the Discord mirror: every change the mirror must see takes the next number, inside the transaction that
makes the change, so a sync that asks for "everything after N" never misses one."""

from __future__ import annotations


def bump(tx, conversation_id: str) -> None:
    """Inside a transaction: mark a conversation as changed."""
    seq = tx.execute("UPDATE mirror_clock SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchone()[0]
    tx.execute("UPDATE conversations SET mirror_rev = ? WHERE id = ?", (seq, conversation_id))
