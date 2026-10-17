"""Gate 1 foundations: schema versions, the instance lock, the secrets store, tokens, fallbacks."""

from __future__ import annotations

import dataclasses
import importlib
import sqlite3

import pytest

from clara.agent.model import ModelTimeout
from clara.agent.turn import ChatRequest
from clara.db import AlreadyRunning, InstanceLock, SchemaTooNew, migrate
from clara.llm import LlmError, NoModel
from clara.secrets import KeyMaterial, SecretError, SecretsStore, WrongKey
from clara.settings import SettingsError, load_settings
from conftest import parse_sse
from fakes import Hang

migrate_module = importlib.import_module("clara.db.migrate")


def test_an_old_build_refuses_a_newer_database(tmp_path):
    path = tmp_path / "clara.db"
    migrate(path, tmp_path / "backups")
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {migrate_module.LATEST_VERSION + 1}")
    with pytest.raises(SchemaTooNew):
        migrate(path, tmp_path / "backups")


def test_a_migration_takes_a_backup_first(tmp_path, monkeypatch):
    path = tmp_path / "clara.db"
    migrate(path, tmp_path / "backups")
    real = migrate_module.migrations()
    latest = real[-1].version
    extra = migrate_module.Migration(latest + 1, f"{latest + 1:04d}_test.sql", "CREATE TABLE added (x INTEGER);")
    monkeypatch.setattr(migrate_module, "migrations", lambda: [*real, extra])
    monkeypatch.setattr(migrate_module, "LATEST_VERSION", latest + 1)
    before, after, backup = migrate(path, tmp_path / "backups")
    assert (before, after) == (latest, latest + 1)
    assert backup is not None and backup.exists()
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == latest


def test_two_servers_cannot_share_a_data_folder(tmp_path):
    first = InstanceLock(tmp_path / "run" / "clara.lock")
    first.acquire()
    with pytest.raises(AlreadyRunning):
        InstanceLock(tmp_path / "run" / "clara.lock").acquire()
    first.release()
    InstanceLock(tmp_path / "run" / "clara.lock").acquire()


def test_secrets_raw_key_passphrase_wrong_key_and_rotation(services):
    db = services.db
    db.execute("DELETE FROM secrets_meta")
    passphrase = KeyMaterial.parse("correct horse battery staple", "test")
    store = SecretsStore(db, passphrase)
    store.set("gemini_api_key", "AIza-secret-value")
    meta = db.one("SELECT kdf, n, salt FROM secrets_meta")
    assert meta["kdf"] == "scrypt" and meta["n"] == 2**17 and len(meta["salt"]) == 16
    assert SecretsStore(db, passphrase).get("gemini_api_key") == "AIza-secret-value"
    with pytest.raises(WrongKey):
        SecretsStore(db, KeyMaterial.parse("another passphrase entirely", "test"))
    with pytest.raises(SecretError):
        KeyMaterial.parse("short", "test")
    raw = KeyMaterial.parse("cd" * 32, "test")
    assert store.rotate(raw) == 1
    assert SecretsStore(db, raw).get("gemini_api_key") == "AIza-secret-value"
    with pytest.raises(WrongKey):
        SecretsStore(db, passphrase)
    raw_row = db.one("SELECT ciphertext FROM secrets WHERE name = 'gemini_api_key'")
    assert b"AIza" not in raw_row["ciphertext"]


def test_secret_values_never_come_back_from_the_api_listing(services):
    services.secrets.set("mistral_api_key", "super-secret")
    listing = services.secrets.status()
    assert {"name": "mistral_api_key"}.items() <= next(i for i in listing if i["name"] == "mistral_api_key").items()
    assert "super-secret" not in repr(listing)


