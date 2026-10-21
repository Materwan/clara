"""What happened (an event) and the kinds of event that exist.

A *source* (today: the mailboxes, polled by connectors/mail_watch.py) finds events and hands them to `Triggers.ingest`. An
event is stored once: (source, key) is unique, so the same one seen twice, after a restart or a replay, is one event.

Everything in `data` was written by someone else. It is never trusted: it reaches a model only inside an `<event>` block that
says so, and the run it starts is tainted from its first word (see message.py and the rule gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceEvent:
    source: str  # mail
    kind: str  # mail.received
    key: str  # stable inside the source: gmail:<message id>
    occurred_at: str  # when it happened, ISO UTC (the time a mail was received)
    actor: str = ""  # who caused it (a sender's address)
    self_caused: bool = False  # caused by Clara's own account: a trigger sees it only if it asks to
    title: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEvent(SourceEvent):
    id: int = 0
    received_at: str = ""

    def describe(self) -> dict:
        return {"id": self.id, "source": self.source, "kind": self.kind, "key": self.key, "actor": self.actor,
                "self_caused": self.self_caused, "title": self.title, "data": self.data, "occurred_at": self.occurred_at,
                "received_at": self.received_at}


@dataclass(frozen=True)
class EventKind:
    kind: str
    label: str
    fields: dict[str, str]  # filterable field -> "text" or "bool"
    read_tools: tuple[str, ...]  # what a run may use when the trigger lists no tools (and `notify`)


KINDS: dict[str, EventKind] = {
    "mail.received": EventKind(
        "mail.received", "A new message in the inbox of a connected Gmail or Outlook mailbox",
        {"account": "text", "mailbox": "text", "from": "text", "from_address": "text", "subject": "text", "snippet": "text",
         "has_attachment": "bool", "unread": "bool"},
        ("mail_search", "mail_read", "mail_attachment_read")),
}
NOTIFY = "notify"
