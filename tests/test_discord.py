"""Gate 5: the Discord bot ignores every id but yours, connects with DM intents only, and uses its own token."""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from clara_discord.bot import ClaraBot, split_message
from clara_discord.settings import Settings, SettingsError


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "discord-token").write_text("discord-secret")
    (tmp_path / "clara-token").write_text("clara_1_" + "x" * 43)
    return Settings.load({"CREDENTIALS_DIRECTORY": str(tmp_path), "CLARA_DISCORD_USER_ID": "42",
                          "CLARA_TIMEZONE": "Europe/Paris", "CLARA_DISCORD_STATE": str(tmp_path / "state.json")})


def test_settings_need_an_owner_and_both_credentials(tmp_path):
    with pytest.raises(SettingsError):
        Settings.load({"CLARA_DISCORD_USER_ID": "42"})
    with pytest.raises(SettingsError):
        Settings.load({"CREDENTIALS_DIRECTORY": str(tmp_path), "CLARA_DISCORD_USER_ID": "not-a-number"})


async def test_dm_intents_only(settings):
    bot = ClaraBot(settings)
    intents = bot.intents
    assert intents.dm_messages and not intents.guilds and not intents.guild_messages
    assert not intents.message_content and not intents.members and not intents.presences
    await bot.clara.close()


async def test_every_other_user_is_ignored(settings, monkeypatch):
    bot = ClaraBot(settings)
    asked = []

    async def ask(channel, body):
        asked.append(body)

    monkeypatch.setattr(bot, "ask", ask)

    class DM(discord.DMChannel):
        def __init__(self):  # no Discord connection in a test
            pass

    stranger = SimpleNamespace(author=SimpleNamespace(id=7, bot=False), channel=DM(), attachments=[], content="hi")
    you = SimpleNamespace(author=SimpleNamespace(id=42, bot=False), channel=DM(), attachments=[], content="hi")
    elsewhere = SimpleNamespace(author=SimpleNamespace(id=42, bot=False), channel=object(), attachments=[], content="x")
    await bot.on_message(stranger)
    await bot.on_message(elsewhere)
    assert asked == []
    await bot.on_message(you)
    assert asked and asked[0]["message"] == "hi" and asked[0]["timezone"] == "Europe/Paris"
    await bot.clara.close()


def test_long_answers_are_split_for_discord():
    parts = split_message("word " * 1000)
    assert all(len(part) <= 2000 for part in parts) and len(parts) == 3


class FakeUser:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def send(self, content, view=None):
        message = SimpleNamespace(content=content, view=view, edits=[])

        async def edit(**fields):
            message.edits.append(fields)

        message.edit = edit
        self.sent.append((content, view))
        return message


async def test_approvals_come_with_buttons_except_destructive_ones(settings, monkeypatch):
    bot = ClaraBot(settings)
    user = FakeUser()

    async def owner():
        return user

    monkeypatch.setattr(bot, "owner", owner)
    await bot.show_approval({"id": 1, "level": "write", "summary": "Comment on issue #3", "reason": "default"})
    await bot.show_approval({"id": 2, "level": "destructive", "summary": "Delete notes/old.md", "reason": "default"})
    await bot.show_approval({"id": 1, "level": "write", "summary": "again", "reason": ""})  # shown once
    (first, buttons), (second, none) = user.sent
    assert "#1" in first and [item.custom_id for item in buttons.children] == [
        "clara:approval:1:allow_once", "clara:approval:1:allow_always", "clara:approval:1:refuse_once",
        "clara:approval:1:refuse_always"]
    assert none is None and "web site or with clara-admin" in second

    await bot.deliver({"type": "approval_resolved", "payload": {"approval": {"id": 1, "summary": "Comment", "status": "done"}}})
    assert bot.asked.get(1) is None
    await bot.clara.close()


async def test_events_are_relayed_as_dms(settings, monkeypatch):
    bot = ClaraBot(settings)
    user = FakeUser()

    async def owner():
        return user

    monkeypatch.setattr(bot, "owner", owner)
    await bot.deliver({"type": "reminder", "title": "Reminder", "text": "Call the plumber"})
    await bot.deliver({"type": "alert", "title": "The disk is nearly full", "text": "91% used."})
    await bot.deliver({"type": "server", "state": "running"})  # logged, not sent
    assert [content for content, _ in user.sent] == ["⏰ Call the plumber", "⚠️ **The disk is nearly full**\n91% used."]
    await bot.clara.close()
