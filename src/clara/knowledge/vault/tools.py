"""The vault tools: `open_second_brain`, then eight tools for that conversation.

A conversation starts with only `open_second_brain` and a short note in the prompt (the vault's tools cost tokens in
every round); once Clara opens it, the tools stay for the conversation.

    vault_search, vault_read, vault_list     read                         class read
    vault_write                              create: write; replace or delete: destructive   vault_write / vault_destroy
    vault_edit                               write                        class vault_write
    vault_daily                              read, or write (append)      read / vault_write
    vault_daily_block                        write a block of a daily note   vault_write
    vault_move                               destructive (it rewrites links)                   vault_destroy
"""

from __future__ import annotations

from typing import Any

from ...gate.actions import Action
from ...secretscan import secret_kinds
from ...tools.base import Tool, ToolContext, ToolError, acts_as, fixed, flag, optional_number
from ...tools.days import resolve_day
from .vault import VaultError

OPEN_VAULT = "open_second_brain"
NOTE = {
    "type": "string",
    "description": "A note: its path (Projects/Clara.md), its name (Clara) or a link ([[Clara]]).",
}


def _vault(context: ToolContext):
    if context.services.vault is None:
        raise ToolError("The vault is not available.")
    return context.services.vault


def _in(context: ToolContext) -> str:
    """The conversation a change is made in: it goes on the commit, for the side panel."""
    return context.turn.conversation_id or ""


def _target(arguments: dict, key: str = "note") -> str:
    return "vault:" + str(arguments.get(key) or "").strip()


async def _open(context: ToolContext) -> str:
    vault = _vault(context)
    if context.turn.conversation_id:
        context.services.conversations.update(context.turn.conversation_id, vault_open=True)
    return vault.prompt_block() + "\nThe vault_* tools are now yours for this conversation."


async def _search(context: ToolContext, query: str, folder: str = "", limit: Any = None) -> str:
    try:
        return await _vault(context).search(str(query), str(folder or ""), optional_number(limit, "limit") or 20)
    except VaultError as error:
        raise ToolError(str(error)) from None


async def _read(context: ToolContext, note: str, start_line: Any = 1) -> str:
    try:
        return await _vault(context).read(
            str(note), optional_number(start_line, "start_line") or 1, context.turn.read_chars
        )
    except VaultError as error:
        raise ToolError(str(error)) from None


def _list(context: ToolContext, folder: str = "", depth: Any = 2) -> str:
    try:
        return _vault(context).listing(str(folder or ""), optional_number(depth, "depth") or 2)
    except VaultError as error:
        raise ToolError(str(error)) from None


@acts_as("vault_write", "vault_destroy")
def _describe_write(context: ToolContext, arguments: dict) -> Action:
    mode = str(arguments.get("mode") or "create")
    if mode not in ("create", "replace", "delete"):
        raise ToolError("mode must be create, replace or delete.")
    secrets = tuple(secret_kinds(str(arguments.get("content") or "")))
    if mode == "create":
        return Action(
            "", "write", "vault_write", (_target(arguments),), f"Create the note {arguments.get('note')}", secrets
        )
    return Action(
        "",
        "destructive",
        "vault_destroy",
        (_target(arguments),),
        f"{mode.capitalize()} the note {arguments.get('note')}",
        secrets,
    )


async def _write(context: ToolContext, note: str, mode: str = "create", content: str = "") -> str:
    try:
        return await _vault(context).write(str(note), str(content), str(mode), conversation=_in(context))
    except VaultError as error:
        raise ToolError(str(error)) from None


async def _edit(
    context: ToolContext,
    note: str,
    old_text: str = "",
    new_text: str = "",
    append: str = "",
    heading: str = "",
    replace_all: Any = False,
) -> str:
    if not append and not old_text:
        raise ToolError("Give old_text and new_text to replace a passage, or append (optionally under heading).")
    try:
        return await _vault(context).edit(
            str(note),
            str(old_text),
            str(new_text),
            append=str(append or ""),
            heading=str(heading or ""),
            replace_all=flag(replace_all),
            conversation=_in(context),
        )
    except VaultError as error:
        raise ToolError(str(error)) from None


@acts_as("read", "vault_write")
def _describe_daily(context: ToolContext, arguments: dict) -> Action:
    if str(arguments.get("action") or "read") == "read":
        return Action("", "read", "read", ("vault:daily",), "Read today's daily note")
    return Action(
        "",
        "write",
        "vault_write",
        ("vault:daily",),
        "Add to today's daily note",
        tuple(secret_kinds(str(arguments.get("text") or ""))),
    )


async def _daily(context: ToolContext, action: str = "read", text: str = "") -> str:
    try:
        return await _vault(context).daily(str(action or "read"), str(text or ""), conversation=_in(context))
    except VaultError as error:
        raise ToolError(str(error)) from None


