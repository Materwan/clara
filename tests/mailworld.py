"""In-memory stand-ins for Gmail and its token endpoint, for httpx.MockTransport.

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
    """Gmail and its token endpoint in one place."""

    def __init__(self) -> None:
        self.gmail: dict[str, dict] = {}
        self.gmail_files: dict[str, bytes] = {}
        self.gmail_queries: list[dict] = []
        self.gmail_gets = 0  # message details fetched (what a backlog costs)
        self.gmail_sent: list[dict] = []
        self.gmail_drafts: list[dict] = []
        self.gmail_labels: dict[str, str] = {}  # your labels: id -> name
        self.gmail_labels_deleted: list[str] = []  # each labels.delete: a label, its messages kept
        self.gmail_modified: list[dict] = []  # each messages.modify, as sent
        self.gmail_trashed: list[str] = []  # each messages.trash
        self.gmail_refused: list[str] = []  # each DELETE: Clara must never send one
        self.google_tokens = 0
        self.google_dead = False
        self.google_no_refresh = False
        self.google_calls: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "oauth2.googleapis.com":
            return self.google_token(request)
        if host == "gmail.googleapis.com":
            return self.gmail_api(request)
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
        found = re.fullmatch(r"/labels/([^/]+)", path)
        if found and request.method == "DELETE":
            label = unquote(found.group(1))
            if label not in self.gmail_labels:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Label not found."}})
            self.gmail_labels_deleted.append(label)
            del self.gmail_labels[label]
            for message in self.gmail.values():  # the label comes off its messages; the messages stay
                message["labelIds"] = [item for item in message["labelIds"] if item != label]
            return httpx.Response(204)
        if request.method == "DELETE":  # a message's permanent delete: Clara must never send one
            self.gmail_refused.append(path)
            return httpx.Response(405, json={"error": {"code": 405, "message": "Method not allowed."}})
        if path == "/profile":
            return httpx.Response(200, json={"emailAddress": "me@gmail.com"})
        if path == "/labels" and request.method == "GET":
            system = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "SENT", "name": "SENT", "type": "system"}]
            own = [{"id": key, "name": name, "type": "user"} for key, name in self.gmail_labels.items()]
            return httpx.Response(200, json={"labels": system + own})
        if path == "/labels" and request.method == "POST":
            name = json.loads(request.content)["name"]
            if name in self.gmail_labels.values():
                return httpx.Response(409, json={"error": {"code": 409, "message": "Label name exists or conflicts."}})
            self.gmail_labels[f"Label_{len(self.gmail_labels) + 1}"] = name
            return httpx.Response(200, json={"id": f"Label_{len(self.gmail_labels)}", "name": name, "type": "user"})
        found = re.fullmatch(r"/messages/([^/]+)/modify", path)
        if found and request.method == "POST":
            message = self.gmail.get(unquote(found.group(1)))
            if message is None:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Requested entity was not found."}})
            body = json.loads(request.content)
            known = set(self.gmail_labels) | {"INBOX", "SENT", "UNREAD", "TRASH"}
            if not set(body.get("addLabelIds", []) + body.get("removeLabelIds", [])) <= known:
                return httpx.Response(400, json={"error": {"code": 400, "message": "Invalid label: X"}})
            self.gmail_modified.append({"message": message["id"], **body})
            labels = [item for item in message["labelIds"] if item not in body.get("removeLabelIds", [])]
            message["labelIds"] = list(dict.fromkeys([*labels, *body.get("addLabelIds", [])]))
            return httpx.Response(200, json=message)
        found = re.fullmatch(r"/messages/([^/]+)/trash", path)
        if found and request.method == "POST":
            message = self.gmail.get(unquote(found.group(1)))
            if message is None:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Requested entity was not found."}})
            self.gmail_trashed.append(message["id"])
            message["labelIds"] = ["TRASH"]  # what Gmail does: out of the inbox, into the Trash
            return httpx.Response(200, json=message)
        if path == "/messages" and request.method == "GET":
            self.gmail_queries.append({key: params.get_list(key) for key in set(params.keys())})
            after = re.search(r"after:(\d+)", params.get("q", ""))
            found = sorted((m for m in self.gmail.values() if not after or int(m["internalDate"]) / 1000 > int(after.group(1))),
                           key=lambda m: int(m["internalDate"]), reverse=True)  # newest first, as Gmail lists
            size = min(int(params.get("maxResults", 100)), 500)
            start = int(params.get("pageToken") or 0)
            page = found[start:start + size]
            answer: dict = {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in page]}
            if start + size < len(found):
                answer["nextPageToken"] = str(start + size)
            return httpx.Response(200, json=answer)
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
            self.gmail_gets += 1
            if message is None:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Requested entity was not found."}})
            if params.get("format") == "metadata":
                trimmed = json.loads(json.dumps(message))
                trimmed["payload"] = {"mimeType": message["payload"]["mimeType"], "headers": message["payload"]["headers"]}
                return httpx.Response(200, json=trimmed)
            return httpx.Response(200, json=message)
        return httpx.Response(404, json={"error": {"message": f"no route {path}"}})


def parse_sent(raw: dict) -> object:
    """The RFC 5322 message a Gmail `raw` field carries."""
    data = raw["raw"] + "=" * (-len(raw["raw"]) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(data), policy=policy.default)


def connect_mailbox(services) -> None:
    services.secrets.set("google_client_secret", "gs")
    services.secrets.set("gmail_refresh_token", "rt-0")
    services.connections.set("gmail", "me@gmail.com")
