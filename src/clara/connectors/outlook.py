"""Outlook (outlook.com, hotmail, live, and Microsoft 365 work or school mailboxes), through Microsoft Graph.

Permissions asked (delegated, so only your own mailbox):

    Mail.ReadWrite   read, and create drafts. Graph has no draft-only permission: this one could also change or delete
                     mail, which Clara's tools never do (they are the only thing that uses the token)
    Mail.Send        send
    User.Read        which address this is, shown before you attach it
    offline_access   a refresh token, so Clara need not ask you again

The sign-in is OAuth with PKCE for a *web* app registration (a client secret, kept in the secrets store), bound to the
browser session that started it, like Google's. The authority is `CLARA_MICROSOFT_TENANT` (`common`: personal and work
accounts). Microsoft rotates the refresh token at each use: the new one is stored at once, and refreshes never overlap.
Microsoft has no call to revoke one token: disconnecting forgets it here, and the app can be removed at
https://account.microsoft.com/privacy/app-access (personal) or https://myapps.microsoft.com (work or school).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

import httpx

from .mail import (
    MAX_DOWNLOAD,
    TIMEOUT,
    Attachment,
    MailClient,
    MailError,
    Message,
    Outgoing,
    Search,
    Sent,
    Summary,
    clean_line,
    clean_text,
    html_to_text,
    problem_detail,
)

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0/me"
SCOPES = "offline_access openid email profile User.Read Mail.ReadWrite Mail.Send"
FOLDERS = {"inbox": "mailFolders/inbox", "sent": "mailFolders/sentitems", "drafts": "mailFolders/drafts",
           "trash": "mailFolders/deleteditems", "spam": "mailFolders/junkemail", "all": ""}
SELECT = "id,conversationId,subject,from,receivedDateTime,isRead,hasAttachments,bodyPreview"
ATTACHMENT_LIMIT = 10_000_000
FILE_LIMIT = 2_200_000  # one attachment travels as base64 in one request of 3 MB at most
TEXT_BODY = {"Prefer": 'outlook.body-content-type="text"'}
RECONNECT = frozenset({"invalid_grant", "interaction_required", "consent_required", "login_required"})
_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_time(value: str) -> int:
    """Graph's `2026-10-20T14:03:12Z` (sometimes with seven fraction digits) as milliseconds since the epoch."""
    text = _FRACTION.sub(r"\1", str(value or "").strip()).replace("Z", "+00:00")
    try:
        found = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if found.tzinfo is None:
        found = found.replace(tzinfo=UTC)
    return int(found.timestamp() * 1000)


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:300] if (text or "").strip() else ""