@acts_as("vault_write")
def _describe_block(context: ToolContext, arguments: dict) -> Action:
    return Action(
        "",
        "write",
        "vault_write",
        ("vault:daily",),
        "Write a block of a daily note",
        tuple(secret_kinds(str(arguments.get("content") or ""))),
    )


async def _daily_block(
    context: ToolContext, content: str, date: str = "", block: str = "recap", heading: str = "Récap (Clara)"
) -> str:
    day = resolve_day(context, date)
    try:
        path, dropped = await _vault(context).write_block(
            day, str(content), str(block or "recap"), str(heading or ""), conversation=_in(context)
        )
    except VaultError as error:
        raise ToolError(str(error)) from None
    text = f"Block « {block or 'recap'} » of {day} written in {path} (it replaces the one written before)."
    if dropped:
        text += (
            " Links removed, as no note has that name (or several do): "
            + ", ".join(dropped)
            + ". Look the note up with vault_search and call again if it exists."
        )
    return text


async def _move(context: ToolContext, note: str, destination: str) -> str:
    try:
        return await _vault(context).move(str(note), str(destination), conversation=_in(context))
    except VaultError as error:
        raise ToolError(str(error)) from None


def vault_tools() -> list[Tool]:
    return [
        Tool(
            OPEN_VAULT,
            "Open the person's second brain (their Obsidian vault) for this conversation: their notes, "
            "projects, ideas, knowledge. Call it when they matter.",
            _open,
            fixed("read", "read", "vault:", "Open the vault"),
        ),
        Tool(
            "vault_search",
            "Search the vault's notes (titles and text; accents and case ignored): each match with an excerpt.",
            _search,
            fixed(
                "read",
                "read",
                lambda c, a: "vault:" + str(a.get("folder") or ""),
                lambda a: f"Search the vault: {a.get('query')}",
            ),
            {"query": {"type": "string"}, "folder": {"type": "string"}, "limit": {"type": "integer"}},
            ("query",),
            parallel=True,
        ),
        Tool(
            "vault_read",
            "Read a note, with line numbers.",
            _read,
            fixed("read", "read", lambda c, a: _target(a), lambda a: f"Read {a.get('note')}"),
            {"note": NOTE, "start_line": {"type": "integer"}},
            ("note",),
            parallel=True,
        ),
        Tool(
            "vault_list",
            "The notes of the vault, or of a folder (paths).",
            _list,
            fixed("read", "read", lambda c, a: "vault:" + str(a.get("folder") or ""), "List the vault"),
            {"folder": {"type": "string"}, "depth": {"type": "integer"}},
            parallel=True,
        ),
        Tool(
            "vault_write",
            "Create a note (mode create, folders made as needed), replace a whole note (replace), or "
            "delete one (delete: it goes to .trash/).",
            _write,
            _describe_write,
            {
                "note": NOTE,
                "mode": {"type": "string", "enum": ["create", "replace", "delete"]},
                "content": {"type": "string"},
            },
            ("note",),
        ),
        Tool(
            "vault_edit",
            "Change a note: replace an exact passage (old_text → new_text), or append text at the end or "
            "under a heading.",
            _edit,
            fixed("vault_write", "write", lambda c, a: _target(a), lambda a: f"Edit {a.get('note')}"),
            {
                "note": NOTE,
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "append": {"type": "string"},
                "heading": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            ("note",),
        ),
        Tool(
            "vault_daily",
            "Today's daily note: read it, or append a line (timestamped).",
            _daily,
            _describe_daily,
            {"action": {"type": "string", "enum": ["read", "append"]}, "text": {"type": "string"}},
        ),
        Tool(
            "vault_daily_block",
            "Write a block of a day's daily note (the note is made from the template when it does not "
            "exist): the text goes between two comments under its own heading and replaces what an earlier call wrote "
            "there, so calling again corrects it and the rest of the note is never touched. [[Links]] to notes that do "
            "not exist are removed. `date` defaults to the day of the scheduled run you are in, else today.",
            _daily_block,
            _describe_block,
            {
                "content": {
                    "type": "string",
                    "description": "The whole block, markdown, with no level 1 or 2 heading.",
                },
                "date": {"type": "string", "description": "YYYY-MM-DD, today or yesterday."},
                "block": {
                    "type": "string",
                    "description": "The block's name (lower case, digits, dashes). Default: recap.",
                },
                "heading": {"type": "string", "description": "The heading it goes under. Default: Récap (Clara)."},
            },
            ("content",),
        ),
        Tool(
            "vault_move",
            "Rename or move a note; every [[link]] to it is updated.",
            _move,
            fixed(
                "vault_destroy",
                "destructive",
                lambda c, a: _target(a),
                lambda a: f"Move {a.get('note')} to {a.get('destination')}",
            ),
            {"note": NOTE, "destination": {"type": "string", "description": "Its new path."}},
            ("note", "destination"),
        ),
    ]


VAULT_TOOLS = frozenset(tool.name for tool in vault_tools()) - {OPEN_VAULT}
