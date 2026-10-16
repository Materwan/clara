"""Gate 5: the Discord bot lives in your private server: it answers one user id in one channel, ignores DMs and every
other server, has no member list, sends each kind of message to its own channel, and uses its own token."""

from __future__ import annotations

import fcntl
from types import SimpleNamespace

import discord
import pytest

from clara_discord import bot as bot_module
from clara_discord.bot import ChannelError, ClaraBot, split_message
from clara_discord.settings import CHANNEL_SETTINGS, Settings, SettingsError

GENERAL, NOTIFICATION, APPROVAL, ALERTS = 101, 102, 103, 104
GUILD = 900


def env_for(tmp_path, **more) -> dict[str, str]:
    (tmp_path / "discord-token").write_text("discord-secret")
    (tmp_path / "clara-token").write_text("clara_1_" + "x" * 43)
    return {"CREDENTIALS_DIRECTORY": str(tmp_path), "CLARA_DISCORD_USER_ID": "42", "CLARA_TIMEZONE": "Europe/Paris",
            "CLARA_DISCORD_STATE": str(tmp_path / "state.json"),
            "CLARA_DISCORD_CHANNEL_GENERAL": str(GENERAL), "CLARA_DISCORD_CHANNEL_NOTIFICATION": str(NOTIFICATION),
            "CLARA_DISCORD_CHANNEL_APPROVAL": str(APPROVAL), "CLARA_DISCORD_CHANNEL_ALERTS": str(ALERTS), **more}


@pytest.fixture
def settings(tmp_path):
    return Settings.load(env_for(tmp_path))


class FakeChannel:
    """A text channel that remembers what was sent to it."""

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[tuple[str, object]] = []

    async def send(self, content=None, view=None, file=None):
        message = SimpleNamespace(content=content, view=view, edits=[])

        async def edit(**fields):
            message.edits.append(fields)

        message.edit = edit
        self.sent.append((content, view))
        return message

    def typing(self):
        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Typing()


@pytest.fixture
async def bot(settings):
    found = ClaraBot(settings)
    found.channels = {name: FakeChannel(channel_id) for name, channel_id in settings.channels.items()}
    found.guild_id = GUILD
    yield found
    await found.clara.close()


def texts(channel: FakeChannel) -> list[str]:
    return [content for content, _ in channel.sent]


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def test_settings_need_an_owner_four_channels_and_both_credentials(tmp_path):
    with pytest.raises(SettingsError):
        Settings.load({"CLARA_DISCORD_USER_ID": "42"})
    with pytest.raises(SettingsError):
        Settings.load({"CREDENTIALS_DIRECTORY": str(tmp_path), "CLARA_DISCORD_USER_ID": "not-a-number"})
    with pytest.raises(SettingsError):  # a good owner and credentials, but no channels
        Settings.load({k: v for k, v in env_for(tmp_path).items() if "CHANNEL" not in k})


def test_every_missing_channel_is_named_at_once(tmp_path):
    env = env_for(tmp_path, CLARA_DISCORD_CHANNEL_APPROVAL="general", CLARA_DISCORD_CHANNEL_ALERTS="")
    with pytest.raises(SettingsError) as error:
        Settings.load(env)
    assert "CLARA_DISCORD_CHANNEL_APPROVAL" in str(error.value) and "CLARA_DISCORD_CHANNEL_ALERTS" in str(error.value)
    assert "CLARA_DISCORD_CHANNEL_GENERAL" not in str(error.value)


def test_the_channels_are_read_by_kind(settings):
    assert settings.channels == {"general": GENERAL, "notification": NOTIFICATION, "approval": APPROVAL, "alerts": ALERTS}
    assert set(CHANNEL_SETTINGS) == set(settings.channels)


# ----------------------------------------------------------------------
# Intents and who is answered
# ----------------------------------------------------------------------
async def test_guild_intents_only_and_no_direct_messages(bot):
    intents = bot.intents
    assert intents.guilds and intents.guild_messages and intents.message_content
    assert not intents.dm_messages and not intents.dm_reactions and not intents.dm_typing
    assert not intents.members and not intents.presences


