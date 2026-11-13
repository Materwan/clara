"""The trigger of an automation: a free tree of AND and OR over **nodes**.

    {"type": "or", "of": [
        {"type": "and", "of": [{"type": "window", "from": "18:00", "to": "20:00", "days": [0, 1, 2, 3, 4]},
                               {"type": "event", "kind": "github.push", "filter": {"branch": {"pattern": "^main$"}}}]},
        {"type": "cron", "expr": "0 9 * * *"}]}

The top is an **OR**: a run starts when any branch holds. Six kinds of node:

* `event`   `kind` (mail.received, github.push…) and a `filter` (matching.py). An **event**: it holds for an event of that kind
            the filter accepts.
* `cron`    `expr`, a cron expression (cron.py), read on the clock of the automation's time zone. By default an **instant** (an
            event: it holds at each minute the expression names); `"as": "condition"` makes it a condition, a window to the
            minute (`* 18-19 * * 1-5`) that holds while the minute matches. Left out, `as` is a condition when it stands in an AND
            beside an event, an instant otherwise.
* `once`    `at`, a local date and time: an instant, once. An **event** too.
* `window`  `from`, `to` (HH:MM) and `days` (0 = Monday… default every day). A **condition**: it holds while the clock is in
            [from, to); `to` before `from` crosses midnight (18:30 to 02:15), the days are the days it starts on.
* `and`/`or` `of`: the branches.

**An AND holds at most one event** (a mail, a push, a pull request, a cron instant, a date) among its branches, with conditions
of time beside it. Two events are joined with OR, never AND, so nothing has to be kept between two events to wait for the other.
Every branch holds at least one event: a condition alone never starts a run.

A thing that arrives (an event from a source, or an instant that came) is **evaluated** against the tree at the moment it
happened, in the zone of the automation. The evaluation says why each node holds or not (the Test dry run shows it).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ..timekeeping.clock import MomentError, read_moment
from ..timeutil import resolve_local
from . import cron as cron_module
from . import matching
from .events import EventKind

MAX_NODES = 40
MAX_DEPTH = 6
TIME = "time"  # the kind of an arrival that is an instant, not an event from a source
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_EVENT_WORDS = "a mail, a push, a pull request, a cron instant, a date"

KindOf = Callable[[str], EventKind | None]


class TreeError(ValueError):
    pass


# ----------------------------------------------------------------------
# Reading and checking a tree
# ----------------------------------------------------------------------
def _clock(value: object, where: str) -> str:
    found = _CLOCK.match(str(value or "").strip())
    if found is None:
        raise TreeError(f"{where}: a time is HH:MM (18:30).")
    return f"{int(found.group(1)):02d}:{found.group(2)}"


def _days(value: object, where: str) -> list[int]:
    if value in (None, []):
        return list(range(7))
    if not isinstance(value, list) or any(
        isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in value
    ):
        raise TreeError(f"{where}: days are numbers from 0 (Monday) to 6 (Sunday).")
    return sorted(set(value))


def _node(raw: object, kind_of: KindOf, tz: ZoneInfo, depth: int, count: list[int]) -> dict:
    if not isinstance(raw, dict):
        raise TreeError('A node is an object: {"type": "event" | "cron" | "window" | "once" | "and" | "or", …}.')
    count[0] += 1
    if count[0] > MAX_NODES:
        raise TreeError(f"At most {MAX_NODES} nodes in a trigger.")
    if depth > MAX_DEPTH:
        raise TreeError(f"At most {MAX_DEPTH} levels of AND and OR.")
    type_ = str(raw.get("type") or "").strip().lower()
    if type_ in ("and", "or"):
        children = raw.get("of")
        if not isinstance(children, list) or not children:
            raise TreeError(f'An {type_.upper()} needs "of": a list of branches.')
        return {"type": type_, "of": [_node(child, kind_of, tz, depth + 1, count) for child in children]}
    if type_ == "event":
        kind = kind_of(str(raw.get("kind") or ""))
        if kind is None:
            raise TreeError(f"{raw.get('kind')!r} is not a kind of event.")
        try:
            filter_ = matching.check(kind, raw.get("filter"))
        except matching.FilterError as error:
            raise TreeError(str(error)) from None
        return {"type": "event", "kind": kind.kind, "filter": filter_}
    if type_ == "cron":
        try:
            expr = cron_module.parse(str(raw.get("expr") or "")).text
        except cron_module.CronError as error:
            raise TreeError(str(error)) from None
        node = {"type": "cron", "expr": expr}
        if raw.get("as") not in (None, "instant", "condition"):
            raise TreeError('A cron node is "as": "instant" or "condition".')
        if raw.get("as"):
            node["as"] = raw["as"]
        return node
    if type_ == "window":
        start, end = _clock(raw.get("from"), "window from"), _clock(raw.get("to"), "window to")
        if start == end:
            raise TreeError("A window ends at another time than it starts.")
        return {"type": "window", "from": start, "to": end, "days": _days(raw.get("days"), "window")}
    if type_ == "once":
        try:
            _, local = read_moment(str(raw.get("at") or ""), tz)
        except MomentError as error:
            raise TreeError(f"once: {error}") from None
        return {"type": "once", "at": local.isoformat(timespec="minutes")}
    raise TreeError(f"{raw.get('type')!r} is not a node (event, cron, window, once, and, or).")


def _is_event(node: dict) -> bool:
    """A node that brings an event of its own (what an AND may hold once)."""
    return node["type"] in ("event", "once") or (node["type"] == "cron" and node.get("as") != "condition")


def _carries(node: dict) -> bool:
    """Does this node hold an event somewhere inside, so that a cron beside it is a condition?"""
    if node["type"] in ("and", "or"):
        return any(_carries(child) for child in node["of"])
    return node["type"] in ("event", "once") or (node["type"] == "cron" and node.get("as") == "instant")


def _roles(node: dict) -> None:
    """Put the role of each cron node in the node (`as`), from where it stands."""
    if node["type"] not in ("and", "or"):
        return
    for index, child in enumerate(node["of"]):
        if child["type"] == "cron" and "as" not in child:
            beside = any(_carries(other) for other_index, other in enumerate(node["of"]) if other_index != index)
            child["as"] = "condition" if node["type"] == "and" and beside else "instant"
        _roles(child)


def _most(node: dict) -> int:
    """The most events that may have to be there together for the node to hold."""
    if node["type"] in ("event", "once"):
        return 1
    if node["type"] == "cron":
        return int(node["as"] == "instant")
    if node["type"] == "window":
        return 0
    sizes = [_most(child) for child in node["of"]]
    return sum(sizes) if node["type"] == "and" else max(sizes)


def _least(node: dict) -> int:
    """The fewest events a node needs to hold: 0 means a branch of conditions only, which never starts a run."""
    if node["type"] in ("event", "once"):
        return 1
    if node["type"] == "cron":
        return int(node["as"] == "instant")
    if node["type"] == "window":
        return 0
    sizes = [_least(child) for child in node["of"]]
    return sum(sizes) if node["type"] == "and" else min(sizes)


def _walk(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("of", ()):
        yield from _walk(child)


def _validate(root: dict, kind_of: KindOf) -> None:
    for node in _walk(root):
        if node["type"] == "and" and sum(_most(child) for child in node["of"]) > 1:
            raise TreeError(
                f"An AND holds at most one event ({_EVENT_WORDS}): {words(node, kind_of)}. Join two events with OR, "
                'and put the conditions of time (window, cron "as": "condition") beside one event.'
            )
    for branch in root["of"]:
        if _least(branch) < 1:
            raise TreeError(
                f"{words(branch, kind_of)} holds no event, so it never starts a run: a window or a cron condition needs an event "
                "(a mail, a push, a cron instant, a date) beside it in an AND."
            )


def normalize(raw: object, kind_of: KindOf, tz: ZoneInfo) -> dict:
    """The tree as stored (an OR at the top, every node checked, every cron with its role), or TreeError saying what is wrong."""
    if isinstance(raw, dict) and raw.get("type") != "or" and "of" not in raw:
        raw = {"type": "or", "of": [raw]}  # a single node is a tree of one branch
    if isinstance(raw, list):
        raw = {"type": "or", "of": raw}
    root = _node(raw, kind_of, tz, 1, [0])
    if root["type"] != "or":
        root = {"type": "or", "of": [root]}
    _roles(root)
    _validate(root, kind_of)
    return root


# ----------------------------------------------------------------------
# What a tree holds
# ----------------------------------------------------------------------
def kinds_of(root: dict) -> list[str]:
    """The kinds of event from a source that the tree waits for (not the time ones)."""
    return list(dict.fromkeys(node["kind"] for node in _walk(root) if node["type"] == "event"))


def instants_of(root: dict) -> list[dict]:
    return [
        node for node in _walk(root) if node["type"] == "once" or (node["type"] == "cron" and node["as"] == "instant")
    ]


def once_moment(node: dict, tz: ZoneInfo) -> datetime:
    return resolve_local(datetime.fromisoformat(node["at"]), tz).astimezone(UTC)


def next_instant(root: dict, after: datetime, tz: ZoneInfo) -> datetime | None:
    """The first instant strictly after `after` that any `cron` instant or `once` node names."""
    found: list[datetime] = []
    for node in instants_of(root):
        if node["type"] == "once":
            moment = once_moment(node, tz)
            if moment > after:
                found.append(moment)
        else:
            moment = cron_module.parse(node["expr"]).next_instant(after, tz)
            if moment is not None:
                found.append(moment)
    return min(found) if found else None


def instants_between(
    root: dict, after: datetime, until: datetime, tz: ZoneInfo, limit: int
) -> tuple[list[datetime], int]:
    """The instants in (after, until], oldest first, at most `limit` of them; and how many more there were (counted up to a
    thousand, for the note that says some were left out)."""
    found: list[datetime] = []
    more = 0
    cursor = after
    while (moment := next_instant(root, cursor, tz)) is not None and moment <= until:
        if len(found) < limit:
            found.append(moment)
        else:
            more += 1
            if more >= 1000:
                break
        cursor = moment
    return found, more


# ----------------------------------------------------------------------
# Evaluating a tree
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Arrival:
    """What came: an event from a source (`kind` is its kind) or an instant (`kind` is TIME). `at` is when it happened (aware)."""

    kind: str
    at: datetime
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    type: str
    ok: bool
    why: str
    children: tuple[Verdict, ...] = ()
    time: bool = False  # a node of time (a window, a cron condition)

    def describe(self) -> dict:
        found = {"type": self.type, "ok": self.ok, "why": self.why}
        if self.children:
            found["of"] = [child.describe() for child in self.children]
        return found

    def failed_times(self) -> list[Verdict]:
        """The nodes of time that did not hold, in order."""
        found = [self] if self.time and not self.ok else []
        for child in self.children:
            found += child.failed_times()
        return found


def in_window(node: dict, local: datetime) -> bool:
    start, end = (int(part) for part in node["from"].split(":")), (int(part) for part in node["to"].split(":"))
    begin, stop = next(start) * 60 + next(start), next(end) * 60 + next(end)
    minute = local.hour * 60 + local.minute
    day = local.weekday()
    if begin < stop:
        return day in node["days"] and begin <= minute < stop
    return (day in node["days"] and minute >= begin) or ((day - 1) % 7 in node["days"] and minute < stop)


def evaluate(node: dict, arrival: Arrival, tz: ZoneInfo, *, ignore_time: bool = False) -> Verdict:
    """Does the node hold for this arrival? `ignore_time` counts every window and cron condition as holding (to tell an event
    that the filters accept but the time refuses from one the filters refuse)."""
    local = arrival.at.astimezone(tz).replace(tzinfo=None)
    type_ = node["type"]
    if type_ in ("and", "or"):
        children = tuple(evaluate(child, arrival, tz, ignore_time=ignore_time) for child in node["of"])
        ok = all(child.ok for child in children) if type_ == "and" else any(child.ok for child in children)
        return Verdict(
            type_,
            ok,
            "all of its branches hold"
            if ok and type_ == "and"
            else ("a branch holds" if ok else ("a branch does not hold" if type_ == "and" else "no branch holds")),
            children,
        )
    if type_ == "event":
        if arrival.kind != node["kind"]:
            return Verdict(type_, False, f"it is {_arrival_words(arrival)}; this branch waits for {node['kind']}")
        judged = matching.judge(node["filter"], arrival.data)
        failed = [item for item in judged if not item.ok]
        if failed:
            return Verdict(type_, False, "; ".join(item.why for item in failed))
        return Verdict(
            type_, True, f"{node['kind']} event" + (": " + "; ".join(i.why for i in judged) if judged else "")
        )
    if type_ == "window":
        text = f"{local:%a %H:%M} is {{}} {node['from']}-{node['to']} ({_days_words(node['days'])})"
        ok = ignore_time or in_window(node, local)
        return Verdict(type_, ok, text.format("inside" if ok else "outside"), time=True)
    if type_ == "cron" and node["as"] == "condition":
        ok = ignore_time or cron_module.parse(node["expr"]).matches(local)
        return Verdict(
            type_, ok, f"{local:%a %H:%M} {'matches' if ok else 'does not match'} cron “{node['expr']}”", time=True
        )
    if arrival.kind != TIME:
        return Verdict(type_, False, f"it is {_arrival_words(arrival)}, not an instant")
    if type_ == "once":
        ok = local == datetime.fromisoformat(node["at"])
        return Verdict(
            type_, ok, f"{local:%Y-%m-%d %H:%M} {'is' if ok else 'is not'} the date {node['at'].replace('T', ' ')}"
        )
    ok = cron_module.parse(node["expr"]).matches(local)
    return Verdict(type_, ok, f"{local:%a %H:%M} {'is' if ok else 'is not'} an instant of cron “{node['expr']}”")


def _arrival_words(arrival: Arrival) -> str:
    return "an instant" if arrival.kind == TIME else f"a {arrival.kind} event"


# ----------------------------------------------------------------------
# Saying it in words
# ----------------------------------------------------------------------
def _days_words(days: list[int]) -> str:
    if len(days) == 7:
        return "every day"
    return ", ".join(WEEKDAYS[day] for day in days)


def words(node: dict, kind_of: KindOf | None = None, *, top: bool = True) -> str:
    """The trigger in one readable line: `(a push where branch matches “^main$” and between 18:00 and 20:00) or at cron “0 9 * * *”`."""
    type_ = node["type"]
    if type_ == "event":
        kind = kind_of(node["kind"]) if kind_of else None
        text = kind.noun if kind else node["kind"]
        return f"{text} where {matching.words(node['filter'])}" if node["filter"] else text
    if type_ == "cron":
        return f"at cron “{node['expr']}”" if node.get("as") != "condition" else f"while cron “{node['expr']}” matches"
    if type_ == "once":
        day, _, clock = node["at"].partition("T")
        return f"once, on {day} at {clock}"
    if type_ == "window":
        return f"between {node['from']} and {node['to']} ({_days_words(node['days'])})"
    parts = [words(child, kind_of, top=top and len(node["of"]) == 1) for child in node["of"]]
    if len(parts) == 1:
        return parts[0]
    joined = (" and " if type_ == "and" else " or ").join(parts)
    return joined if top else f"({joined})"
