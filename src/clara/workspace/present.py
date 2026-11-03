"""Presenting a file: a card under Clara's answer. The tool `present` hands a file over to the person.

It does not read the file: the card carries where the file is, its size and where it came from, and the model gets one
line back, never the content. Clara names what to present with a reference, as the Files created tools do:

* `notes/plan.md` (or `ws:notes/plan.md`), a workspace file. A file she wrote in this conversation, or any regular file
  of the workspace when the "Files created" pack is on for the conversation (that pack is the grant, `FILES_PACK`). The
  card has **See in the workspace** and **Download**.
* `vault:3-projects/Clara/Clara.md`, a note of the second brain, once it is open in the conversation. **See in the vault**
  and **Download**.
* `drive:<file id>`, a Google Drive file Clara may use (the scope of her Drive tools). **Open in Drive**, a link made from
  the id alone: nothing of the file passes through Clara's server.

The card is not stored anywhere. It is found again in the stored conversation from the `present` calls whose result says
they worked (`presented`), and then looked up as it is now (`cards_for`), so a file that was deleted since shows as gone.
`canonical` gives the reference without looking at anything: it is the first line of the result, and what the Discord mirror
finds the same way, which keeps what it writes equal on both of its paths (live and sync).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..connectors.drive import DriveError
from .paths import PathError, clean
from .store import human_size

if TYPE_CHECKING:
    from ..services import Services

PRESENT = "present"
FILES_PACK = "files_created"
MAX_PER_ANSWER = 5
PRESENTED = "Presented"  # a result that starts with this line, then the reference, is a call that worked
WORKSPACE, VAULT, DRIVE = "workspace", "vault", "drive"
PREFIXES = (("vault:", VAULT), ("drive:", DRIVE), ("ws:", WORKSPACE))
DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")  # as connectors/tools.py::_file
MAX_NAME = 200


class PresentError(ValueError):
    """Why a file cannot be presented, in words for the model."""


def split(reference: object) -> tuple[str, str]:
    """(kind, what) of what Clara named: a workspace path, `vault:<note>` or `drive:<file id>` (`ws:<path>` too)."""
    text = str(reference or "").strip()
    for prefix, kind in PREFIXES:
        if text.lower().startswith(prefix):
            return kind, text[len(prefix) :].strip()
    return WORKSPACE, text


def canonical(reference: object) -> str:
    """The reference as the result says it, with nothing looked up: a clean workspace path, `vault:<clean path>` or
    `drive:<id>`. PathError when it cannot be one. A workspace path may be empty (the caller says so)."""
    kind, what = split(reference)
    if kind == DRIVE:
        if not DRIVE_ID.fullmatch(what):
            raise PathError("drive: must be followed by a Drive file id, as listed.")
        return f"drive:{what}"
    name = clean(what)
    return f"vault:{name}" if kind == VAULT else name


def result(card: dict) -> str:
    """What the model reads when the card is made: the reference on the first line (history finds the card from it),
    never the content, and never a name that came from Drive."""
    where = {
        WORKSPACE: "to download or to see in the workspace",
        VAULT: "to download or to see in the vault",
        DRIVE: "to open in Google Drive",
    }[card["kind"]]
    size = f" ({human_size(card['size'])})" if card.get("size") is not None else ""
    return (
        f"{PRESENTED} {card['ref']}\n"
        f"The person is offered it{size} {where}. You did not read it and it is not repeated to you: say in a sentence "
        "what it is, do not paste its content."
    )


# ----------------------------------------------------------------------
# What lets a call through
# ----------------------------------------------------------------------
def granted(s: Services, conversation_id: str) -> bool:
    """Whether the conversation may present any workspace file: the "Files created" pack is on for it."""
    if s.packs is None:
        return False
    conversation = s.conversations.get(conversation_id) if conversation_id else None
    return FILES_PACK in s.packs.active(conversation)


def vault_open(s: Services, conversation_id: str) -> bool:
    """Whether the second brain is open in the conversation (`open_second_brain`)."""
    conversation = s.conversations.get(conversation_id) if conversation_id else None
    return conversation is not None and bool(conversation.vault_open)


# ----------------------------------------------------------------------
# The cards
# ----------------------------------------------------------------------
def workspace_card(s: Services, ref: str) -> dict:
    """A workspace file: its size and, when it came from outside and was not trusted, where from. PresentError, or the
    workspace's own error, when the file cannot be served (it does not exist, a folder, over the limit)."""
    if s.workspace is None:
        raise PresentError("The workspace is not available.")
    size = s.workspace.file_size(ref)
    mark = s.provenance.untrusted(ref) if s.provenance is not None else None
    return {
        "kind": WORKSPACE,
        "ref": ref,
        "path": ref,
        "size": size,
        "origin": mark.source if mark is not None else None,
    }


