"""Gmail, through its REST API, with two narrow scopes and a token of its own (it is not the Drive token):

    gmail.readonly   search and read messages and attachments
    gmail.compose    save drafts, send messages and drafts

Not asked, so the token cannot do them whatever Clara or a prompt injection tries: `gmail.modify` (label, archive, mark
read, trash), `gmail.settings.*` (filters, forwarding) and the full `mail.google.com` scope.

The sign-in is the Drive one (OAuth, PKCE, bound to the browser session, `GoogleAuth` of drive.py) with other scopes. A
Google app whose consent screen is in "Testing" ends its tokens after 7 days; these scopes are "restricted", so an app in
production that Google has not verified shows a warning at consent (fine for your own account; see docs/OPERATIONS.md).
"""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
import time
from urllib.parse import quote, urlencode

import httpx

from .drive import AUTH_URL, DriveError, GoogleAuth
from .mail import (
    LISTING_MAX,
    MAX_DOWNLOAD,
    NEW_BATCH,
    Attachment,
    MailClient,
    MailError,
    Message,
    Outgoing,
    Search,
    Sent,
    Summary,
    build_mime,
    clean_line,
    clean_text,
    decode_header_value,
    html_to_text,
    problem_detail,
    reply_subject,
    threading_headers,
)

log = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose openid email"
LABELS = {"inbox": "INBOX", "sent": "SENT", "drafts": "DRAFT", "trash": "TRASH", "spam": "SPAM"}
METADATA = ("From", "To", "Subject", "Date")
PARALLEL = 5
MAX_PART_DEPTH = 20
ATTACHMENT_LIMIT = 7_000_000  # raw bytes in all: the message travels as base64 in one JSON request


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """Like Drive's, with the mail scopes. `include_granted_scopes` is left out on purpose: this token carries mail
    scopes only, and the Drive token carries Drive's only."""
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _charset(part: dict) -> str:
    for header in part.get("headers", []) or []:
        if str(header.get("name", "")).lower() == "content-type":
            found = re.search(r"charset=\"?([A-Za-z0-9_.:-]+)", str(header.get("value", "")))
            if found:
                return found.group(1)
    return "utf-8"


def _decode(part: dict) -> str:
    data = (part.get("body") or {}).get("data") or ""
    raw = _b64decode(data)
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _walk(part: dict, depth: int = 0):
    yield part
    if depth < MAX_PART_DEPTH:
        for child in part.get("parts", []) or []:
            yield from _walk(child, depth + 1)


def _headers(raw: dict) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): decode_header_value(str(h.get("value", "")))
        for h in (raw.get("payload") or {}).get("headers", []) or []
    }


def summary_of(raw: dict) -> Summary:
    headers = _headers(raw)
    mime = str((raw.get("payload") or {}).get("mimeType", ""))
    return Summary(
        id=str(raw["id"]),
        thread=str(raw.get("threadId", "")),
        subject=clean_line(headers.get("subject", "")) or "(no subject)",
        sender=clean_line(headers.get("from", "")),
        received_ms=int(raw.get("internalDate") or 0),
        snippet=clean_line(html.unescape(str(raw.get("snippet", ""))), 160),
        unread="UNREAD" in (raw.get("labelIds") or []),
        # `metadata` carries no parts: a "multipart/mixed" message is, nearly always, one with attachments
        attachments=mime.startswith("multipart/mixed"),
    )


def attachments_of(raw: dict) -> list[Attachment]:
    found = []
    for part in _walk(raw.get("payload") or {}):
        if part.get("filename") and part.get("partId"):
            found.append(
                Attachment(
                    str(part["partId"]),
                    clean_line(part["filename"], 120),
                    str(part.get("mimeType", "")),
                    int((part.get("body") or {}).get("size") or 0),
                )
            )
    return found


