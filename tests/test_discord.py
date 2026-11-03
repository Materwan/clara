"""Gate 5: the Discord bot lives in your private server: it answers one user id in one channel, ignores DMs and every
other server, has no member list, sends each kind of message to its own channel, and uses its own token."""

from __future__ import annotations

import asyncio
import fcntl
from datetime import timedelta
from types import SimpleNamespace

import discord
import pytest

from clara.timeutil import iso, utcnow
from clara_discord import bot as bot_module
from clara_discord.api import ClaraError
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


BOT, OWNER = 7, 42  # the ids of the bot's own user and of the owner, for the messages in a fake channel


class FakeChannel:
    """A text channel that remembers what was sent to it (`sent`) and what is in it now (`messages`, oldest first)."""

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.name = f"channel-{channel_id}"
        self.sent: list[tuple[str, object]] = []
        self.messages: list[SimpleNamespace] = []
        self.made = 0  # messages ever put here: an id is never given twice, whatever was deleted since

    def put(self, content: str, author: int = BOT, view=None) -> SimpleNamespace:
        """A message that is in the channel: the bot's own by default."""
        self.made += 1
        message = SimpleNamespace(id=self.id * 1000 + self.made, content=content,
                                  view=view, edits=[], channel=self, author=SimpleNamespace(id=author))

        async def edit(**fields):
            message.edits.append(fields)

        async def delete():
            if message not in self.messages:
                raise refusal(404, "Unknown Message")
            self.messages.remove(message)

        message.edit, message.delete = edit, delete
        self.messages.append(message)
        return message

    async def send(self, content=None, view=None, file=None):
        self.sent.append((content, view))
        return self.put(content, view=view)

    async def history(self, limit=None):
        for message in list(reversed(self.messages))[:limit]:
            yield message

    def get_partial_message(self, message_id):
        """What discord.py gives for any id, even one that is gone: deleting it then fails with 404, as on Discord."""
        async def delete():
            message = next((m for m in self.messages if m.id == message_id), None)
            if message is None:
                raise refusal(404, "Unknown Message")
            self.messages.remove(message)

        return SimpleNamespace(id=message_id, delete=delete)

    def typing(self):
        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Typing()


@pytest.fixture
async def bot(settings):
    with pytest.MonkeyPatch.context() as patch:  # not the test's own monkeypatch: that one is undone before this teardown
        patch.setattr(ClaraBot, "user", SimpleNamespace(id=BOT))
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

    async def ask(channel, body, remember=True, mirrored=False):
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
    told = message(42, GENERAL)  # #general is not a chat: it points to the forum
    await bot.on_message(told)
    assert asked == [] and "Conversations forum" in texts(told.channel)[-1]


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
    assert [message.content for message in approval.messages] == [second]  # a settled request leaves #approval


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


async def test_a_file_clara_presents_is_a_line_with_its_path_and_is_not_uploaded(bot, monkeypatch):
    async def stream(method, path, body=None, **options):
        yield {"type": "turn", "conversation": "c1"}
        yield {"type": "present", "path": "reports/plan.md", "size": 7, "origin": None}
        yield {"type": "present", "path": "reports/plan.md", "size": 7, "origin": None}  # the same file again: one line
        yield {"type": "done", "reply": "Below.", "files": []}

    monkeypatch.setattr(bot.clara, "stream", stream)
    monkeypatch.setattr(bot, "set_conversation", lambda conversation: None)
    await bot.ask(bot.channels["general"], {"message": "give me the plan"})
    assert texts(bot.channels["general"]) == ["Below.\n📎 reports/plan.md"]
    assert len(bot.channels["general"].sent) == 1  # one message: nothing was fetched from the workspace to upload


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
# Routes chosen on the web site, and test messages
# ----------------------------------------------------------------------
async def test_the_channel_the_server_names_wins_and_off_posts_nothing(bot):
    await bot.deliver({"type": "reminder", "text": "Call", "channel": "general"})
    await bot.deliver({"type": "notification", "title": "New email", "text": "x: y", "channel": "alerts"})
    await bot.deliver({"type": "task", "title": "Task: taxes", "text": "Due.", "channel": "off"})
    await bot.deliver({"type": "alert", "title": "Disk", "text": "full", "channel": "notification"})
    assert texts(bot.channels["general"]) == ["⏰ Call"]
    assert texts(bot.channels["alerts"]) == ["🔔 **New email**\nx: y"]
    assert texts(bot.channels["notification"]) == ["⚠️ **Disk**\nfull"]
    assert not bot.channels["approval"].sent


@pytest.mark.parametrize("told", ["approval", "elsewhere", "", None, 7, ["general"]])
async def test_a_channel_the_bot_does_not_take_from_the_server_means_its_own_table(bot, told):
    """#approval carries only what waits for your answer, whatever a server says."""
    await bot.deliver({"type": "reminder", "text": "Call", "channel": told})
    assert texts(bot.channels["notification"]) == ["⏰ Call"]
    assert not bot.channels["approval"].sent and not bot.channels["general"].sent


def watch_heartbeats(bot, monkeypatch) -> list:
    calls = []

    async def call(method, path, **options):
        calls.append((method, path, options.get("json")))
        return {}

    monkeypatch.setattr(bot.clara, "call", call)
    return calls


def a_test(channel: str, event: int = 7, age: float = 0.0, **more) -> dict:
    from datetime import UTC, datetime, timedelta

    from clara.timeutil import iso

    return {"id": event, "type": "notification", "source": "discord-test", "channel": channel,
            "title": "Test from Clara's web site", "text": f"This is #{channel}.",
            "created_at": iso(datetime.now(UTC) - timedelta(seconds=age)), **more}


async def test_a_test_message_is_posted_in_the_channel_it_names_and_the_result_is_reported_at_once(bot, monkeypatch):
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    monkeypatch.setattr(ClaraBot, "user", "Clara#1234")
    calls = watch_heartbeats(bot, monkeypatch)
    await bot.deliver(a_test("approval"))
    assert texts(bot.channels["approval"]) == ["🧪 **Test from Clara's web site**\nThis is #approval."]
    assert not any(bot.channels[name].sent for name in ("general", "notification", "alerts"))
    [(method, path, body)] = calls
    assert (method, path) == ("POST", "/v1/clients/heartbeat")
    assert body["state"] == "connected" and body["bot"] == "Clara#1234" and body["test"] == {"event": 7, "ok": True, "error": ""}


async def test_a_test_message_the_bot_may_not_post_is_reported_with_discord_s_reason(bot, monkeypatch):
    class Refusing(FakeChannel):
        async def send(self, content=None, view=None, file=None):
            raise refusal(403, "Missing Permissions")

    bot.channels["alerts"] = Refusing(ALERTS)
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    calls = watch_heartbeats(bot, monkeypatch)
    await bot.deliver(a_test("alerts", event=9))
    assert calls[0][2]["test"] == {"event": 9, "ok": False, "error": "Missing Permissions"}


