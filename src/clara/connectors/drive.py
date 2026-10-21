"""Google Drive, with the narrow `drive.file` scope: Clara sees only files she created (in a "Clara" folder she makes
when you connect) and files you open to her with the Google Picker on the Connections page.

Connecting is OAuth with PKCE. The flow is bound to the browser session that started it, the Google address being
attached is shown before you confirm, and disconnecting revokes the token at Google.

    read          list, read (Google Docs, Sheets and Slides are exported as text), search      class drive_read
    write         create a file                                                                 class drive_write
    destructive   replace a file's content, move a file to Drive's trash                        class drive_write
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from ..knowledge.ingest import IngestError, extract
from ..knowledge.projects import read_lines

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address, not a password
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
SCOPES = "https://www.googleapis.com/auth/drive.file openid email"
TIMEOUT = httpx.Timeout(20.0, read=90.0)
FOLDER = "application/vnd.google-apps.folder"
DOC = "application/vnd.google-apps.document"
EXPORTS = {
    DOC: "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
FIELDS = "id,name,mimeType,size,parents,trashed,modifiedTime,webViewLink"
MAX_FILE_BYTES = 30_000_000
LIST_LIMIT = 300


class DriveError(ValueError):
    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
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


def email_of(id_token: str) -> str:
    """The Google address in an id_token that came straight from Google's token endpoint over TLS."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return ""
    return str(claims.get("email") or "")


