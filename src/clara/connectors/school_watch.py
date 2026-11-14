"""New things at Moodle and Auriga, for automations: a poll of the two sites, on its own loop (ops/upkeep.py). Neither site can call
Clara (she is on your tailnet only), so she looks, with your own tokens and only to read. Five streams, each looked at only while an
enabled automation waits for one of its kinds, one pass per site per poll, call after call (never at the same time):

* **Moodle quizzes** (`moodle.quiz_opens`, `moodle.quiz_closes`): a quiz opens and closes at a time the site gives in advance, so its
  event is stored when that time has come, dated that time, however long the poll's interval is. The place is only the time of the last
  look: the quizzes of the courses in progress are read again each time, and every opening or closing between the last look and now is
  an event. (A quiz that left the courses in progress, or was deleted, before its time is not found: the site no longer says it.)
* **Moodle activities** (`moodle.activity_added`): the ids of the visible activities of each course in progress; one that is not in the
  list of the last look is an event. A course seen for the first time is only noted.
* **Moodle grades** (`moodle.grade_posted`): a hash per grade line of each course in progress; a line with a grade that is new, or
  whose grade is not the one hashed, is an event.
* **Auriga planning** (`auriga.planning_changed`): the classes of the next four weeks. An entry (found by the site's id for it) whose
  start or end moved, whose room or teachers changed, that was added or that is gone is an event; the words of its title are not
  looked at. What the events say (before and after) needs the old values: the state keeps, for each entry, its start, end, room,
  teachers and title, never more.
* **Auriga grades** (`auriga.grade_posted`): a hash per line of the synthèse and of the éval results of the school year, as for Moodle.

A grade is never kept in the state, only a hash of it (with the line it belongs to): enough to see that it changed, not to read it.
What an event says was written by other people, and is cleaned to one line.

Like the other polls: the place of a stream moves in the same transaction that stores the events and plans the runs
(`Automations.ingest`), so a crash loses none and repeats none, and an event key names what happened, so a replay is one event. The
first look of a stream only notes where things stand (a new automation never replays history); with no automation waiting for a
stream, its place is forgotten. A site that is not connected, whose sign-in lapsed or that the pack cannot use is skipped without a
word: the alert of the site already tells you, and nothing here signs in again (the one try at the Auriga password sign-in is the
client's, made once per lapse whoever calls). Any other failure is logged once and tried again at the next poll. The whole poll
has a deadline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..automations.events import SourceEvent
from ..automations.service import STALE_AFTER
from ..timeutil import iso, now_iso, parse_iso, zone
from .moodle import Course, MoodleError, Quiz
from .school import SITES, Event, SchoolError, line, school_year

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

# the stream -> the kinds of event it makes; a stream is looked at while an automation waits for one of them
STREAMS = {
    ("moodle", "quizzes"): ("moodle.quiz_opens", "moodle.quiz_closes"),
    ("moodle", "activities"): ("moodle.activity_added",),
    ("moodle", "grades"): ("moodle.grade_posted",),
    ("auriga", "planning"): ("auriga.planning_changed",),
    ("auriga", "grades"): ("auriga.grade_posted",),
}
DEADLINE = 300  # seconds for a whole poll: what is not finished is looked at again at the next one
MAX_COURSES = 30  # courses in progress read in a poll (each costs a call or two)
MAX_EVENTS = 50  # events one stream hands over in one poll; a course that was restored (every id new) must not queue hundreds of runs
PLANNING_DAYS = 28  # the planning is looked at from today to this many days ahead
SHORT = 300


def digest(*parts: object) -> str:
    """What the state keeps in place of a text: enough to see that it changed, not to read it."""
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()[:12]


def given(value: str) -> bool:
    """Whether a grade cell holds a grade: Moodle writes "-" and Auriga nothing for a line with none yet."""
    return value.strip() not in ("", "-", "–", "—")


def entry_id(event: Event) -> str:
    """An Auriga planning entry by the site's own id for it (`id`: a guess to check on the real site). With none, an entry is
    followed by what it is and when it starts, so a class that moved reads as one that disappeared and another that was added."""
    return event.ident or "h" + digest(event.kind, event.title, event.start.isoformat())


class SchoolWatch:
    def __init__(self, services: Services):
        self.s = services
        self._last_error: dict[str, str] = {}  # what was last logged per site, stream or course: an outage is one line
        self._streams = {
            ("moodle", "quizzes"): self._quizzes,
            ("moodle", "activities"): self._activities,
            ("moodle", "grades"): self._moodle_grades,
            ("auriga", "planning"): self._planning,
            ("auriga", "grades"): self._auriga_grades,
        }

    # ------------------------------------------------------------------
    def cursor(self, site: str, stream: str) -> dict | None:
        row = self.s.db.one("SELECT state FROM school_cursors WHERE site = ? AND stream = ?", (site, stream))
        return json.loads(row["state"]) if row else None

    def save(self, site: str, stream: str, state: dict) -> None:
        self.s.db.execute(
            "INSERT INTO school_cursors (site, stream, state, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (site, stream) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
            (site, stream, json.dumps(state), now_iso()),
        )

    def keep(self, wanted: list[tuple[str, str]]) -> None:
        """Forget the place of a stream nobody waits for: the next listener starts from then, not from an old place."""
        for row in self.s.db.all("SELECT site, stream FROM school_cursors"):
            if (row["site"], row["stream"]) not in wanted:
                self.s.db.execute(
                    "DELETE FROM school_cursors WHERE site = ? AND stream = ?", (row["site"], row["stream"])
                )

    def forget(self, site: str) -> None:
        """A site was disconnected, or connected as another account: the next poll starts afresh."""
        self.s.db.execute("DELETE FROM school_cursors WHERE site = ?", (site,))

    def now(self) -> datetime:
        return self.s.automations.local_now("UTC")

    # ------------------------------------------------------------------
    async def poll(self) -> None:
        wanted = [key for key, kinds in STREAMS.items() if any(self.s.automations.wants(kind) for kind in kinds)]
        self.keep(wanted)
        if not wanted:
            return
        try:
            async with asyncio.timeout(DEADLINE):
                for site in SITES:
                    state = self.s.connectors.school_state(site)
                    if state == "off":
                        self.forget(site)  # not connected: what it was is no place to compare with
                    elif state == "ok":  # a lapsed sign-in is skipped: the site's alert says why
                        await self._site(site, [stream for who, stream in wanted if who == site])
        except TimeoutError:
            log.warning(
                "school: a poll took longer than %d s and was stopped; the rest is looked at again at the next one",
                DEADLINE,
            )

    async def _site(self, site: str, streams: list[str]) -> None:
        if not streams:
            return
        connectors = self.s.connectors
        client = connectors.moodle_client() if site == "moodle" else connectors.auriga_client()
        if client is None:
            return
        now, worked = self.now(), False
        try:
            courses = await client.courses("inprogress") if site == "moodle" else []
            if len(courses) > MAX_COURSES:
                self._report(
                    "moodle courses",
                    MoodleError(f"{len(courses)} courses are in progress: only the first {MAX_COURSES} are looked at."),
                )
                courses = courses[:MAX_COURSES]
            for stream in streams:
                try:
                    await self._streams[(site, stream)](client, courses, now)
                    worked = True
                    self._last_error.pop(f"{site} {stream}", None)
                except SchoolError as error:
                    if error.reconnect:
                        raise  # the sign-in is refused: every other call would be too
                    self._report(f"{site} {stream}", error)
                except Exception:
                    log.exception("school %s (%s): the poll failed", site, stream)
        except SchoolError as error:
            # a refused sign-in is marked and the site's alert raised, as a tool's call does
            connectors.school_failed(site, error)
            self._report(site, error)
        except Exception:
            log.exception("school %s: the poll failed", site)
        if worked:
            connectors.school_worked(site)

    def _report(self, key: str, error: Exception) -> None:
        if self._last_error.get(key) != str(error):
            self._last_error[key] = str(error)
            log.warning("school %s: %s", key, error)

    def _hand_over(self, site: str, stream: str, events: list[SourceEvent], state: dict) -> None:
        """Store the events, plan their runs and move the place, in one transaction."""
        events = sorted(events, key=lambda event: event.occurred_at)
        if len(events) > MAX_EVENTS:
            log.warning(
                "school %s (%s): %d events at once; only the first %d were kept", site, stream, len(events), MAX_EVENTS
            )
            events = events[:MAX_EVENTS]

        def move() -> None:
            self.save(site, stream, state)

        if events:
            # a quiz time found at the first look after it came is on time, not a backlog, whatever the interval
            late = max(STALE_AFTER, timedelta(seconds=self.s.settings.school_poll_seconds + 60))
            self.s.automations.ingest(events, also=move, stale_after=late)
        else:
            move()

    def _shown(self, moment: datetime | None) -> str:
        """A time as the person reads it: on the clock of the owner's zone."""
        return f"{moment.astimezone(zone(self.s.automations.home_zone)):%Y-%m-%d %H:%M}" if moment else ""

    # ------------------------------------------------------------------
    # Moodle: quizzes
    # ------------------------------------------------------------------
    async def _quizzes(self, client: Any, courses: list[Course], now: datetime) -> None:
        found = self.cursor("moodle", "quizzes")
        if found is None:  # a time already past at the first look is not an event
            self.save("moodle", "quizzes", {"checked": iso(now)})
            return
        checked = parse_iso(found["checked"])
        events = [
            self._quiz_event(quiz, what, at)
            for quiz in await client.quiz_times(courses)
            for what, at in (("opens", quiz.opens), ("closes", quiz.closes))
            if at is not None and checked < at <= now
        ]
        self._hand_over("moodle", "quizzes", events, {"checked": iso(now)})

    def _quiz_event(self, quiz: Quiz, what: str, at: datetime) -> SourceEvent:
        course, name = line(quiz.course), line(quiz.name)
        return SourceEvent(
            source="moodle",
            kind=f"moodle.quiz_{what}",
            key=f"moodle:quiz:{quiz.id}:{what}:{int(at.timestamp())}",
            # when it happened, not when the poll saw it: found late, it is a backlog for `catch_up`
            occurred_at=iso(at),
            title=f"{course}: quiz {name} {what}"[:SHORT],
            data={"course": course, "quiz": name, "opens": self._shown(quiz.opens), "closes": self._shown(quiz.closes)},
        )

    # ------------------------------------------------------------------
    # Moodle: activities
    # ------------------------------------------------------------------
    async def _activities(self, client: Any, courses: list[Course], now: datetime) -> None:
        known: dict[str, list[int]] = (self.cursor("moodle", "activities") or {}).get("courses", {})
        listed = {str(course.id) for course in courses}
        # a course that is no longer in progress is forgotten
        seen = {key: ids for key, ids in known.items() if key in listed}
        events = []
        for course in courses:
            try:
                sections = await client.contents(course.id)
            except MoodleError as error:
                if error.reconnect:
                    raise
                self._report(f"moodle activities {course.id}", error)  # this course is tried again at the next poll
                continue
            # a label is a line of text on the course page, not an activity
            shown = [(s, m) for s in sections for m in s.modules if m.visible and m.kind != "label"]
            current = {module.id for _, module in shown}
            old = known.get(str(course.id))
            if old is not None:  # a course seen for the first time is noted, not announced
                events += [self._activity_event(course, s, m, now) for s, m in shown if m.id not in set(old)]
                if sections.notes:  # some entries were left out: they are not gone
                    current |= set(old)
            seen[str(course.id)] = sorted(current)
        self._hand_over("moodle", "activities", events, {"courses": seen})

    @staticmethod
    def _activity_event(course: Course, section: Any, module: Any, now: datetime) -> SourceEvent:
        title, place, name = line(course.fullname), line(section.name) or f"section {section.number}", line(module.name)
        return SourceEvent(
            source="moodle",
            kind="moodle.activity_added",
            key=f"moodle:activity:{course.id}:{module.id}",
            occurred_at=iso(now),
            title=f"{title}: new {module.kind} {name}"[:SHORT],
            data={"course": title, "section": place, "name": name, "type": module.kind},
        )

    # ------------------------------------------------------------------
    # Moodle: grades
    # ------------------------------------------------------------------
    async def _moodle_grades(self, client: Any, courses: list[Course], now: datetime) -> None:
        known: dict[str, dict[str, str]] = (self.cursor("moodle", "grades") or {}).get("courses", {})
        listed = {str(course.id) for course in courses}
        seen = {key: lines for key, lines in known.items() if key in listed}
        events = []
        for course in courses:
            try:
                items = await client.grades(course.id)
            except MoodleError as error:
                if error.reconnect:
                    raise
                self._report(f"moodle grades {course.id}", error)
                continue
            current: dict[str, str] = {}
            old = known.get(str(course.id))
            for item in items:
                ident = str(item.id or digest(item.kind, item.module, item.name))
                current[ident] = hashed = digest(course.id, ident, item.grade if given(item.grade) else "")
                if old is not None and given(item.grade) and old.get(ident) != hashed:
                    events.append(self._moodle_grade_event(course, item, ident, hashed, now))
            seen[str(course.id)] = {**old, **current} if old is not None and items.notes else current
        self._hand_over("moodle", "grades", events, {"courses": seen})

    @staticmethod
    def _moodle_grade_event(course: Course, item: Any, ident: str, hashed: str, now: datetime) -> SourceEvent:
        title = line(course.fullname)
        name = item.name or (
            "Course total" if item.kind == "course" else "Category total" if item.kind == "category" else "(unnamed)"
        )
        return SourceEvent(
            source="moodle",
            kind="moodle.grade_posted",
            key=f"moodle:grade:{course.id}:{ident}:{hashed}",
            occurred_at=iso(now),
            title=f"{title}: {name}: {item.grade}"[:SHORT],
            data={"course": title, "item": name, "grade": item.grade, "range": item.range},
        )

    # ------------------------------------------------------------------
    # Auriga: grades
    # ------------------------------------------------------------------
    async def _auriga_grades(self, client: Any, courses: list[Course], now: datetime) -> None:
        year = school_year(now.date())
        found = self.cursor("auriga", "grades")
        # a new school year is new lines everywhere: noted, not announced
        first = found is None or found.get("year") != year
        before: dict[str, dict[str, str]] = {} if first else found["lines"]
        lines: dict[str, dict[str, str]] = {}
        events = []
        for report in ("synthese", "eval"):
            result = await client.grades(report, year)
            old, current, places = before.get(report, {}), {}, Counter()
            for grade in result.lines:
                if report == "synthese":
                    ident = grade.code
                else:  # a mark has no id of its own here: the lines of one code and type are told apart by their order
                    ident = f"{grade.code}|{grade.kind}|{places[(grade.code, grade.kind)]}"
                    places[(grade.code, grade.kind)] += 1
                # the kind of a synthèse line says whether its value is the final average or the one before the RATT retake
                current[ident] = hashed = digest(
                    report, ident, grade.value if given(grade.value) else "", grade.kind if report == "synthese" else ""
                )
                if not first and given(grade.value) and old.get(ident) != hashed:
                    events.append(self._auriga_grade_event(report, ident, hashed, grade, now))
            lines[report] = {**old, **current} if result.notes else current
        self._hand_over("auriga", "grades", events, {"year": year, "lines": lines})

    @staticmethod
    def _auriga_grade_event(report: str, ident: str, hashed: str, grade: Any, now: datetime) -> SourceEvent:
        title = " ".join(part for part in (grade.code, grade.title) if part)
        return SourceEvent(
            source="auriga",
            kind="auriga.grade_posted",
            key=f"auriga:grade:{report}:{ident}:{hashed}",
            occurred_at=iso(now),
            title=f"{title}: {grade.value}"[:SHORT],
            data={"code": grade.code, "title": grade.title, "value": grade.value, "kind": grade.kind, "report": report},
        )

    # ------------------------------------------------------------------
    # Auriga: planning
    # ------------------------------------------------------------------
    async def _planning(self, client: Any, courses: list[Course], now: datetime) -> None:
        today = now.date()
        until = today + timedelta(days=PLANNING_DAYS)
        found = self.cursor("auriga", "planning")
        entries = await client.planning(today, until)
        # start, end, room, teachers, title: what the events say, before and after
        current = {
            entry_id(entry): [
                iso(entry.start),
                iso(entry.end) if entry.end else "",
                entry.room,
                entry.teachers,
                entry.title,
            ]
            for entry in entries
        }
        state = {"until": until.isoformat(), "entries": current}
        if found is None:
            self.save("auriga", "planning", state)
            return
        before: dict[str, list[str]] = found["entries"]
        upcoming = {key: entry for key, entry in before.items() if parse_iso(entry[0]) > now}
        if not current and upcoming:  # a site that answers an empty planning while classes are known is not believed
            log.warning(
                "school auriga (planning): the planning came back empty; its %d known classes are kept", len(upcoming)
            )
            return
        events = []
        covered = datetime.fromisoformat(found["until"]).date()
        for key, new in current.items():
            old = before.get(key)
            if old is None:  # an entry beyond the window of the last look was only out of sight, not new
                if parse_iso(new[0]).date() <= covered:
                    events.append(self._planning_event(key, "added", None, new, now))
                continue
            if old[:2] != new[:2]:
                events.append(self._planning_event(key, "moved", old, new, now))
            if old[2] != new[2]:
                events.append(self._planning_event(key, "room", old, new, now))
            if old[3] != new[3]:
                events.append(self._planning_event(key, "teachers", old, new, now))
        if not entries.notes:  # with entries left out, a missing one may only be unreadable
            events += [
                self._planning_event(key, "cancelled", old, old, now)
                for key, old in upcoming.items()
                if key not in current
            ]
        else:
            state["entries"] = {**{key: old for key, old in upcoming.items() if key not in current}, **current}
        self._hand_over("auriga", "planning", events, state)

    def _planning_event(
        self, ident: str, change: str, old: list[str] | None, new: list[str], now: datetime
    ) -> SourceEvent:
        tz = zone(self.s.automations.home_zone)
        start = parse_iso(new[0]).astimezone(tz)

        def span(entry: list[str], day: bool = False) -> str:
            begin, end = parse_iso(entry[0]).astimezone(tz), parse_iso(entry[1]).astimezone(tz) if entry[1] else None
            return f"{begin:%Y-%m-%d }" * day + f"{begin:%H:%M}" + (f"–{end:%H:%M}" if end and end > begin else "")

        def what(entry: list[str]) -> str:
            return ", ".join(part for part in (span(entry), entry[2], entry[3]) if part)

        if change == "moved":
            details, hashed = f"{span(old, True)} → {span(new, True)}", digest(old[0], old[1], new[0], new[1])
        elif change == "room":
            details, hashed = f"room {old[2] or 'none'} → {new[2] or 'none'}", digest(old[2], new[2])
        elif change == "teachers":
            details, hashed = f"teachers {old[3] or 'none'} → {new[3] or 'none'}", digest(old[3], new[3])
        elif change == "cancelled":
            details, hashed = f"was {what(new)}", digest(new[0], new[1])
        else:
            details, hashed = what(new), digest(new[0], new[1])
        day, title = f"{start:%Y-%m-%d}", line(new[4])
        return SourceEvent(
            source="auriga",
            kind="auriga.planning_changed",
            key=f"auriga:planning:{ident}:{change}:{hashed}",
            occurred_at=iso(now),
            title=f"{day} {title}: {change}"[:SHORT],
            data={
                "date": day,
                "title": title,
                "change": change,
                "details": line(details, 200),
                "start": new[0],
                "end": new[1],
            },
        )