@pytest.mark.parametrize("event", [a_test("general", age=121), a_test("elsewhere"), a_test("general", created_at="yesterday"),
                                   a_test("general", created_at=None)])
async def test_a_stale_or_unaddressed_test_is_dropped_without_a_word(bot, monkeypatch, event):
    """The bot was away when it was asked: the message would arrive hours later and mean nothing."""
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    calls = watch_heartbeats(bot, monkeypatch)
    await bot.deliver(event)
    assert not any(channel.sent for channel in bot.channels.values()) and not calls


async def test_a_message_that_only_looks_like_a_test_is_posted_by_its_kind(bot):
    """Only the server's own test source means a test; the channel a notification names is still limited to three."""
    await bot.deliver({"type": "notification", "source": "client:script", "title": "Hi", "text": "x", "channel": "approval"})
    assert texts(bot.channels["notification"]) == ["🔔 **Hi**\nx"] and not bot.channels["approval"].sent


async def test_the_heartbeat_tells_who_the_bot_is_and_what_it_sees_in_each_channel(bot, monkeypatch):
    class Rights:
        def __init__(self, lacks):
            self.lacks = lacks

        def __getattr__(self, right):
            return right not in self.lacks

    lacks = {GENERAL: {"manage_threads", "read_message_history"}, NOTIFICATION: set(),
             APPROVAL: {"send_messages", "attach_files"}, ALERTS: {"read_message_history"}}
    live = {channel_id: SimpleNamespace(name=f"live-{channel_id}", permissions_for=lambda me, c=channel_id: Rights(lacks[c]))
            for channel_id in lacks}
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: SimpleNamespace(me=object()))
    monkeypatch.setattr(bot, "get_channel", live.get)
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    monkeypatch.setattr(ClaraBot, "user", "Clara#1234")
    calls = watch_heartbeats(bot, monkeypatch)
    await bot.beat()
    [(_, _, body)] = calls
    assert body == {"state": "connected", "bot": "Clara#1234", "channels": {
        "general": {"name": f"live-{GENERAL}", "missing": ["manage_threads"]},
        "notification": {"name": f"live-{NOTIFICATION}", "missing": []},
        "approval": {"name": f"live-{APPROVAL}", "missing": ["send_messages", "attach_files"]},
        "alerts": {"name": f"live-{ALERTS}", "missing": ["read_message_history"]}}}


async def test_a_bot_that_is_not_ready_says_only_that_it_is_not(bot, monkeypatch):
    monkeypatch.setattr(bot, "is_ready", lambda: False)
    calls = watch_heartbeats(bot, monkeypatch)
    await bot.beat(test={"event": 1, "ok": True, "error": ""})
    assert calls == [("POST", "/v1/clients/heartbeat", {"state": "disconnected"})]


async def test_a_heartbeat_that_fails_is_not_fatal(bot, monkeypatch):
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    monkeypatch.setattr(ClaraBot, "user", "Clara#1234")

    async def call(method, path, **options):
        raise ClaraError(0, "Clara cannot be reached")

    monkeypatch.setattr(bot.clara, "call", call)
    await bot.beat()  # logged, not raised


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
    for who, where in ((7, GUILD), (42, GUILD + 1), (42, None)):
        call, said = interaction(who, where)
        await command.callback(call)
        assert said == [("Not for you.", True)]
    call, said = interaction(42, GUILD)
    await command.callback(call)
    assert said == [("The Conversations forum is not made yet.", True)]  # /new opens a thread in it, once it exists


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


async def test_tasks_show_their_deadline_on_their_own_clock_not_in_utc(bot, monkeypatch):
    """Audit (data integrity, high): `due_at[:16]` showed the stored UTC time as if it were local."""
    found = [
        {"id": 1, "title": "Taxes", "due_at": "2026-10-21T19:30:00+00:00", "timezone": "Europe/Paris"},
        {"id": 2, "title": "Call", "due_at": "2026-01-05T12:00:00+00:00", "timezone": "Not/AZone"},
        {"id": 3, "title": "Idle", "due_at": None, "timezone": "Europe/Paris"},
    ]

    async def call(method, path, **options):
        return {"tasks": found}

    monkeypatch.setattr(bot.clara, "call", call)
    ok, said = interaction(42, GUILD)
    await bot.tree.get_command("tasks").callback(ok)
    lines = said[0][0].splitlines()
    assert lines[0] == "[1] Taxes — due Wed 2026-10-21 21:30 CEST"  # 19:30 UTC is 21:30 in Paris
    assert lines[1] == "[2] Call — due Mon 2026-01-05 13:00 CET"  # an unknown zone: the bot's own (Paris)
    assert lines[2] == "[3] Idle"


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


# ----------------------------------------------------------------------
# The end-of-day check-in: a thread under the question
# ----------------------------------------------------------------------
class FakeThread(discord.Thread):
    def __init__(self, thread_id: int, parent_id: int, log: list | None = None, may_delete: bool = True):
        self.id = thread_id  # no Discord connection in a test
        self.parent_id = parent_id
        self.sent: list[tuple[str, object]] = []
        self.deleted = self.archived = False
        self.may_delete = may_delete
        self.log = log if log is not None else []

    async def send(self, content=None, view=None, file=None):
        self.sent.append((content, view))
        self.log.append(("thread", content))

    async def delete(self):
        if not self.may_delete:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        self.deleted = True
        self.log.append(("deleted", self.id))

    async def edit(self, **fields):
        self.archived = bool(fields.get("archived"))

    def typing(self):
        return FakeChannel(self.id).typing()


class ThreadingChannel(FakeChannel):
    """#general, where a message can start a thread (or the bot may not)."""

    def __init__(self, channel_id: int, forbidden: bool = False):
        super().__init__(channel_id)
        self.forbidden = forbidden
        self.threads: list[tuple[str, FakeThread]] = []

    async def send(self, content=None, view=None, file=None):
        message = await super().send(content, view, file)

        async def create_thread(name, auto_archive_duration):
            if self.forbidden:
                raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
            thread = FakeThread(777, self.id)
            self.threads.append((name, thread))
            return thread

        message.create_thread = create_thread
        return message


CHECKIN = {"type": "ask", "title": "Bilan du dimanche 18 octobre 2026", "text": "Voici ta journée… Et le reste ?",
           "payload": {"conversation": "c-checkin", "stage": "asked", "thread": True}, "conversation": "c-checkin"}


async def test_a_check_in_is_posted_in_general_with_a_thread_that_carries_your_answer(bot):
    bot.channels["general"] = general = ThreadingChannel(GENERAL)
    bot.set_conversation("c-current")
    await bot.deliver(CHECKIN)
    assert texts(general) == ["📓 **Bilan du dimanche 18 octobre 2026**\nVoici ta journée… Et le reste ?"]
    assert general.threads[0][0] == "Bilan du dimanche 18 octobre 2026"
    assert bot.threads() == {"777": "c-checkin"} and bot.conversation() == "c-current"  # yours is left alone
    assert not bot.channels["notification"].sent


