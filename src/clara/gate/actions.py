"""What the gate decides about: an *action*, which every tool call is described as before it runs.

An action has a tool, a level (read, write, destructive), an action class (which gives the default when no rule
matches), its targets, and a one-line summary you read in an approval request.

Targets name what is touched, with a prefix:

    ws:<path>          a workspace path            (a rule may leave the prefix out: `scratch/**`)
    github:<owner/repo>                            drive:<file id>          vault:<note path>
    mail:gmail             a mailbox (read, draft)   mail:to:<address>   one recipient of a message to send
    domain:<host>      a web address's host        net                      a script that asks for the network
    memory, reminders, tasks, notify, forms        Clara's own records
    history            the conversations, tasks and files of a day, read back (the packs' tools)
    discord            a thread of your Discord server

The default of each class, when no rule matches, for a chat (interactive) and for an unattended run:
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

LEVELS = ("read", "write", "destructive")
EFFECTS = ("allow", "ask", "refuse")
STRICTNESS = {"allow": 0, "ask": 1, "refuse": 2}
MODES = ("interactive", "unattended")
SITUATIONS = ("interactive", "unattended", "tainted")

# class -> (default in a chat, default unattended, what it is)
CLASSES: dict[str, tuple[str, str, str]] = {
    "read": ("allow", "allow", "Read the workspace, projects, the vault, memory, files sent"),
    "memory": ("allow", "allow", "Clara's own records: facts, reminders, tasks, notifications, forms"),
    "ws_write": ("allow", "allow", "Create or edit workspace files (git history)"),
    "ws_destroy": ("ask", "ask", "Delete or overwrite workspace files"),
    "run": ("allow", "ask", "Run a script, no network"),
    "run_network": ("ask", "ask", "Run a script with network"),
    "github_read": ("allow", "allow", "GitHub read"),
    "github_write": ("ask", "ask", "GitHub issue, branch, pull request, commit"),
    "drive_read": ("allow", "allow", "Drive read (files Clara created or you opened to her)"),
    "drive_write": ("ask", "ask", "Drive create, update, delete"),
    "mail_read": ("allow", "allow", "Mail read: search, read a message or an attachment (Gmail)"),
    "mail_draft": ("allow", "allow", "Mail draft: save a draft in your mailbox (nothing is sent)"),
    "mail_send": ("ask", "refuse", "Mail send: an email leaves your mailbox"),
    "vault_write": ("allow", "allow", "Create or edit vault notes (git history)"),
    "vault_destroy": ("ask", "ask", "Delete or move vault notes"),
    "web_search": ("allow", "allow", "Web search"),
    "fetch_trusted": ("allow", "allow", "Fetch a URL you wrote or a search returned"),
    "fetch_other": ("ask", "refuse", "Fetch any other URL"),
    "discord": ("allow", "allow", "Open, write in or remove a Discord thread"),
}
# Classes raised to at least `ask` in a tainted turn (one that read untrusted content), when the rule is on
RAISED_WHEN_TAINTED = frozenset(
    {"github_write", "drive_write", "mail_send", "vault_write", "vault_destroy", "run_network"}
)
# Classes whose content leaves the machine: content that looks like a secret raises them to `ask`
OUTSIDE = frozenset(
    {
        "github_write",
        "drive_write",
        "mail_draft",
        "mail_send",
        "vault_write",
        "run_network",
        "fetch_other",
        "fetch_trusted",
    }
)
# Classes whose call sends something off the machine (a query, an address, content): refused in a local-only turn.
# The vault's are there because its changes are pushed to its remote (not when CLARA_VAULT_PUSH is off).
LEAVES_MACHINE = OUTSIDE | frozenset(
    {"web_search", "github_read", "drive_read", "mail_read", "vault_destroy", "discord"}
)

PREFIXES = ("ws:", "github:", "drive:", "mail:", "vault:", "domain:")
SPECIAL_TARGETS = ("net", "search", "memory", "reminders", "tasks", "notify", "forms", "history", "discord")
_GLOB_CHARS = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class Action:
    tool: str
    level: str
    klass: str
    targets: tuple[str, ...]
    summary: str
    secret_kinds: tuple[str, ...] = ()  # the content seems to carry secrets (secretscan.py)

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"unknown level {self.level}")
        if self.klass not in CLASSES:
            raise ValueError(f"unknown action class {self.klass}")

    @property
    def target(self) -> str:
        return " ".join(self.targets)


@dataclass(frozen=True)
class Situation:
    mode: str  # interactive (a chat) or unattended (a schedule, a reminder, a follow-up)
    tainted: bool = False  # the turn read untrusted content (a web page, a fetched file, an issue…)
    local_only: bool = False  # a local-only conversation: nothing may leave the person's machines

    def describe(self) -> str:
        return f"{self.mode}{', tainted' if self.tainted else ''}{', local only' if self.local_only else ''}"


def normal_target(pattern: str) -> str:
    """A rule's target as matched: a workspace path may be written without `ws:`."""
    pattern = pattern.strip()
    if not pattern or pattern in SPECIAL_TARGETS:
        return pattern
    if pattern.startswith(PREFIXES):
        return pattern
    return "ws:" + pattern.lstrip("/")


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A glob as a regex: `**` crosses folders, `*` and `?` stay within one path segment."""
    out = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            if index < len(pattern) and pattern[index] == "/":
                out[-1] = "(?:.*/)?"
                index += 1
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append(fnmatch.translate(pattern[index : end + 1])[4:-3])
                index = end
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def literal_length(pattern: str) -> int:
    """How many literal characters a pattern has (wildcards left out): the more, the more specific. `scratch/*.txt`
    (12) is more specific than `scratch/**` (8)."""
    without_brackets = re.sub(r"\[[^\]]*\]", "", pattern)
    return len(_GLOB_CHARS.sub("", without_brackets))


@dataclass(frozen=True)
class Decision:
    effect: str
    reason: str
    rule_id: int | None = None
    floor: bool = False
    raised: tuple[str, ...] = field(default=())  # why it was raised to ask (tainted turn, secret-looking content)
