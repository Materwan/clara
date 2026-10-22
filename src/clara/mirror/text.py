"""What a message looks like in Discord: the text of a question, the answer with the line of tools Clara used, and the
digest that tells whether what was written still matches. The tool line reads as the web site's does (`web/src/lib/tools.ts`):
"Searched the web ×24, read a page ×6 · 31 actions". The results of tools are never shown.
"""

from __future__ import annotations

import hashlib
import re

from ..knowledge.attachments import DOCUMENT_BLOCK, documents_in

# tool → what she did, as the web site words it (its NOTES table, without the icons and the arguments)
LABELS: dict[str, str] = {
    "remember": "Remembered",
    "forget": "Forgot a fact",
    "recall_facts": "Looked in memory",
    "search_history": "Searched earlier conversations",
    "remind": "Set a reminder",
    "list_reminders": "Checked your reminders",
    "cancel_reminder": "Cancelled a reminder",
    "notify": "Notified you",
    "add_task": "Added a task",
    "list_tasks": "Read your tasks",
    "update_task": "Updated a task",
    "delete_task": "Deleted a task",
    "read_conversation_file": "Read the file",
    "list_project_files": "Listed the project's files",
    "read_project_file": "Read",
    "search_project": "Searched the project",
    "ws_list": "Listed the workspace",
    "ws_read": "Read",
    "ws_search": "Searched the workspace",
    "ws_log": "Read the history",
    "ws_write": "Wrote",
    "ws_edit": "Edited",
    "ws_move": "Moved",
    "ws_delete": "Deleted",
    "ws_run": "Ran",
    "web_search": "Searched the web",
    "web_fetch": "Read a page",
    "github_read": "Looked at",
    "github_write": "Changed a file in",
    "github_branch": "Worked on a branch",
    "github_pr": "Worked on a pull request",
    "github_issue": "Worked on an issue",
    "drive_list": "Listed Drive files",
    "drive_read": "Read a Drive file",
    "drive_search": "Searched Drive",
    "drive_create": "Created on Drive",
    "drive_update": "Replaced a Drive file",
    "drive_delete": "Moved a Drive file to the trash",
    "open_second_brain": "Opened the vault",
    "vault_search": "Searched the vault",
    "vault_read": "Read the note",
    "vault_list": "Listed the notes",
    "vault_write": "Wrote the note",
    "vault_edit": "Edited the note",
    "vault_daily": "Opened the daily note",
    "vault_move": "Moved the note",
    "local_task": "Sub agent",
}
MAX_LINE = 100  # Discord's limit for a thread's name is 100 characters; the same bound keeps the tool line short
MAX_KINDS = 3


def label(name: str) -> str:
    return LABELS.get(name, name.replace("_", " "))


def tools_line(names: list[str]) -> str:
    """ "Searched the web ×2, read a page · 3 actions" (no line when no tool was used)."""
    if not names:
        return ""
    counts: dict[str, int] = {}
    for name in names:
        kind = label(name)
        counts[kind] = counts.get(kind, 0) + 1
    kinds = []
    for index, (kind, count) in enumerate(counts.items()):
        text = kind if index == 0 else kind[:1].lower() + kind[1:]
        kinds.append(f"{text} ×{count}" if count > 1 else text)
    shown = ", ".join(kinds[:MAX_KINDS] + (["…"] if len(kinds) > MAX_KINDS else []))
    return f"🔧 {shown} · {len(names)} actions"


def question_text(text: str) -> tuple[str, list[str]]:
    """What the question shows: its words, with each attached document named (its text is not repeated: it is Clara's)."""
    names = [name for name, _ in documents_in(text)]
    body = DOCUMENT_BLOCK.sub("", text).strip()
    lines = [body] if body else []
    lines += [f"📎 {name}" for name in names]
    return "\n".join(lines), names


def answer_text(names: list[str], text: str) -> str:
    """The answer with its tool line above it."""
    line = tools_line(names)
    return "\n".join(part for part in (line, text.strip()) if part)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def clip(text: str, limit: int = 60) -> str:
    """A thread's name from a title: one line, cut where a word ends."""
    flat = re.sub(r"\s+", " ", text).strip() or "Conversation"
    if len(flat) <= limit:
        return flat
    cut = flat.rfind(" ", 0, limit)
    return flat[: cut if cut > limit // 2 else limit].rstrip() + "…"