async def test_what_you_write_in_the_thread_goes_to_the_check_in_not_to_the_current_chat(bot, monkeypatch):
    asked = []

    async def ask(channel, body, remember=True):
        asked.append((channel, body, remember))

    monkeypatch.setattr(bot, "ask", ask)
    bot.set_conversation("c-current")
    bot.remember_thread(777, "c-checkin")

    def message(channel, author_id=42):
        return SimpleNamespace(author=SimpleNamespace(id=author_id, bot=False), channel=channel, attachments=[], content="j'ai couru")

    await bot.on_message(message(FakeThread(777, GENERAL), author_id=7))  # a stranger
    await bot.on_message(message(FakeThread(888, GENERAL)))  # a thread that is not a check-in
    await bot.on_message(message(FakeThread(777, NOTIFICATION)))  # the same id under another channel
    assert asked == []
    thread = FakeThread(777, GENERAL)
    await bot.on_message(message(thread))
    assert asked[0][0] is thread and asked[0][1]["conversation"] == "c-checkin" and asked[0][2] is False
    await bot.on_message(message(FakeChannel(GENERAL)))  # #general no longer continues the current conversation
    assert len(asked) == 1


async def test_an_answer_in_the_thread_does_not_make_it_the_current_conversation(bot, monkeypatch):
    class Clara:
        async def stream(self, method, path, body=None, read=0):
            yield {"type": "turn", "conversation": "c-checkin"}
            yield {"type": "done", "reply": "Noté.", "files": []}

    monkeypatch.setattr(bot, "clara", Clara())
    bot.set_conversation("c-current")
    thread = FakeThread(777, GENERAL)
    await bot.ask(thread, {"message": "j'ai couru", "conversation": "c-checkin"}, remember=False)
    assert bot.conversation() == "c-current" and thread.sent[0][0] == "Noté."


async def test_without_the_right_to_open_threads_the_check_in_becomes_the_current_conversation(bot):
    bot.channels["general"] = general = ThreadingChannel(GENERAL, forbidden=True)
    await bot.deliver(CHECKIN)
    assert bot.conversation() == "c-checkin" and bot.threads() == {}
    assert len(general.sent) == 2 and "Réponds ici" in general.sent[1][0]


async def test_the_reminder_and_the_closing_notice_go_in_the_thread(bot):
    thread = FakeThread(777, GENERAL)

    async def fetch_channel(channel_id):
        return thread

    bot.fetch_channel = fetch_channel
    bot.remember_thread(777, "c-checkin")
    await bot.deliver({**CHECKIN, "title": "Bilan en attente", "text": "Toujours en attente.",
                       "payload": {"conversation": "c-checkin", "stage": "reminder"}})
    assert thread.sent[0][0] == "📓 **Bilan en attente**\nToujours en attente." and not bot.channels["general"].sent


def test_the_threads_are_kept_next_to_the_current_conversation_and_are_limited(bot):
    bot.set_conversation("c-current")
    for number in range(40):
        bot.remember_thread(1000 + number, f"c-{number}")
    bot.set_conversation("c-other")
    threads = bot.threads()
    assert len(threads) == 30 and "1039" in threads and "1000" not in threads and bot.conversation() == "c-other"
    bot.remember_thread(1039, "c-new")  # a conversation has one thread
    assert bot.threads()["1039"] == "c-new" and len(bot.threads()) == 30


# ----------------------------------------------------------------------
# The thread goes once the recap is written
# ----------------------------------------------------------------------
WRITTEN = {"type": "ask", "title": "Récap écrit", "text": "Le récap du dimanche 18 octobre 2026 est écrit dans 1-daily/2026/2026-10-18.md.",
           "payload": {"conversation": "c-checkin", "stage": "finished"}, "conversation": "c-checkin"}


def with_thread(bot, may_delete: bool = True) -> tuple[FakeThread, list]:
    order: list = []
    thread = FakeThread(777, GENERAL, order, may_delete)

    async def fetch_channel(channel_id):
        return thread

    bot.fetch_channel = fetch_channel
    bot.remember_thread(777, "c-checkin")
    return thread, order


async def test_the_thread_is_deleted_and_general_says_where_the_recap_is(bot):
    thread, _ = with_thread(bot)
    bot.set_conversation("c-current")
    await bot.deliver(WRITTEN)
    assert texts(bot.channels["general"]) == ["📓 **Récap écrit**\nLe récap du dimanche 18 octobre 2026 est écrit dans 1-daily/2026/2026-10-18.md."]
    assert thread.deleted and bot.threads() == {} and bot.conversation() == "c-current"  # yours is left alone


async def test_when_the_recap_is_written_during_your_answer_the_answer_is_posted_before_the_thread_goes(bot, monkeypatch):
    thread, order = with_thread(bot)
    real_send = bot.channels["general"].send

    async def general_send(content=None, view=None, file=None):
        order.append(("general", content))
        return await real_send(content, view, file)

    bot.channels["general"].send = general_send

    class Clara:
        async def stream(self, method, path, body=None, read=0):
            yield {"type": "turn", "conversation": "c-checkin"}
            await bot.deliver(WRITTEN)  # the event arrives while the answer is still being written
            assert not thread.deleted
            yield {"type": "done", "reply": "C'est écrit dans la note.", "files": []}

    monkeypatch.setattr(bot, "clara", Clara())
    await bot.ask(thread, {"message": "voilà", "conversation": "c-checkin"}, remember=False)
    assert [kind for kind, _ in order] == ["thread", "general", "deleted"]
    assert order[0][1] == "C'est écrit dans la note." and bot.threads() == {} and not bot.finished and not bot.answering


async def test_a_thread_the_bot_may_not_delete_is_archived_and_still_answers(bot):
    thread, _ = with_thread(bot, may_delete=False)
    await bot.deliver(WRITTEN)
    assert thread.archived and not thread.deleted and bot.threads() == {"777": "c-checkin"}  # a late answer still goes in
    assert len(bot.channels["general"].sent) == 1


async def test_a_check_in_closed_without_your_answer_also_removes_its_thread(bot):
    thread, _ = with_thread(bot)
    await bot.deliver({**WRITTEN, "title": "Bilan clos sans réponse", "text": "Tu n'as pas répondu…",
                       "payload": {"conversation": "c-checkin", "stage": "closed"}})
    assert thread.deleted and texts(bot.channels["general"])[0].startswith("📓 **Bilan clos sans réponse**")


async def test_a_thread_already_gone_is_not_an_error(bot):
    async def fetch_channel(channel_id):
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel")

    bot.fetch_channel = fetch_channel
    bot.remember_thread(777, "c-checkin")
    await bot.deliver(WRITTEN)
    assert bot.threads() == {} and len(bot.channels["general"].sent) == 1


async def test_a_check_in_that_became_the_current_chat_stops_being_it_once_written(bot):
    bot.set_conversation("c-checkin")  # the bot could not open a thread
    await bot.deliver(WRITTEN)
    assert bot.conversation() is None and len(bot.channels["general"].sent) == 1


