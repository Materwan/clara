"""`write_daily_recap`: the one tool of the daily check-in, offered only in a check-in conversation."""

from __future__ import annotations

from datetime import date

from ..gate.actions import Action
from ..knowledge.vault import VaultError
from ..secretscan import secret_kinds
from ..tools.base import Tool, ToolContext, ToolError

WRITE_RECAP = "write_daily_recap"


def _describe(context: ToolContext, arguments: dict) -> Action:
    return Action("", "write", "vault_write", ("vault:daily",), "Write the day's recap in the daily note",
                  tuple(secret_kinds(str(arguments.get("content") or ""))))


async def _write(context: ToolContext, content: str) -> str:
    services = context.services
    recap = services.recaps.for_conversation(context.turn.conversation_id) if services.recaps else None
    if recap is None or services.vault is None:
        raise ToolError("There is no daily check-in in this conversation.")
    try:
        path, dropped = await services.vault.write_recap(date.fromisoformat(recap.day), str(content))
    except VaultError as error:
        raise ToolError(str(error)) from None
    services.recaps.mark_written(recap.day, path, closed=context.turn.mode == "unattended")
    text = f"Récap du {recap.day} écrit dans {path} (il remplace le précédent)."
    if dropped:
        text += (" Liens retirés, faute de note de ce nom (ou de plusieurs notes du même nom) : "
                 + ", ".join(dropped) + ". Cherche la note avec vault_search et rappelle l'outil si elle existe.")
    return text


def recap_tool() -> Tool:
    return Tool(
        WRITE_RECAP,
        "Write the recap of the day being checked in, in the vault's daily note of that day: it fills the block "
        "« Récap (Clara) » (made from the daily template when the note does not exist) and replaces what an earlier call "
        "wrote there. Give the whole recap in markdown; [[links]] to notes that do not exist are removed.",
        _write, _describe,
        {"content": {"type": "string", "description": "The whole recap, markdown, French, no level 1 or 2 heading."}},
        ("content",))
