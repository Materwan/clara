"""Connections: GitHub (a fine-grained token you paste) and Google Drive (OAuth, `drive.file`), and the resources
Clara may use: the repositories you add, the Drive files and folders you pick.

The Google consent is bound to the browser session that started it: Google sends the browser back to `callback`
(which needs no token: it only forwards to the web site), the web site, signed in, finishes the exchange, shows the
Google address that would be attached, and you confirm. Disconnecting revokes the token at Google.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..connectors.drive import FOLDER, DriveError, authorize_url, email_of, pkce
from ..connectors.github import REPO, GitHub, GitHubError
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1/connections", tags=["connections"])


def _connectors(request: Request):
    s = services(request)
    if s.connectors is None:
        raise ApiError(404, "Connections are not available.")
    return s, s.connectors


def _redirect_uri(request: Request) -> str:
    s = services(request)
    base = s.settings.public_url or (s.tailscale.url if s.tailscale and s.tailscale.url else "")
    if not base:
        raise ApiError(409, "Set CLARA_PUBLIC_URL (your https://<machine>.<tailnet>.ts.net address) for Google's redirect.")
    return f"{base.rstrip('/')}/v1/connections/google/callback"


@router.get("")
def status(request: Request) -> dict:
    s, connectors = _connectors(request)
    github = s.connections.get("github")
    google = s.connections.get("google")
    return {
        "github": {"connected": bool(github and s.secrets.has("github_token")), "account": github["account"] if github else "",
                   "status": github["status"] if github else "", "repos": [r.describe() for r in s.connections.resources("github_repo")]},
        "google": {"configured": connectors.google_auth() is not None, "connected": bool(google and s.secrets.has("google_refresh_token")),
                   "account": google["account"] if google else "", "status": google["status"] if google else "",
                   "picker": bool(s.settings.google_project_number and s.secrets.has("google_picker_api_key")),
                   "files": [r.describe() for r in s.connections.resources() if r.kind.startswith("drive")]},
    }


# ----------------------------------------------------------------------
# GitHub
# ----------------------------------------------------------------------
class GitHubToken(BaseModel):
    token: str = Field(min_length=10, max_length=400)


@router.put("/github")
async def connect_github(body: GitHubToken, request: Request) -> dict:
    s, _ = _connectors(request)
    try:
        login = await GitHub(body.token.strip()).whoami()
    except GitHubError as error:
        raise ApiError(422, f"GitHub refused this token: {error}") from None
    s.secrets.set("github_token", body.token.strip())
    s.connections.set("github", login)
    s.alerts.clear("github")
    return {"account": login}


@router.delete("/github")
def disconnect_github(request: Request) -> dict:
    s, _ = _connectors(request)
    s.secrets.unset("github_token")
    s.connections.remove("github")
    return {"connected": False}


class Repo(BaseModel):
    repo: str = Field(min_length=3, max_length=201)
    branch: str = Field(default="", max_length=200)


@router.post("/github/repos", status_code=201)
async def add_repo(body: Repo, request: Request) -> dict:
    s, connectors = _connectors(request)
    name = body.repo.strip().removeprefix("https://github.com/").strip("/")
    if not REPO.match(name):
        raise ApiError(422, "Give the repository as owner/name.")
    client = connectors.github_client()
    if client is None:
        raise ApiError(409, "Set your GitHub token first.")
    try:
        await client.default_branch(name)  # the token reaches it
    except GitHubError as error:
        raise ApiError(422, str(error)) from None
    return s.connections.add("github", "github_repo", name, name, body.branch.strip()).describe()


@router.delete("/resources/{resource_id}")
def remove_resource(resource_id: int, request: Request) -> dict:
    s, _ = _connectors(request)
    if not s.connections.delete(resource_id):
        raise ApiError(404, f"No resource {resource_id}.")
    return {"deleted": True}


# ----------------------------------------------------------------------
# Google
# ----------------------------------------------------------------------
@router.post("/google/start")
def google_start(request: Request) -> dict:
    s, connectors = _connectors(request)
    who = caller(request)
    if who.web_session is None:
        raise ApiError(409, "Connect Google Drive from the web site: the consent is tied to your browser.")
    if connectors.google_auth() is None:
        raise ApiError(409, "Set CLARA_GOOGLE_CLIENT_ID and the secret google_client_secret first.")
    verifier, challenge = pkce()
    state = connectors.new_flow(who.web_session, verifier)
    return {"url": authorize_url(s.settings.google_client_id, _redirect_uri(request), state, challenge)}


@router.get("/google/callback", include_in_schema=False)
def google_callback(state: str = Query("", max_length=100), code: str = Query("", max_length=2000),
                    error: str = Query("", max_length=200)) -> RedirectResponse:
    """Google sends the browser here. Nothing is done: the signed-in web site finishes it."""
    query = urlencode({"google_state": state, "google_code": code, "google_error": error})
    return RedirectResponse(f"/#/connections?{query}", status_code=303)


class Finish(BaseModel):
    state: str = Field(min_length=10, max_length=100)
    code: str = Field(min_length=1, max_length=2000)


@router.post("/google/finish")
async def google_finish(body: Finish, request: Request) -> dict:
    s, connectors = _connectors(request)
    try:
        flow = connectors.flow(body.state, caller(request).web_session)
    except ValueError as error:
        raise ApiError(409, str(error)) from None
    auth = connectors.google_auth()
    if auth is None:
        raise ApiError(409, "Google is not configured.")
    try:
        tokens = await auth.exchange(body.code, flow.verifier, _redirect_uri(request))
    except DriveError as error:
        raise ApiError(422, str(error)) from None
    if not tokens.get("refresh_token"):
        raise ApiError(422, "Google gave no refresh token: remove Clara from your Google account's access, then retry.")
    flow.refresh_token = tokens["refresh_token"]
    flow.email = email_of(tokens.get("id_token", ""))
    return {"account": flow.email, "confirm": "Attach this Google account to Clara?"}


class Confirm(BaseModel):
    state: str = Field(min_length=10, max_length=100)


@router.post("/google/confirm")
async def google_confirm(body: Confirm, request: Request) -> dict:
    s, connectors = _connectors(request)
    try:
        flow = connectors.flow(body.state, caller(request).web_session)
    except ValueError as error:
        raise ApiError(409, str(error)) from None
    if not flow.refresh_token:
        raise ApiError(409, "Finish the Google sign-in first.")
    old = s.secrets.get("google_refresh_token")
    s.secrets.set("google_refresh_token", flow.refresh_token)
    s.connections.set("google", flow.email)
    connectors.flows.pop(body.state, None)
    auth = connectors.google_auth()
    if old and old != flow.refresh_token and auth is not None:
        await auth.revoke(old)
    drive = connectors.drive_client()
    try:
        folder = await drive.ensure_folder("Clara")
        s.connections.add("google", "drive_folder", folder["id"], "Clara (her own folder)", mime="clara")
    except DriveError as error:
        raise ApiError(502, f"Connected, but Clara's folder could not be made: {error}") from None
    s.alerts.clear("google")
    return {"account": flow.email}


@router.delete("/google")
async def google_disconnect(request: Request) -> dict:
    s, connectors = _connectors(request)
    token = s.secrets.get("google_refresh_token")
    auth = connectors.google_auth()
    revoked = bool(token and auth and await auth.revoke(token))
    s.secrets.unset("google_refresh_token")
    s.connections.remove("google")
    return {"connected": False, "revoked": revoked}


@router.get("/google/picker")
async def google_picker(request: Request) -> dict:
    """What the picker page needs: only for the signed-in browser."""
    s, connectors = _connectors(request)
    if caller(request).web_session is None:
        raise ApiError(403, "The picker is for the web site.")
    drive = connectors.drive_client()
    key = s.secrets.get("google_picker_api_key")
    if drive is None or not key or not s.settings.google_project_number:
        raise ApiError(409, "Connect Drive, set CLARA_GOOGLE_PROJECT_NUMBER and the secret google_picker_api_key.")
    try:
        token = await drive.access_token()
    except DriveError as error:
        raise ApiError(502, str(error)) from None
    return {"api_key": key, "app_id": s.settings.google_project_number, "client_id": s.settings.google_client_id,
            "token": token}


class Picked(BaseModel):
    id: str = Field(min_length=5, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=300)
    mimeType: str = Field(default="", max_length=200)  # noqa: N815 - Google's name


class PickedFiles(BaseModel):
    files: list[Picked] = Field(min_length=1, max_length=100)


@router.post("/google/picked", status_code=201)
def google_picked(body: PickedFiles, request: Request) -> dict:
    s, _ = _connectors(request)
    added = [s.connections.add("google", "drive_folder" if item.mimeType == FOLDER else "drive_file", item.id, item.name,
                               mime=item.mimeType).describe() for item in body.files]
    return {"added": added}
