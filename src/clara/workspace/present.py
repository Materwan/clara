"""Presenting a workspace file: a card under Clara's answer with **Download** and **See in the workspace**.

The tool `present` hands a file over to the person. It does not read the file: the card carries the path, the size and
where the file came from, and the model gets one line back, never the content. Two sources are allowed:

* a file that Clara wrote in this conversation (the files the side panel lists, found from the git history);
* any other regular file of the workspace, when the "Files created" pack is on for the conversation: that pack is the
  grant (`FILES_PACK`).

The card is not stored anywhere. It is found again in the stored conversation from the `present` calls whose result
says they worked (`presented`), and then looked up in the workspace as it is now (`card`), so a file that was deleted
since shows as gone. The Discord mirror finds the same paths the same way, which keeps what it writes equal on
both of its paths (live and sync).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .paths import PathError, clean
from .store import WorkspaceError, human_size

if TYPE_CHECKING:
    from ..services import Services

PRESENT = "present"
FILES_PACK = "files_created"
MAX_PER_ANSWER = 5
PRESENTED = "Presented"  # a result that starts with this line, then the path, is a call that worked


def result(path: str, size: int) -> str:
    """What the model reads when the card is made: the path on the first line (history finds the card from it)."""
    return (
        f"{PRESENTED} {path}\n"
        f"The person is offered it ({human_size(size)}) to download or to see in the workspace. You did not read it and "
        "it is not repeated to you: say in a sentence what it is, do not paste its content."
    )


def granted(s: Services, conversation_id: str) -> bool:
    """Whether the conversation may present any workspace file: the "Files created" pack is on for it."""
    if s.packs is None:
        return False
    conversation = s.conversations.get(conversation_id) if conversation_id else None
    return FILES_PACK in s.packs.active(conversation)


def card(s: Services, path: str) -> dict:
    """What the page shows for a file: its path, its size and, when it came from outside and was not trusted, where from.
    WorkspaceError or PathError when the file cannot be served (it does not exist, a folder, over the limit)."""
    if s.workspace is None:
        raise WorkspaceError("The workspace is not available.")
    name = clean(path)
    size = s.workspace.file_size(name)
    mark = s.provenance.untrusted(name) if s.provenance is not None else None
    return {"path": name, "size": size, "origin": mark.source if mark is not None else None}


def presented(calls: list[dict], results: list[str]) -> list[str]:
    """The paths a message presented, in the order of its calls: those of its `present` calls (`{name, arguments}`) that
    one of the results that followed (the texts of its tool messages) says worked. Each path once."""
    firsts = {text.split("\n", 1)[0] for text in results}
    found: list[str] = []
    for call in calls:
        if call.get("name") != PRESENT or not isinstance(call.get("arguments"), dict):
            continue
        try:
            name = clean(str(call["arguments"].get("path") or ""))
        except PathError:
            continue
        if name and name not in found and f"{PRESENTED} {name}" in firsts:
            found.append(name)
    return found


def cards_for(s: Services, calls: list[dict], results: list[str]) -> list[dict]:
    """The cards of a stored message. A file that is gone, or cannot be served any more, is a card marked `missing`."""
    cards = []
    for path in presented(calls, results):
        try:
            cards.append(card(s, path))
        except (WorkspaceError, PathError, OSError):
            cards.append({"path": path, "size": None, "origin": None, "missing": True})
    return cards
