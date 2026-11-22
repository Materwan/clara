"""Drive with the whole-Drive scope: the scope, the refusal to modify Google-native files, the old narrow connections,
the removed Picker, and the old Picker setting and secret (still accepted, ignored)."""

from __future__ import annotations

import json

import httpx
import pytest

from clara.connectors.drive import DRIVE_SCOPE, SCOPES, Drive, DriveError, GoogleAuth, OldScope
from clara.settings import load_settings
from clara.tools.base import ToolContext, TurnState


class Stand:
    """Drive and Google's token endpoint as stand-ins: `files` by id, what was patched, the scope a refresh grants."""

    def __init__(self, scope: str | None = f"{DRIVE_SCOPE} openid email"):
        self.scope = scope
        self.files: dict[str, dict] = {}
        self.patches: list[tuple[str, bytes]] = []
        self.listed: list[str] = []

    def add(self, ident: str, mime: str, name: str = "Thing") -> None:
        self.files[ident] = {"id": ident, "name": name, "mimeType": mime}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            found = {"access_token": "at", "expires_in": 3600}
            if self.scope is not None:
                found["scope"] = self.scope
            return httpx.Response(200, json=found)
        if request.url.path == "/drive/v3/files" and request.method == "GET":
            self.listed.append(request.url.params["q"])
            return httpx.Response(200, json={"files": list(self.files.values())})
        ident = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET" and ident in self.files:
            return httpx.Response(200, json=self.files[ident])
        if request.method == "PATCH" and ident in self.files:
            self.patches.append((ident, request.content))
            return httpx.Response(200, json=self.files[ident])
        return httpx.Response(404, json={"error": {"message": "File not found"}})

    def drive(self) -> Drive:
        transport = httpx.MockTransport(self.handle)
        return Drive(GoogleAuth("cid", "cs", transport), "rt", transport)


def test_the_drive_scope_is_the_whole_drive():
    from clara.connectors.drive import authorize_url

    assert SCOPES == "https://www.googleapis.com/auth/drive openid email"
    assert "drive.file" not in SCOPES and "readonly" not in SCOPES
    assert "code_challenge_method=S256" in authorize_url("id", "https://x/cb", "state", "challenge")


@pytest.mark.parametrize(
    "mime",
    [
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
        "application/vnd.google-apps.folder",
        "application/vnd.google-apps.shortcut",
        "application/vnd.google-apps.form",
    ],
)
async def test_a_google_native_file_is_never_replaced_and_nothing_is_sent(mime):
    stand = Stand()
    stand.add("f1", mime, "Plan")
    with pytest.raises(DriveError, match=r"This is a Google-native file \(Plan\): Clara never modifies those\. "):
        await stand.drive().update("f1", "new text")
    assert stand.patches == []


async def test_a_plain_file_is_replaced():
    stand = Stand()
    stand.add("f1", "text/plain", "Notes")
    assert (await stand.drive().update("f1", "new text"))["id"] == "f1"
    assert stand.patches == [("f1", b"new text")]


async def test_a_file_whose_type_cannot_be_read_is_not_replaced_and_the_error_is_clear():
    stand = Stand()
    with pytest.raises(DriveError, match="Drive cannot find that file"):
        await stand.drive().update("nope", "x")
    assert stand.patches == []


async def test_trash_and_restore_still_work_on_a_native_file():
    stand = Stand()
    stand.add("f1", "application/vnd.google-apps.document")
    await stand.drive().trash("f1")
    assert [ident for ident, _ in stand.patches] == ["f1"] and json.loads(stand.patches[0][1]) == {"trashed": True}


async def test_the_clara_folder_is_looked_for_among_the_folders_you_own():
    stand = Stand()
    stand.add("d1", "application/vnd.google-apps.folder", "Clara")
    assert (await stand.drive().ensure_folder())["id"] == "d1"
    assert "'me' in owners" in stand.listed[0]


# ----------------------------------------------------------------------
# A connection made with the old scope
# ----------------------------------------------------------------------
async def test_a_refresh_that_grants_only_drive_file_is_the_old_scope():
    old = Stand(scope="https://www.googleapis.com/auth/drive.file openid email")
    with pytest.raises(OldScope) as caught:
        await old.drive().meta("f1")
    assert caught.value.reconnect and "connect Drive again" in str(caught.value)
    # the exact scope, not a text that merely contains it
    stand = Stand(scope=f"openid email {DRIVE_SCOPE}")
    stand.add("f1", "text/plain")
    assert (await stand.drive().meta("f1"))["id"] == "f1"
    no_scope = Stand(scope=None)  # a response that does not say: nothing is concluded
    no_scope.add("f1", "text/plain")
    assert (await no_scope.drive().meta("f1"))["id"] == "f1"