async def test_a_correction_after_the_thread_is_gone_only_says_where_the_recap_is(bot):
    await bot.deliver(WRITTEN)  # no thread known any more (the correction was asked on the web site)
    assert len(bot.channels["general"].sent) == 1 and bot.threads() == {}


# ----------------------------------------------------------------------
# What a scheduled run says: ask, post, reminder, thread of its own
# ----------------------------------------------------------------------
async def test_a_question_without_the_thread_pack_makes_the_run_the_current_conversation(bot):
    await bot.deliver({**CHECKIN, "payload": {"conversation": "c-checkin", "stage": "asked", "thread": False}})
    assert bot.conversation() == "c-checkin" and bot.threads() == {}
    assert texts(bot.channels["general"])[0].startswith("📓 **Bilan du dimanche") and "Réponds ici" in texts(bot.channels["general"])[1]


async def test_a_second_question_goes_in_the_thread_that_exists(bot):
    thread, _ = with_thread(bot)
    await bot.deliver(CHECKIN)
    assert thread.sent[0][0].startswith("📓 **Bilan du dimanche") and not bot.channels["general"].sent


SAID = {"type": "ask", "title": "Facture", "text": "Facture 42 : 120 EUR.\nÀ payer avant le 1er novembre.",
        "payload": {"conversation": "c-mail", "stage": "said"}, "conversation": "c-mail"}


async def test_a_message_to_answer_is_posted_in_general_and_its_conversation_becomes_the_current_one(bot):
    bot.set_conversation("c-before")
    await bot.deliver(SAID)
    assert texts(bot.channels["general"])[0] == "📓 **Facture**\nFacture 42 : 120 EUR.\nÀ payer avant le 1er novembre."
    assert "Réponds ici" in texts(bot.channels["general"])[1]
    assert bot.conversation() == "c-mail" and bot.threads() == {}
    assert not bot.channels["notification"].sent  # a ping goes there, a conversation does not


async def test_a_message_to_answer_for_a_conversation_with_a_thread_goes_in_the_thread_and_leaves_the_current_chat(bot):
    thread, _ = with_thread(bot)
    bot.set_conversation("c-before")
    await bot.deliver({**SAID, "payload": {"conversation": "c-checkin", "stage": "said"}, "conversation": "c-checkin"})
    assert thread.sent[0][0].startswith("📓 **Facture**") and not bot.channels["general"].sent
    assert bot.conversation() == "c-before"


async def test_a_long_answer_is_said_in_several_messages(bot):
    paragraph = "Une ligne de détail de la facture. " * 20
    await bot.deliver({**SAID, "text": "\n\n".join([paragraph] * 6)})
    sent = texts(bot.channels["general"])
    assert len(sent) > 2 and all(len(part) <= 2000 for part in sent) and "Réponds ici" in sent[-1]


async def test_a_run_can_open_write_in_and_remove_a_thread_by_itself(bot):
    bot.channels["general"] = general = ThreadingChannel(GENERAL)
    event = {"type": "ask", "title": "Rapport", "text": "Premier message", "conversation": "c-run",
             "payload": {"conversation": "c-run", "stage": "thread_open", "thread": True}}
    await bot.deliver(event)
    assert general.threads[0][0] == "Rapport" and bot.threads() == {"777": "c-run"}
    thread = general.threads[0][1]

    async def fetch_channel(channel_id):
        return thread

    bot.fetch_channel = fetch_channel
    await bot.deliver({**event, "text": "La suite", "payload": {"conversation": "c-run", "stage": "thread_post"}})
    assert thread.sent[0][0] == "📓 **Rapport**\nLa suite"
    await bot.deliver({**event, "payload": {"conversation": "c-run", "stage": "thread_close"}})
    assert thread.deleted and bot.threads() == {} and len(general.sent) == 1  # no line in #general for a plain close


async def test_the_tools_command_lists_and_switches_the_packs_of_the_conversation(bot, monkeypatch):
    listing = [{"id": "web", "label": "Web search", "available": True, "reason": ""},
               {"id": "day_conversations", "label": "Day conversations", "available": True, "reason": ""},
               {"id": "daily_notes", "label": "Daily notes", "available": False, "reason": "No vault is set up."}]
    calls = []

    async def call(method, path, **options):
        calls.append((method, path, options.get("json")))
        if path == "/v1/packs":
            return {"packs": listing}
        if path == "/v1/conversations" and method == "POST":
            return {"id": "c-new", "packs": options["json"]["packs"]}
        return {"packs": ["web"]}

    monkeypatch.setattr(bot.clara, "call", call)
    text = await bot.tools_text(None, None)
    assert "✅ `web`" in text and "▫️ `day_conversations`" in text and "(No vault is set up.)" in text
    text = await bot.tools_text("Day conversations", "on")  # no conversation yet: one is made with it
    assert bot.conversation() == "c-new" and ("POST", "/v1/conversations", {"packs": ["web", "day_conversations"]}) in calls
    bot.set_conversation("c-1")
    await bot.tools_text("web", None)  # no state: the other one
    assert ("PATCH", "/v1/conversations/c-1", {"packs": []}) in calls
    assert "No tool called" in await bot.tools_text("nothing", "on")


async def test_the_level_command_shows_and_sets_the_level_of_the_conversation(bot, monkeypatch):
    ladder = {"top": 5, "defaults": {"chat": 3, "unattended": 3},
              "levels": [{"position": n, "name": name} for n, name in enumerate(
                  ["No tool", "Trusted read", "Any read", "Files and vault", "Mail", "Everything"])]}
    calls = []

    async def call(method, path, **options):
        calls.append((method, path, options.get("json")))
        if path == "/v1/levels":
            return ladder
        return {"own": 2, "level": 2, "name": "Any read", "source": "own", "top": 5}

    monkeypatch.setattr(bot.clara, "call", call)
    assert "No conversation here" in await bot.level_text(None)
    assert "inside a conversation" in await bot.level_text("2")
    bot.set_conversation("c-1")
    assert "Level `2` (Any read), from own" in await bot.level_text(None)
    await bot.level_text("2")
    await bot.level_text("follow")
    assert ("PUT", "/v1/conversations/c-1/level", {"level": 2}) in calls
    assert ("PUT", "/v1/conversations/c-1/level", {"level": None}) in calls
    assert "A level is" in await bot.level_text("9") and "A level is" in await bot.level_text("lots")


# ----------------------------------------------------------------------
# A run that delivers to Discord lives in one forum thread: nothing in #general, the link in #notification
# ----------------------------------------------------------------------
class ForumThread(FakeThread):
    jump_url = f"https://discord.com/channels/{GUILD}/555"


def forum_thread(bot, conversation: str = "c-run") -> ForumThread:
    thread = ForumThread(555, 600)

    async def fetch_channel(channel_id):
        return thread

    bot.fetch_channel = fetch_channel
    bot.mirror.threads[conversation] = "555"  # the mirror made it as the run's first turn began
    return thread