def quoted(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


class GoogleAuth:
    def __init__(self, client_id: str, client_secret: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self._transport = transport

    async def token(self, data: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.post(
                    TOKEN_URL, data={**data, "client_id": self.client_id, "client_secret": self.client_secret}
                )
        except httpx.HTTPError as error:
            raise DriveError(f"Google could not be reached: {error}") from None
        try:
            found = response.json()
        except ValueError:
            found = {}
        if response.is_error:
            if found.get("error") == "invalid_grant":
                raise DriveError(
                    "Google no longer accepts this connection (access removed, or the Google app is still "
                    "in 'Testing' mode, where it ends after 7 days): connect it again.",
                    True,
                )
            raise DriveError(
                f"Google refused: {found.get('error_description') or found.get('error') or response.status_code}"
            )
        return found

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        return await self.token(
            {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri}
        )

    async def revoke(self, token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            return False
        return response.status_code == 200


class Drive:
    def __init__(self, auth: GoogleAuth, refresh_token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.auth = auth
        self.refresh_token = refresh_token
        self._transport = transport
        self._access: tuple[str, float] | None = None

    async def access_token(self) -> str:
        if self._access and self._access[1] > time.monotonic() + 60:
            return self._access[0]
        found = await self.auth.token({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        self._access = (found["access_token"], time.monotonic() + int(found.get("expires_in", 3000)))
        return self._access[0]

    async def request(self, method: str, url: str, **options: Any) -> httpx.Response:
        extra = options.pop("headers", {})
        for attempt in (1, 2):
            access = await self.access_token()
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport, follow_redirects=True) as http:
                    response = await http.request(
                        method, url, headers={**extra, "Authorization": f"Bearer {access}"}, **options
                    )
            except httpx.HTTPError as error:
                raise DriveError(f"Google could not be reached: {error}") from None
            if response.status_code == 401 and attempt == 1:
                self._access = None
                continue
            if response.is_error:
                raise self.problem(response)
            return response
        raise DriveError("Google refused the connection: connect it again.", True)

    @staticmethod
    def problem(response: httpx.Response) -> DriveError:
        status = response.status_code
        try:
            error = response.json().get("error", {})
            detail = error.get("message", "") if isinstance(error, dict) else str(error)
            reason = (error.get("errors") or [{}])[0].get("reason", "") if isinstance(error, dict) else ""
        except (ValueError, AttributeError):
            detail = reason = ""
        if status == 401:
            return DriveError("Google refused the connection: connect it again.", True)
        if status == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED"):
            return DriveError("The Google Drive API is not turned on in your Google Cloud project.")
        if status == 403 and "Limit" in reason:
            return DriveError("Google says too many requests for now: try again later.")
        if status == 404:
            return DriveError("Drive cannot find that file, or Clara may not see it (open it to her with the Picker).")
        return DriveError(f"Google Drive answered HTTP {status}: {detail or reason or 'error'}")

    async def get(self, url: str, **params: Any) -> Any:
        wanted = {key: value for key, value in params.items() if value is not None}
        return (await self.request("GET", url, params=wanted)).json()

    # ------------------------------------------------------------------
    async def meta(self, file_id: str) -> dict:
        return await self.get(f"{API}/files/{file_id}", fields=FIELDS, supportsAllDrives="true")

    async def ensure_folder(self, name: str = "Clara") -> dict:
        found = await self.get(
            f"{API}/files",
            q=f"name = {quoted(name)} and mimeType = {quoted(FOLDER)} and trashed = false",
            fields=f"files({FIELDS})",
            pageSize=10,
        )
        if found.get("files"):
            return found["files"][0]
        made = await self.request(
            "POST", f"{API}/files", params={"fields": FIELDS}, json={"name": name, "mimeType": FOLDER}
        )
        return made.json()

    async def children(self, folder_id: str) -> list[dict]:
        found: list[dict] = []
        token = None
        while len(found) < 1000:
            page = await self.get(
                f"{API}/files",
                q=f"{quoted(folder_id)} in parents and trashed = false",
                fields=f"nextPageToken,files({FIELDS})",
                pageSize=200,
                pageToken=token,
                orderBy="folder,name",
                supportsAllDrives="true",
                includeItemsFromAllDrives="true",
            )
            found += page.get("files", [])
            token = page.get("nextPageToken")
            if not token:
                break
        return found

    async def search(self, query: str) -> list[dict]:
        if not query.strip():
            raise DriveError("The query is empty.")
        text = quoted(query.strip()[:200])
        found = await self.get(
            f"{API}/files",
            q=f"(name contains {text} or fullText contains {text}) and trashed = false",
            fields=f"files({FIELDS})",
            pageSize=50,
            supportsAllDrives="true",
            includeItemsFromAllDrives="true",
        )
        return found.get("files", [])

    async def read(self, file_id: str, start: int, max_chars: int) -> str:
        info = await self.meta(file_id)
        name, kind = info["name"], info["mimeType"]
        if kind == FOLDER:
            raise DriveError(f"{name} is a folder: list it.")
        if kind in EXPORTS:
            response = await self.request("GET", f"{API}/files/{file_id}/export", params={"mimeType": EXPORTS[kind]})
            text = response.text
        elif kind.startswith("application/vnd.google-apps."):
            raise DriveError(f"{name} is a {kind.rsplit('.', 1)[-1]}, which cannot be read as text.")
        else:
            if int(info.get("size") or 0) > MAX_FILE_BYTES:
                raise DriveError(f"{name} is too big to read.")
            response = await self.request("GET", f"{API}/files/{file_id}", params={"alt": "media"})
            try:
                text = extract(name, response.content).text
            except IngestError as error:
                raise DriveError(f"{name}: {error}.") from None
        return read_lines(name, text, start, None, max_chars)

    async def create(self, name: str, content: str, parent: str, google_doc: bool) -> dict:
        metadata: dict[str, Any] = {"name": name, "parents": [parent]}
        if google_doc:
            metadata["mimeType"] = DOC
        boundary = "clara" + base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
        body = (
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
                f"--{boundary}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n"
            ).encode()
            + content.encode("utf-8")
            + f"\r\n--{boundary}--".encode()
        )
        response = await self.request(
            "POST",
            f"{UPLOAD}/files",
            params={"uploadType": "multipart", "fields": FIELDS},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        return response.json()

    async def update(self, file_id: str, content: str) -> dict:
        response = await self.request(
            "PATCH",
            f"{UPLOAD}/files/{file_id}",
            params={"uploadType": "media", "fields": FIELDS},
            headers={"Content-Type": "text/plain; charset=UTF-8"},
            content=content.encode("utf-8"),
        )
        return response.json()

    async def trash(self, file_id: str) -> dict:
        response = await self.request(
            "PATCH", f"{API}/files/{file_id}", params={"fields": FIELDS}, json={"trashed": True}
        )
        return response.json()
