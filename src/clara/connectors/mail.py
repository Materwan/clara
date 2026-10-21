"""Mail, in common: what a message is, how untrusted text is cleaned, how a message to send is checked and built, and the
HTTP client a mailbox connector builds on (`gmail.py`).

Clara reaches a mailbox through the provider's own API with an OAuth token you gave on the Connections page; she never
sees a password. What she may do, in the order of risk:

    read      search, read a message, read an attachment's text, save an attachment in the workspace
    draft     save a draft in the mailbox (nothing is sent)
    send      send a message: the rule gate asks you first, with the whole text and every address in front of you

Everything that comes out of a mailbox is written by someone else. `clean_text` removes what is invisible to you but
not to a model (zero-width and direction-control characters, Unicode "tag" characters, text hidden with CSS), and the
tools hand the text over as data, never as instructions.
"""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any

import httpx

PROVIDERS = ("gmail",)
LABELS = {"gmail": "Gmail"}
FOLDERS = ("inbox", "sent", "drafts", "trash", "spam", "all")
SEARCH_MAX = 25
MAX_RECIPIENTS = 20
MAX_SUBJECT = 300
MAX_BODY = 6_000  # characters of a message to send: the approval request shows all of it, so it must fit there
MAX_FILES = 5
MAX_DOWNLOAD = 25_000_000  # an attachment bigger than this is not fetched
TIMEOUT = httpx.Timeout(20.0, read=90.0)
SHOWN_FIELD = 200  # a subject or a sender in a list, cut here
NEW_BATCH = 100  # messages `new_since` returns at once
LISTING_MAX = 5_000  # ids looked at to find the oldest new messages (Gmail lists newest first); more is logged, not lost silently


class MailError(ValueError):
    """A problem to tell the model (and you). `reconnect` means the token no longer works."""

    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


# ----------------------------------------------------------------------
# What a mailbox gives back
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Summary:
    id: str
    thread: str
    subject: str
    sender: str
    received_ms: int
    snippet: str = ""
    unread: bool = False
    attachments: bool = False

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.received_ms / 1000, UTC).strftime("%Y-%m-%d %H:%MZ") if self.received_ms else "?"


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    mime: str
    size: int


@dataclass(frozen=True)
class Message:
    summary: Summary
    to: str = ""
    cc: str = ""
    reply_to: str = ""
    body: str = ""
    attachments: tuple[Attachment, ...] = field(default=())


@dataclass(frozen=True)
class Search:
    query: str = ""
    sender: str = ""
    to: str = ""
    subject: str = ""
    folder: str = "inbox"
    unread: bool = False
    has_attachment: bool = False
    after: str = ""  # YYYY-MM-DD
    before: str = ""
    limit: int = 10


@dataclass(frozen=True)
class File:
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class Outgoing:
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str = ""
    body: str = ""
    reply_to: str = ""  # the id of the message answered ('' for a new message)
    files: tuple[File, ...] = ()

    @property
    def everyone(self) -> tuple[str, ...]:
        found: list[str] = []
        for address in (*self.to, *self.cc, *self.bcc):
            if address not in found:
                found.append(address)
        return tuple(found)


@dataclass(frozen=True)
class Sent:
    id: str
    link: str = ""


# ----------------------------------------------------------------------
# Untrusted text
# ----------------------------------------------------------------------
# Invisible to a reader, fully visible to a model: zero-width characters, direction overrides, the soft hyphen, the byte
# order mark, and the Unicode "tag" block (which can spell ASCII text that no screen shows). ZWNJ and ZWJ are kept: scripts
# and emoji sequences need them.
_INVISIBLE = re.compile("[­​‎‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"\n{3,}")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em|%)?\s*(?:;|$)|"
                           r"mso-hide\s*:\s*all|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)", re.IGNORECASE)


