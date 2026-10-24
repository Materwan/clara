"""Does a text look like it carries a secret?

Used where a secret must not go: a fact (refused), and content Clara is about to send out of the machine (a GitHub
issue, a Drive file, a vault note, a networked script): there the rule gate raises the action to *ask*, whatever the
rules say, so a prompt injection cannot quietly copy a key out. It is a screen, not a guarantee: it knows the
common shapes only.
"""

from __future__ import annotations

import re

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}\b")),
    ("API key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b")),
    ("Clara token", re.compile(r"\bclara_\d{1,12}_[A-Za-z0-9_-]{43}\b")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("password", re.compile(r"(?i)\b(?:password|passwd|mot de passe|mdp)\s*[:=]\s*\S{6,}")),
    ("connection string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{6,}@")),
)


def secret_kinds(text: str) -> list[str]:
    """The kinds of secret the text seems to hold (empty: none found)."""
    if not text:
        return []
    return [name for name, pattern in PATTERNS if pattern.search(text)]


def looks_secret(text: str) -> bool:
    return bool(secret_kinds(text))


HIDDEN = "[hidden]"


def mask(text: str) -> str:
    """The text with what looks like a secret replaced by `[hidden]` (a private key hides everything after its header,
    for the key's body is on the lines that follow). For text that is shown, never for text that is kept."""
    for name, pattern in PATTERNS:
        if name == "private key":
            found = pattern.search(text)
            if found:
                text = text[: found.start()] + HIDDEN
        else:
            text = pattern.sub(HIDDEN, text)
    return text
