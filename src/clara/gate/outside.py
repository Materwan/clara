"""Words someone else wrote, in the summary of an approval request.

To know which mail a request is about you need its subject and its sender, and which Drive file, its name. Those are
written by a stranger, so they are put in one fixed place and shape, after being made harmless:

    <what Clara would do, written by the code> "<what they wrote>" from <who> [<id>]

`named` makes the quoted part: one line, no quote or bracket inside (the words cannot close their own quotation and go on
as if Clara said them), cut short. `shown` puts it last, after the verb, which never depends on them. `plain` finds the shape
again and takes the words out: the summary as Clara's prompt and the check of a booked call read it, `<verb> <id>`
(DECISIONS: a mail's subject reaches the conversation only through the quarantined reader).
"""

from __future__ import annotations

import re
import unicodedata

WHAT_SHOWN = 80  # characters of a subject or a file name
WHO_SHOWN = 60  # characters of a sender

# every quotation mark that could look like the end of ours
_QUOTES = str.maketrans({char: "'" for char in '"“”„‟«»‹›＂❝❞〝〞'})
_BRACKETS = str.maketrans({"[": "(", "]": ")"})
# the end of a summary: the words, who wrote them if known, and the id they stand for
_SHAPE = re.compile(r' "[^"]*"(?: from [^"\[\]]*)? \[([^\[\]]*)\]$')


def one_line(text: str, limit: int) -> str:
    """One plain line of at most `limit` characters: control and format characters (a newline, a right-to-left override, a
    zero-width space…) become spaces, runs of spaces one space, quotes and square brackets lose their meaning."""
    cleaned = "".join(
        " " if unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") else char for char in str(text or "")
    )
    line = " ".join(cleaned.translate(_QUOTES).translate(_BRACKETS).split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def named(what: str, who: str = "", *, empty: str = "(untitled)") -> str:
    """`"<what>" from <who>`: the words someone wrote and, if it is known, who wrote them."""
    line = f'"{one_line(what, WHAT_SHOWN) or empty}"'
    sender = one_line(who, WHO_SHOWN)
    return f"{line} from {sender}" if sender else line


def shown(fixed: str, words: str, ref: str, limit: int) -> str:
    """The summary `fixed "<what>" from <who> [<ref>]`, whole within `limit` characters: when the room is short it is the
    fixed text that is cut, never the words."""
    tail = f" {words} [{one_line(ref, 60)}]"
    room = max(limit - len(tail), 0)
    head = fixed if len(fixed) <= room else fixed[: max(room - 1, 0)].rstrip() + "…"
    return head + tail


def plain(summary: str) -> str:
    """The summary without the words someone else wrote: `<verb> <id>`, what it said before they were added."""
    return "\n".join(_SHAPE.sub(r" \1", line) for line in summary.split("\n"))
