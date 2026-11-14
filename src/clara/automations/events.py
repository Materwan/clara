"""What happened (an event) and the kinds of event that exist.

A *source* (the mailboxes, polled by connectors/mail_watch.py; GitHub repositories, polled by connectors/github_watch.py; Moodle
and Auriga, polled by connectors/school_watch.py) finds events and hands them to `Automations.ingest`. An event is stored once:
(source, key) is unique, so the same one seen twice, after a restart or a replay, is one event.

A kind declares its own fields (what a filter may look at), the tools a run may use when the automation lists none, the text
of a notification, and whether what it carries is tainted (written by someone else). The engine knows no kind by name: a
new source adds a kind to `KINDS` and nothing else changes.

Everything in `data` was written by someone else. It is never trusted: it reaches a model only inside an `<event>` block that
says so, and a run an event started is tainted from its first word (see message.py and the rule gate) when its kind says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceEvent:
    source: str  # mail, github, moodle, auriga
    kind: str  # mail.received, github.pull_request
    key: str  # stable inside the source: gmail:<message id>, owner/repo#12:opened:<time>
    occurred_at: str  # when it happened, ISO UTC (the time a mail was received)
    actor: str = ""  # who caused it (a sender's address, a GitHub login)
    self_caused: bool = False  # caused by Clara's own account: an automation sees it only if it asks to (mail only)
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
    read_tools: tuple[str, ...]  # what a run may use when the automation lists no tools (and `notify`)
    template: str = "{from}: {subject}"  # the text of a notification automation that has none of its own
    noun: str = "an event"  # how a sentence names it: "a mail", "a push"
    tainted: bool = True  # what it carries was written by someone else: a run it starts is tainted from its first word


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
        noun="a mail",
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
        noun="a pull request",
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
        noun="a push",
    ),
    # School (connectors/school_watch.py): every field is text written by other people (a course, a teacher, a teacher's comment).
    # Reading the school sites with the tools does not taint a turn (DECISIONS.md), but an event that starts a run does.
    "moodle.quiz_opens": EventKind(
        "moodle.quiz_opens",
        "A Moodle quiz opens (at its opening time, in a course in progress)",
        {"course": "text", "quiz": "text", "opens": "text", "closes": "text"},
        ("school_calendar", "moodle_courses"),
        "Quiz {quiz} ({course}) opens at {opens}",
        noun="a quiz opening",
    ),
    "moodle.quiz_closes": EventKind(
        "moodle.quiz_closes",
        "A Moodle quiz closes (at its closing time, in a course in progress)",
        {"course": "text", "quiz": "text", "opens": "text", "closes": "text"},
        ("school_calendar", "moodle_courses"),
        "Quiz {quiz} ({course}) closes at {closes}",
        noun="a quiz closing",
    ),
    "moodle.activity_added": EventKind(
        "moodle.activity_added",
        "A new activity or file appears in a Moodle course in progress",
        {"course": "text", "section": "text", "name": "text", "type": "text"},
        ("moodle_files", "moodle_courses"),
        "{course}: new {type} “{name}” in {section}",
        noun="a new Moodle activity",
    ),
    "moodle.grade_posted": EventKind(
        "moodle.grade_posted",
        "A grade appears or changes in a Moodle course in progress",
        {"course": "text", "item": "text", "grade": "text", "range": "text"},
        ("school_grades", "moodle_courses"),
        "{course}: {item}: {grade}",
        noun="a Moodle grade",
    ),
    "auriga.grade_posted": EventKind(
        "auriga.grade_posted",
        "An average or a mark appears or changes on Auriga (this school year)",
        {"code": "text", "title": "text", "value": "text", "kind": "text"},
        ("school_grades",),
        "{code} {title}: {value}",
        noun="an Auriga grade",
    ),
    "auriga.planning_changed": EventKind(
        "auriga.planning_changed",
        "A class on Auriga's planning is added, moved, changes room or teachers, or is cancelled (the next four weeks)",
        {"date": "text", "title": "text", "change": "text", "details": "text"},
        ("school_calendar",),
        "{date} {title}: {change} ({details})",
        noun="a change of planning",
    ),
}
NOTIFY = "notify"