def vault_card(s: Services, ref: str, note: str) -> dict:
    """A note of the vault, found as `vault_read` finds it (a path or a name)."""
    if s.vault is None:
        raise PresentError("No vault is set up (CLARA_VAULT_PATH).")
    found = s.vault.find(note)
    return {
        "kind": VAULT,
        "ref": ref,
        "path": found,
        "size": (s.vault.root / found).stat().st_size,
        "origin": None,
    }


def drive_card(ref: str, file_id: str, name: str) -> dict:
    """A Drive file. The link is made from the id, never taken from what Drive sends back: the id has a fixed alphabet."""
    return {
        "kind": DRIVE,
        "ref": ref,
        "path": file_id,
        "name": name[:MAX_NAME],
        "size": None,
        "origin": None,
        "url": f"https://drive.google.com/open?id={file_id}",
    }


async def drive_live(s: Services, ref: str, file_id: str) -> dict:
    """A Drive file the connection can see: its metadata is asked for, which is what says Clara may use it."""
    client = s.connectors.drive_client() if s.connectors is not None else None
    if client is None:
        raise PresentError("Google Drive is not connected.")
    try:
        meta = await client.meta(file_id)
    except DriveError as error:
        s.connectors.drive_failed(error)
        raise PresentError(str(error)) from None
    if meta.get("trashed"):
        raise PresentError("That file is in Drive's trash.")
    return drive_card(ref, file_id, str(meta.get("name") or ""))


def drive_name(s: Services, file_id: str) -> str:
    """The name of a Drive file from what is kept here, with no call to Drive: a file Clara made, or one that was shared
    with her. '' when it is neither (the card then says « Drive file »)."""
    row = s.db.one(
        "SELECT name FROM created_files WHERE kind = 'drive' AND locator = ? ORDER BY id DESC LIMIT 1", (file_id,)
    )
    if row is None:
        row = s.db.one("SELECT label AS name FROM resources WHERE kind LIKE 'drive%' AND locator = ?", (file_id,))
    return str(row["name"]) if row else ""


def local_card(s: Services, ref: str) -> dict:
    """The card of a reference as it is now, with no network: what a stored conversation shows when it is opened."""
    kind, what = split(ref)
    if kind == DRIVE:
        return drive_card(ref, what, drive_name(s, what))
    if kind == VAULT:
        return vault_card(s, ref, what)
    return workspace_card(s, ref)


# ----------------------------------------------------------------------
# Finding the cards again
# ----------------------------------------------------------------------
def presented(calls: list[dict], results: list[str]) -> list[str]:
    """The references a message presented, in the order of its calls: those of its `present` calls (`{name, arguments}`)
    that one of the results that followed (the texts of its tool messages) says worked. Each one once."""
    firsts = {text.split("\n", 1)[0] for text in results}
    found: list[str] = []
    for call in calls:
        if call.get("name") != PRESENT or not isinstance(call.get("arguments"), dict):
            continue
        try:
            ref = canonical(call["arguments"].get("path"))
        except PathError:
            continue
        if ref and ref not in found and f"{PRESENTED} {ref}" in firsts:
            found.append(ref)
    return found


def cards_for(s: Services, calls: list[dict], results: list[str]) -> list[dict]:
    """The cards of a stored message. A file that is gone, or cannot be served any more, is a card marked `missing`."""
    cards = []
    for ref in presented(calls, results):
        try:
            cards.append(local_card(s, ref))
        except (ValueError, OSError):  # the workspace's, the vault's and this module's errors are all ValueErrors
            kind, what = split(ref)
            cards.append({"kind": kind, "ref": ref, "path": what, "size": None, "origin": None, "missing": True})
    return cards
