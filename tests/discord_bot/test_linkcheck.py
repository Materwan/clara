"""`/linkcheck` on Discord: the request the bot makes, the errors it reads, and what it says about the answer."""

import httpx
import pytest

from clara.discord_bot.backend import ClaraError
from clara.discord_bot.commands import link_check_text
from clara.discord_bot.remote import RemoteBackend
from clara.discord_bot.texts import ENGLISH, FRENCH


def found(**changes) -> dict:
    """The server's answer for a linked account (accountapi.check_link), with what a test changes."""
    answer = {
        "user": {"name": "erwan", "enabled": True},
        "signed_in_as": "erwan",
        "known": True,
        "same_person": True,
        "accounts": ["cli:erwan", "discord:111"],
        "sign_in_required": True,
        "answers": True,
    }
    return {**answer, **changes}


async def test_the_bot_asks_the_server_and_reads_its_answer():
    seen = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["params"] = request.url.path, dict(request.url.params)
        return httpx.Response(200, json=found())

    api = RemoteBackend("http://clara.test", "t", transport=httpx.MockTransport(answer))
    assert await api.check_link(5, 111, "erwan") == found()
    assert seen["path"] == "/v1/accounts/check"
    assert seen["params"] == {"surface": "discord", "user_id": "5", "for_user_id": "111", "clara_user": "erwan"}
    await api.close()


async def test_a_refusal_keeps_its_status_for_the_command():
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Only the owner checks the links of an account"})

    api = RemoteBackend("http://clara.test", "t", transport=httpx.MockTransport(refuse))
    with pytest.raises(ClaraError) as caught:
        await api.check_link(5, 111, "erwan")
    assert caught.value.status == 403 and "owner" in caught.value.detail
    await api.close()


def test_a_linked_account_is_reported_as_answered():
    text = link_check_text(ENGLISH, "Erwan", found())
    assert text.splitlines() == [
        "**Erwan** on Discord, and the Clara user **erwan**",
        "✅ Signed in as **erwan**.",
        "✅ The same person as **erwan** (accounts: `cli:erwan`, `discord:111`).",
        "✅ Clara answers this Discord account.",
    ]


def test_an_account_signed_in_as_someone_else_says_so():
    text = link_check_text(ENGLISH, "Bob", found(signed_in_as="bob", same_person=False, answers=False))
    assert "❌ Signed in as **bob**, not **erwan**." in text
    assert "❌ Clara does not answer this Discord account." in text


def test_an_account_not_signed_in_and_unknown_is_refused():
    text = link_check_text(ENGLISH, "Ghost", found(signed_in_as=None, known=False, same_person=False, answers=False))
    assert "❌ Not signed in to Clara on Discord." in text and "does not know this account yet" in text
    assert text.endswith("❌ Clara does not answer this Discord account.")


def test_a_disabled_user_is_said_and_not_answered():
    text = link_check_text(ENGLISH, "Erwan", found(user={"name": "erwan", "enabled": False}, answers=False))
    assert "is disabled" in text and text.endswith("❌ Clara does not answer this Discord account.")


def test_when_sign_in_is_not_required_it_is_said():
    text = link_check_text(ENGLISH, "Erwan", found(sign_in_required=False))
    assert "Sign-in is not required on Discord here." in text


def test_the_owner_reads_it_in_french_when_the_discord_is_french():
    text = link_check_text(FRENCH, "Erwan", found(signed_in_as=None, known=False, same_person=False, answers=False))
    assert text.startswith("**Erwan** sur Discord, et l'utilisateur Clara **erwan**")
    assert "Pas connecté à Clara sur Discord." in text and "Clara ne répond pas" in text