async def test_the_next_provider_answers_when_the_first_fails(client, services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    services.runtime.set("fallbacks", ["pc:backup"])
    llm.by_model["local:fake"] = [LlmError("busy", 503)]
    llm.by_model["pc:backup"] = ["From the PC."]
    response = await client.post("/v1/chat/stream", json={"message": "hi"})
    events = parse_sse(response.text)
    assert any(event["type"] == "retrying" for event in events)
    assert events[-1]["reply"] == "From the PC." and events[-1]["model"] == "pc:backup"


async def test_payment_required_moves_on_and_alerts(client, services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    services.runtime.set("fallbacks", ["pc:backup"])
    llm.by_model["local:fake"] = [LlmError("pay", 402)]
    llm.by_model["pc:backup"] = ["Still here."]
    response = await client.post("/v1/chat", json={"message": "hi"})
    assert response.json()["reply"] == "Still here."
    assert "provider_payment:local" in services.alerts.active


def test_a_local_model_never_falls_back_to_a_hosted_one(services):
    services.secrets.set("gemini_api_key", "k")
    services.runtime.set("providers_enabled", ["local", "pc", "gemini"])
    services.runtime.set("fallbacks", ["gemini:gemini-2.5-flash", "pc:other"])
    choice = services.router.choose("local:fake")
    assert [option.ref for option in choice.fallbacks] == ["pc:other"]
    hosted = services.router.choose("gemini:gemini-2.5-flash")
    assert hosted.local is False
    with pytest.raises(NoModel):
        services.router.choose("gemini:gemini-2.5-flash", local_only=True)


async def test_a_local_only_conversation_refuses_a_hosted_model(client, services):
    services.secrets.set("gemini_api_key", "k")
    services.runtime.set("providers_enabled", ["local", "gemini"])
    services.runtime.set("default_model", "gemini:gemini-2.5-flash")
    response = await client.post("/v1/chat", json={"message": "hi", "local_only": True})
    assert response.status_code == 503
    assert "local-only" in response.json()["error"]["message"]


async def test_web_sign_in_needs_the_header_and_sets_a_strict_cookie(services, token):
    import httpx

    from clara.app import create_app

    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://clara.test") as browser:
        refused = await browser.post("/v1/auth/web-login", json={"token": token})
        assert refused.status_code == 400
        signed = await browser.post("/v1/auth/web-login", json={"token": token}, headers={"X-Clara-Web": "1"})
        assert signed.status_code == 200
        cookie = signed.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
        without_header = await browser.get("/v1/conversations")
        assert without_header.status_code == 401  # the cookie alone is never enough
        with_header = await browser.get("/v1/conversations", headers={"X-Clara-Web": "1"})
        assert with_header.status_code == 200
        services.tokens.revoke("tester")
        assert (await browser.get("/v1/conversations", headers={"X-Clara-Web": "1"})).status_code == 401


async def test_repeated_wrong_tokens_block_the_address(services):
    import httpx

    from clara.app import create_app

    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test") as stranger:
        for _ in range(10):
            await stranger.get("/v1/conversations", headers={"Authorization": "Bearer clara_1_" + "x" * 43})
        blocked = await stranger.get("/v1/conversations", headers={"Authorization": "Bearer nope"})
        assert blocked.status_code == 429


def test_settings_refuse_secrets_in_the_environment_but_not_the_key_file(tmp_path):
    base = {"CLARA_DATA_DIR": str(tmp_path), "CLARA_TIMEZONE": "Europe/Paris"}
    with pytest.raises(SettingsError) as refused:
        load_settings({**base, "CLARA_GEMINI_API_KEY": "abc", "CLARA_RESTIC_PASSWORD": "x"}, use_file=False)
    assert "CLARA_GEMINI_API_KEY" in str(refused.value) and "CLARA_RESTIC_PASSWORD" in str(refused.value)
    key_file = tmp_path / "key"
    settings = load_settings({**base, "CLARA_SECRET_KEY_FILE": str(key_file)}, use_file=False)
    assert settings.secret_key_file == key_file
    env_file = tmp_path / ".env"
    env_file.write_text("CLARA_SECRET_KEY=not-here\n")
    with pytest.raises(SettingsError, match="must not be written in the .env file"):
        load_settings(base, env_file)


async def test_a_silent_model_hands_over_to_the_next(services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    services.runtime.set("fallbacks", ["pc:backup"])
    services.agent.runner.first_token_timeout = 0.2
    llm.by_model["local:fake"] = [Hang(5)]
    llm.by_model["pc:backup"] = ["Awake."]
    assert await services.agent.answer(ChatRequest(message="hi", client="cli")) == "Awake."


async def test_a_spent_answer_budget_does_not_hand_over(services, llm):
    services.runtime.set("providers_enabled", ["local", "pc"])
    services.runtime.set("fallbacks", ["pc:backup"])
    services.agent.runner.first_token_timeout = 0.2
    services.agent.settings = dataclasses.replace(services.agent.settings, answer_budget=0.2)
    llm.by_model["local:fake"] = [Hang(5)]
    llm.by_model["pc:backup"] = ["Should not answer."]
    with pytest.raises(ModelTimeout):
        await services.agent.answer(ChatRequest(message="hi", client="cli"))
    assert not any(call["model"] == "pc:backup" for call in llm.seen)