def clean_text(text: str) -> str:
    """Text from a message, without what hides in it. Line breaks are kept; runs of blank lines are shortened."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", _INVISIBLE.sub("", text))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return _BLANKS.sub("\n\n", text).strip()


def clean_line(text: str, limit: int = SHOWN_FIELD) -> str:
    """One line (a subject, a sender), cleaned and cut."""
    line = " ".join(clean_text(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def decode_header_value(value: str) -> str:
    """A header with its RFC 2047 encoded words (=?UTF-8?B?…?=) read."""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError):
        return value


_BLOCKS = frozenset({"p", "div", "tr", "li", "ul", "ol", "table", "blockquote", "section", "article", "header", "footer",
                     "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr"})
_SKIPPED = frozenset({"script", "style", "head", "title", "template", "noscript", "svg", "object", "embed"})
_VOID = frozenset({"br", "img", "input", "hr", "meta", "link", "area", "base", "col", "source", "wbr"})


class _HtmlText(HTMLParser):
    """HTML as plain text: blocks become lines, links keep their address, and anything hidden by CSS is dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.hidden: list[str] = []  # the tags that opened a hidden subtree (a stack of names)
        self.link: str | None = None
        self.link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self.skip or self.hidden:
            if tag in _SKIPPED and self.skip:
                self.skip += 1
            elif self.hidden and tag == self.hidden[-1] and tag not in _VOID:
                self.hidden.append(tag)
            return
        if tag in _SKIPPED:
            self.skip = 1
            return
        if tag not in _VOID and (_HIDDEN_STYLE.search(values.get("style") or "") or "hidden" in values):
            self.hidden.append(tag)
            return
        if tag == "br" or tag in _BLOCKS:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            href = (values.get("href") or "").strip()
            self.link = href if href.lower().startswith(("http://", "https://")) else None
            self.link_text = []

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in _SKIPPED:
                self.skip -= 1
            return
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in _BLOCKS:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" ")
        if tag == "a" and self.link:
            shown = "".join(self.link_text).strip()
            if self.link not in shown:
                self.out.append(f" ({self.link[:200]})")
            self.link = None

    def handle_data(self, data: str) -> None:
        if self.skip or self.hidden:
            return
        text = re.sub(r"\s+", " ", data)
        self.out.append(text)
        if self.link is not None:
            self.link_text.append(text)


def html_to_text(markup: str) -> str:
    parser = _HtmlText()
    with contextlib.suppress(Exception):  # broken markup gives what was read so far
        parser.feed(markup)
        parser.close()
    text = "".join(parser.out)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return clean_text(text)


# ----------------------------------------------------------------------
# What may be sent, and how
# ----------------------------------------------------------------------
_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_ID = re.compile(r"^[A-Za-z0-9_=+/-]{1,600}$")


def parse_address(text: Any) -> str:
    """One plain email address, lower case (`Name <a@b.c>` is read as `a@b.c`). Anything else is refused: an address is
    what a rule matches and what you read in the approval, so it must be exactly one."""
    value = str(text or "").strip()
    if "<" in value and value.endswith(">"):
        value = value[value.rindex("<") + 1:-1].strip()
    if len(value) > 254 or not _ADDRESS.match(value) or ".." in value or value.startswith(".") or "@." in value:
        raise MailError(f"{str(text)[:80]!r} is not an email address (give plain addresses such as name@example.com).")
    return value.lower()


def message_id(value: Any, name: str = "message") -> str:
    found = str(value or "").strip()
    if not _ID.match(found):
        raise MailError(f"{name} must be a message id, as listed by mail_search.")
    return found


