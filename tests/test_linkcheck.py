"""`/linkcheck`: the owner checks whether a Discord account speaks as a Clara user (and is the same person)."""

from dataclasses import replace

import pytest
from conftest import OWNER, OWNER_PASSWORD, FakeBackend, fake_providers, owner_headers
from fastapi.testclient import TestClient

from clara.discord_bot.backend import ClaraError
from clara.discord_bot.local import LocalBackend
from clara.server import create_app

BOT = {"Authorization": "Bearer secret-discord"}
PASSWORD = "correct horse battery"


@pytest.fixture
def make_client(settings):
    clients = []

    def build(*rounds):
        discord = replace(settings, login_surfaces=frozenset({"discord"}))
        client = TestClient(create_app(discord, fake_providers(discord, FakeBackend(*rounds))))
        client.__enter__()
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


def check(client, for_user_id, clara_user, asker="999"):
    params = {"surface": "discord", "user_id": asker, "for_user_id": for_user_id, "clara_user": clara_user}
    return client.get("/v1/accounts/check", params=params, headers=BOT)


def test_the_owner_sees_a_linked_account_and_that_clara_answers_it(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    sign_owner_in(client)
    found = check(client, "111", "erwan").json()
    assert found["signed_in_as"] == "erwan" and found["known"] is True
    assert found["same_person"] is True and found["answers"] is True
    assert found["sign_in_required"] is True and found["user"] == {"name": "erwan", "enabled": True}
    assert "discord:111" in found["accounts"]


def test_an_account_signed_in_as_someone_else_is_not_linked(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    sign_owner_in(client)
    found = check(client, "222", "erwan").json()
    assert found["signed_in_as"] == "bob" and found["same_person"] is False and found["answers"] is False


def test_an_account_that_is_not_signed_in_is_reported_not_refused(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    sign_owner_in(client)
    found = check(client, "333", "erwan").json()
    assert found["signed_in_as"] is None and found["known"] is False and found["answers"] is False


def test_a_disabled_user_signs_its_accounts_out_and_is_not_answered(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    sign_owner_in(client)
    client.app.state.users.set_disabled("erwan", True)  # signs its accounts out too
    found = check(client, "111", "erwan").json()
    assert found["signed_in_as"] is None and found["same_person"] is True
    assert found["answers"] is False and found["user"]["enabled"] is False


def test_only_the_owner_checks_links(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    register(client, "222", "bob", "Bob")
    refused = check(client, "111", "erwan", asker="222")
    assert refused.status_code == 403 and "owner" in refused.json()["detail"]


def test_the_asker_must_be_signed_in(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    assert check(client, "111", "erwan", asker="555").status_code == 403


def test_an_unknown_clara_user_is_a_404(make_client):
    client = make_client()
    sign_owner_in(client)
    assert check(client, "111", "ghost").status_code == 404


async def test_the_built_in_bot_gets_the_same_answer_without_http(make_client):
    client = make_client()
    register(client, "111", "erwan", "Erwan")
    sign_owner_in(client)
    backend = LocalBackend(client.app)
    found = await backend.check_link(999, 111, "erwan")
    assert found["signed_in_as"] == "erwan" and found["same_person"] is True and found["answers"] is True
    with pytest.raises(ClaraError) as refused:
        await backend.check_link(111, 111, "erwan")  # erwan is not the owner
    assert refused.value.status == 403
