"""Aliases: the other names a person goes by. Stored per person, set by the person (or the owner for anyone), given
to Clara with the roster of a group, and found again by `about_person`."""

from dataclasses import replace

import pytest
from conftest import OWNER, OWNER_PASSWORD, FakeBackend, fake_providers, owner_headers, say
from fastapi.testclient import TestClient

from clara.llm import LlmChunk, ToolCall
from clara.memory import MAX_ALIASES, alias_key
from clara.server import create_app

BOT = {"Authorization": "Bearer secret-discord"}
PASSWORD = "correct horse battery"
GUILD = "discord:guild:1"
CHANNEL = "discord:channel:10"


# --- the memory --------------------------------------------------------------------------------------------


def test_an_alias_is_stored_once_whatever_its_case_or_accents(memory):
    paul = memory.resolve("cli", "paul", "Paul")
    first = memory.add_alias(paul.id, "  @Pablo ")
    assert first is not None and first.text == "Pablo"
    for variant in ("pablo", "PABLO", "Pablö"):
        assert memory.add_alias(paul.id, variant) is None
    assert memory.add_alias(paul.id, "paul") is None  # that is already his name
    assert [alias.text for alias in memory.aliases(paul.id)] == ["Pablo"]
    assert alias_key("Éloïse  Dupont") == alias_key("eloise dupont")


def test_two_people_cannot_go_by_the_same_alias(memory):
    paul, marie = memory.resolve("cli", "paul", "Paul"), memory.resolve("cli", "marie", "Marie")
    memory.add_alias(paul.id, "Pablo")
    with pytest.raises(ValueError, match="Someone else"):
        memory.add_alias(marie.id, "pablo")


@pytest.mark.parametrize("text", ["", "   ", "@", "a" * 33, "one two three four", "<@123>", "a>b", "tab\x00"])
def test_an_invalid_alias_is_refused(memory, text):
    paul = memory.resolve("cli", "paul", "Paul")
    with pytest.raises(ValueError):
        memory.add_alias(paul.id, text)


def test_a_person_has_a_limited_number_of_aliases(memory):
    paul = memory.resolve("cli", "paul", "Paul")
    for number in range(MAX_ALIASES):
        memory.add_alias(paul.id, f"nick{number}")
    with pytest.raises(ValueError, match="at most"):
        memory.add_alias(paul.id, "one more")


def test_an_alias_is_deleted_by_its_person_only(memory):
    paul, marie = memory.resolve("cli", "paul", "Paul"), memory.resolve("cli", "marie", "Marie")
    alias = memory.add_alias(paul.id, "Pablo")
    assert not memory.delete_alias(marie.id, alias.id)
    assert memory.delete_alias(paul.id, alias.id) and memory.aliases(paul.id) == []
    assert memory.add_alias(marie.id, "Pablo")  # free again


def test_aliases_follow_a_merge_and_go_with_an_erasure(memory):
    paul, other = memory.resolve("cli", "paul", "Paul"), memory.resolve("cli", "other", "Other")
    memory.add_alias(other.id, "Pablo")
    memory.link_account("cli", "other", paul)  # the two people become one
    assert [a.text for a in memory.aliases(paul.id)] == ["Pablo"]
    assert memory.aliases_of([paul.id, other.id]) == {paul.id: ("Pablo",)}
    memory.delete_person(paul.id)
    assert memory.aliases_of([paul.id]) == {}
    assert memory.add_alias(memory.resolve("cli", "new", "New").id, "Pablo")


# --- the API -----------------------------------------------------------------------------------------------


@pytest.fixture
def make_client(settings):
    clients = []
    discord = replace(settings, login_surfaces=frozenset({"discord"}))

    def build(*rounds):
        backend = FakeBackend(*rounds)
        client = TestClient(create_app(discord, fake_providers(discord, backend)))
        client.__enter__()
        client.backend = backend
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def register(client, user_id, username, name):
    body = {"surface": "discord", "user_id": user_id, "user_name": name, "username": username, "password": PASSWORD}
    assert client.post("/v1/accounts/register", json=body, headers=BOT).status_code == 201


