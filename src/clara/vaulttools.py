"""The vault_* tools: how Clara reads and writes the second brain (see vault/vault.py).

They are offered only to the people allowed to use the vault (settings `CLARA_VAULT_OWNERS`), and not in group
conversations, where what a tool returns would be shown to everybody present. A conversation gets them once it needs
them: until then it has only `open_second_brain` (and a short note in the prompt), which opens them for good there.
Upkeep (sync, the semantic index, the check-up) is the console's `/vault`, not the model's. Git and embeddings can take seconds,
so every tool runs in a thread and does not hold up the server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .tools import Tool, ToolContext, _flag, _whole_number
from .vault import Vault

LIST_LIMIT = {"type": "integer", "description": "How many to show (default 10-50, at most 200)."}
NOTE = {
    "type": "string",
    "description": "The note: its path (5-knowledge/Idea.md), its name (Idea) or a link ([[Idea]]).",
}
TYPES_HELP = "inbox, idea, project, area, knowledge, source, person, note, map"


def _vault(context: ToolContext) -> Vault:
    if context.vault is None:
        raise ValueError("The vault is not available.")
    return context.vault


def _items(value: Any) -> list[str]:
    """A list of texts: the model may send a list, or one text with commas."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("expected a list of texts.")


def _mapping(value: Any, name: str) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            return loaded
    raise ValueError(f"{name} must be an object like {{\"key\": \"value\"}}.")


def _count(value: Any, default: int, name: str = "limit") -> int:
    return default if value is None or value == "" else _whole_number(value, f"{name} must be a number.")


async def _in_thread(function, *args, **kwargs) -> str:
    return await asyncio.to_thread(function, *args, **kwargs)


# --- reading ----------------------------------------------------------------------------------------------


async def _info(context: ToolContext, what: str = "overview", name: str = "", conventions: Any = False, limit: Any = None) -> str:
    vault = _vault(context)
    what = str(what or "overview").strip().lower()
    if what == "overview":
        return await _in_thread(vault.overview, _flag(conventions))
    if what == "tags":
        return await _in_thread(vault.tags, str(name or ""), _count(limit, 60))
    if what == "properties":
        return await _in_thread(vault.properties, str(name or ""))
    if what == "templates":
        return await _in_thread(vault.templates)
    raise ValueError("what is overview, tags, properties or templates.")


async def _list(
    context: ToolContext, folder: str = "", type: str = "", status: str = "", tag: str = "", recursive: Any = True,
    sort: str = "updated", limit: Any = None,
) -> str:
    return await _in_thread(
        _vault(context).list_notes, folder, type, status, tag, recursive is not False and str(recursive).lower() != "false",
        sort, _count(limit, 50),
    )


async def _read(context: ToolContext, note: str, start_line: Any = 1) -> str:
    return await _in_thread(_vault(context).read, note, _count(start_line, 1, "start_line"))


async def _search(
    context: ToolContext, query: str, meaning: Any = False, folder: str = "", type: str = "", status: str = "",
    tag: str = "", limit: Any = None,
) -> str:
    if _flag(meaning):
        return await _in_thread(_vault(context).semantic_search, query, folder, type, tag, _count(limit, 8))
    return await _in_thread(_vault(context).search, query, folder, type, status, tag, _count(limit, 10))


async def _query(
    context: ToolContext, type: str = "", status: str = "", tags: Any = None, any_tag: Any = False, folder: str = "",
    where: Any = None, text: str = "", created_after: str = "", created_before: str = "", updated_after: str = "",
    updated_before: str = "", sort: str = "updated", limit: Any = None, show: Any = None,
) -> str:
    return await _in_thread(
        _vault(context).query, type, status, _items(tags), _flag(any_tag), folder, _mapping(where, "where"), text,
        created_after, created_before, updated_after, updated_before, sort, _count(limit, 50), _items(show),
    )


