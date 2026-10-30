"""What the gate decides about: an *action*, which every tool call is described as before it runs.

An action has a tool, a level (read, write, destructive), an action class (which gives the default when no rule
matches), its targets, and a one-line summary you read in an approval request.

Targets name what is touched, with a prefix:

    ws:<path>          a workspace path            (a rule may leave the prefix out: `scratch/**`)
    github:<owner/repo>                            drive:<file id>          vault:<note path>
    mail:gmail             a mailbox (read, draft)   mail:to:<address>   one recipient of a message to send
    domain:<host>      a web address's host        net                      a script that asks for the network
    folder:<path>      a path in the folder you opened on your computer (a web chat; the empty path is its top)
    memory, reminders, tasks, notify, forms        Clara's own records
    history            the conversations, tasks and files of a day, read back (the packs' tools)
    discord            a thread of your Discord server

What happens to a class of action when no rule matches is the level of the conversation (gate/levels.py): each tool is
refused, asked or run according to it, and CLASSES says where the tools of a class start (the owner may change any tool).
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

# The five built-in levels of the ladder between « no tool » and « everything » (gate/levels.py keeps the ladder itself, which the
# owner may extend); a class is tied to them by key so that its default follows a level that was renamed or moved
LEVEL_KEYS = ("none", "trusted_read", "any_read", "files_vault", "mail", "all")

# class -> (the level key a tool of the class starts to ask at, the level key it starts to run at, what it is). Below the first it is
# refused; the owner changes the thresholds of any tool on the Auto mode page.
CLASSES: dict[str, tuple[str, str, str]] = {
    "read": ("trusted_read", "trusted_read", "Read the workspace, projects, the vault, memory, files sent"),
    "memory": ("any_read", "files_vault", "Clara's own records: facts, reminders, tasks, notifications, forms"),
    "ws_write": ("any_read", "files_vault", "Create or edit workspace files (git history)"),
    "ws_destroy": ("any_read", "files_vault", "Delete or overwrite workspace files"),
    "run": ("any_read", "files_vault", "Run a script, no network"),
    "run_network": ("mail", "all", "Run a script with network"),
    "github_read": ("any_read", "any_read", "GitHub read"),
    "github_write": ("mail", "all", "GitHub issue, branch, pull request, commit"),
    "drive_read": ("any_read", "any_read", "Drive read (files Clara created or you opened to her)"),
    "drive_write": ("mail", "all", "Drive create, update, delete"),
    "mail_read": ("any_read", "any_read", "Mail read: search, read a message or an attachment (Gmail)"),
    "mail_draft": ("files_vault", "mail", "Mail draft: save a draft in your mailbox (nothing is sent)"),
    "mail_send": ("mail", "all", "Mail send: an email leaves your mailbox"),
    "mail_label": (
        "files_vault",
        "mail",
        "Mail label: add or remove your labels on a message, or make one (nothing is archived)",
    ),
    "mail_trash": ("files_vault", "mail", "Mail trash: move a message to the Trash (never emptied)"),
    "mail_mark": ("files_vault", "mail", "Mail mark: take the unread mark off a message (nothing else changes)"),
    "mail_label_delete": ("mail", "all", "Mail label delete: delete one of your labels (its messages stay)"),
    "vault_write": ("any_read", "files_vault", "Create or edit vault notes (git history)"),
    "vault_destroy": ("any_read", "files_vault", "Delete or move vault notes"),
    "web_search": ("any_read", "any_read", "Web search"),
    "fetch_trusted": ("trusted_read", "trusted_read", "Fetch a URL you wrote or a search returned"),
    "fetch_other": ("any_read", "any_read", "Fetch any other URL"),
    "discord": ("mail", "all", "Open, write in or remove a Discord thread"),
    "automation": ("mail", "all", "Create, change or delete an automation: a schedule or a hook"),
    "discord_forget": (
        "mail",
        "all",
        "Delete a conversation, a project or an automation because its Discord thread or forum was deleted",
    ),
    "folder_read": (
        "any_read",
        "any_read",
        "Read a folder on your computer (a web chat): list, find, search, or read a file that is not hidden or ignored",
    ),
    "folder_sensitive": (
        "any_read",
        "all",
        "Read a hidden or ignored file of a folder on your computer, or search them: a card can allow it for the chat",
    ),
}
# Classes of a web chat only: a run nobody watches has no browser to answer them, so no level and no rule opens them
CHAT_ONLY = frozenset({"folder_read", "folder_sensitive"})
# Classes an approval card may allow for the rest of its conversation ("Allow for this conversation")
FOR_A_CONVERSATION = frozenset({"folder_sensitive"})
# Classes whose requests are not published as events: their summary names a file of the person's computer, which must
# not reach Discord's #approval or another client; the web chat shows them on its turn
NOT_PUBLISHED = frozenset({"folder_sensitive"})
# Classes raised to at least `ask` in a tainted turn (one that read untrusted content), when the rule is on
RAISED_WHEN_TAINTED = frozenset(
    {
        "github_write",
        "drive_write",
        "mail_send",
        "mail_label_delete",
        "vault_write",
        "vault_destroy",
        "run_network",
        "automation",
    }
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
    {
        "web_search",
        "github_read",
        "drive_read",
        "mail_read",
        "mail_label",
        "mail_trash",
        "mail_mark",
        "mail_label_delete",
        "vault_destroy",
        "discord",
    }
)

PREFIXES = ("ws:", "github:", "drive:", "mail:", "vault:", "domain:", "folder:")
SPECIAL_TARGETS = (
    "net",
    "search",
    "memory",
    "reminders",
    "tasks",
    "notify",
    "forms",
    "history",
    "discord",
    "automations",
)
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
    # The auto level of the conversation (gate/levels.py). Toolbox.call fills it from the conversation at every call; a Situation
    # built by hand (the rule tester without a level, a record in the audit log) leaves None, which Gate.level_of reads as the
    # default level of the mode, not as the level of any conversation.
    level: int | None = None

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
    by_level: bool = False  # the level of the conversation refused it: no rule can change that