FORUM = {"type": "ask", "title": "Bilan du dimanche", "text": "Et le reste ?", "conversation": "c-run",
         "payload": {"conversation": "c-run", "stage": "asked", "thread": False, "forum": True}}


def said_in_forum(**payload) -> dict:
    return {**FORUM, "payload": {"conversation": "c-run", "forum": True, **payload}}


async def test_a_forum_run_asks_in_its_forum_thread_and_general_hears_nothing(bot):
    thread = forum_thread(bot)
    bot.channels["general"] = general = ThreadingChannel(GENERAL)
    bot.set_conversation("c-current")
    await bot.deliver(FORUM)
    assert texts(thread) == ["📓 **Bilan du dimanche**\nEt le reste ?"]
    assert not general.sent and not general.threads and not bot.channels["notification"].sent
    assert bot.conversation() == "c-current" and bot.threads() == {}  # nothing became the current chat, no thread in #general


async def test_its_reminder_and_its_end_are_written_in_its_thread_which_stays(bot):
    thread = forum_thread(bot)
    await bot.deliver({**said_in_forum(stage="reminder"), "title": "Bilan", "text": "Toujours en attente."})
    await bot.deliver({**said_in_forum(stage="finished"), "title": "Bilan", "text": "Récap écrit dans la note du jour."})
    assert texts(thread) == ["📓 **Bilan**\nToujours en attente.", "📓 **Bilan**\nRécap écrit dans la note du jour."]
    assert not thread.deleted and not thread.archived and not bot.channels["general"].sent  # it is the conversation: it stays


async def test_the_end_waits_for_nothing_even_while_you_are_answering(bot):
    thread = forum_thread(bot)
    bot.answering.add("c-run")  # a check-in thread would hold the end back until your answer was written
    await bot.deliver({**said_in_forum(stage="closed"), "title": "Bilan", "text": "Fermé sans réponse."})
    assert texts(thread) == ["📓 **Bilan**\nFermé sans réponse."] and "c-run" not in bot.finished


async def test_its_answer_is_not_said_again_because_the_mirror_wrote_it_in_the_thread(bot):
    thread = forum_thread(bot)
    await bot.deliver({**SAID, "conversation": "c-run", "payload": {"conversation": "c-run", "stage": "said", "forum": True}})
    assert not thread.sent and not bot.channels["general"].sent and bot.conversation() != "c-run"


async def test_without_its_thread_a_forum_run_goes_where_it_went_before_and_still_opens_no_thread(bot, monkeypatch):
    monkeypatch.setattr(bot_module, "THREAD_WAIT", 0.0)  # its history is switched off, or the mirror is stopped
    bot.channels["general"] = general = ThreadingChannel(GENERAL)
    await bot.deliver({**FORUM, "payload": {**FORUM["payload"], "thread": True}})
    assert texts(general)[0] == "📓 **Bilan du dimanche**\nEt le reste ?" and "Réponds ici" in texts(general)[1]
    assert bot.conversation() == "c-run" and not general.threads
    await bot.deliver({**SAID, "conversation": "c-run", "payload": {"conversation": "c-run", "stage": "said", "forum": True}})
    assert texts(general)[2].startswith("📓 **Facture**")  # its answer is not lost
    await bot.deliver({**said_in_forum(stage="finished"), "title": "Bilan", "text": "Fini."})
    assert texts(general)[-1] == "📓 **Bilan**\nFini." and bot.conversation() is None


def started(**more) -> dict:
    return {"type": "notification", "title": "Scheduled task: Bilan", "text": "Started.", "source": "automations",
            "conversation": "c-run", "channel": "notification", "created_at": iso(utcnow()),
            "payload": {"schedule": 1, "run": 3, "conversation": "c-run", "thread": ""}, **more}


async def test_a_started_run_is_announced_in_notification_with_the_link_of_its_forum_thread(bot):
    forum_thread(bot)
    await bot.deliver(started())
    assert texts(bot.channels["notification"]) == [
        f"▶️ **Scheduled task: Bilan**\nStarted.\n🔗 https://discord.com/channels/{GUILD}/555"]
    assert not bot.channels["general"].sent and not bot.channels["alerts"].sent


async def test_the_notice_follows_the_channel_the_server_names_and_off_posts_nothing(bot):
    forum_thread(bot)
    await bot.deliver(started(channel="general"))
    assert texts(bot.channels["general"])[0].startswith("▶️ **Scheduled task: Bilan**") and not bot.channels["notification"].sent
    await bot.deliver(started(channel="off"))
    assert len(bot.channels["general"].sent) == 1 and not bot.channels["notification"].sent


async def test_a_notice_for_a_thread_that_is_made_after_it_waits_for_it(bot):
    async def made_later():
        await asyncio.sleep(0.2)
        forum_thread(bot)

    maker = asyncio.create_task(made_later())
    await bot.deliver(started())
    await maker
    assert texts(bot.channels["notification"])[0].endswith(f"🔗 https://discord.com/channels/{GUILD}/555")


async def test_a_notice_whose_thread_never_comes_is_posted_without_a_link_and_a_stale_one_is_dropped(bot, monkeypatch):
    monkeypatch.setattr(bot_module, "THREAD_WAIT", 0.0)
    await bot.deliver(started())
    assert texts(bot.channels["notification"]) == ["▶️ **Scheduled task: Bilan**\nStarted."]
    await bot.deliver(started(created_at=iso(utcnow() - timedelta(minutes=20))))  # the bot was away: it is not news
    await bot.deliver(started(created_at="not a date"))
    assert len(bot.channels["notification"].sent) == 1


# ----------------------------------------------------------------------
# Clean channels: #approval holds what waits, #alerts what lasts, #notification no line about a gone thread
# ----------------------------------------------------------------------
def pending_are(bot, monkeypatch, *ids: int):
    async def call(method, path, **options):
        assert (method, path, options.get("params")) == ("GET", "/v1/approvals", {"status": "pending", "limit": 500})
        return {"approvals": [{"id": number} for number in ids]}

    monkeypatch.setattr(bot.clara, "call", call)


def left(channel: FakeChannel) -> list[str]:
    return [message.content for message in channel.messages]


async def test_a_request_that_expires_leaves_approval_like_one_that_is_settled(bot):
    await bot.show_approval({"id": 4, "level": "write", "summary": "Create a file", "reason": ""})
    await bot.show_approval({"id": 5, "level": "write", "summary": "Send a mail", "reason": ""})
    await bot.deliver({"type": "approval_resolved", "payload": {"approval": {"id": 4, "summary": "Create a file", "status": "expired"}}})
    assert len(bot.channels["approval"].messages) == 1 and "#5" in left(bot.channels["approval"])[0]
    await bot.deliver({"type": "approval_resolved", "payload": {"approval": {"id": 4, "summary": "Create a file", "status": "expired"}}})
    assert len(bot.channels["approval"].messages) == 1  # said twice, or deleted by hand: no error