async def test_only_you_in_general_are_answered(bot, monkeypatch):
    asked = []

    async def ask(channel, body):
        asked.append((channel, body))

    monkeypatch.setattr(bot, "ask", ask)

    def message(author_id, channel_id, content="hi", is_bot=False):
        return SimpleNamespace(author=SimpleNamespace(id=author_id, bot=is_bot), channel=FakeChannel(channel_id),
                               attachments=[], content=content)

    await bot.on_message(message(7, GENERAL))  # a stranger in #general
    await bot.on_message(message(42, GENERAL, is_bot=True))
    for elsewhere in (NOTIFICATION, APPROVAL, ALERTS, 555):  # you, but not in #general (or in another server)
        await bot.on_message(message(42, elsewhere))
    direct = SimpleNamespace(author=SimpleNamespace(id=42, bot=False), channel=discord.DMChannel.__new__(discord.DMChannel),
                             attachments=[], content="hi")  # a DM has no id to match
    direct.channel.id = 12345
    await bot.on_message(direct)
    assert asked == []
    await bot.on_message(message(42, GENERAL))
    assert len(asked) == 1 and asked[0][1]["message"] == "hi" and asked[0][1]["timezone"] == "Europe/Paris"


def test_long_answers_are_split_for_discord():
    parts = split_message("word " * 1000)
    assert all(len(part) <= 2000 for part in parts) and len(parts) == 3


# ----------------------------------------------------------------------
# Finding the channels
# ----------------------------------------------------------------------
class Text(discord.TextChannel):
    def __init__(self, channel_id: int, guild_id: int = GUILD):  # no Discord connection in a test
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id)


def reply_with(bot, found: dict):
    async def fetch_channel(channel_id):
        result = found[channel_id]
        if isinstance(result, Exception):
            raise result
        return result

    bot.fetch_channel = fetch_channel


def refusal(status: int, reason: str) -> discord.HTTPException:
    return discord.NotFound(SimpleNamespace(status=status, reason=reason), reason)


async def test_the_four_channels_are_found_in_one_server(settings):
    bot = ClaraBot(settings)
    reply_with(bot, {i: Text(i) for i in (GENERAL, NOTIFICATION, APPROVAL, ALERTS)})
    await bot.resolve_channels()
    assert set(bot.channels) == {"general", "notification", "approval", "alerts"} and bot.guild_id == GUILD
    await bot.clara.close()


@pytest.mark.parametrize("problem, expected", [
    ({APPROVAL: refusal(404, "Unknown Channel")}, "#approval"),
    ({ALERTS: object()}, "#alerts"),  # not a text channel
    ({NOTIFICATION: Text(NOTIFICATION, guild_id=GUILD + 1)}, "one server"),
])
async def test_a_wrong_channel_is_refused_with_no_dm_fallback(settings, problem, expected):
    bot = ClaraBot(settings)
    found = {i: Text(i) for i in (GENERAL, NOTIFICATION, APPROVAL, ALERTS)} | problem
    reply_with(bot, found)
    with pytest.raises(ChannelError, match=expected):
        await bot.resolve_channels()
    await bot.clara.close()


# ----------------------------------------------------------------------
# Approvals: #approval, with a pointer in #general
# ----------------------------------------------------------------------
async def test_approvals_come_with_buttons_in_the_approval_channel(bot):
    approval = bot.channels["approval"]
    assert await bot.show_approval({"id": 1, "level": "write", "summary": "Comment on issue #3", "reason": "default"})
    assert await bot.show_approval({"id": 2, "level": "destructive", "summary": "Delete notes/old.md", "reason": "default"})
    assert not await bot.show_approval({"id": 1, "level": "write", "summary": "again", "reason": ""})  # shown once
    (first, buttons), (second, none) = approval.sent
    assert "#1" in first and [item.custom_id for item in buttons.children] == [
        "clara:approval:1:allow_once", "clara:approval:1:allow_always", "clara:approval:1:refuse_once",
        "clara:approval:1:refuse_always"]
    assert none is None and "web site or with clara-admin" in second
    assert not any(bot.channels[name].sent for name in ("general", "notification", "alerts"))

    await bot.deliver({"type": "approval_resolved", "payload": {"approval": {"id": 1, "summary": "Comment", "status": "done"}}})
    assert bot.asked.get(1) is None


async def test_a_request_during_a_chat_is_pointed_to_from_general(bot, monkeypatch):
    approval = {"id": 5, "level": "write", "summary": "Create a file", "reason": ""}

    async def stream(method, path, body=None, **options):
        yield {"type": "turn", "conversation": "c1"}
        yield {"type": "approval", "approval": approval}
        yield {"type": "approval", "approval": approval}  # the same request again: shown once
        yield {"type": "done", "reply": "Done.", "files": []}

    monkeypatch.setattr(bot.clara, "stream", stream)
    monkeypatch.setattr(bot, "set_conversation", lambda conversation: None)
    await bot.ask(bot.channels["general"], {"message": "make it"})
    assert texts(bot.channels["general"]) == [f"🔐 Waiting for your answer in <#{APPROVAL}>.", "Done."]
    assert len(bot.channels["approval"].sent) == 1


