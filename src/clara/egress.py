"""Does a text that leaves the machine carry what the conversation read in private?

A search query is free text, so `secretscan.py` (the shapes of keys and passwords) cannot tell a private sentence from a public
one. What can be told is where a text *came from*: a conversation that read a mail, a file or a note knows that text, and a
query that repeats it is suspect. `PrivateText` keeps what the turn read from private sources, and what the person wrote
themselves (which they may search for), and says whether a query repeats the first and not the second.

It is a screen, not a guarantee. It sees four words in a row from the same place, or an identifier (an email address, or a word
of five letters or more with a digit in it: an account number, a reference); a paraphrase, one word, an encoding (base64, hex)
or a text spread over many queries passes. The tainted-turn rule asks for any address the person did not write and no search
returned (`fetch_other`), so a query is the one channel this has to watch.
"""

from __future__ import annotations

import re
import unicodedata

PRIVATE = "text read earlier in this conversation"  # what the gate names when a query repeats it
WINDOW = 4  # words in a row
MIN_WINDOW_CHARS = 14  # four short words ("a b c d") say nothing
MAX_ENTRIES = 300_000  # a bound on what one turn remembers (a few MB)
_WORD = re.compile(r"\w+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def _grams(words: list[str]) -> set[str]:
    found = (" ".join(words[start : start + WINDOW]) for start in range(len(words) - WINDOW + 1))
    return {gram for gram in found if len(gram) >= MIN_WINDOW_CHARS}


def _identifiers(text: str, words: list[str]) -> set[str]:
    found = {word for word in words if len(word) >= 5 and any(char.isdigit() for char in word)}
    return found | {email.casefold() for email in _EMAIL.findall(unicodedata.normalize("NFKC", text))}


class PrivateText:
    """What a turn knows from private sources, and what the person wrote. Not stored: it lives as long as the turn."""

    def __init__(self) -> None:
        self._private: set[str] = set()
        self._written: set[str] = set()

    def _keep(self, into: set[str], text: str) -> None:
        if not text or len(into) >= MAX_ENTRIES:
            return
        words = _words(text)
        into |= _grams(words) | _identifiers(text, words)

    def add(self, text: str) -> None:
        """Text the conversation read from a private source (a mail, a file, a note, a fact)."""
        self._keep(self._private, text)

    def allow(self, text: str) -> None:
        """Text the person wrote: they may search for it."""
        self._keep(self._written, text)

    def leaks(self, text: str) -> bool:
        """Does `text` repeat private text that the person did not write?"""
        if not self._private or not text:
            return False
        words = _words(text)
        found = _grams(words) | _identifiers(text, words)
        return bool((found & self._private) - self._written)