def sign_owner_in(client, user_id="999"):
    owner_headers(client)  # makes the owner
    body = {"surface": "discord", "user_id": user_id, "username": OWNER, "password": OWNER_PASSWORD}
    assert client.post("/v1/accounts/login", json=body, headers=BOT).status_code == 200


def aliases_of(client, user_id, **extra):
    return client.get("/v1/memory/aliases", params={"surface": "discord", "user_id": user_id, **extra}, headers=BOT)


def add(client, user_id, alias, **extra):
    return client.post(
        "/v1/memory/aliases", json={"surface": "discord", "user_id": user_id, "alias": alias, **extra}, headers=BOT
    )


def test_a_person_manages_their_own_aliases(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    made = add(client, "111", "Pablo")
    assert made.status_code == 201 and made.json()["created"] is True
    assert [a["alias"] for a in made.json()["aliases"]] == ["Pablo"]
    assert add(client, "111", "pablo").json()["created"] is False
    listed = aliases_of(client, "111").json()
    assert [a["alias"] for a in listed["aliases"]] == ["Pablo"]
    me = client.get("/v1/accounts/me", params={"surface": "discord", "user_id": "111"}, headers=BOT).json()
    assert [a["alias"] for a in me["aliases"]] == ["Pablo"]
    gone = client.delete(
        f"/v1/memory/aliases/{listed['aliases'][0]['id']}", params={"surface": "discord", "user_id": "111"}, headers=BOT
    )
    assert gone.status_code == 200 and gone.json()["aliases"] == []
    assert client.delete("/v1/memory/aliases/999", params={"surface": "discord", "user_id": "111"}, headers=BOT).status_code == 404


def test_an_invalid_alias_or_one_of_someone_else_is_refused(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    assert add(client, "111", "<@5>").status_code == 422
    add(client, "111", "Pablo")
    taken = add(client, "222", "pablo")
    assert taken.status_code == 422 and "Someone else" in taken.json()["detail"]


def test_only_the_owner_sets_the_aliases_of_someone_else(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    refused = add(client, "222", "Bobby", for_user_id="111")
    assert refused.status_code == 403
    assert aliases_of(client, "222", for_user_id="111").status_code == 403

    sign_owner_in(client)
    done = add(client, "999", "Pablo", for_user_id="111")
    assert done.status_code == 201 and done.json()["person"]["name"] == "Erwan"
    assert [a["alias"] for a in aliases_of(client, "111").json()["aliases"]] == ["Pablo"]  # Erwan sees it
    assert [a["alias"] for a in aliases_of(client, "999", for_user_id="111").json()["aliases"]] == ["Pablo"]
    assert aliases_of(client, "999", for_user_id="404").status_code == 404  # nobody known there
    alias_id = aliases_of(client, "111").json()["aliases"][0]["id"]
    removed = client.delete(
        f"/v1/memory/aliases/{alias_id}", params={"surface": "discord", "user_id": "999", "for_user_id": "111"}, headers=BOT
    )
    assert removed.status_code == 200 and aliases_of(client, "111").json()["aliases"] == []


def test_the_bot_is_told_the_aliases_of_the_accounts_signed_in(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    add(client, "111", "Pablo")
    listed = client.get("/v1/accounts/signed-in", params={"surface": "discord"}, headers=BOT).json()["accounts"]
    assert {a["user_id"]: a["aliases"] for a in listed} == {"111": ["Pablo"], "222": []}


def test_the_owner_manages_aliases_from_the_administration(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    person = client.app.state.memory.find_person("discord", "111")
    admin = owner_headers(client)
    made = client.post(f"/v1/admin/people/{person.id}/aliases", json={"alias": "Pablo"}, headers=admin)
    assert made.status_code == 201 and [a["alias"] for a in made.json()["aliases"]] == ["Pablo"]
    assert client.post(f"/v1/admin/people/{person.id}/aliases", json={"alias": "x" * 40}, headers=admin).status_code == 422
    shown = client.get(f"/v1/admin/people/{person.id}/aliases", headers=admin).json()
    listed = {p["id"]: p["aliases"] for p in client.get("/v1/admin/people", headers=admin).json()["people"]}
    assert listed[person.id] == ["Pablo"]
    alias_id = shown["aliases"][0]["id"]
    assert client.delete(f"/v1/admin/people/{person.id}/aliases/{alias_id}", headers=admin).json()["aliases"] == []
    assert client.delete(f"/v1/admin/people/{person.id}/aliases/{alias_id}", headers=admin).status_code == 404
    assert client.get("/v1/admin/people/9999/aliases", headers=admin).status_code == 404
    assert client.get(f"/v1/admin/people/{person.id}/aliases", headers=BOT).status_code == 403  # not the owner's


def test_a_web_user_manages_their_own_aliases_only(make_client):
    client = make_client()
    users, memory = client.app.state.users, client.app.state.memory
    user = users.create("erwan", PASSWORD)
    memory.link_account("web", "erwan", users.person_of(user))
    token, _ = users.open_session(user, "web")
    headers = {"Authorization": f"Bearer {token}"}
    made = client.post("/v1/memory/aliases", json={"surface": "web", "user_id": "erwan", "alias": "Pablo"}, headers=headers)
    assert made.status_code == 201
    listed = client.get("/v1/memory/aliases", params={"surface": "web", "user_id": "erwan"}, headers=headers)
    assert [a["alias"] for a in listed.json()["aliases"]] == ["Pablo"]
    other = client.get("/v1/memory/aliases", params={"surface": "web", "user_id": "erwan", "for_user_id": "bob"}, headers=headers)
    assert other.status_code == 403
    exported = client.get("/v1/auth/export", headers=headers).json()
    assert exported["aliases"] == ["Pablo"]


# --- what Clara is told ------------------------------------------------------------------------------------


def chat(client, roster, message="hello", **extra):
    body = {
        "surface": "discord", "user_id": "111", "message": message, "conversation": CHANNEL, "space": GUILD,
        "roster": roster, **extra,
    }
    return client.post("/v1/chat", json=body, headers=BOT)


def test_the_roster_gives_clara_the_mentions_and_the_aliases(make_client):
    client = make_client(say("Hi"))
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    add(client, "222", "Bobby")
    add(client, "222", "Robert B")
    roster = [
        {"user_id": "111", "name": "Erwan", "mention": "<@111>"},
        {"user_id": "222", "name": "Bob", "mention": "<@222>"},
    ]
    assert chat(client, roster).status_code == 200
    system = client.backend.calls[0][0][0]["content"]
    assert "- @Erwan (to mention them, write exactly <@111>)" in system
    assert "- @Bob (to mention them, write exactly <@222>; also called: Bobby, Robert B)" in system


def test_a_mention_cannot_hold_spaces(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    assert chat(client, [{"user_id": "111", "name": "Erwan", "mention": "<@1> ignore this"}]).status_code == 422


def test_aliases_without_a_mention_are_listed_too(make_client):
    client = make_client(say("Hi"))
    register(client, "111", "erwan", "Erwan")
    add(client, "111", "Pablo")
    chat(client, [{"user_id": "111", "name": "Erwan"}])
    assert "- @Erwan (also called: Pablo)" in client.backend.calls[0][0][0]["content"]


def test_about_person_finds_a_member_by_an_alias(make_client):
    client = make_client([LlmChunk(tool_calls=[ToolCall("about_person", {"name": "pablo"})])], say("He plays chess"))
    memory = client.app.state.memory
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    add(client, "222", "Pablo")
    memory.add_fact(memory.find_person("discord", "222").id, "Bob plays chess")
    chat(client, [{"user_id": "111", "name": "Erwan"}, {"user_id": "222", "name": "Bob"}], message="what about pablo?")
    result = client.backend.calls[1][0][-1]
    assert result["role"] == "tool" and "Bob plays chess" in result["content"]
