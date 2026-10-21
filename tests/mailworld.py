"""In-memory stand-ins for Gmail, Microsoft Graph and their token endpoints, for httpx.MockTransport.

They answer the way the real services are documented to, closely enough to run the real clients, and record what they
were asked so a test can check it (the queries, the messages sent, the drafts made, the tokens handed out).
"""

from __future__ import annotations

import base64
import json
import re
from email import message_from_bytes, policy
from urllib.parse import unquote

import httpx

from clara.connectors.outlook import parse_time


def b64(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def gmail_message(message_id: str, sender: str, subject: str, text: str, ms: int, *, html: str = "", to: str = "me@gmail.com",
                  attachments: tuple[tuple[str, str, bytes], ...] = (), unread: bool = True, thread: str = "") -> dict:
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": to}, {"name": "Subject", "value": subject},
               {"name": "Message-ID", "value": f"<{message_id}@mail.example>"}]
    body = [{"partId": "0.0", "mimeType": "text/plain", "filename": "", "headers": [{"name": "Content-Type", "value": 'text/plain; charset="UTF-8"'}],
             "body": {"size": len(text), "data": b64(text)}}]
    if html:
        body.append({"partId": "0.1", "mimeType": "text/html", "filename": "", "body": {"size": len(html), "data": b64(html)}})
    payload: dict = {"mimeType": "multipart/alternative", "headers": headers, "parts": body}
    if attachments:
        payload = {"mimeType": "multipart/mixed", "headers": headers, "partId": "", "body": {"size": 0}, "parts": [
            {"partId": "0", "mimeType": "multipart/alternative", "filename": "", "body": {"size": 0}, "parts": body},
            *({"partId": str(number), "mimeType": mime, "filename": name,
               "body": {"attachmentId": f"att-{message_id}-{number}", "size": len(data)}}
              for number, (name, mime, data) in enumerate(attachments, 1))]}
    return {"id": message_id, "threadId": thread or f"t-{message_id}", "labelIds": ["INBOX"] + (["UNREAD"] if unread else []),
            "snippet": text[:60], "internalDate": str(ms), "payload": payload}