async def _links(context: ToolContext, note: str, unlinked: Any = False, related: Any = False, limit: Any = None) -> str:
    if _flag(related):
        return await _in_thread(_vault(context).related, note, _count(limit, 10))
    return await _in_thread(_vault(context).links, note, _flag(unlinked))


async def _tasks(
    context: ToolContext, status: str = "open", folder: str = "", tag: str = "", due_before: str = "",
    text: str = "", limit: Any = None,
) -> str:
    return await _in_thread(_vault(context).tasks, status, folder, tag, due_before, text, _count(limit, 50))


async def _history(context: ToolContext, note: str, limit: Any = None) -> str:
    return await _in_thread(_vault(context).history, note, _count(limit, 15))


async def _daily_read(context: ToolContext, date: str = "today") -> str:
    return await _in_thread(_vault(context).daily_read, date, context.timezone)


# --- writing ----------------------------------------------------------------------------------------------


async def _create(
    context: ToolContext, title: str, type: str, content: str = "", tags: Any = None, status: str = "",
    summary: str = "", folder: str = "", parent: str = "", properties: Any = None, aliases: Any = None,
    overwrite: Any = False,
) -> str:
    return await _in_thread(
        _vault(context).create, title, type, content, tags=_items(tags), status=status, summary=summary,
        folder=folder, parent=parent, properties=_mapping(properties, "properties"), aliases=_items(aliases),
        tz=context.timezone, overwrite=_flag(overwrite),
    )


async def _capture(context: ToolContext, text: str, title: str = "", source: str = "", tags: Any = None) -> str:
    return await _in_thread(_vault(context).capture, text, title, source, _items(tags), context.timezone)


async def _edit(context: ToolContext, note: str, old_text: str, new_text: str, replace_all: Any = False) -> str:
    return await _in_thread(_vault(context).edit, note, old_text, new_text, _flag(replace_all), context.timezone)


async def _append(context: ToolContext, note: str, text: str, heading: str = "") -> str:
    return await _in_thread(_vault(context).append, note, text, heading, context.timezone)


async def _rewrite(context: ToolContext, note: str, content: str) -> str:
    return await _in_thread(_vault(context).rewrite, note, content, context.timezone)


async def _set_properties(
    context: ToolContext, note: str, set: Any = None, remove: Any = None, add_tags: Any = None,
    remove_tags: Any = None,
) -> str:
    return await _in_thread(
        _vault(context).set_properties, note, _mapping(set, "set"), _items(remove), _items(add_tags),
        _items(remove_tags), context.timezone,
    )


async def _move(context: ToolContext, note: str, to: str) -> str:
    return await _in_thread(_vault(context).move, note, to, context.timezone)


async def _delete(context: ToolContext, note: str) -> str:
    return await _in_thread(_vault(context).delete, note, context.timezone)


async def _restore(context: ToolContext, note: str, commit: str) -> str:
    return await _in_thread(_vault(context).restore, note, str(commit), context.timezone)


async def _daily_append(context: ToolContext, text: str, section: str = "Log", date: str = "today") -> str:
    return await _in_thread(_vault(context).daily_append, text, section or "Log", date, context.timezone)


async def _set_task(context: ToolContext, note: str, task: Any, done: Any = True) -> str:
    return await _in_thread(
        _vault(context).set_task, note, str(task), done is not False and str(done).lower() != "false", context.timezone
    )


def _open(context: ToolContext) -> str:
    context.memory.open_vault(context.conversation)
    return "The second brain is open: its vault_* tools are yours from now on, in this conversation."


def vault_tools() -> list[Tool]:
    """Offered, with a vault, to the people who may use it: open_second_brain, then the vault_* tools once the
    conversation opened it (agent.py)."""
    return [open_vault_tool()] + _note_tools()


