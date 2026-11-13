"""Moodle (EPITA's "Plateforme d'enseignement"), read only, through its web-service REST API with the token of the mobile
app (the secret `moodle_token`).

The site has one way in, "Forge ID" (a browser sign-in with no password form), so the usual username-and-password token
cannot be had. The owner signs in once in a browser to Moodle's mobile-app launch page; it answers with a link
`moodlemobile://token=<base64 of "siteid:::token">`, and the owner pastes that link (`token_from_link`). Moodle has no
refresh: when the token lapses every call answers `invalidtoken` and the owner pastes a new link.

The token has the owner's full rights and Moodle cannot make one read-only (it can call writes: `mod_assign_save_submission`,
`core_message_send_instant_messages`). So `Moodle.call` runs only the functions of READ_FUNCTIONS, and nothing else, whatever
a tool asks. The token goes only to this site's address: a document's link that points elsewhere is not followed.

`core_webservice_get_site_info` also answers a `userprivateaccesskey`, which opens a session without a token: it is never
kept, shown or logged (only the user id and the name are read from the answer).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import httpx

from .mail import html_to_text, human
from .school import Event, Noted, SchoolError, line

BASE = "https://moodle.epita.fr"
ENDPOINT = "/webservice/rest/server.php"
LAUNCH = "/admin/tool/mobile/launch.php"
FILES = "/webservice/pluginfile.php/"
TIMEOUT = httpx.Timeout(20.0, read=60.0)
MAX_DOWNLOAD = 60_000_000  # a document bigger than this is not downloaded
DOWNLOAD_SECONDS = 120.0  # a whole download: the timeouts above are per read, so a body that trickles would never end
MAX_ERROR_ANSWER = 4096  # an error Moodle answers to a download is a few hundred bytes of JSON; more is the document
CHUNK = 64 * 1024
PAGE = 50  # courses and events per call (Moodle's own limit for events)
MAX_COURSE_PAGES = 10
MAX_EVENT_PAGES = 8
ENROLLED_FOR = 600.0  # seconds the list of every course of the owner is kept (it changes once a term)
TOKEN = re.compile(r"^[A-Za-z0-9]{16,128}$")

# The only functions this client calls. Nothing else, whatever a tool asks (a write function is never here).
READ_FUNCTIONS = frozenset(
    {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_course_get_enrolled_courses_by_timeline_classification",
        "core_course_get_contents",
        "core_calendar_get_action_events_by_timesort",
        "mod_quiz_get_quizzes_by_courses",
        "gradereport_user_get_grade_items",
    }
)
CLASSIFICATIONS = ("inprogress", "past", "future")


class MoodleError(SchoolError):
    pass


# ----------------------------------------------------------------------
# Signing in
# ----------------------------------------------------------------------
def launch_url() -> str:
    """The page the owner opens, signed in to Moodle: it answers with the link to paste back."""
    query = {
        "service": "moodle_mobile_app",
        "passport": secrets.token_hex(8),
        "urlscheme": "moodlemobile",
        "confirmed": "1",
    }
    return f"{BASE}{LAUNCH}?{urlencode(query)}"


def token_from_link(text: str) -> str:
    """The token inside what the owner pasted: the link `moodlemobile://token=<base64>`, its base64 part alone, or a bare
    token. Only the token is kept: a private access key the link may carry as a third part is thrown away."""
    found = str(text or "").strip().strip("<>\"' ")
    if re.fullmatch(r"[0-9a-f]{32}", found):  # the token alone
        return found
    match = re.search(r"token=([^\s&\"'<>]+)", found)
    payload = unquote(match.group(1) if match else found).replace("-", "+").replace("_", "/")
    try:
        decoded = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        decoded = ""
    parts = decoded.split(":::")
    if len(parts) < 2 or not TOKEN.fullmatch(parts[1]):
        raise MoodleError(
            "That is not the link of Moodle's mobile sign-in. Open the sign-in address you were given while signed in "
            "to Moodle, then copy the address of the link «Cliquez ici si l'app ne s'ouvre pas automatiquement» that "
            "it shows: it starts with moodlemobile://token="
        )
    return parts[1]


# ----------------------------------------------------------------------
# What the site says, kept small
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Course:
    id: int
    fullname: str
    shortname: str
    start: int
    end: int
    progress: float | None  # None unless Moodle tracks the progress of this course (`hasprogress`)
    hidden: bool = False  # hidden from the course overview by the owner: in none of the Timeline classes


@dataclass(frozen=True)
class Item:
    """One file or link inside an activity of a course."""

    number: int  # its place among the activity's contents, from 1
    kind: str  # file or url
    name: str
    size: int
    modified: int
    url: str = field(repr=False)


@dataclass(frozen=True)
class Module:
    id: int  # the course-module id (cmid)
    kind: str  # resource, folder, page, quiz, assign, forum, hvp…
    name: str
    visible: bool
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Section:
    number: int
    name: str
    modules: tuple[Module, ...]


@dataclass(frozen=True)
class GradeItem:
    name: str
    kind: str
    module: str
    grade: str
    range: str
    percentage: str
    feedback: str
    graded: int


# Moodle's own text: a name may hold `{mlang en}…{mlang}{mlang fr}…{mlang}` blocks (every language is kept, joined with " / "),
# entities (`&amp;`, `&ndash;`) and tags (an anchor round a grade). The raw text is cut first: a hostile run of openers with no
# closer would make the block search quadratic.
_MLANG_OPEN = re.compile(r"\{mlang[ \t]+[^{}]{0,40}\}", re.IGNORECASE)
_MLANG_CLOSE = re.compile(r"\{mlang\}", re.IGNORECASE)
_MLANG_STRAY = re.compile(r"\{mlang[^{}]{0,40}\}", re.IGNORECASE)
_BREAKS = re.compile(r"<\s*/?\s*(?:br|p|div|li|ul|ol|tr|td|th|table|h[1-6])\b[^<>]*>", re.IGNORECASE)
_TAG = re.compile(r"<(?:/?[A-Za-z][^<>]*|!--.*?--)>", re.DOTALL)
MAX_RAW_TEXT = 4000


def languages(text: str) -> str:
    """Each run of `{mlang}` blocks as the text of every language, joined with " / " (the same text twice is kept once).
    One pass with no backtracking: the cost is linear whatever the text holds."""
    out: list[str] = []
    pos = 0
    while True:
        first = _MLANG_OPEN.search(text, pos)
        if first is None:
            break
        parts: list[str] = []
        opened: re.Match | None = first
        stop = first.start()
        while opened is not None:
            closed = _MLANG_CLOSE.search(text, opened.end())
            if closed is None:
                break
            parts.append(text[opened.end() : closed.start()].strip())
            stop = closed.end()
            skip = stop
            while skip < len(text) and text[skip].isspace():
                skip += 1
            opened = _MLANG_OPEN.match(text, skip)
        if not parts:  # an opener with no closer: none follows either
            break
        out += [text[pos : first.start()], " / ".join(dict.fromkeys(part for part in parts if part)), " "]
        pos = stop
    out.append(text[pos:])
    return _MLANG_STRAY.sub(" ", "".join(out))


def moodle_text(value: Any, limit: int = 160) -> str:
    """One cleaned line of what Moodle writes (a name, a grade cell): `{mlang}` blocks joined, tags dropped, entities read."""
    text = languages(str(value if value is not None else "")[:MAX_RAW_TEXT])
    text = _TAG.sub("", _BREAKS.sub(" ", text))
    return line(
        _TAG.sub("", html.unescape(text)), limit
    )  # an escaped tag (`&lt;b&gt;`) is dropped too, not shown as one


def records(found: Any, what: str) -> list:
    """The list an answer must be. Anything else is a failure to say, not an empty list (`what` says what was asked)."""
    if not isinstance(found, list):
        raise MoodleError(
            f"Moodle's answer about {what} is not what Clara expects (a list): the site may have changed."
        )
    return found


def number(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


MAX_SECONDS = 253_402_300_799  # 9999-12-31 23:59:59: later than that datetime has no year


def seconds(value: Any) -> int:
    """A time as Moodle writes it (seconds since 1970), or 0 when it is not one `when` can turn into a date: a site that
    answers a huge or a negative number gives a line without a date, not a calendar that fails."""
    found = number(value)
    return found if 0 < found <= MAX_SECONDS else 0


def when(stamp: int) -> datetime:
    return datetime.fromtimestamp(stamp, UTC)


def encode(params: dict) -> dict[str, str]:
    """Form fields as Moodle reads them: a list is `name[0]`, `name[1]`."""
    out: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for position, item in enumerate(value):
                out[f"{key}[{position}]"] = str(item)
        else:
            out[key] = str(value)
    return out


class Moodle:
    def __init__(self, token: str, base: str = BASE, transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        self.base = base.rstrip("/")
        self._transport = transport
        self._userid = 0
        self._enrolled: tuple[float, list[Course]] | None = None
        self._enrolling = asyncio.Lock()

    def _open(self) -> httpx.AsyncClient:
        """One connection for the calls that follow each other (the pages of one answer), so that a TLS handshake is not
        made for each. It is not kept between tool calls: nothing is left open to close."""
        return httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport)

    # ------------------------------------------------------------------
    async def call(self, function: str, params: dict | None = None, http: httpx.AsyncClient | None = None) -> Any:
        if function not in READ_FUNCTIONS:
            raise MoodleError(f"{function} is not one of the read functions Clara may call on Moodle.")
        # the fixed fields come last: a parameter named wsfunction must not replace the function that was checked above
        data = {**encode(params or {}), "wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json"}
        try:
            if http is None:
                async with self._open() as own:
                    response = await own.post(f"{self.base}{ENDPOINT}", data=data)
            else:
                response = await http.post(f"{self.base}{ENDPOINT}", data=data)
        except httpx.HTTPError as error:
            raise MoodleError(f"Moodle could not be reached: {error}") from None
        if response.status_code >= 400:
            raise MoodleError(f"Moodle answered HTTP {response.status_code}.")
        try:
            found = response.json()
        except (ValueError, RecursionError):  # not JSON, or nested so deep that the parser gives up
            raise MoodleError("Moodle did not answer with data (the site may be down for maintenance).") from None
        self._check(found)
        return found

    @staticmethod
    def _check(found: Any) -> None:
        """Moodle answers an error with HTTP 200 and an object that has `exception` (or `error`) in it."""
        if not isinstance(found, dict) or not ("exception" in found or "errorcode" in found):
            return
        code = str(found.get("errorcode") or "")
        if code == "invalidtoken":
            raise MoodleError(
                "Moodle no longer accepts the token (it expired or was deleted): sign in again on the Connections "
                "page, or with clara moodle connect.",
                True,
            )
        raise MoodleError(f"Moodle refused: {line(found.get('message') or code or 'an error', 200)}")

    async def whoami(self) -> tuple[int, str]:
        """The user id and the name behind the token. The private access key of the answer is not read."""
        found = await self.call("core_webservice_get_site_info")
        userid = number(found.get("userid")) if isinstance(found, dict) else 0
        if not userid:
            raise MoodleError("Moodle did not say whose token this is.")
        name = line(found.get("fullname") or found.get("username") or "", 80)
        self._userid = userid
        return userid, name

    async def userid(self) -> int:
        return self._userid or (await self.whoami())[0]

    # ------------------------------------------------------------------
    # Courses
    # ------------------------------------------------------------------
    @staticmethod
    def _course(row: dict) -> Course:
        progress = row.get("progress")
        # most courses track no progress and say 0: only a course that tracks it shows one
        tracked = row.get("hasprogress")
        if tracked is None:  # the list of every course has no such field: its progress is null when nothing is tracked
            tracked = isinstance(progress, int | float) and not isinstance(progress, bool)
        return Course(
            number(row.get("id")),
            moodle_text(row.get("fullname") or row.get("displayname"), 200),
            moodle_text(row.get("shortname"), 80),
            seconds(row.get("startdate")),
            seconds(row.get("enddate")),
            float(progress) if tracked is True and isinstance(progress, int | float) else None,
            row.get("hidden") is True,
        )

    @classmethod
    def _courses(cls, raw: list) -> Noted:
        found = Noted(cls._course(row) for row in raw if isinstance(row, dict))
        if len(found) < len(raw):
            found.notes.append(
                f"{len(raw) - len(found)} of the courses Moodle sent could not be read and are left out."
            )
        return found

    async def courses(self, classification: str = "inprogress") -> Noted:
        """The courses of the owner: `inprogress`, `past`, `future`, or `all` (every year, the hidden ones too)."""
        if classification == "all":
            rows = await self.call("core_enrol_get_users_courses", {"userid": await self.userid()})
            return self._courses(records(rows, "your courses"))
        found = Noted()
        offset = 0
        async with self._open() as http:
            for _ in range(MAX_COURSE_PAGES):
                page = await self.call(
                    "core_course_get_enrolled_courses_by_timeline_classification",
                    {"classification": classification, "limit": PAGE, "offset": offset, "sort": "fullname"},
                    http,
                )
                raw = records(page.get("courses") if isinstance(page, dict) else None, "your courses")
                batch = self._courses(raw)
                found += batch
                found.notes += batch.notes
                if len(raw) < PAGE:
                    break
                following = number(page.get("nextoffset"))
                offset = following if following > offset else offset + PAGE
            else:
                found.notes.append(
                    f"Only the first {MAX_COURSE_PAGES * PAGE} courses Moodle sent were read: courses may be missing."
                )
        return found

    async def enrolled(self, max_age: float = ENROLLED_FOR) -> list[Course]:
        """Every course of the owner (`courses("all")`), kept for `max_age` seconds: a tool that finds a course by its
        name or its id would otherwise ask the site for the whole list each time it runs."""
        async with self._enrolling:  # parallel lookups ask once
            if self._enrolled and time.monotonic() - self._enrolled[0] < max_age:
                return list(self._enrolled[1])
            found = await self.courses("all")
            self._enrolled = (time.monotonic(), found)
            return list(found)

    # ------------------------------------------------------------------
    # Dates
    # ------------------------------------------------------------------
    async def events(self, start: datetime, end: datetime) -> Noted:
        """What is to do between two moments (the Timeline of Moodle): assignments to hand in, activities to complete, and the
        close of a quiz (the Timeline carries it: `quizzes` adds only the opening)."""
        found = Noted()
        after = 0
        async with self._open() as http:
            for _ in range(MAX_EVENT_PAGES):
                params = {"timesortfrom": int(start.timestamp()), "timesortto": int(end.timestamp()), "limitnum": PAGE}
                if after:
                    params["aftereventid"] = after
                page = await self.call("core_calendar_get_action_events_by_timesort", params, http)
                raw = records(page.get("events") if isinstance(page, dict) else None, "your calendar")
                rows = [row for row in raw if isinstance(row, dict)]
                events, dropped = self._events(rows)
                found += events
                if dropped + len(raw) - len(rows):
                    found.notes.append(
                        f"{dropped + len(raw) - len(rows)} Moodle entries had no usable date or shape and are left out."
                    )
                if len(raw) < PAGE:
                    break
                after = number(page.get("lastid") or (rows[-1].get("id") if rows else 0))
                if not after:
                    break
            else:
                found.notes.append(
                    f"Only the first {MAX_EVENT_PAGES * PAGE} Moodle entries of the period were read: some may be missing."
                )
        return found

    @staticmethod
    def _events(rows: list[dict]) -> tuple[list[Event], int]:
        """The events of a page, and how many rows had no date a calendar can hold."""
        found: list[Event] = []
        dropped = 0
        for row in rows:
            stamp = seconds(row.get("timesort") or row.get("timestart"))
            if not stamp:
                dropped += 1
                continue
            course = row.get("course") if isinstance(row.get("course"), dict) else {}
            kind = " ".join(part for part in (line(row.get("modulename"), 30), line(row.get("eventtype"), 30)) if part)
            found.append(
                Event(
                    when(stamp),
                    None,
                    "moodle",
                    kind or "event",
                    moodle_text(row.get("name"), 200),
                    (moodle_text(course.get("fullname") or course.get("shortname"), 120),) if course else (),
                    ("overdue",) if row.get("overdue") else (),
                )
            )
        return found, dropped

    async def quizzes(self, courses: list[Course], start: datetime, end: datetime) -> Noted:
        """When the quizzes of these courses open. Only that: the Timeline (`events`) already carries the close of a quiz, and
        adding it from here showed it twice."""
        found = Noted()
        if not courses:
            return found
        names = {course.id: course.fullname for course in courses}
        page = await self.call("mod_quiz_get_quizzes_by_courses", {"courseids": [course.id for course in courses]})
        raw = records(page.get("quizzes") if isinstance(page, dict) else None, "your quizzes")
        for quiz in raw:
            if not isinstance(quiz, dict):
                continue
            where = (names.get(number(quiz.get("course")), ""),)
            limit = number(quiz.get("timelimit"))
            more = (f"time limit {limit // 60} min",) if limit else ()
            stamp = seconds(quiz.get("timeopen"))
            if stamp and start <= when(stamp) < end:
                found.append(
                    Event(
                        when(stamp), None, "moodle", "quiz opens", moodle_text(quiz.get("name"), 200), (*where, *more)
                    )
                )
        if sum(isinstance(quiz, dict) for quiz in raw) < len(raw):
            found.notes.append("Some quizzes Moodle sent could not be read and are left out.")
        return found

    # ------------------------------------------------------------------
    # Contents and grades
    # ------------------------------------------------------------------
    async def contents(self, course_id: int) -> Noted:
        rows = records(await self.call("core_course_get_contents", {"courseid": course_id}), "this course")
        sections = Noted()
        dropped = 0
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                dropped += 1
                continue
            modules: list[Module] = []
            for module in row.get("modules") or []:
                if not isinstance(module, dict):
                    dropped += 1
                    continue
                contents = module.get("contents")
                contents = contents if isinstance(contents, list) else []
                items = tuple(
                    Item(
                        place,
                        line(item.get("type"), 10),
                        line(item.get("filename"), 200),
                        number(item.get("filesize")),
                        seconds(item.get("timemodified")),
                        str(item.get("fileurl") or ""),
                    )
                    for place, item in enumerate(contents, 1)
                    if isinstance(item, dict)
                )
                dropped += len(contents) - len(items)
                modules.append(
                    Module(
                        number(module.get("id")),
                        line(module.get("modname"), 30),
                        moodle_text(module.get("name"), 200),
                        module.get("uservisible") is not False,
                        items,
                    )
                )
            sections.append(
                Section(number(row.get("section", position)), moodle_text(row.get("name"), 200), tuple(modules))
            )
        if dropped:
            sections.notes.append(f"{dropped} entries of this course could not be read and are left out.")
        return sections

    async def grades(self, course_id: int) -> Noted:
        page = await self.call(
            "gradereport_user_get_grade_items", {"courseid": course_id, "userid": await self.userid()}
        )
        reports = records(page.get("usergrades") if isinstance(page, dict) else None, "the grades of this course")
        raw = [
            item
            for report in reports
            if isinstance(report, dict)
            for item in (report.get("gradeitems") if isinstance(report.get("gradeitems"), list) else [])
        ]
        items = [item for item in raw if isinstance(item, dict)]
        found = Noted(
            GradeItem(
                moodle_text(item.get("itemname"), 200),
                line(item.get("itemtype"), 20),
                line(item.get("itemmodule"), 30),
                moodle_text(item.get("gradeformatted"), 40),
                moodle_text(item.get("rangeformatted"), 40),
                moodle_text(item.get("percentageformatted"), 20),
                # one line: a teacher's text adds none
                line(html_to_text(languages(str(item.get("feedback") or "")[:MAX_RAW_TEXT])), 300),
                seconds(item.get("gradedategraded")),
            )
            for item in items
        )
        if len(items) < len(raw):
            found.notes.append(f"{len(raw) - len(items)} grade items Moodle sent could not be read and are left out.")
        return found

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def file_url(self, url: str) -> str:
        """The download address of a document, with the token, for a file on this site only: its scheme, host and port,
        and a path under the documents' folder that does not climb out of it (the HTTP client removes `..` before it
        sends, so a link written `…/pluginfile.php/../../user/x` would reach another page of the site with the token).
        A fragment is dropped: the token would end up inside it and never be sent."""
        parts = urlsplit(str(url or "").split("#", 1)[0])
        site = urlsplit(self.base)
        try:
            same_port = parts.port == site.port
        except ValueError:
            same_port = False
        climbs = {"..", "."} & set(unquote(parts.path).replace("\\", "/").split("/"))
        if (
            parts.scheme != site.scheme
            or parts.hostname != site.hostname
            or not same_port
            or not parts.path.startswith(FILES)
            or climbs
        ):
            raise MoodleError("That file is not on Moodle's own address: it is not downloaded.")
        return parts.geturl() + ("&" if parts.query else "?") + urlencode({"token": self.token})

    async def download(self, url: str, limit: int = MAX_DOWNLOAD) -> bytes:
        """A document's bytes. The size in the listing is not trusted: the bytes are counted as they come. The whole
        download has a deadline. A redirect is not followed (the token would go where it points). Only a small JSON answer
        is read as Moodle talking (an error); a document whose bytes look like one is still a document."""
        address = self.file_url(url)
        chunks: list[bytes] = []
        total = 0
        try:
            async with (
                asyncio.timeout(DOWNLOAD_SECONDS),
                httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http,
                http.stream("GET", address) as response,
            ):
                if response.status_code != 200:
                    raise MoodleError(f"Moodle answered HTTP {response.status_code} for that file.")
                if number(response.headers.get("content-length")) > limit:
                    raise MoodleError(f"That file is bigger than {human(limit)}: it is not downloaded.")
                async for chunk in response.aiter_bytes(CHUNK):
                    total += len(chunk)
                    if total > limit:
                        raise MoodleError(f"That file is bigger than {human(limit)}: it is not downloaded.")
                    chunks.append(chunk)
                kind = response.headers.get("content-type", "")
        except TimeoutError:
            raise MoodleError(
                f"The download took longer than {DOWNLOAD_SECONDS:.0f} s: it was stopped and nothing was saved."
            ) from None
        except httpx.HTTPError as error:
            raise MoodleError(f"Moodle could not be reached: {error}") from None
        data = b"".join(chunks)
        if "json" in kind and len(data) <= MAX_ERROR_ANSWER and data[:1] == b"{":  # an error, answered as data
            try:
                found = json.loads(data)
            except (ValueError, RecursionError):  # a JSON document (or one nested too deep to read), not an error
                found = None
            self._check(found)
        return data


async def sign_in(link: str) -> tuple[str, str]:
    """What the owner pasted, proved: the token in it and the name Moodle gives it, once Moodle accepts it."""
    token = token_from_link(link)
    return token, (await Moodle(token).whoami())[1]
