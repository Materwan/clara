"""Restore for Drive, and a class of its own for Drive's trash: the default verdicts (built in, shipped file, an old file),
`drive_delete` as `drive_trash`, the Drive client against a stand-in, and the tools through the gate."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from clara.connectors.drive import Drive, DriveError, GoogleAuth
from clara.gate import Action, Situation
from clara.gate.actions import LEAVES_MACHINE, OUTSIDE, RAISED_WHEN_TAINTED
from clara.gate.modes import builtin_default, complete, parse_default
from clara.tools.base import ToolContext, TurnState
from modeset import line, make

ROOT = Path(__file__).resolve().parent.parent
# class -> (verdict in a normal turn, verdict in a tainted turn)
WANTED = {
    "drive_read": ("approve", "approve"),
    "drive_write": ("ask", "ask"),
    "drive_trash": ("refuse", "refuse"),
    "drive_restore": ("approve", "approve"),
    "mail_restore": ("approve", "approve"),
}


def verdicts(mode) -> dict[str, tuple[str, str]]:
    found = {}
    for klass in WANTED:
        best = mode.best(Action("t", "write", klass, ("drive:f1",), "x"), False)
        found[klass] = (best.verdict, best.when(True))
    return found


def test_the_default_mode_has_the_four_verdicts_built_in_in_the_shipped_file_and_for_a_file_without_them():
    shipped = parse_default((ROOT / "deploy" / "default-mode.toml").read_text(encoding="utf-8"), "default-mode.toml", {"t"})
    old, filled = complete(parse_default('[[verdict]]\nclass = "read"\nverdict = "approve"\n', "old.toml", {"t"}))
    assert {"drive_write", "drive_trash", "drive_restore", "mail_restore"} <= set(filled)
    for mode in (builtin_default(), shipped, old):
        assert verdicts(mode) == WANTED


def test_the_gate_gives_the_four_verdicts_in_a_normal_and_a_tainted_turn(services):
    effects = {"approve": "allow", "ask": "ask", "refuse": "refuse"}
    for klass, (normal, tainted) in WANTED.items():
        act = Action("t", "write", klass, ("drive:f1",), "x")
        assert services.gate.decide(act, Situation("interactive")).effect == effects[normal], klass
        assert services.gate.decide(act, Situation("interactive", tainted=True)).effect == effects[tainted], klass


def test_the_new_classes_are_in_the_gate_sets_they_belong_to():
    assert "drive_trash" in RAISED_WHEN_TAINTED and not {"drive_restore", "mail_restore"} & RAISED_WHEN_TAINTED
    assert {"drive_trash", "drive_restore", "mail_restore"} <= LEAVES_MACHINE  # refused in a local-only turn
    assert not {"drive_trash", "drive_restore", "mail_restore"} & OUTSIDE  # they send no content


def ctx(services) -> ToolContext:
    return ToolContext(services, TurnState("t1", "", "web", mode="interactive", timezone="Europe/Paris"))


def test_drive_delete_is_a_trash_a_line_on_drive_write_does_not_cover_it_and_a_line_on_drive_trash_does(services):
    act = services.toolbox.action("drive_delete", ctx(services), {"file": "f1"})
    assert (act.klass, act.level, act.targets) == ("drive_trash", "destructive", ("drive:f1",))
    assert services.toolbox.action("drive_update", ctx(services), {"file": "f1", "content": "x"}).klass == "drive_write"
    assert services.toolbox.action("drive_restore", ctx(services), {"file": "f1"}).klass == "drive_restore"
    make(services, "Opened", line("approve", klass="drive_write"))
    inside = Situation("interactive", mode_name="Opened")
    assert services.gate.decide(act, inside).effect == "refuse"  # the default mode's trash verdict: refuse
    update = services.toolbox.action("drive_update", ctx(services), {"file": "f1", "content": "x"})
    assert services.gate.decide(update, inside).effect == "allow"
    make(services, "Trashing", line("ask", klass="drive_trash"))
    assert services.gate.decide(act, Situation("interactive", mode_name="Trashing")).effect == "ask"


# ----------------------------------------------------------------------
# The client against a stand-in of Drive
# ----------------------------------------------------------------------
class Stand:
    def __init__(self, trashed: bool):
        self.file = {"id": "f1", "name": "Budget", "mimeType": "text/plain", "trashed": trashed}
        self.patches: list[dict] = []
        self.queries: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if request.url.path == "/drive/v3/files" and request.method == "GET":
            self.queries.append(request.url.params["q"])
            return httpx.Response(200, json={"files": [self.file]})
        if request.url.path == "/drive/v3/files/f1" and request.method == "GET":
            return httpx.Response(200, json=self.file)
        if request.url.path == "/drive/v3/files/f1" and request.method == "PATCH":
            self.patches.append(json.loads(request.content))
            self.file["trashed"] = self.patches[-1]["trashed"]
            return httpx.Response(200, json=self.file)
        return httpx.Response(404, json={"error": {"message": "no route"}})


def drive_on(stand: Stand) -> Drive:
    transport = httpx.MockTransport(stand.handle)
    return Drive(GoogleAuth("cid", "cs", transport), "rt", transport)


async def test_a_drive_restore_sets_trashed_back_to_false():
    stand = Stand(trashed=True)
    restored = await drive_on(stand).restore("f1")
    assert stand.patches == [{"trashed": False}] and restored["trashed"] is False


async def test_a_drive_restore_of_a_file_that_is_not_in_the_trash_says_so_and_changes_nothing():
    stand = Stand(trashed=False)
    with pytest.raises(DriveError, match="not in the trash"):
        await drive_on(stand).restore("f1")
    assert stand.patches == []


async def test_a_search_lists_the_files_or_the_trash():
    stand = Stand(trashed=True)
    drive = drive_on(stand)
    await drive.search("budget")
    await drive.search("budget", trashed=True)
    assert stand.queries[0].endswith("and trashed = false") and stand.queries[1].endswith("and trashed = true")


# ----------------------------------------------------------------------
# The tools, through the gate
# ----------------------------------------------------------------------
class FakeDrive:
    def __init__(self):
        self.searched: list[tuple[str, bool]] = []
        self.restored: list[str] = []
        self.trashed: list[str] = []

    async def search(self, query, trashed=False):
        self.searched.append((query, trashed))
        return [{"id": "f1", "name": "Budget", "mimeType": "text/plain"}]

    async def restore(self, ident):
        self.restored.append(ident)
        return {"id": ident, "name": "Budget"}

    async def trash(self, ident):
        self.trashed.append(ident)
        return {"id": ident, "name": "Budget"}


@pytest.fixture
def fake(services, monkeypatch):
    found = FakeDrive()
    monkeypatch.setattr(services.connectors, "drive_client", lambda: found)
    return found


async def call(services, name, arguments, tainted=False):
    context = ToolContext(services, TurnState("t1", "", "web", mode="interactive", timezone="Europe/Paris", tainted=tainted))
    return await services.toolbox.call(name, context, arguments, services.toolbox.names)


async def test_the_default_mode_approves_a_restore_refuses_a_trash_and_asks_for_a_modify(services, fake):
    for tainted in (False, True):
        restored = await call(services, "drive_restore", {"file": "f1"}, tainted)
        assert restored.ran and "out of Drive's trash" in restored.text
    assert fake.restored == ["f1", "f1"]
    trashed = await call(services, "drive_delete", {"file": "f1"})
    assert not trashed.ran and trashed.approval is None and fake.trashed == []
    asked = await call(services, "drive_update", {"file": "f1", "content": "x"})
    assert asked.approval is not None and asked.approval.action_class == "drive_write"


async def test_drive_search_can_look_in_the_trash(services, fake):
    assert (await call(services, "drive_search", {"query": "budget"})).ran
    found = await call(services, "drive_search", {"query": "budget", "trashed": True})
    assert "[f1] Budget" in found.text and fake.searched == [("budget", False), ("budget", True)]