async def test_a_button_answer_removes_the_card_and_a_refused_one_says_why(bot, monkeypatch):
    await bot.show_approval({"id": 6, "level": "write", "summary": "Create a file", "reason": ""})
    card = bot.asked[6]
    answers = iter([{"status": "approved", "summary": "Create a file"}, bot_module.ClaraError(409, "answered a moment ago")])

    async def call(method, path, **options):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def defer():
        pass

    monkeypatch.setattr(bot.clara, "call", call)
    press = SimpleNamespace(user=SimpleNamespace(id=OWNER), client=bot, response=SimpleNamespace(defer=defer), message=card)
    await bot_module.ApprovalButton(6, "allow_once").callback(press)
    assert bot.channels["approval"].messages == [] and 6 not in bot.asked
    other = bot.channels["approval"].put("🔐 Clara asks (#7, write): x")
    await bot_module.ApprovalButton(7, "allow_once").callback(SimpleNamespace(
        user=SimpleNamespace(id=OWNER), client=bot, response=SimpleNamespace(defer=defer), message=other))
    assert other.edits == [{"content": "🔐 #7: answered a moment ago", "view": None}] and bot.channels["approval"].messages == [other]


async def test_asking_again_for_the_waiting_requests_replaces_their_messages(bot, monkeypatch):
    await bot.show_approval({"id": 1, "level": "write", "summary": "Create a file", "reason": ""})

    async def call(method, path, **options):
        return {"approvals": [{"id": 1, "level": "write", "summary": "Create a file", "reason": ""}]}

    monkeypatch.setattr(bot.clara, "call", call)
    call_asked, said = interaction(42, GUILD)
    await bot.tree.get_command("approvals").callback(call_asked)
    assert said == [("1 waiting.", True)] and len(bot.channels["approval"].messages) == 1


async def test_at_start_approval_keeps_only_the_cards_of_requests_that_still_wait(bot, monkeypatch):
    approval = bot.channels["approval"]
    approval.put("🔐 Clara asks (#1, write): Settled while the bot was away")
    approval.put("🔐 #2 Comment on issue #3: **approved**")  # the old way of saying a request was settled
    approval.put("🔐 Clara asks (#3, write): Still waiting, an old card")
    approval.put("🧪 **Test from Clara's web site**\nThis is #approval.")
    approval.put("Can you look at #3?", author=OWNER)
    approval.put("🔐 #9 not the bot's", author=555)
    newest = approval.put("🔐 Clara asks (#3, write): Still waiting")
    approval.put("🔐 Clara asks (#4, destructive): Delete notes/old.md\nThis one is answered on the web site or with clara-admin.")
    pending_are(bot, monkeypatch, 3, 4)
    assert await bot.sweep_approvals() == 3
    assert left(approval) == ["🧪 **Test from Clara's web site**\nThis is #approval.", "Can you look at #3?",
                              "🔐 #9 not the bot's", newest.content, "🔐 Clara asks (#4, destructive): Delete notes/old.md\n"
                              "This one is answered on the web site or with clara-admin."]
    assert bot.asked[3] is newest and 4 in bot.asked  # the next answer finds them
    await bot.deliver({"type": "approval_resolved", "payload": {"approval": {"id": 3, "summary": "x", "status": "approved"}}})
    assert newest not in approval.messages


async def test_a_card_the_bot_is_tracking_is_the_one_that_stays(bot, monkeypatch):
    approval = bot.channels["approval"]
    old = approval.put("🔐 Clara asks (#3, write): posted before the restart")
    await bot.show_approval({"id": 3, "level": "write", "summary": "asked again", "reason": ""})
    pending_are(bot, monkeypatch, 3)
    assert await bot.sweep_approvals() == 1
    assert old not in approval.messages and bot.asked[3] in approval.messages


async def test_when_clara_cannot_say_what_waits_nothing_is_deleted(bot, monkeypatch):
    approval = bot.channels["approval"]
    approval.put("🔐 Clara asks (#1, write): x")

    async def down(method, path, **options):
        raise bot_module.ClaraError(503, "down")

    monkeypatch.setattr(bot.clara, "call", down)
    await bot.attempt("#approval", bot.sweep_approvals)
    assert len(approval.messages) == 1


async def test_an_alert_is_deleted_when_its_problem_is_over_and_only_that_one(bot):
    disk = {"type": "alert", "title": "The disk is nearly full", "text": "91% used.", "payload": {"key": "disk"}}
    await bot.deliver(disk)
    await bot.deliver({"type": "alert", "title": "Backup failed", "text": "no space", "payload": {"key": "backup"}})
    await bot.deliver({"type": "alert", "title": "Old kind of alert", "text": "no key"})
    alerts = bot.channels["alerts"]
    assert len(alerts.messages) == 3 and set(bot.alert_messages()) == {"disk", "backup"}
    await bot.deliver({"type": "alert_resolved", "title": "Alert over", "text": "disk", "payload": {"key": "disk"}})
    assert left(alerts) == ["⚠️ **Backup failed**\nno space", "⚠️ **Old kind of alert**\nno key"]
    assert set(bot.alert_messages()) == {"backup"}
    await bot.deliver({"type": "alert_resolved", "payload": {"key": "disk"}})  # not told twice, and never an error
    await bot.deliver({"type": "alert_resolved", "payload": {}})
    assert len(alerts.messages) == 2


async def test_an_alert_that_was_repeated_and_one_the_routes_moved_are_all_deleted(bot):
    await bot.deliver({"type": "alert", "title": "Disk", "text": "full", "payload": {"key": "disk"}})
    await bot.deliver({"type": "alert", "title": "Disk", "text": "still full", "payload": {"key": "disk"}, "channel": "general"})
    await bot.deliver({"type": "alert", "title": "Long", "text": "word " * 600, "payload": {"key": "long"}})
    assert len(bot.channels["alerts"].messages) == 3 and len(bot.channels["general"].messages) == 1
    await bot.deliver({"type": "alert_resolved", "payload": {"key": "disk"}})
    await bot.deliver({"type": "alert_resolved", "payload": {"key": "long"}})
    assert not bot.channels["alerts"].messages and not bot.channels["general"].messages and bot.alert_messages() == {}


async def test_the_alerts_that_last_are_remembered_across_a_restart_of_the_bot(bot, settings):
    await bot.deliver({"type": "alert", "title": "Disk", "text": "full", "payload": {"key": "disk"}})
    again = ClaraBot(settings)  # same state file
    again.channels = bot.channels
    await again.deliver({"type": "alert_resolved", "payload": {"key": "disk"}})
    assert not bot.channels["alerts"].messages
    await again.clara.close()


async def test_the_memory_of_alerts_is_bounded(bot, monkeypatch):
    monkeypatch.setattr(bot_module, "ALERTS_KEPT", 3)
    monkeypatch.setattr(bot_module, "ALERT_MESSAGES_KEPT", 2)
    for number in range(5):
        await bot.deliver({"type": "alert", "title": "A", "text": "x", "payload": {"key": f"k{number}"}})
    for _ in range(4):
        await bot.deliver({"type": "alert", "title": "A", "text": "x", "payload": {"key": "k4"}})
    kept = bot.alert_messages()
    assert list(kept) == ["k2", "k3", "k4"] and len(kept["k4"]) == 2