# ----------------------------------------------------------------------
# Events: each kind to its own channel
# ----------------------------------------------------------------------
async def test_events_go_to_the_channel_of_their_kind(bot):
    await bot.deliver({"type": "reminder", "title": "Reminder", "text": "Call the plumber"})
    await bot.deliver({"type": "alert", "title": "The disk is nearly full", "text": "91% used."})
    await bot.deliver({"type": "notification", "title": "Answer ready", "text": "Your summary is done."})
    await bot.deliver({"type": "task", "title": "Task: taxes", "text": "Due tomorrow."})
    await bot.deliver({"type": "schedule", "title": "Scheduled task: news", "text": "Three items."})
    await bot.deliver({"type": "update", "title": "Updated to v1.2.0", "text": "The new release is running."})
    await bot.deliver({"type": "server", "state": "running"})  # logged, not sent
    assert texts(bot.channels["alerts"]) == ["⚠️ **The disk is nearly full**\n91% used."]
    assert texts(bot.channels["notification"]) == [
        "⏰ Call the plumber", "🔔 **Answer ready**\nYour summary is done.", "📋 **Task: taxes**\nDue tomorrow.",
        "🗓 **Scheduled task: news**\nThree items.", "⬆️ **Updated to v1.2.0**\nThe new release is running."]
    assert not bot.channels["general"].sent and not bot.channels["approval"].sent


# ----------------------------------------------------------------------
# Slash commands: your server, you only
# ----------------------------------------------------------------------
def interaction(user_id: int, guild_id: int | None):
    said = []

    async def send_message(content, ephemeral=False):
        said.append((content, ephemeral))

    return SimpleNamespace(user=SimpleNamespace(id=user_id), guild_id=guild_id,
                           response=SimpleNamespace(send_message=send_message)), said


async def test_commands_work_for_you_in_your_server_only(bot):
    command = bot.tree.get_command("new")
    contexts = command.allowed_contexts
    assert contexts.guild and not contexts.dm_channel and not contexts.private_channel
    bot.set_conversation("old")
    for who, where in ((7, GUILD), (42, GUILD + 1), (42, None)):
        call, said = interaction(who, where)
        await command.callback(call)
        assert said == [("Not for you.", True)] and bot.conversation() == "old"
    call, said = interaction(42, GUILD)
    await command.callback(call)
    assert bot.conversation() is None and "New conversation" in said[0][0]


async def test_command_results_are_ephemeral_but_errors_stay_visible(bot, monkeypatch):
    answers = {
        "/v1/admin/status": {"version": "1.0", "state": "ok", "models": {"default": "m"}, "approvals_pending": 0,
                             "alerts": {}},
        "/v1/tasks": {"tasks": [{"id": 1, "title": "Taxes", "due_at": None}]},
        "/v1/reminders": {"reminders": [{"id": 2, "local": "10:00", "text": "Call", "system": False}]},
        "/v1/approvals": {"approvals": []},
    }
    failing = False

    async def call(method, path, **options):
        if failing:
            raise bot_module.ClaraError(503, "down")
        return answers[path]

    monkeypatch.setattr(bot.clara, "call", call)
    for name in ("new", "status", "tasks", "reminders", "approvals"):
        command = bot.tree.get_command(name)
        failing = False
        ok, said = interaction(42, GUILD)
        await command.callback(ok)
        assert len(said) == 1 and said[0][1] is True, name
        if name == "new":
            continue
        failing = True
        bad, said = interaction(42, GUILD)
        await command.callback(bad)
        assert len(said) == 1 and said[0][0].startswith("⚠️") and said[0][1] is False, name


# ----------------------------------------------------------------------
# Running it
# ----------------------------------------------------------------------
async def test_closing_twice_says_goodbye_once_and_does_not_fail(bot, monkeypatch):
    calls = []

    async def call(method, path, **options):
        calls.append((method, path, options.get("json")))
        return {}

    monkeypatch.setattr(bot.clara, "call", call)
    await bot.close()
    await bot.close()  # discord.py closes on a refused intent, then the runner closes again
    assert calls == [("POST", "/v1/clients/heartbeat", {"state": "disconnected"})]


def test_a_second_bot_with_the_same_state_file_does_not_start(tmp_path, monkeypatch):
    for key, value in env_for(tmp_path).items():
        monkeypatch.setenv(key, value)
    with open(tmp_path / "state.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert bot_module.main() == bot_module.EXIT_RUNNING


def test_wrong_settings_exit_with_the_code_that_is_not_retried(tmp_path, monkeypatch):
    for key in env_for(tmp_path):
        monkeypatch.delenv(key, raising=False)
    assert bot_module.main() == bot_module.EXIT_SETTINGS
