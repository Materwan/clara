"""Dictation into conversations, per client: `POST /v1/dictation`, the per-client settings and the new-conversation action.

The transcription is the scripted `FakeTranscriber` (tests/test_dictation.py covers the route's limits); the model is `FakeLLM`."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from importlib import resources

import httpx
import pytest

from clara.api.middleware import MEGABYTE, limit_for
from clara.app import create_app
from clara.db.migrate import LATEST_VERSION, migrate
from clara.dictation import Transcript
from clara.gate import Line
from clara.tools.base import Tool, fixed
from fakes import calls

AUDIO = b"RIFF....fake audio"
TITLE = re.compile(r"^Dictation · phone · \d{1,2} [A-Z][a-z]{2} \d\d:\d\d$")


async def dictate(client, who="phone", **query):
    return await client.post("/v1/dictation", params={"client": who, **query}, content=AUDIO)


async def settings(client, who="phone", **body):
    return await client.put(f"/v1/dictation/clients/{who}", json=body)


async def settled(services) -> None:
    """Wait until every Run is over, and the background jobs they started."""
    for _ in range(300):
        if not services.agent.runs.active:
            break
        await asyncio.sleep(0.01)
    await services.agent.drain_background()


def as_token(services, name: str, kind: str) -> httpx.AsyncClient:
    token = services.tokens.create(name, kind)[1]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)),
        base_url="http://clara.test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------------------------- off: review and send
async def test_without_automatic_sending_the_text_comes_back_and_nothing_is_sent(client, services, transcriber, llm):
    transcriber.replies.append(Transcript("  Bonjour Clara ", 3.0))
    answer = (await dictate(client)).json()
    assert answer["text"] == "Bonjour Clara" and answer["seconds"] == 3.0 and answer["sent"] is False and "running" not in answer
    made = services.conversations.get(answer["conversation"])
    assert made is not None and services.conversations.read_back(made.id) == [] and not services.agent.runs.active
    assert llm.seen == []


async def test_a_first_dictation_makes_a_titled_tainted_current_conversation_and_the_next_one_reuses_it(client, services, transcriber):
    first = (await dictate(client)).json()["conversation"]
    made = services.conversations.get(first)
    assert TITLE.match(made.title) and made.client == "phone" and made.project_id is None and made.kind == "chat"
    assert services.conversations.is_tainted(first) and services.conversations.is_dictation(first)
    assert (await client.get("/v1/dictation/clients/phone")).json()["conversation"] == first
    assert (await dictate(client)).json()["conversation"] == first
    assert [c["id"] for c in (await client.get("/v1/conversations")).json()["conversations"]] == [first]  # one, not two
    other = (await dictate(client, "island")).json()["conversation"]  # each client has its own
    assert other != first and services.conversations.get(other).title.startswith("Dictation · island · ")


async def test_the_title_stays_when_the_first_exchange_would_have_titled_it(client, services, transcriber, llm):
    await settings(client, auto_send=True)
    llm.replies = ["Hello.", "A new title from the first message"]
    conversation = (await dictate(client)).json()["conversation"]
    await settled(services)
    assert TITLE.match(services.conversations.get(conversation).title)
    renamed = await client.patch(f"/v1/conversations/{conversation}", json={"title": "My notes"})
    assert renamed.json()["title"] == "My notes"


async def test_silence_sends_nothing_and_makes_nothing(client, services, transcriber, llm):
    await settings(client, auto_send=True)
    transcriber.replies.append(Transcript("   ", 1.5))
    answer = (await dictate(client)).json()
    assert answer == {"text": "", "seconds": 1.5, "conversation": None, "sent": False}
    assert services.conversations.listing() == [] and llm.seen == [] and not services.agent.runs.active
    assert (await client.get("/v1/dictation/clients/phone")).json()["conversation"] is None


# ---------------------------------------------------------------------------------------------- on: sent at once
async def test_automatic_sending_runs_the_turn_with_no_client_connected(client, services, transcriber, llm):
    """The locked screen: the request is over and nobody follows the run; the conversation still gets the question and the answer."""
    await settings(client, auto_send=True)
    transcriber.replies.append(Transcript("What time is it", 2.0))
    llm.replies = ["It is noon."]
    answer = (await dictate(client)).json()
    assert answer["sent"] is True and answer["running"] is True and answer["text"] == "What time is it"
    await settled(services)
    stored = services.conversations.read_back(answer["conversation"])
    assert [(m["role"], m["content"]) for m in stored] == [("user", "What time is it"), ("assistant", "It is noon.")]
    assert services.conversations.get(answer["conversation"]).client == "phone"
    assert (await client.get(f"/v1/conversations/{answer['conversation']}/run")).status_code == 404  # over


async def test_the_run_a_dictation_starts_is_the_one_a_client_follows(client, services, transcriber, llm):
    let_go = asyncio.Event()

    async def hold(context) -> str:
        await let_go.wait()
        return "released"

    services.toolbox.add(Tool("hold", "h", hold, fixed("read", "read", "ws:x", "wait"), {}, ()))
    await settings(client, auto_send=True)
    llm.replies = [calls(("hold", {})), "Done."]
    conversation = (await dictate(client)).json()["conversation"]
    run = services.agent.runs.of(conversation)  # what GET /v1/conversations/{id}/run follows
    assert run is not None and run.question == "bonjour"
    let_go.set()
    await settled(services)
    assert services.conversations.read_back(conversation)[-1]["content"] == "Done."


async def test_dictating_while_clara_answers_is_a_409_before_transcribing(client, services, transcriber, llm):
    let_go = asyncio.Event()

    async def hold(context) -> str:
        await let_go.wait()
        return "released"

    services.toolbox.add(Tool("hold", "h", hold, fixed("read", "read", "ws:x", "wait"), {}, ()))
    await settings(client, auto_send=True)
    llm.replies = [calls(("hold", {})), "Done."]
    conversation = (await dictate(client)).json()["conversation"]
    assert services.agent.runs.of(conversation) is not None
    busy = await dictate(client)
    assert busy.status_code == 409 and len(transcriber.calls) == 1  # the second audio was not even transcribed
    assert (await dictate(client, conversation=conversation)).status_code == 409
    let_go.set()
    await settled(services)


async def test_a_dictation_is_a_typed_message_but_for_the_taint(client, services, transcriber, llm):
    await settings(client, auto_send=True)
    llm.replies = ["Ok."]
    conversation = (await dictate(client)).json()["conversation"]
    await settled(services)
    assert services.agent.starts_tainted(conversation) and services.conversations.get(conversation).archived_at is None


# ---------------------------------------------------------------------------------------------- an existing conversation
async def test_dictating_into_an_existing_conversation_taints_it_from_then_on(client, services, transcriber, llm):
    plain = services.conversations.create("web").id
    assert not services.conversations.is_tainted(plain)
    answer = (await dictate(client, "island", conversation=plain)).json()
    assert answer["conversation"] == plain and answer["sent"] is False
    assert services.conversations.is_tainted(plain)
    listed = {c["id"]: c for c in (await client.get("/v1/conversations")).json()["conversations"]}
    assert listed[plain]["dictation"] is True
    assert (await client.get("/v1/dictation/clients/island")).json()["conversation"] is None  # it did not become the current one


async def test_an_unknown_or_archived_conversation_is_refused_before_transcribing(client, services, transcriber):
    assert (await dictate(client, conversation="c" + "0" * 16)).status_code == 404
    kept = services.conversations.create("web").id
    await client.patch(f"/v1/conversations/{kept}", json={"archived": True})
    assert (await dictate(client, conversation=kept)).status_code == 409
    assert (await dictate(client, conversation="nope")).status_code == 422
    assert transcriber.calls == [] and not services.conversations.is_tainted(kept)


async def test_the_list_marks_dictation_conversations_only(client, services, transcriber):
    plain = services.conversations.create("web").id
    made = (await dictate(client)).json()["conversation"]
    listed = {c["id"]: c["dictation"] for c in (await client.get("/v1/conversations")).json()["conversations"]}
    assert listed == {plain: False, made: True}
    assert (await client.get(f"/v1/conversations/{made}")).json()["dictation"] is True


async def test_an_archived_current_conversation_is_replaced_by_a_new_one(client, services, transcriber):
    first = (await dictate(client)).json()["conversation"]
    await client.patch(f"/v1/conversations/{first}", json={"archived": True})
    second = (await dictate(client)).json()["conversation"]
    assert second != first


# ---------------------------------------------------------------------------------------------- the new-conversation action
async def test_a_new_dictation_conversation_leaves_the_previous_one_and_takes_the_clients_mode_and_packs(client, services, transcriber):
    services.modes.create("Loose", ["github"], [Line("approve", klass="vault_write")])
    assert (await settings(client, mode="Loose", packs=["mail"])).status_code == 200
    before = (await dictate(client)).json()["conversation"]
    kept = (before, services.conversations.get(before).title, services.conversations.get(before).mode)
    made = await client.post("/v1/dictation/clients/phone/conversation")
    assert made.status_code == 201
    new = made.json()["conversation"]
    assert new != before and TITLE.match(made.json()["title"])
    found = services.conversations.get(new)
    assert found.mode == "Loose" and set(found.active_packs()) == {"mail", "github"}
    assert services.conversations.is_dictation(new) and found.project_id is None
    mode = (await client.get(f"/v1/conversations/{new}/mode")).json()
    assert mode["mode"] == "Loose" and mode["source"] == "own"
    again = services.conversations.get(before)
    assert (again.id, again.title, again.mode) == kept and services.conversations.is_dictation(before)
    assert (await client.get("/v1/dictation/clients/phone")).json()["conversation"] == new
    assert (await dictate(client)).json()["conversation"] == new  # the dictations that follow go to it


async def test_any_token_may_make_a_dictation_conversation_but_the_mode_stays_the_clients(services, transcriber):
    async with as_token(services, "script", "script") as script:
        made = await script.post("/v1/dictation/clients/cli/conversation")
        assert made.status_code == 201 and services.conversations.is_dictation(made.json()["conversation"])


# ---------------------------------------------------------------------------------------------- the settings
async def test_the_five_clients_have_defaults_and_a_change_is_kept(client, services):
    listed = (await client.get("/v1/dictation/clients")).json()["clients"]
    assert [c["client"] for c in listed] == ["web", "island", "phone", "cli", "discord"]
    assert all(c["auto_send"] is False and c["mode"] is None and c["conversation"] is None and "web" in c["packs"] for c in listed)
    changed = (await settings(client, "island", auto_send=True)).json()
    assert changed["auto_send"] is True and changed["mode"] is None
    services.modes.create("Loose", [], [])
    assert (await settings(client, "island", mode="Loose", packs=["web", "mail"])).json()["mode"] == "Loose"
    kept = (await client.get("/v1/dictation/clients/island")).json()
    assert kept["auto_send"] is True and kept["mode"] == "Loose" and kept["packs"] == ["web", "mail"]
    assert (await settings(client, "island", mode=None)).json()["mode"] is None  # null: the default mode
    assert (await settings(client, "island", packs=["web"])).json()["mode"] is None  # absent: unchanged
    assert (await client.get("/v1/dictation/clients/web")).json()["auto_send"] is False  # the others did not move


async def test_the_settings_are_checked(client, services):
    assert (await settings(client, mode="Nope")).status_code == 422
    assert (await settings(client, mode="default")).status_code == 422  # the default mode is chosen with null
    assert (await settings(client, packs=["nothing"])).status_code == 422
    assert (await settings(client, packs=["discord_thread"])).status_code == 422  # not a chat pack
    assert (await settings(client, auto_send="yes please")).status_code == 422
    assert (await client.get("/v1/dictation/clients/phone")).json()["auto_send"] is False


@pytest.mark.parametrize("kind", ["script", "discord"])
async def test_a_script_or_the_bot_cannot_change_settings_but_can_dictate(services, transcriber, kind):
    async with as_token(services, "weak", kind) as weak:
        refused = await settings(weak, auto_send=True)
        assert refused.status_code == 403 and "answer given in advance" in refused.json()["error"]["message"]
        assert (await settings(weak, mode="x")).status_code == 403
        assert (await weak.get("/v1/dictation/clients")).status_code == 200
        assert (await dictate(weak)).status_code == 200
    assert services.dictation.clients.get("phone")["auto_send"] is False


async def test_an_unknown_client_is_a_422(client, transcriber):
    assert (await dictate(client, "tablet")).status_code == 422
    assert (await client.post("/v1/dictation", content=AUDIO)).status_code == 422  # no client at all
    assert (await client.get("/v1/dictation/clients/tablet")).status_code == 422
    assert (await settings(client, "tablet", auto_send=True)).status_code == 422
    assert (await client.post("/v1/dictation/clients/tablet/conversation")).status_code == 422
    assert transcriber.calls == []


async def test_the_dictation_route_needs_a_token(services, transcriber):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url="http://clara.test") as anonymous:
        assert (await anonymous.post("/v1/dictation?client=phone", content=AUDIO)).status_code == 401
        assert (await anonymous.get("/v1/dictation/clients")).status_code == 401


def test_the_audio_may_be_as_big_as_for_the_transcription_route():
    assert limit_for("/v1/dictation") == limit_for("/v1/dictation/transcribe") == 100 * MEGABYTE


# ---------------------------------------------------------------------------------------------- deletion
async def test_deleting_the_conversation_clears_the_current_and_the_next_dictation_makes_a_new_one(client, services, transcriber):
    first = (await dictate(client)).json()["conversation"]
    assert (await client.delete(f"/v1/conversations/{first}")).status_code == 200
    assert (await client.get("/v1/dictation/clients/phone")).json()["conversation"] is None
    second = (await dictate(client)).json()["conversation"]
    assert second != first and services.conversations.is_dictation(second)


async def test_deleting_a_mode_a_client_uses_asks_to_confirm_and_falls_back_to_the_default(client, services):
    services.modes.create("Loose", [], [])
    await settings(client, "cli", mode="Loose")
    assert services.modes.uses("Loose")["dictation_clients"] == 1
    asked = await client.delete("/v1/modes/Loose", params={"conversations": 0, "automation_conversations": 0, "automations": 0, "projects": 0})
    assert asked.status_code == 409 and "1 dictation client(s)" in asked.json()["error"]["message"]
    done = await client.delete(
        "/v1/modes/Loose",
        params={"conversations": 0, "automation_conversations": 0, "automations": 0, "projects": 0, "dictation_clients": 1},
    )
    assert done.status_code == 200 and done.json()["moved"]["dictation_clients"] == 1
    assert (await client.get("/v1/dictation/clients/cli")).json()["mode"] is None
    services.modes.create("Other", [], [])  # nothing uses it: the four numbers are still all that is asked
    assert "dictation_clients" not in services.modes.uses("Other")


# ---------------------------------------------------------------------------------------------- tainted: the rule, and no approval by voice
@pytest.fixture
def probe(services) -> list[dict]:
    """A tool of the class `fetch_other`: it runs with no question in a normal conversation and asks once tainted."""
    ran: list[dict] = []
    services.toolbox.add(Tool("probe", "p", lambda context, **a: ran.append(a) or "done", fixed("fetch_other", "write", "web:example.org", "probe")))
    object.__setattr__(services.settings, "approval_wait", 0.2)
    services.approvals.followup_delay = 0.0
    return ran


async def test_an_outside_write_asks_in_a_dictation_conversation_even_with_automatic_sending(client, services, transcriber, llm, probe):
    plain = services.conversations.create("web").id
    llm.replies = [calls(("probe", {})), "Done."]
    await client.post("/v1/chat", json={"message": "go", "conversation": plain})
    assert probe == [{}]  # a normal conversation runs it with no question

    await settings(client, auto_send=True)
    llm.replies = [calls(("probe", {})), "I asked.", "Done."]
    conversation = (await dictate(client)).json()["conversation"]
    for _ in range(200):
        if services.approvals.waiting(conversation):
            break
        await asyncio.sleep(0.01)
    waiting = services.approvals.waiting(conversation)
    assert len(waiting) == 1 and waiting[0].status == "pending" and probe == [{}]  # still the first one: this one asks
    # a dictated message never answers it: with automatic sending it is refused while Clara answers, without it nothing is sent
    assert (await dictate(client)).status_code == 409
    await settings(client, auto_send=False)
    assert (await dictate(client, conversation=conversation)).json()["sent"] is False
    assert [a.status for a in services.approvals.waiting(conversation)] == ["pending"] and probe == [{}]
    web = services.tokens.create("browser", "web")
    await services.approvals.decide(waiting[0].id, "refuse_once", web[0])
    await settled(services)
    await asyncio.gather(*services.approvals._tasks)
    assert probe == [{}] and services.agent.starts_tainted(conversation)


# ---------------------------------------------------------------------------------------------- the migration
def _real(version: int, destination):
    destination.mkdir()
    for item in resources.files("clara.db.migrations").iterdir():
        if re.match(r"^\d{4}_", item.name) and int(item.name[:4]) <= version:
            (destination / item.name).write_bytes(item.read_bytes())
    return destination


def test_migration_0039_on_an_empty_database(tmp_path):
    assert LATEST_VERSION >= 39
    path = tmp_path / "clara.db"
    assert migrate(path, tmp_path / "backups")[:2] == (0, LATEST_VERSION)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        names = [row[1] for row in db.execute("PRAGMA table_info(dictation_clients)")]
        assert names == ["client", "conversation_id", "auto_send", "mode", "packs", "updated_at"]
        db.execute("INSERT INTO dictation_clients (client, updated_at) VALUES ('phone', 'now')")
        assert db.execute("SELECT auto_send, mode, packs, conversation_id FROM dictation_clients").fetchall() == [(0, None, "[]", None)]
        for bad in ("INSERT INTO dictation_clients (client, updated_at) VALUES ('tablet', 'now')",
                    "INSERT INTO dictation_clients (client, auto_send, updated_at) VALUES ('web', 2, 'now')",
                    "INSERT INTO dictation_clients (client, conversation_id, updated_at) VALUES ('cli', 'cmissing', 'now')"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(bad)


def test_migration_0039_on_a_database_at_0038_with_rows(tmp_path):
    source = _real(38, tmp_path / "old")
    path = tmp_path / "clara.db"
    assert migrate(path, tmp_path / "backups", source)[1] == 38
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO conversations (id, title, titled_by, client, kind, created_at, updated_at)"
            " VALUES ('c0123456789abcdef', 'Old', 'you', 'web', 'chat', 'then', 'then')"
        )
    before, after, backup = migrate(path, tmp_path / "backups")
    assert (before, after) == (38, LATEST_VERSION) and backup is not None
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        assert db.execute("SELECT title FROM conversations").fetchall() == [("Old",)]
        assert db.execute("SELECT COUNT(*) FROM dictation_clients").fetchone() == (0,)
        db.execute("INSERT INTO dictation_clients (client, conversation_id, updated_at) VALUES ('web', 'c0123456789abcdef', 'now')")
        db.execute("DELETE FROM conversations")  # the conversation's deletion clears the current one
        assert db.execute("SELECT conversation_id FROM dictation_clients").fetchall() == [(None,)]