async def test_once_the_backlog_of_alerts_goes_but_not_what_the_bot_posted_by_key_or_what_is_yours(bot):
    alerts = bot.channels["alerts"]
    alerts.put("⚠️ **Disk**\nan alert from long ago")
    alerts.put("⚠️ **Backup**\nfixed since")
    alerts.put("my own note", author=OWNER)
    await bot.deliver({"type": "alert", "title": "Brave", "text": "refused", "payload": {"key": "brave"}})
    backlog = await bot.own_messages(alerts, None)
    assert await bot.empty_alerts(backlog) == 2
    assert left(alerts) == ["my own note", "⚠️ **Brave**\nrefused"] and bot._state()["alerts_swept"] is True


async def test_a_backlog_that_could_not_all_be_deleted_is_done_again_at_the_next_start(bot):
    alerts = bot.channels["alerts"]
    stuck = alerts.put("⚠️ **Disk**\nold")

    async def refused():
        raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "down")

    stuck.delete = refused
    alerts.put("⚠️ **Backup**\nold")
    assert await bot.empty_alerts(await bot.own_messages(alerts, None)) == 1
    assert "alerts_swept" not in bot._state()


async def test_tidy_lists_alerts_before_the_relay_may_post_and_empties_them_only_once(bot, monkeypatch):
    async def ready():
        pass

    swept = []

    async def sweep(*args):
        swept.append(args)
        return 0

    monkeypatch.setattr(bot, "wait_until_ready", ready)
    monkeypatch.setattr(bot, "sweep_approvals", sweep)
    monkeypatch.setattr(bot, "sweep_notification", sweep)
    monkeypatch.setattr(bot_module, "TIDY_EVERY", 0.01)
    alerts = bot.channels["alerts"]
    alerts.put("⚠️ **Disk**\nold")
    assert not bot.listed.is_set()
    task = asyncio.create_task(bot.tidy())
    await asyncio.wait_for(bot.listed.wait(), 2)
    await asyncio.sleep(0.1)
    task.cancel()
    assert alerts.messages == [] and len(swept) >= 2  # both sweeps ran again after the pause
    alerts.put("⚠️ **Disk**\nnew")
    task = asyncio.create_task(bot.tidy())
    await asyncio.sleep(0.05)
    task.cancel()
    assert left(alerts) == ["⚠️ **Disk**\nnew"]  # the backlog goes once


def run_line(thread_id: int, guild: int = GUILD, title: str = "Scheduled task: Bilan") -> str:
    return f"▶️ **{title}**\nStarted.\n🔗 https://discord.com/channels/{guild}/{thread_id}"


def threads_are(bot, monkeypatch, alive: set[int], unsure: set[int] = frozenset()):
    asked = []

    async def fetch_channel(channel_id):
        asked.append(channel_id)
        if channel_id in unsure:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "down")
        if channel_id not in alive:
            raise refusal(404, "Unknown Channel")
        return SimpleNamespace(id=channel_id)

    monkeypatch.setattr(bot, "fetch_channel", fetch_channel)
    return asked


async def test_a_line_about_a_thread_that_is_gone_leaves_notification_and_nothing_else_does(bot, monkeypatch):
    notification = bot.channels["notification"]
    notification.put(run_line(111))  # its thread was deleted
    live = notification.put(run_line(222))
    notification.put(run_line(111, title="Hook: PR review"))  # the same thread again: asked once
    reminder = notification.put(f"🔔 **Hook: PR review**\nlook at it\n🔗 https://discord.com/channels/{GUILD}/111")  # not a run's line
    elsewhere = notification.put(run_line(111, guild=GUILD + 1))  # another server's thread: not ours to judge
    nolink = notification.put("▶️ **Scheduled task: Bilan**\nStarted.")  # the thread never came
    yours = notification.put(run_line(111), author=OWNER)
    asked = threads_are(bot, monkeypatch, alive={222})
    assert await bot.sweep_notification() == 2
    assert notification.messages == [live, reminder, elsewhere, nolink, yours] and asked.count(111) == 1


async def test_only_a_thread_discord_says_is_missing_deletes_its_line(bot, monkeypatch):
    notification = bot.channels["notification"]
    unknown = notification.put(run_line(333))
    notification.put(run_line(444))  # its thread does not exist
    threads_are(bot, monkeypatch, alive=set(), unsure={333})
    assert await bot.sweep_notification() == 1
    assert notification.messages == [unknown] and "notification_swept" not in bot._state()  # the whole channel is read again


async def test_a_thread_that_discord_still_has_cached_is_not_asked_for(bot, monkeypatch):
    notification = bot.channels["notification"]
    line = notification.put(run_line(222))
    asked = threads_are(bot, monkeypatch, alive=set())
    monkeypatch.setattr(bot, "get_channel", lambda channel_id: SimpleNamespace(id=channel_id))
    assert await bot.sweep_notification() == 0 and asked == [] and notification.messages == [line]


async def test_the_first_pass_reads_the_whole_channel_and_later_ones_the_newest_messages(bot, monkeypatch):
    monkeypatch.setattr(bot_module, "NOTIFICATION_RECENT", 5)
    notification = bot.channels["notification"]
    oldest = notification.put(run_line(111))
    notification.put(run_line(112))
    for number in range(20):
        notification.put(f"🔔 note {number}")
    newest = notification.put(run_line(113))
    threads_are(bot, monkeypatch, alive=set())
    assert await bot.sweep_notification() == 3 and oldest not in notification.messages
    assert bot._state()["notification_swept"] is True
    older = notification.put(run_line(114))
    for number in range(8):
        notification.put(f"🔔 later {number}")
    assert await bot.sweep_notification() == 0 and older in notification.messages and newest not in notification.messages
    del notification.messages[-8:]
    assert await bot.sweep_notification() == 1 and older not in notification.messages  # now among the newest


async def test_a_pass_that_cannot_read_the_channel_deletes_nothing_and_is_tried_again(bot, monkeypatch):
    notification = bot.channels["notification"]
    notification.put(run_line(111))

    async def history(limit=None):
        raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
        yield

    notification.history = history
    await bot.attempt("#notification", bot.sweep_notification)
    assert len(notification.messages) == 1 and "notification_swept" not in bot._state()


async def test_a_button_answer_whose_card_cannot_be_deleted_at_least_loses_its_buttons(bot, monkeypatch):
    await bot.show_approval({"id": 8, "level": "write", "summary": "Create a file", "reason": ""})
    card = bot.asked[8]

    async def refused():
        raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "down")

    async def call(method, path, **options):
        return {"status": "approved", "summary": "Create a file"}

    async def defer():
        pass

    card.delete = refused
    monkeypatch.setattr(bot.clara, "call", call)
    await bot_module.ApprovalButton(8, "allow_once").callback(SimpleNamespace(
        user=SimpleNamespace(id=OWNER), client=bot, response=SimpleNamespace(defer=defer), message=card))
    assert card.edits == [{"view": None}] and 8 not in bot.asked