async def call(services, name, arguments):
    context = ToolContext(services, TurnState("t1", "", "web", mode="interactive", timezone="Europe/Paris"))
    return await services.toolbox.call(name, context, arguments, services.toolbox.names)


@pytest.fixture
def stand(services, monkeypatch):
    found = Stand()
    services.secrets.set("google_refresh_token", "rt")
    services.connections.set("google", "me@gmail.com")
    drive = found.drive()
    monkeypatch.setattr(services.connectors, "drive_client", lambda: drive)
    return found, drive


async def test_the_drive_tools_answer_the_old_scope_and_the_page_says_to_connect_again(services, client, stand):
    found, _ = stand
    assert (await client.get("/v1/connections")).json()["google"]["old_scope"] is False
    found.scope = "https://www.googleapis.com/auth/drive.file openid email"
    for name, arguments in (("drive_list", {}), ("drive_read", {"file": "f1"}), ("drive_search", {"query": "x"})):
        result = await call(services, name, arguments)
        assert not result.ran or "old narrow permission" in result.text
    google = (await client.get("/v1/connections")).json()["google"]
    assert google["old_scope"] is True and google["status"] == "needs_reconnect"
    assert "Drive is connected with the old narrow permission: connect Drive again on the Connections page." in (
        services.alerts.active["google"]
    )


async def test_a_drive_list_shows_the_root_of_the_whole_drive_with_no_picked_resource(services, stand):
    found, _ = stand
    found.add("f1", "text/plain", "By hand")
    found.add("d1", "application/vnd.google-apps.folder", "Projects")
    assert not services.connections.resources()
    listed = await call(services, "drive_list", {})
    assert listed.ran and "[f1] By hand" in listed.text and "[d1] Projects/" in listed.text
    assert "'root' in parents" in found.listed[-1]
    searched = await call(services, "drive_search", {"query": "hand"})
    assert searched.ran and "[f1] By hand" in searched.text


async def test_a_google_native_file_is_refused_by_the_tool_before_any_patch(services, stand):
    from modeset import at_level

    found, _ = stand
    at_level(services, 5)  # a modify runs, so only the tool can refuse
    found.add("g1", "application/vnd.google-apps.document", "Plan")
    refused = await call(services, "drive_update", {"file": "g1", "content": "x"})
    assert refused.ran and "Google-native file (Plan)" in refused.text and found.patches == []


# ----------------------------------------------------------------------
# The Picker is gone
# ----------------------------------------------------------------------
async def test_the_picker_routes_are_gone_and_the_state_has_no_picker_or_files(client):
    assert (await client.get("/v1/connections/google/picker")).status_code in (404, 405)
    assert (await client.post("/v1/connections/google/picked", json={"files": []})).status_code in (404, 405)
    assert (await client.get("/picker.html")).status_code == 200  # the site's own page answers (the app), not a picker
    google = (await client.get("/v1/connections")).json()["google"]
    assert "picker" not in google and "files" not in google


def test_the_old_picker_setting_is_still_accepted_and_nothing_reads_it(environ, services):
    settings = load_settings({**environ, "CLARA_GOOGLE_PROJECT_NUMBER": "1234567890"}, use_file=False)
    assert settings.google_project_number == "1234567890"
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "clara"
    readers = [
        path.name for path in src.rglob("*.py") if "google_project_number" in path.read_text(encoding="utf-8")
    ]
    assert sorted(readers) == ["settings.py"]


async def test_the_old_picker_secret_is_still_known_listed_and_unset_but_nothing_reads_it(services):
    from clara.secrets.store import KNOWN_SECRETS

    assert "google_picker_api_key" in KNOWN_SECRETS
    services.secrets.set("google_picker_api_key", "AIza-old")
    assert {item["name"] for item in services.secrets.status()} >= {"google_picker_api_key"}
    assert services.secrets.unset("google_picker_api_key") and not services.secrets.has("google_picker_api_key")
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "clara"
    readers = [path.name for path in src.rglob("*.py") if "google_picker_api_key" in path.read_text(encoding="utf-8")]
    assert sorted(readers) == ["store.py"]
