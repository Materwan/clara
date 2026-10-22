"""What happened (an event) and the kinds of event that exist.

A *source* (the mailboxes, polled by connectors/mail_watch.py; GitHub repositories, polled by connectors/github_watch.py)
finds events and hands them to `Triggers.ingest`. An event is stored once: (source, key) is unique, so the same one seen
twice, after a restart or a replay, is one event.

Everything in `data` was written by someone else. It is never trusted: it reaches a model only inside an `<event>` block that
says so, and the run it starts is tainted from its first word (see message.py and the rule gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceEvent:
    source: str  # mail, github
    kind: str  # mail.received, github.pull_request
    key: str  # stable inside the source: gmail:<message id>, owner/repo#12:opened:<time>
    occurred_at: str  # when it happened, ISO UTC (the time a mail was received)
    actor: str = ""  # who caused it (a sender's address, a GitHub login)
    self_caused: bool = False  # caused by Clara's own account: a trigger sees it only if it asks to (mail only)
    title: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEvent(SourceEvent):
    id: int = 0
    received_at: str = ""

    def describe(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "key": self.key,
            "actor": self.actor,
            "self_caused": self.self_caused,
            "title": self.title,
            "data": self.data,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class EventKind:
    kind: str
    label: str
    fields: dict[str, str]  # filterable field -> "text" or "bool"
    read_tools: tuple[str, ...]  # what a run may use when the trigger lists no tools (and `notify`)
    template: str = "{from}: {subject}"  # the text of a notification hook that has none of its own


KINDS: dict[str, EventKind] = {
    "mail.received": EventKind(
        "mail.received",
        "A new message in the inbox of a connected Gmail mailbox",
        {
            "account": "text",
            "mailbox": "text",
            "from": "text",
            "from_address": "text",
            "subject": "text",
            "snippet": "text",
            "has_attachment": "bool",
            "unread": "bool",
        },
        ("mail_search", "mail_read", "mail_attachment_read"),
    ),
    "github.pull_request": EventKind(
        "github.pull_request",
        "A pull request opened, closed or merged in a repository you added",
        {
            "repo": "text",
            "action": "text",
            "title": "text",
            "author": "text",
            "branch": "text",
            "base": "text",
            "snippet": "text",
            "draft": "bool",
            "from_fork": "bool",
        },
        ("github_read",),
        "{repo} #{number} {action} by {author}: {title}",
    ),
    "github.push": EventKind(
        "github.push",
        "A push to a branch of a repository you added (a new branch included)",
        {
            "repo": "text",
            "branch": "text",
            "author": "text",
            "message": "text",
            "created": "bool",
            "forced": "bool",
        },
        ("github_read",),
        "{repo} {branch}: {author} pushed {message}",
    ),
}
NOTIFY = "notify"