class MicrosoftAuth:
    def __init__(self, client_id: str, client_secret: str, tenant: str = "common",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant = tenant
        self._transport = transport

    @property
    def authority(self) -> str:
        return f"{LOGIN}/{self.tenant}/oauth2/v2.0"

    def authorize_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        return f"{self.authority}/authorize?" + urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": redirect_uri, "response_mode": "query",
            "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "prompt": "select_account",
        })

    async def token(self, data: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.post(f"{self.authority}/token", data={**data, "client_id": self.client_id,
                                                                            "client_secret": self.client_secret})
        except httpx.HTTPError as error:
            raise MailError(f"Microsoft could not be reached: {error}") from None
        try:
            found = response.json()
        except ValueError:
            found = {}
        if response.is_error:
            code = str(found.get("error", ""))
            detail = _first_line(str(found.get("error_description", "")))
            if code in RECONNECT:
                raise self._lost(detail)
            if code == "invalid_client":
                raise MailError("Microsoft refused the app's credentials: the client secret is wrong or has expired "
                                f"(renew it in Azure, then update the secret microsoft_client_secret). {detail}")
            raise MailError(f"Microsoft refused: {detail or code or response.status_code}")
        return found

    @staticmethod
    def _lost(detail: str) -> MailError:
        return MailError("Microsoft no longer accepts this connection (access removed, the password changed, or it was "
                         f"unused for 90 days): connect it again. {detail}".strip(), True)

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        return await self.token({"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                 "redirect_uri": redirect_uri, "scope": SCOPES})

    async def refresh(self, refresh_token: str) -> dict:
        return await self.token({"grant_type": "refresh_token", "refresh_token": refresh_token, "scope": SCOPES})


async def check_account(access_token: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """The address of the account an access token is for, after proving the token reaches a mailbox: right after the
    sign-in, before anything is stored, so a missing permission or an account with no mailbox is told at once."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as http:
            who = await http.get(GRAPH, params={"$select": "mail,userPrincipalName"}, headers=headers)
            box = await http.get(f"{GRAPH}/mailFolders/inbox", params={"$select": "id"}, headers=headers)
    except httpx.HTTPError as error:
        raise MailError(f"Microsoft could not be reached: {error}") from None
    if who.is_error:
        raise MailError(f"Microsoft would not say which account this is (HTTP {who.status_code}).")
    if box.is_error:
        _, detail = problem_detail(box)
        raise MailError("Outlook cannot read this account's mailbox (no mailbox on the account, or the mail permission was "
                        f"not granted; a work account may need an administrator's approval). {detail}".strip())
    found = who.json()
    return str(found.get("mail") or found.get("userPrincipalName") or "")


def _people(items: list | None) -> str:
    out = []
    for item in items or []:
        mailbox = (item or {}).get("emailAddress") or {}
        address = str(mailbox.get("address") or "")
        name = str(mailbox.get("name") or "")
        out.append(f"{name} <{address}>" if name and name != address else address)
    return clean_line(", ".join(part for part in out if part), 500)


def summary_of(raw: dict) -> Summary:
    sender = ((raw.get("from") or {}).get("emailAddress")) or {}
    address = str(sender.get("address") or "")
    name = str(sender.get("name") or "")
    return Summary(
        id=str(raw["id"]), thread=str(raw.get("conversationId", "")), subject=clean_line(raw.get("subject") or "") or "(no subject)",
        sender=clean_line(f"{name} <{address}>" if name and name != address else address),
        received_ms=parse_time(raw.get("receivedDateTime", "")), snippet=clean_line(raw.get("bodyPreview") or "", 160),
        unread=raw.get("isRead") is False, attachments=bool(raw.get("hasAttachments")))


def _recipients(addresses: tuple[str, ...]) -> list[dict]:
    return [{"emailAddress": {"address": address}} for address in addresses]


def _reply_html(text: str, original: str) -> str:
    """Our text on top of the quoted message Graph made, inside its <body>."""
    ours = "<div>" + html.escape(text).replace("\n", "<br>") + "</div><br>"
    found = re.search(r"<body[^>]*>", original, re.IGNORECASE)
    return original[:found.end()] + ours + original[found.end():] if found else ours + original


def _odata(value: str) -> str:
    return value.replace("'", "''")


class Outlook(MailClient):
    provider = "outlook"
    label = "Outlook"
    attachment_limit = ATTACHMENT_LIMIT
    file_limit = FILE_LIMIT

    def __init__(self, auth: MicrosoftAuth, refresh_token: str, transport: httpx.AsyncBaseTransport | None = None,
                 on_refresh: Callable[[str], None] | None = None):
        super().__init__(transport)
        self.auth = auth
        self.refresh_token = refresh_token
        self.on_refresh = on_refresh  # told the new refresh token Microsoft gives at each refresh, to store it
        self._lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self._lock:  # two refreshes at once would each rotate the token
            if self._access and self._access[1] > time.monotonic() + 60:
                return self._access[0]
            found = await self.auth.refresh(self.refresh_token)
            renewed = found.get("refresh_token")
            if renewed and renewed != self.refresh_token:
                self.refresh_token = renewed
                if self.on_refresh is not None:
                    self.on_refresh(renewed)
            self._access = (found["access_token"], time.monotonic() + int(found.get("expires_in", 3000)))
            return self._access[0]

    def problem(self, response: httpx.Response) -> MailError:
        status = response.status_code
        code, detail = problem_detail(response)
        if status == 401:
            return MailError("Outlook refused the connection: connect it again.", True)
        if status == 403:
            return MailError("Outlook refused this (access denied). For a work or school account, an administrator may "
                             f"have to allow the app, or the permission was not granted: connect it again. {detail}".strip())
        if status == 404 or code in ("ErrorItemNotFound", "ErrorInvalidIdMalformed"):
            return MailError("Outlook cannot find that message.")
        if status == 429:
            return MailError("Microsoft says too many requests for now: try again later.")
        if status == 400:
            return MailError(f"Outlook did not accept the request: {detail or code or 'bad request'}.")
        if status >= 500:
            return MailError(f"Outlook is not available right now (HTTP {status}).")
        return MailError(f"Outlook answered HTTP {status}: {detail or code or 'error'}")

    # ------------------------------------------------------------------
    async def whoami(self) -> str:
        found = await self.json("GET", GRAPH, params={"$select": "mail,userPrincipalName"})
        return str(found.get("mail") or found.get("userPrincipalName") or "")

    @staticmethod
    def _base(folder: str) -> str:
        path = FOLDERS.get(folder, FOLDERS["inbox"])
        return f"{GRAPH}/{path}/messages" if path else f"{GRAPH}/messages"

    @staticmethod
    def _kept(found: Summary, find: Search) -> bool:
        if find.unread and not found.unread:
            return False
        if find.has_attachment and not found.attachments:
            return False
        if find.after and found.received_ms < _day_ms(find.after):
            return False
        return not (find.before and found.received_ms >= _day_ms(find.before))

    async def search(self, find: Search) -> list[Summary]:
        words = [find.query.replace('"', " ")] if find.query else []
        for key, value in (("from", find.sender), ("to", find.to), ("subject", find.subject)):
            if value:
                words.append(f"{key}:{value.replace(chr(34), ' ')}")
        if words:
            # $search cannot be mixed with $filter or $orderby: the rest is filtered here
            listing = await self.json("GET", self._base(find.folder), params={
                "$search": '"' + " ".join(words) + '"', "$top": 50, "$select": SELECT})
            found = [summary_of(item) for item in listing.get("value", []) or []]
            return [item for item in found if self._kept(item, find)][: find.limit]
        # the property that orders must lead the filter, or Graph says the query is "too complex"
        filters = [f"receivedDateTime ge {find.after or '1900-01-01'}T00:00:00Z"]
        if find.before:
            filters.append(f"receivedDateTime lt {find.before}T00:00:00Z")
        if find.unread:
            filters.append("isRead eq false")
        if find.has_attachment:
            filters.append("hasAttachments eq true")
        listing = await self.json("GET", self._base(find.folder), params={
            "$filter": " and ".join(filters), "$orderby": "receivedDateTime desc", "$top": find.limit, "$select": SELECT})
        return [summary_of(item) for item in listing.get("value", []) or []]

    async def attachments(self, message: str) -> list[Attachment]:
        listing = await self.json("GET", f"{GRAPH}/messages/{quote(message, safe='')}/attachments",
                                  params={"$select": "id,name,contentType,size,isInline"})
        return [Attachment(str(item["id"]), clean_line(item.get("name") or "attachment", 120), str(item.get("contentType") or ""),
                           int(item.get("size") or 0))
                for item in listing.get("value", []) or []
                if item.get("@odata.type") == "#microsoft.graph.fileAttachment" and not item.get("isInline")]

    async def read(self, message: str) -> Message:
        raw = await self.json("GET", f"{GRAPH}/messages/{quote(message, safe='')}", headers=TEXT_BODY, params={
            "$select": SELECT + ",toRecipients,ccRecipients,replyTo,body"})
        body = raw.get("body") or {}
        content = str(body.get("content") or "")
        text = html_to_text(content) if str(body.get("contentType", "")).lower() == "html" else clean_text(content)
        files = await self.attachments(message) if raw.get("hasAttachments") else []
        return Message(summary_of(raw), to=_people(raw.get("toRecipients")), cc=_people(raw.get("ccRecipients")),
                       reply_to=_people(raw.get("replyTo")), body=text, attachments=tuple(files))

    async def attachment(self, message: str, attachment: str) -> tuple[Attachment, bytes]:
        for found in await self.attachments(message):
            if found.id == attachment:
                if found.size > MAX_DOWNLOAD:
                    raise MailError(f"{found.name} is too big to fetch.")
                response = await self.request("GET", f"{GRAPH}/messages/{quote(message, safe='')}/attachments/"
                                              f"{quote(attachment, safe='')}/$value")
                return found, response.content
        raise MailError("That message has no such attachment (list them with mail_read).")

    # ------------------------------------------------------------------
    async def _discard(self, draft: str) -> None:
        with contextlib.suppress(MailError):
            await self.request("DELETE", f"{GRAPH}/messages/{quote(draft, safe='')}")

    async def _draft(self, mail: Outgoing) -> dict:
        """A draft in the mailbox with everything in it. A step that fails takes the half-made draft away again."""
        if mail.reply_to:
            made = await self.json("POST", f"{GRAPH}/messages/{quote(mail.reply_to, safe='')}/createReply", json={})
        else:
            made = await self.json("POST", f"{GRAPH}/messages", json={
                "subject": mail.subject, "body": {"contentType": "text", "content": mail.body},
                "toRecipients": _recipients(mail.to), "ccRecipients": _recipients(mail.cc),
                "bccRecipients": _recipients(mail.bcc)})
        draft = str(made["id"])
        try:
            if mail.reply_to:
                quoted = made.get("body") or {}
                original = str(quoted.get("content") or "")
                if str(quoted.get("contentType", "")).lower() == "html":
                    merged = {"contentType": "html", "content": _reply_html(mail.body, original)}
                else:
                    merged = {"contentType": "text", "content": f"{mail.body}\n\n{original}"}
                patch: dict = {"toRecipients": _recipients(mail.to), "ccRecipients": _recipients(mail.cc),
                               "bccRecipients": _recipients(mail.bcc), "body": merged}
                if mail.subject:
                    patch["subject"] = mail.subject
                made = await self.json("PATCH", f"{GRAPH}/messages/{quote(draft, safe='')}", json=patch)
            for file in mail.files:
                await self.request("POST", f"{GRAPH}/messages/{quote(draft, safe='')}/attachments", json={
                    "@odata.type": "#microsoft.graph.fileAttachment", "name": file.name, "contentType": file.mime,
                    "contentBytes": base64.b64encode(file.data).decode()})
        except MailError:
            await self._discard(draft)
            raise
        made["id"] = draft
        return made

    async def draft(self, mail: Outgoing, sender: str) -> Sent:
        made = await self._draft(mail)
        return Sent(str(made["id"]), str(made.get("webLink") or ""))

    async def send(self, mail: Outgoing, sender: str) -> Sent:
        made = await self._draft(mail)
        draft = str(made["id"])
        try:
            await self.request("POST", f"{GRAPH}/messages/{quote(draft, safe='')}/send")
        except MailError:
            await self._discard(draft)
            raise
        return Sent(draft)

    async def new_since(self, received_ms: int) -> list[Summary]:
        since = datetime.fromtimestamp(max(0, received_ms - 1000) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        listing = await self.json("GET", self._base("inbox"), params={
            "$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime desc", "$top": 50, "$select": SELECT})
        return sorted((summary_of(item) for item in listing.get("value", []) or []), key=lambda item: item.received_ms)


def _day_ms(day: str) -> int:
    return parse_time(f"{day}T00:00:00Z")