def message_of(raw: dict) -> Message:
    headers = _headers(raw)
    plain: list[str] = []
    markup: list[str] = []
    for part in _walk(raw.get("payload") or {}):
        if part.get("filename"):
            continue
        kind = str(part.get("mimeType", ""))
        if kind == "text/plain" and (part.get("body") or {}).get("data"):
            plain.append(_decode(part))
        elif kind == "text/html" and (part.get("body") or {}).get("data"):
            markup.append(_decode(part))
    body = clean_text("\n\n".join(plain)) if plain else html_to_text("\n".join(markup))
    return Message(
        summary_of(raw),
        to=clean_line(headers.get("to", ""), 500),
        cc=clean_line(headers.get("cc", ""), 500),
        reply_to=clean_line(headers.get("reply-to", "")),
        body=body,
        attachments=tuple(attachments_of(raw)),
    )


class Gmail(MailClient):
    provider = "gmail"
    label = "Gmail"
    attachment_limit = ATTACHMENT_LIMIT
    file_limit = ATTACHMENT_LIMIT

    def __init__(self, auth: GoogleAuth, refresh_token: str, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(transport)
        self.auth = auth
        self.refresh_token = refresh_token

    async def access_token(self) -> str:
        if self._access and self._access[1] > time.monotonic() + 60:
            return self._access[0]
        try:
            found = await self.auth.token({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        except DriveError as error:  # GoogleAuth is shared with Drive and speaks its error type
            raise MailError(str(error), error.reconnect) from None
        self._access = (found["access_token"], time.monotonic() + int(found.get("expires_in", 3000)))
        return self._access[0]

    def problem(self, response: httpx.Response) -> MailError:
        status = response.status_code
        code, detail = problem_detail(response)
        if status == 401:
            return MailError("Gmail refused the connection: connect it again.", True)
        if status == 403:
            if code in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used in project" in detail:
                return MailError("The Gmail API is not turned on in your Google Cloud project.")
            if code in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
                return MailError(
                    "Gmail says the permission is missing: connect Gmail again and allow reading and writing drafts.",
                    True,
                )
            if code in ("rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED"):
                return MailError("Google says too many requests for now: try again later.")
        if status == 404:
            return MailError("Gmail cannot find that message.")
        if status == 429:
            return MailError("Google says too many requests for now: try again later.")
        if status == 400:
            return MailError(f"Gmail did not accept the request: {detail or code or 'bad request'}.")
        if status >= 500:
            return MailError(f"Gmail is not available right now (HTTP {status}).")
        return MailError(f"Gmail answered HTTP {status}: {detail or code or 'error'}")

    # ------------------------------------------------------------------
    async def whoami(self) -> str:
        return str((await self.json("GET", f"{API}/profile")).get("emailAddress") or "")

    async def summaries(self, ids: list[str]) -> list[Summary]:
        """The summaries of these messages, in this order (one that vanished meanwhile is left out)."""
        gate = asyncio.Semaphore(PARALLEL)

        async def one(http: httpx.AsyncClient, message: str) -> Summary | None:
            async with gate:
                try:
                    raw = await self.json(
                        "GET",
                        f"{API}/messages/{quote(message, safe='')}",
                        http=http,
                        params={"format": "metadata", "metadataHeaders": list(METADATA)},
                    )
                except MailError as error:
                    if "cannot find" in str(error):
                        return None
                    raise
            return summary_of(raw)

        async with self.session() as http:
            found = await asyncio.gather(*(one(http, message) for message in ids))
        return [item for item in found if item is not None]

    async def search(self, find: Search) -> list[Summary]:
        terms = [find.query] if find.query else []
        for key, value in (("from", find.sender), ("to", find.to), ("subject", find.subject)):
            if value:
                terms.append(f'{key}:"{value.replace(chr(34), "")}"')
        if find.unread:
            terms.append("is:unread")
        if find.has_attachment:
            terms.append("has:attachment")
        if find.after:
            terms.append("after:" + find.after.replace("-", "/"))
        if find.before:
            terms.append("before:" + find.before.replace("-", "/"))
        params: dict = {"maxResults": find.limit}
        if terms:
            params["q"] = " ".join(terms)
        label = LABELS.get(find.folder)
        if label:
            params["labelIds"] = [label]
        if find.folder in ("trash", "spam"):
            params["includeSpamTrash"] = "true"
        listing = await self.json("GET", f"{API}/messages", params=params)
        return await self.summaries([str(item["id"]) for item in listing.get("messages", []) or []])

    async def raw(self, message: str) -> dict:
        return await self.json("GET", f"{API}/messages/{quote(message, safe='')}", params={"format": "full"})

    async def read(self, message: str) -> Message:
        return message_of(await self.raw(message))

    async def attachment(self, message: str, attachment: str) -> tuple[Attachment, bytes]:
        raw = await self.raw(message)
        for part in _walk(raw.get("payload") or {}):
            if str(part.get("partId", "")) != attachment or not part.get("filename"):
                continue
            body = part.get("body") or {}
            found = Attachment(
                attachment, clean_line(part["filename"], 120), str(part.get("mimeType", "")), int(body.get("size") or 0)
            )
            if found.size > MAX_DOWNLOAD:
                raise MailError(f"{found.name} is too big to fetch.")
            if body.get("attachmentId"):
                # the id of an attachment is not stable between calls, so it is read from the message just fetched
                data = await self.json(
                    "GET",
                    f"{API}/messages/{quote(message, safe='')}/attachments/{quote(str(body['attachmentId']), safe='')}",
                )
                return found, _b64decode(str(data.get("data", "")))
            return found, _b64decode(str(body.get("data", "")))
        raise MailError("That message has no such attachment (list them with mail_read).")

    # ------------------------------------------------------------------
    async def _prepare(self, mail: Outgoing, sender: str) -> tuple[bytes, str]:
        """The message as bytes, and the thread it belongs to ('' for a new one)."""
        extra: dict[str, str] = {}
        thread = ""
        if mail.reply_to:
            original = await self.json(
                "GET",
                f"{API}/messages/{quote(mail.reply_to, safe='')}",
                params={"format": "metadata", "metadataHeaders": ["Message-ID", "References", "Subject"]},
            )
            headers = _headers(original)
            thread = str(original.get("threadId", ""))
            extra = threading_headers(headers.get("message-id", ""), headers.get("references", ""))
            if not mail.subject:
                mail = Outgoing(
                    mail.to,
                    mail.cc,
                    mail.bcc,
                    reply_subject(headers.get("subject", "")),
                    mail.body,
                    mail.reply_to,
                    mail.files,
                )
        try:
            return build_mime(sender, mail, extra), thread
        except (ValueError, TypeError) as error:
            raise MailError(f"The message cannot be built: {error}") from None

    async def draft(self, mail: Outgoing, sender: str) -> Sent:
        data, thread = await self._prepare(mail, sender)
        message: dict = {"raw": base64.urlsafe_b64encode(data).decode()}
        if thread:
            message["threadId"] = thread
        made = await self.json("POST", f"{API}/drafts", json={"message": message})
        return Sent(str(made.get("id", "")), f"https://mail.google.com/mail/?authuser={quote(sender, safe='@')}#drafts")

    async def send(self, mail: Outgoing, sender: str) -> Sent:
        data, thread = await self._prepare(mail, sender)
        body: dict = {"raw": base64.urlsafe_b64encode(data).decode()}
        if thread:
            body["threadId"] = thread
        made = await self.json("POST", f"{API}/messages/send", json=body)
        return Sent(str(made.get("id", "")))

    async def new_since(self, received_ms: int, limit: int = NEW_BATCH) -> list[Summary]:
        """The oldest `limit` inbox messages received since `received_ms`. Gmail lists newest first, so every id since then is
        listed (ids only: 500 a page, cheap), and the details are fetched for the oldest `limit` of them alone. A backlog
        thus comes in order across polls, and nothing is skipped however many mails arrived while Clara was off."""
        seconds = max(
            0, received_ms // 1000 - 1
        )  # `after:` counts whole seconds: look a little back, the caller dedupes
        ids: list[str] = []
        token = ""
        while len(ids) < LISTING_MAX:
            params: dict = {"labelIds": ["INBOX"], "q": f"after:{seconds}", "maxResults": 500}
            if token:
                params["pageToken"] = token
            page = await self.json("GET", f"{API}/messages", params=params)
            ids += [str(item["id"]) for item in page.get("messages", []) or []]
            token = str(page.get("nextPageToken") or "")
            if not token:
                break
        if token:
            log.warning("gmail: more than %d messages since the cursor: the oldest ones may be missed", LISTING_MAX)
        found = await self.summaries(ids[-limit:])
        return sorted(found, key=lambda item: item.received_ms)