def parse_day(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise MailError(f"{name} must be a date written YYYY-MM-DD.") from None


def safe_filename(name: Any, fallback: str = "attachment") -> str:
    """A file name without folders, control characters or characters a system dislikes."""
    text = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/").rsplit("/", 1)[-1]
    text = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", text).strip(" .")
    return text[:120] or fallback


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def threading_headers(message_id_header: str, references: str) -> dict[str, str]:
    """In-Reply-To and References from the answered message's headers: only well-formed <ids> are kept."""
    own = re.findall(r"<[^<>\s]{1,300}>", message_id_header or "")
    older = re.findall(r"<[^<>\s]{1,300}>", references or "")[-20:]
    if not own:
        return {}
    return {"In-Reply-To": own[0], "References": " ".join([*older, own[0]])}


def build_mime(sender: str, mail: Outgoing, extra: dict[str, str] | None = None) -> bytes:
    """The message as RFC 5322 bytes. The standard library refuses a line break inside a header, so a subject or an
    address cannot add a header of its own."""
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = ", ".join(mail.to)
    if mail.cc:
        message["Cc"] = ", ".join(mail.cc)
    if mail.bcc:
        message["Bcc"] = ", ".join(mail.bcc)
    message["Subject"] = mail.subject
    for key, value in (extra or {}).items():
        message[key] = value
    message.set_content(mail.body)
    for file in mail.files:
        maintype, _, subtype = (file.mime or "application/octet-stream").partition("/")
        message.add_attachment(file.data, maintype=maintype or "application", subtype=subtype or "octet-stream",
                               filename=file.name)
    return message.as_bytes()


def reply_subject(original: str) -> str:
    original = clean_line(original, MAX_SUBJECT) or "(no subject)"
    return original if re.match(r"(?i)^(re|réf|aw|sv)\s*:", original) else f"Re: {original}"


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024  # type: ignore[assignment]
    return f"{size} B"


# ----------------------------------------------------------------------
# The client both mailboxes share
# ----------------------------------------------------------------------
class MailClient:
    """HTTP with a bearer token: a stale one is refreshed once, a throttled call waits and tries again, any other failure is
    turned into text by the mailbox's `problem`. Subclasses fill in the calls (see gmail.py):

        whoami()                 the mailbox's own address
        search(Search)           summaries, newest first
        read(id)                 one Message, with the list of its attachments
        attachment(id, att)      (Attachment, bytes)
        draft(Outgoing, sender)  saved in the mailbox, not sent
        send(Outgoing, sender)   sent
        new_since(ms, limit)     the OLDEST `limit` inbox messages received since then, oldest first: a backlog is
                                 worked through from its start, never from its end (the caller moves its cursor on)
    """

    provider = ""
    label = ""
    attachment_limit = 0  # bytes of attachments one message to send may carry, in all
    file_limit = 0  # bytes of one attachment (0: the same as the total)

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._access: tuple[str, float] | None = None

    async def access_token(self) -> str:
        raise NotImplementedError

    def problem(self, response: httpx.Response) -> MailError:
        raise NotImplementedError

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
            yield http

    async def request(self, method: str, url: str, *, http: httpx.AsyncClient | None = None, **options: Any) -> httpx.Response:
        extra = options.pop("headers", {})
        for attempt in (1, 2, 3):
            access = await self.access_token()
            try:
                if http is not None:
                    response = await http.request(method, url, headers={**extra, "Authorization": f"Bearer {access}"}, **options)
                else:
                    async with self.session() as once:
                        response = await once.request(method, url, headers={**extra, "Authorization": f"Bearer {access}"},
                                                      **options)
            except httpx.HTTPError as error:
                raise MailError(f"{self.label} could not be reached: {error}") from None
            if response.status_code == 401 and attempt == 1:
                self._access = None
                continue
            if response.status_code in (429, 503) and attempt < 3:
                try:
                    wait = float(response.headers.get("retry-after", "1"))
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(max(0.0, min(wait, 5.0)))
                continue
            if response.is_error:
                raise self.problem(response)
            return response
        raise MailError(f"{self.label} refused the connection: connect it again.", True)

    async def json(self, method: str, url: str, **options: Any) -> Any:
        response = await self.request(method, url, **options)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise MailError(f"{self.label} answered something that is not JSON.") from None

    # The calls below are the contract; the mailbox connector implements them.
    async def whoami(self) -> str:
        raise NotImplementedError

    async def search(self, find: Search) -> list[Summary]:
        raise NotImplementedError

    async def read(self, message: str) -> Message:
        raise NotImplementedError

    async def attachment(self, message: str, attachment: str) -> tuple[Attachment, bytes]:
        raise NotImplementedError

    async def draft(self, mail: Outgoing, sender: str) -> Sent:
        raise NotImplementedError

    async def send(self, mail: Outgoing, sender: str) -> Sent:
        raise NotImplementedError

    async def new_since(self, received_ms: int, limit: int = NEW_BATCH) -> list[Summary]:
        raise NotImplementedError


def problem_detail(response: httpx.Response) -> tuple[str, str]:
    """(code, message) out of a provider's JSON error, whichever shape it has."""
    try:
        found = response.json()
    except ValueError:
        return "", ""
    error = found.get("error", found) if isinstance(found, dict) else {}
    if isinstance(error, str):
        return error, str(found.get("error_description", ""))
    if not isinstance(error, dict):
        return "", ""
    reason = ""
    if isinstance(error.get("errors"), list) and error["errors"] and isinstance(error["errors"][0], dict):
        reason = str(error["errors"][0].get("reason", ""))
    return str(error.get("code") or reason or error.get("status") or ""), str(error.get("message") or "")