class World:
    """Gmail, Outlook and both token endpoints in one place."""

    def __init__(self) -> None:
        self.gmail: dict[str, dict] = {}
        self.gmail_files: dict[str, bytes] = {}
        self.gmail_queries: list[dict] = []
        self.gmail_sent: list[dict] = []
        self.gmail_drafts: list[dict] = []
        self.google_tokens = 0
        self.google_dead = False
        self.google_no_refresh = False
        self.google_calls: list[str] = []

        self.graph: dict[str, dict] = {}
        self.graph_files: dict[str, bytes] = {}
        self.graph_attachment_info: dict[str, tuple[str, str]] = {}
        self.graph_queries: list[dict] = []
        self.graph_calls: list[tuple[str, str]] = []
        self.graph_bodies: list[dict] = []
        self.graph_send_fails = False
        self.microsoft_tokens = 0
        self.microsoft_dead = False
        self.microsoft_seen: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "oauth2.googleapis.com":
            return self.google_token(request)
        if host == "gmail.googleapis.com":
            return self.gmail_api(request)
        if host == "login.microsoftonline.com":
            return self.microsoft_token(request)
        if host == "graph.microsoft.com":
            return self.graph_api(request)
        return httpx.Response(404, json={"error": f"unexpected host {host}"})

    # ------------------------------------------------------------------
    # Google
    # ------------------------------------------------------------------
    def google_token(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/revoke":
            self.google_calls.append("revoke")
            return httpx.Response(200)
        self.google_tokens += 1
        if self.google_dead:
            return httpx.Response(400, json={"error": "invalid_grant"})
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        answer = {"access_token": f"gat-{self.google_tokens}", "expires_in": 3600}
        if form.get("grant_type") == "authorization_code" and not self.google_no_refresh:
            answer["refresh_token"] = f"grt-{self.google_tokens}"
        return httpx.Response(200, json=answer)

    def gmail_api(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer gat-") or (self.google_tokens == 0):
            return httpx.Response(401, json={"error": {"code": 401, "message": "bad token"}})
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        params = request.url.params
        if path == "/profile":
            return httpx.Response(200, json={"emailAddress": "me@gmail.com"})
        if path == "/messages" and request.method == "GET":
            self.gmail_queries.append({key: params.get_list(key) for key in set(params.keys())})
            after = re.search(r"after:(\d+)", params.get("q", ""))
            found = [m for m in self.gmail.values() if not after or int(m["internalDate"]) / 1000 > int(after.group(1))]
            return httpx.Response(200, json={"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in found]
                                             [: int(params.get("maxResults", 10))]})
        if path == "/messages/send":
            self.gmail_sent.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "sent-1", "threadId": "t-sent"})
        if path == "/drafts":
            self.gmail_drafts.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "draft-1", "message": {"id": "dm-1"}})
        found = re.fullmatch(r"/messages/([^/]+)/attachments/([^/]+)", path)
        if found:
            data = self.gmail_files.get(unquote(found.group(2)))
            return httpx.Response(200, json={"size": len(data), "data": b64(data)}) if data is not None else httpx.Response(404, json={})
        found = re.fullmatch(r"/messages/([^/]+)", path)
        if found:
            message = self.gmail.get(unquote(found.group(1)))
            if message is None:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Requested entity was not found."}})
            if params.get("format") == "metadata":
                trimmed = json.loads(json.dumps(message))
                trimmed["payload"] = {"mimeType": message["payload"]["mimeType"], "headers": message["payload"]["headers"]}
                return httpx.Response(200, json=trimmed)
            return httpx.Response(200, json=message)
        return httpx.Response(404, json={"error": {"message": f"no route {path}"}})

    # ------------------------------------------------------------------
    # Microsoft
    # ------------------------------------------------------------------
    def microsoft_token(self, request: httpx.Request) -> httpx.Response:
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        self.microsoft_tokens += 1
        if self.microsoft_dead:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "AADSTS700082: expired\r\nTrace ID: x"})
        self.microsoft_seen.append(unquote(form.get("refresh_token", form.get("code", ""))))
        return httpx.Response(200, json={"access_token": f"mat-{self.microsoft_tokens}", "expires_in": 3600,
                                         "refresh_token": f"mrt-{self.microsoft_tokens}"})

    def graph_api(self, request: httpx.Request) -> httpx.Response:
        if not request.headers.get("authorization", "").startswith("Bearer mat-"):
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken", "message": "bad token"}})
        path = request.url.path.removeprefix("/v1.0/me")
        params = request.url.params
        self.graph_calls.append((request.method, path))
        if request.content:
            self.graph_bodies.append(json.loads(request.content))
        if path == "":
            return httpx.Response(200, json={"mail": "me@outlook.com", "userPrincipalName": "me@outlook.com"})
        if path == "/mailFolders/inbox" and request.method == "GET":
            return httpx.Response(200, json={"id": "inbox"})
        if path.endswith("/messages") and request.method == "GET":
            self.graph_queries.append({"path": path, **dict(params)})
            since = re.search(r"receivedDateTime ge (\S+)", params.get("$filter", ""))
            floor = parse_time(since.group(1)) if since else 0
            return httpx.Response(200, json={"value": [m for m in self.graph.values() if parse_time(m["receivedDateTime"]) >= floor]})
        if path == "/messages" and request.method == "POST":
            return httpx.Response(201, json={"id": "draft-1", "webLink": "https://outlook.example/draft-1"})
        found = re.fullmatch(r"/messages/([^/]+)/createReply", path)
        if found:
            return httpx.Response(201, json={"id": "reply-1", "webLink": "https://outlook.example/reply-1", "body": {
                "contentType": "html", "content": "<html><body><div>--- quoted original ---</div></body></html>"}})
        found = re.fullmatch(r"/messages/([^/]+)/send", path)
        if found:
            return httpx.Response(500, json={"error": {"code": "ErrorInternalServerError", "message": "boom"}}) if self.graph_send_fails \
                else httpx.Response(202)
        found = re.fullmatch(r"/messages/([^/]+)/attachments", path)
        if found and request.method == "POST":
            return httpx.Response(201, json={"id": "att-new"})
        if found:
            return httpx.Response(200, json={"value": [
                {"@odata.type": "#microsoft.graph.fileAttachment", "id": key, "name": name, "contentType": mime, "size": len(self.graph_files[key]),
                 "isInline": False} for key, (name, mime) in self.graph_attachment_info.items() if key in self.graph_files]})
        found = re.fullmatch(r"/messages/([^/]+)/attachments/([^/]+)/\$value", path)
        if found:
            return httpx.Response(200, content=self.graph_files[unquote(found.group(2))])
        found = re.fullmatch(r"/messages/([^/]+)", path)
        if found and request.method == "GET":
            message = self.graph.get(unquote(found.group(1)))
            return httpx.Response(200, json=message) if message else httpx.Response(404, json={"error": {"code": "ErrorItemNotFound", "message": "x"}})
        if found and request.method in ("PATCH", "DELETE"):
            return httpx.Response(200, json={"id": found.group(1)}) if request.method == "PATCH" else httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": f"no route {path}"}})



def graph_message(message_id: str, sender: str, subject: str, text: str, when: str, *, name: str = "", unread: bool = True,
                  attachments: bool = False, html: bool = False) -> dict:
    return {"id": message_id, "conversationId": f"c-{message_id}", "subject": subject, "bodyPreview": text[:60],
            "from": {"emailAddress": {"name": name, "address": sender}}, "receivedDateTime": when, "isRead": not unread,
            "hasAttachments": attachments, "toRecipients": [{"emailAddress": {"name": "Me", "address": "me@outlook.com"}}],
            "ccRecipients": [], "replyTo": [], "body": {"contentType": "html" if html else "text", "content": text}}


def parse_sent(raw: dict) -> object:
    """The RFC 5322 message a Gmail `raw` field carries."""
    data = raw["raw"] + "=" * (-len(raw["raw"]) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(data), policy=policy.default)
