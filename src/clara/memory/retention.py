"""Automatic deletion of the conversations automations make (DECISIONS.md, « Automation retention »).

**What is in scope.** The conversation of each hook run, and each conversation a schedule makes when it runs one per run.
A schedule's own shared conversation is not (it is the schedule's memory of its runs), nor are the chats reminders and
tasks post into. The link is the one the history already uses: the run that made the conversation.

**The limits.** An age (days and hours since the conversation's last message, `updated_at`) and a count (the latest N
conversations of each schedule or hook), whichever comes first. They are runtime settings (`automation_keep_days`,
`automation_keep_hours`, `automation_keep_last`; 0 is off, and all three are 0 at first); a schedule or a hook may set its
own, and a value left NULL follows the global one. The age is one pair: either both days and hours are the automation's
own (0 and 0: none), or both follow.

**What is kept whatever the limits.** A pinned conversation; one *you* archived (an archive made by Discord's idle week or
by switching a group's history off is automatic, and does not save a conversation); a run that is queued, running or
waiting for an answer; a conversation with an approval request still pending. A kept conversation takes its place in the
count: the latest N are counted among all, and a kept one beyond N is simply not deleted.

Deleting goes through `Conversations.delete_where`, as a conversation you delete does: its messages and files go, its
Discord thread is queued for removal, the run keeps its row (its conversation link becomes NULL) and the facts its
automation learned stay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from ..timeutil import iso, utcnow

if TYPE_CHECKING:
    from ..db import Database
    from ..runtime import RuntimeSettings
    from .conversations import Conversations

MAX_DAYS = 3650
MAX_HOURS = 8760
MAX_LAST = 10_000
CHUNK = 400  # conversations deleted in one statement

SCHEDULE = "schedule"
HOOK = "hook"
KEEP_FIELDS = ("keep_days", "keep_hours", "keep_last")
KEEP_AGE = (
    "Days and hours since the last message after which a conversation of its runs is deleted; send both, or neither "
    "(null: follow the automation_keep_days and automation_keep_hours settings; 0 and 0: no age limit)"
)
KEEP_LAST = (
    "How many of the latest conversations of its runs are kept (null: follow automation_keep_last; 0: no count limit)"
)


class RetentionError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    """What applies to an automation: 0 is « no limit »."""

    days: int = 0
    hours: int = 0
    last: int = 0

    @property
    def age_hours(self) -> int:
        return self.days * 24 + self.hours

    @property
    def active(self) -> bool:
        return self.age_hours > 0 or self.last > 0


@dataclass(frozen=True)
class Own:
    """What an automation sets itself: None follows the global setting. The age is both days and hours, or neither."""

    days: int | None = None
    hours: int | None = None
    last: int | None = None


def _whole(value, name: str, high: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise RetentionError(f"{name} must be a whole number from 0 to {high:,}, or left empty to follow the setting.")
    return value


def check_own(days=None, hours=None, last=None) -> Own:
    """The values an automation may store. Giving only one of days and hours makes the other 0."""
    days, hours = _whole(days, "keep_days", MAX_DAYS), _whole(hours, "keep_hours", MAX_HOURS)
    if days is not None or hours is not None:
        days, hours = days or 0, hours or 0
    return Own(days, hours, _whole(last, "keep_last", MAX_LAST))


def own_of(row) -> Own:
    return Own(row["keep_days"], row["keep_hours"], row["keep_last"])


def effective(own: Own, general: Limits) -> Limits:
    if own.days is not None or own.hours is not None:
        days, hours = own.days or 0, own.hours or 0
    else:
        days, hours = general.days, general.hours
    return Limits(days, hours, general.last if own.last is None else own.last)


def in_words(limits: Limits) -> str:
    parts = []
    if limits.age_hours:
        days, hours = divmod(limits.age_hours, 24)
        age = ", ".join(
            text
            for text in (f"{days} day{'s' if days != 1 else ''}" if days else "", f"{hours} h" if hours else "")
            if text
        )
        parts.append(f"older than {age}")
    if limits.last:
        parts.append(f"beyond the latest {limits.last}")
    return " or ".join(parts) or "never"


@dataclass(frozen=True)
class Doomed:
    kind: str
    id: int
    name: str
    limits: Limits
    ids: tuple[str, ...]


# A conversation nobody may delete for you: pinned, archived by you, an approval request still pending.
_KEPT = (
    "c.pinned = 1 OR (c.archived_at IS NOT NULL AND c.archived_by = 'you')"
    " OR EXISTS (SELECT 1 FROM approvals a WHERE a.conversation_id = c.id AND a.status = 'pending')"
)


def _members(kind: str) -> str:
    """The conversations of one kind of automation, with the automation (the latest run that made it), and whether they are kept."""
    runs, owner, busy, own_conversation = (
        ("schedule_runs", "schedule_id", "'queued', 'running', 'waiting'", "schedules")
        if kind == SCHEDULE
        else ("trigger_runs", "trigger_id", "'queued', 'running'", "")
    )
    shared = (
        f" AND c.id NOT IN (SELECT conversation_id FROM {own_conversation} WHERE conversation_id IS NOT NULL)"
        if own_conversation
        else ""
    )
    return (
        f"SELECT c.id, c.updated_at, c.created_at, (SELECT r.{owner} FROM {runs} r WHERE r.conversation_id = c.id"
        f" ORDER BY r.id DESC LIMIT 1) AS owner, ({_KEPT} OR EXISTS (SELECT 1 FROM {runs} r"
        f" WHERE r.conversation_id = c.id AND r.status IN ({busy}))) AS kept"
        f" FROM conversations c WHERE c.id IN (SELECT conversation_id FROM {runs} WHERE conversation_id IS NOT NULL){shared}"
    )


class AutomationRetention:
    def __init__(self, db: Database, conversations: Conversations, runtime: RuntimeSettings, clock=utcnow) -> None:
        self.db = db
        self.conversations = conversations
        self.runtime = runtime
        self._clock = clock

    def general(self) -> Limits:
        get = self.runtime.get
        return Limits(get("automation_keep_days"), get("automation_keep_hours"), get("automation_keep_last"))

    def _automations(self) -> dict[tuple[str, int], tuple[str, Own]]:
        found: dict[tuple[str, int], tuple[str, Own]] = {}
        for kind, table in ((SCHEDULE, "schedules"), (HOOK, "triggers")):
            for row in self.db.all(f"SELECT id, name, keep_days, keep_hours, keep_last FROM {table}"):
                found[(kind, row["id"])] = (row["name"], own_of(row))
        return found

    def exists(self, kind: str, automation_id: int) -> bool:
        return (kind, automation_id) in self._automations()

    def plan(
        self, *, general: Limits | None = None, only: tuple[str, int] | None = None, own: Own | None = None
    ) -> list[Doomed]:
        """The conversations to delete now. `general` replaces the global limits and `own` the limits of the automation `only`
        names (the one the person is editing): both for a preview, neither for the real sweep."""
        general = general or self.general()
        automations = self._automations()
        if only is not None and own is not None and only in automations:
            automations[only] = (automations[only][0], own)
        now = self._clock()
        grouped: dict[tuple[str, int], list] = {}
        for kind in (SCHEDULE, HOOK):
            rows = self.db.all(_members(kind) + " ORDER BY c.updated_at DESC, c.created_at DESC, c.id DESC")
            for row in rows:
                if row["owner"] is not None and (only is None or only == (kind, row["owner"])):
                    grouped.setdefault((kind, row["owner"]), []).append(row)
        doomed = []
        for key, rows in grouped.items():
            if key not in automations:
                continue
            name, mine = automations[key]
            limits = effective(mine, general)
            if not limits.active:
                continue
            cutoff = iso(now - timedelta(hours=limits.age_hours)) if limits.age_hours else None
            ids = tuple(
                row["id"]
                for rank, row in enumerate(rows)
                if not row["kept"]
                and ((cutoff is not None and row["updated_at"] <= cutoff) or (limits.last and rank >= limits.last))
            )
            if ids:
                doomed.append(Doomed(key[0], key[1], name, limits, ids))
        return doomed

    def sweep(self) -> int:
        """Delete what the limits say. Returns the number of conversations deleted."""
        deleted = 0
        for item in self.plan():
            for start in range(0, len(item.ids), CHUNK):
                chunk = item.ids[start : start + CHUNK]
                deleted += self.conversations.delete_where(f"id IN ({', '.join('?' * len(chunk))})", chunk)
        return deleted

    def preview(
        self,
        *,
        general: Limits | None = None,
        only: tuple[str, int] | None = None,
        own: Own | None = None,
    ) -> dict:
        """What the next sweep would delete if these limits were saved. `would_delete` is the total; `more` is what this change
        adds to what the limits now saved delete anyway (the number to warn about: the rest is already going to happen).
        Per automation: `count` and `more` likewise."""
        doomed = self.plan(general=general, only=only, own=own)
        already = {conversation for item in self.plan(only=only) for conversation in item.ids}
        found = [
            {
                "kind": item.kind,
                "id": item.id,
                "name": item.name,
                "count": len(item.ids),
                "more": sum(conversation not in already for conversation in item.ids),
                "limits": in_words(item.limits),
            }
            for item in doomed
        ]
        return {
            "would_delete": sum(item["count"] for item in found),
            "more": sum(item["more"] for item in found),
            "automations": found,
        }

    def preview_change(self, kind: str, automation_id: int, **fields) -> dict:
        """The preview for one automation whose retention fields would take these values (the others stay as stored)."""
        automations = self._automations()
        if (kind, automation_id) not in automations:
            raise RetentionError(f"No {kind} {automation_id}.")
        current = automations[(kind, automation_id)][1]
        values = {
            "days": fields.get("keep_days", current.days),
            "hours": fields.get("keep_hours", current.hours),
            "last": fields.get("keep_last", current.last),
        }
        return self.preview(only=(kind, automation_id), own=check_own(**values))