async def test_the_parts_of_a_long_alert_are_remembered_even_if_a_later_one_fails(bot, monkeypatch):
    alerts = bot.channels["alerts"]
    send = alerts.send
    posted = []

    async def flaky(content=None, view=None, file=None):
        if posted:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "down")
        posted.append(await send(content))
        return posted[-1]

    monkeypatch.setattr(alerts, "send", flaky)
    with pytest.raises(discord.HTTPException):
        await bot.deliver({"type": "alert", "title": "Long", "text": "word " * 600, "payload": {"key": "long"}})
    assert bot.alert_messages() == {"long": [[alerts.id, posted[0].id]]}
    await bot.deliver({"type": "alert_resolved", "payload": {"key": "long"}})
    assert alerts.messages == []


async def test_a_channel_whose_history_the_bot_may_not_read_is_never_counted_as_swept(bot, monkeypatch):
    """Discord answers such a bot with an empty list, not an error: the one-time sweeps must wait for the right."""
    async def ready():
        pass

    lacking = {name: ("x", ["read_message_history"]) for name in ("alerts", "notification")}
    monkeypatch.setattr(bot, "lacking_rights", lambda: lacking)
    monkeypatch.setattr(bot, "wait_until_ready", ready)
    monkeypatch.setattr(bot, "sweep_approvals", lambda: asyncio.sleep(0, 0))
    monkeypatch.setattr(bot_module, "TIDY_EVERY", 0.01)
    bot.channels["alerts"].put("⚠️ **Disk**\nold")
    bot.channels["notification"].put(run_line(111))
    threads_are(bot, monkeypatch, alive=set())
    task = asyncio.create_task(bot.tidy())
    await asyncio.wait_for(bot.listed.wait(), 2)
    await asyncio.sleep(0.05)
    task.cancel()
    assert "alerts_swept" not in bot._state() and "notification_swept" not in bot._state()
    assert len(bot.channels["alerts"].messages) == 1 and len(bot.channels["notification"].messages) == 1
    lacking.clear()  # the right is given: the next start does the whole job
    assert await bot.sweep_notification() == 1 and bot._state()["notification_swept"] is True


async def test_an_ended_alert_that_discord_would_not_delete_is_deleted_by_a_later_pass(bot, monkeypatch):
    await bot.deliver({"type": "alert", "title": "Disk", "text": "full", "payload": {"key": "disk"}})
    alerts = bot.channels["alerts"]
    real = alerts.get_partial_message
    down = [True]

    def partial(message_id):
        found = real(message_id)
        delete = found.delete

        async def maybe():
            if down[0]:
                raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "down")
            await delete()

        return SimpleNamespace(id=message_id, delete=maybe)

    monkeypatch.setattr(alerts, "get_partial_message", partial)
    await bot.deliver({"type": "alert_resolved", "payload": {"key": "disk"}})  # Clara says it once
    assert len(alerts.messages) == 1 and bot.alert_messages() == {} and len(bot.undeleted_alerts()) == 1
    assert await bot.delete_undeleted_alerts() == 0 and len(bot.undeleted_alerts()) == 1  # still down: kept
    down[0] = False
    assert await bot.delete_undeleted_alerts() == 1
    assert alerts.messages == [] and bot.undeleted_alerts() == []
    assert await bot.delete_undeleted_alerts() == 0


async def test_a_message_that_is_already_gone_or_whose_channel_is_unknown_is_not_tried_again(bot):
    alerts = bot.channels["alerts"]
    bot._save(alerts_undeleted=[[ALERTS, 12345], [555, 1]])  # no such message; a channel the routes no longer use
    assert await bot.delete_undeleted_alerts() == 2 and bot.undeleted_alerts() == []
    assert alerts.messages == []


async def test_the_hourly_pass_deletes_what_an_ended_alert_left_behind(bot, monkeypatch):
    async def ready():
        pass

    async def nothing():
        return 0

    monkeypatch.setattr(bot, "wait_until_ready", ready)
    monkeypatch.setattr(bot, "sweep_approvals", nothing)
    monkeypatch.setattr(bot, "sweep_notification", nothing)
    monkeypatch.setattr(bot_module, "TIDY_EVERY", 0.01)
    bot._save(alerts_swept=True)
    old = bot.channels["alerts"].put("⚠️ **Disk**\nfull")
    bot._save(alerts_undeleted=[[ALERTS, old.id]])
    task = asyncio.create_task(bot.tidy())
    await asyncio.sleep(0.05)
    task.cancel()
    assert bot.channels["alerts"].messages == [] and bot.undeleted_alerts() == []


async def test_a_slow_reading_of_alerts_does_not_hold_the_relay_back_and_is_finished_at_the_next_start(bot, monkeypatch):
    alerts = bot.channels["alerts"]
    for number in range(3):
        alerts.put(f"⚠️ **Old {number}**")
    release = asyncio.Event()

    async def slow(limit=None):  # Discord answers the first page, then the rate limit holds the next ones
        for message in list(reversed(alerts.messages))[:2]:
            yield message
        await release.wait()

    async def ready():
        pass

    monkeypatch.setattr(alerts, "history", slow)
    monkeypatch.setattr(bot, "wait_until_ready", ready)
    monkeypatch.setattr(bot, "sweep_approvals", lambda: asyncio.sleep(0, 0))
    monkeypatch.setattr(bot, "sweep_notification", lambda: asyncio.sleep(0, 0))
    monkeypatch.setattr(bot_module, "LISTING_LIMIT", 0.05)
    monkeypatch.setattr(bot_module, "TIDY_EVERY", 60)
    task = asyncio.create_task(bot.tidy())
    await asyncio.wait_for(bot.listed.wait(), 2)  # the limit, not the end of the reading, lets the relay go
    await asyncio.sleep(0.05)
    task.cancel()
    assert left(alerts) == ["⚠️ **Old 0**"]  # the two newest were read, and cleaned
    assert "alerts_swept" not in bot._state()  # the oldest one is for the next start


async def test_a_forum_thread_found_alive_is_not_asked_about_again_for_a_while(bot, monkeypatch):
    notification = bot.channels["notification"]
    notification.put(run_line(222))
    asked = threads_are(bot, monkeypatch, alive={222})
    assert await bot.sweep_notification() == 0 and asked == [222]
    assert await bot.sweep_notification() == 0 and asked == [222]  # the second pass trusts the first one
    bot.alive[222] -= bot_module.THREAD_RECHECK + 1  # a long time later
    assert await bot.sweep_notification() == 0 and asked == [222, 222]  # it is asked again
    bot.alive[222] -= bot_module.THREAD_RECHECK + 1
    threads_are(bot, monkeypatch, alive=set())  # and when Discord says it is gone, the line goes
    assert await bot.sweep_notification() == 1 and 222 not in bot.alive and notification.messages == []
