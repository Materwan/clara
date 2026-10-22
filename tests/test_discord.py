"""Gate 5: the Discord bot lives in your private server: it answers one user id in one channel, ignores DMs and every
other server, has no member list, sends each kind of message to its own channel, and uses its own token."""

from __future__ import annotations

import fcntl
from types import SimpleNamespace

import discord
import pytest

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


class FakeChannel:
    """A text channel that remembers what was sent to it."""

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.name = f"channel-{channel_id}"
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

    async def ask(channel, body, remember=True):
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

    lacks = {GENERAL: {"manage_threads"}, NOTIFICATION: set(), APPROVAL: {"send_messages", "attach_files"}, ALERTS: set()}
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
        "alerts": {"name": f"live-{ALERTS}", "missing": []}}}


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
    await bot.on_message(message(FakeChannel(GENERAL)))
    assert asked[1][1]["conversation"] == "c-current" and asked[1][2] is True


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