def _note_tools() -> list[Tool]:
    return [
        Tool(
            name="vault_info",
            description=(
                "What the vault holds. overview: notes per folder and type, inbox to sort, active projects, top tags, "
                "recent changes, sync state (start here when you do not know the vault). tags: the tags in use, or "
                "the notes of tag `name`. properties: the front matter properties in use, or the values of property "
                "`name` (look before inventing one). templates: each kind of note, its folder, statuses and sections "
                "(read before creating a kind you have not made yet)."
            ),
            function=_info,
            parameters={
                "what": {"type": "string", "enum": ["overview", "tags", "properties", "templates"]},
                "name": {"type": "string", "description": "tags: a tag without #; properties: a property name."},
                "conventions": {"type": "boolean", "description": "overview: also the written conventions."},
                "limit": LIST_LIMIT,
            },
            required=("what",),
        ),
        Tool(
            name="vault_list",
            description="List notes (path, type/status, last update, summary), optionally of one folder, type, status or tag.",
            function=_list,
            parameters={
                "folder": {"type": "string", "description": "Folder, e.g. 3-projects or 5-knowledge/programming."},
                "type": {"type": "string", "description": f"One of: {TYPES_HELP}, daily."},
                "status": {"type": "string"},
                "tag": {"type": "string", "description": "Tag without #; sub tags (a/b) match their parent (a)."},
                "recursive": {"type": "boolean", "description": "false: only this folder, and list its sub folders."},
                "sort": {"type": "string", "enum": ["updated", "created", "name", "oldest"]},
                "limit": LIST_LIMIT,
            },
        ),
        Tool(
            name="vault_read",
            description=(
                "Read a note with line numbers (about 20,000 characters at once; read on with start_line), and its "
                "type, status, tags and backlink count. Read a note before changing it."
            ),
            function=_read,
            parameters={"note": NOTE, "start_line": {"type": "integer", "description": "First line (default 1)."}},
            required=("note",),
        ),
        Tool(
            name="vault_search",
            description=(
                "Search the notes by words (accents and case ignored; titles and tags weigh most): ranked notes with "
                "the matching lines. meaning=true searches by meaning instead, for a vague question or when words "
                "find nothing. Search before creating a note, to extend one rather than duplicate it."
            ),
            function=_search,
            parameters={
                "query": {"type": "string", "description": "Words, or with meaning=true a sentence or question."},
                "meaning": {"type": "boolean"},
                "folder": {"type": "string"},
                "type": {"type": "string"},
                "status": {"type": "string", "description": "Words search only."},
                "tag": {"type": "string"},
                "limit": LIST_LIMIT,
            },
            required=("query",),
        ),
        Tool(
            name="vault_query",
            description=(
                "Filter notes like a database (Dataview) by type, status, tags, folder, properties and dates. `where` "
                "maps a property to 'value', '*' (set), '!value' (not), '>=2026-01-01', '<5' or '~word' (contains)."
            ),
            function=_query,
            parameters={
                "type": {"type": "string"},
                "status": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "any_tag": {"type": "boolean", "description": "true: at least one tag, not all."},
                "folder": {"type": "string"},
                "where": {"type": "object", "description": "Property conditions, e.g. {\"project\": \"Clara\", \"priority\": \">=2\"}."},
                "text": {"type": "string", "description": "Notes whose text contains this."},
                "created_after": {"type": "string", "description": "YYYY-MM-DD"},
                "created_before": {"type": "string"},
                "updated_after": {"type": "string"},
                "updated_before": {"type": "string"},
                "sort": {"type": "string", "description": "updated, created, oldest, name, or a property name."},
                "limit": LIST_LIMIT,
                "show": {"type": "array", "items": {"type": "string"}, "description": "Properties to show on each line."},
            },
        ),
        Tool(
            name="vault_links",
            description=(
                "A note's links: those it points to, broken ones, and backlinks (with the line); unlinked=true adds "
                "notes that mention its name without a link. related=true gives instead the notes close to it (links, "
                "tags, words, meaning) with the reason: what a new note should link to."
            ),
            function=_links,
            parameters={"note": NOTE, "unlinked": {"type": "boolean"}, "related": {"type": "boolean"}, "limit": LIST_LIMIT},
            required=("note",),
        ),
        Tool(
            name="vault_tasks",
            description="The checkbox tasks of the notes ('- [ ] text 📅 2026-10-31') by due date, optionally filtered.",
            function=_tasks,
            parameters={
                "status": {"type": "string", "enum": ["open", "done", "all"]},
                "folder": {"type": "string"},
                "tag": {"type": "string"},
                "due_before": {"type": "string", "description": "YYYY-MM-DD"},
                "text": {"type": "string"},
                "limit": LIST_LIMIT,
            },
        ),
        Tool(
            name="vault_history",
            description="The git history of a note (commit, date, who, what): who changed it and when.",
            function=_history,
            parameters={"note": NOTE, "limit": LIST_LIMIT},
            required=("note",),
        ),
        Tool(
            name="vault_daily_read",
            description="Read a daily note (today by default).",
            function=_daily_read,
            parameters={"date": {"type": "string", "description": "today, yesterday, tomorrow or YYYY-MM-DD."}},
        ),
        Tool(
            name="vault_create_note",
            description=(
                "Create a note of a type in its folder, with front matter and template. idea: draft thoughts; "
                "project, area: a folder and a hub note; knowledge: an atomic evergreen note; source: a book, article "
                "or video (properties.source_type); person; note: a working note in a project or area (`parent`); "
                "map. The title is the unique file name. `content` follows the template's sections, in the person's "
                "language, with [[wikilinks]] to related notes. Search first; unsure where it goes: vault_capture."
            ),
            function=_create,
            parameters={
                "title": {"type": "string", "description": "Becomes the file name."},
                "type": {"type": "string", "enum": ["idea", "project", "area", "knowledge", "source", "person", "note", "map", "inbox"]},
                "content": {"type": "string", "description": "The markdown body, without front matter."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Without #; a/b for sub tags."},
                "status": {"type": "string", "description": "Default: the type's first."},
                "summary": {"type": "string", "description": "One line shown in lists."},
                "folder": {"type": "string", "description": "Optional sub folder of the type's folder."},
                "parent": {"type": "string", "description": "type note: its project or area."},
                "properties": {"type": "object", "description": "More front matter, e.g. {\"source_type\": \"book\"}."},
                "aliases": {"type": "array", "items": {"type": "string"}, "description": "Other names for [[links]]."},
                "overwrite": {"type": "boolean", "description": "Rarely: replace a note of the same path."},
            },
            required=("title", "type"),
        ),
        Tool(
            name="vault_capture",
            description=(
                "Drop anything in the inbox fast, without deciding where it goes (a thought, link, quote, to-do): "
                "it is sorted later. Better than losing it or guessing a place."
            ),
            function=_capture,
            parameters={
                "text": {"type": "string", "description": "What to keep, as the person said it."},
                "title": {"type": "string", "description": "Optional (else the first line)."},
                "source": {"type": "string", "description": "Optional: a URL or where it came from."},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            required=("text",),
        ),
        Tool(
            name="vault_edit_note",
            description=(
                "Replace a passage of a note. old_text is exactly as in the note (without vault_read's line numbers) "
                "and appears once, unless replace_all; an empty new_text deletes it. Read the note first."
            ),
            function=_edit,
            parameters={
                "note": NOTE,
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            required=("note", "old_text", "new_text"),
        ),
        Tool(
            name="vault_append_note",
            description="Add text at the end of a note, or of its section `heading` (created if missing).",
            function=_append,
            parameters={
                "note": NOTE,
                "text": {"type": "string", "description": "Markdown to add."},
                "heading": {"type": "string", "description": "e.g. 'Next steps'."},
            },
            required=("note", "text"),
        ),
        Tool(
            name="vault_rewrite_note",
            description=(
                "Replace the whole text of a note (front matter kept, old version in git). Only for a deep "
                "restructure; otherwise vault_edit_note."
            ),
            function=_rewrite,
            parameters={"note": NOTE, "content": {"type": "string", "description": "The new markdown body."}},
            required=("note", "content"),
        ),
        Tool(
            name="vault_set_properties",
            description=(
                "Change a note's front matter, not its text: set properties (status, summary, a link like "
                "\"[[Project]]\"...), remove some, add or remove tags. Statuses are checked against the note's type."
            ),
            function=_set_properties,
            parameters={
                "note": NOTE,
                "set": {"type": "object", "description": "e.g. {\"status\": \"done\"}."},
                "remove": {"type": "array", "items": {"type": "string"}, "description": "Property names to remove."},
                "add_tags": {"type": "array", "items": {"type": "string"}},
                "remove_tags": {"type": "array", "items": {"type": "string"}},
            },
            required=("note",),
        ),
        Tool(
            name="vault_move_note",
            description=(
                "Move and/or rename a note, updating every [[link]] to it. `to`: a new name, a folder ending with / "
                "('8-archive/') or a full path. To file an inbox note, archive something finished, or rename."
            ),
            function=_move,
            parameters={"note": NOTE, "to": {"type": "string"}},
            required=("note", "to"),
        ),
        Tool(
            name="vault_delete_note",
            description=(
                "Delete a note (to the trash, kept in git; says which notes still link to it). Only when asked, or "
                "for a clear duplicate or an empty capture you processed."
            ),
            function=_delete,
            parameters={"note": NOTE},
            required=("note",),
        ),
        Tool(
            name="vault_restore_note",
            description="Bring a note back as it was at a commit of vault_history, also a deleted one.",
            function=_restore,
            parameters={"note": NOTE, "commit": {"type": "string", "description": "A commit id from vault_history."}},
            required=("note", "commit"),
        ),
        Tool(
            name="vault_daily_append",
            description=(
                "Add to a daily note (today by default, created if missing) under a section: Log (what happened), "
                "Tasks ('- [ ] ...') or Notes. The journal: what the person did, decided, felt, learned."
            ),
            function=_daily_append,
            parameters={
                "text": {"type": "string", "description": "Markdown; list items start with '- '."},
                "section": {"type": "string", "description": "Default Log."},
                "date": {"type": "string", "description": "today, yesterday, tomorrow or YYYY-MM-DD."},
            },
            required=("text",),
        ),
        Tool(
            name="vault_set_task",
            description="Check or uncheck a task ('- [ ] ...') of a note, found by its line number or words of its text.",
            function=_set_task,
            parameters={
                "note": NOTE,
                "task": {"type": "string", "description": "The task's line number, or words of its text."},
                "done": {"type": "boolean", "description": "Default true; false to uncheck."},
            },
            required=("note", "task"),
        ),
    ]


OPEN_VAULT = "open_second_brain"


def open_vault_tool() -> Tool:
    """What a conversation is offered instead of the vault_* tools until it needs them: they weigh thousands of tokens
    in every round, and most conversations never touch the vault."""
    return Tool(
        name=OPEN_VAULT,
        description=(
            "Load the tools of the second brain (the person's Obsidian vault) for this conversation. Call it before "
            "anything with their notes: reading, searching, keeping or organising something there."
        ),
        function=_open,
        parameters={},
    )


VAULT_TOOLS = frozenset(tool.name for tool in _note_tools())  # without open_second_brain

CLOSED_BLOCK = (
    "## Your second brain (the vault)\n"
    "You share an Obsidian vault with the person: their notes, ideas, projects, sources and journal. Its tools are "
    f"not loaded in this conversation: call {OPEN_VAULT} as soon as the vault is concerned (the person mentions it "
    "or their notes, wants something kept, or asks about something they wrote down)."
)
