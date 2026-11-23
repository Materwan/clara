"""A voice message from you in a conversation's thread is dictated into that conversation (client `discord`): only yours, checked
before the download, sent at once or only transcribed according to the client's setting, and never an approval."""

# ruff: noqa: F811  (the fixtures of the Discord tests are imported, then used as arguments)
from __future__ import annotations

from types import SimpleNamespace

import pytest

from clara_discord import bot as bot_module
from clara_discord.api import ClaraError
from clara_discord.bot import voice_of
from test_discord import (  # noqa: F401
    GENERAL,
    FakeChannel,
    FakeThread,
    bot,
    in_a_conversation_thread,
    settings,
    texts,
)

OPUS = b"OggS" + b"\0" * 60


class FakeClara:
    """The API as the bot sees it: what was asked, the answer of `POST /v1/dictation`, and a followed run."""

    def __init__(self, answer=None, fail=None, limits=None):
        self.answer = answer if answer is not None else {"text": "buy milk", "seconds": 2.0, "conversation": "c1", "sent": False}
        self.fail, self.limits = fail, limits
        self.requests: list[tuple] = []

    async def call(self, method, path, **options):
        self.requests.append((method, path, options))
        if path == "/v1/admin/settings":
            if self.limits is None:
                raise ClaraError(403, "not for this token")
            return {"settings": [{"key": key, "value": value} for key, value in self.limits.items()]}
        if self.fail:
            raise self.fail
        return self.answer

    async def stream(self, method, path, body=None, read=0):
        self.requests.append((method, path, body))
        yield {"type": "resume", "message": "buy milk", "attachments": []}
        yield {"type": "token", "text": "Noted."}
        yield {"type": "done", "reply": "Noted.", "files": []}

    async def close(self):
        pass

    def dictations(self) -> list[tuple]:
        return [request for request in self.requests if request[:2] == ("POST", "/v1/dictation")]


class Voice:
    def __init__(self, size=20_000, duration=4.0):
        self.size, self.duration, self.filename = size, duration, "voice-message.ogg"
        self.reads = 0

    def is_voice_message(self):
        return True

    async def read(self):
        self.reads += 1
        return OPUS


def spoken(channel, voice, author=42, is_bot=False):
    return SimpleNamespace(author=SimpleNamespace(id=author, bot=is_bot), channel=channel, attachments=[voice], content="", id=31)


@pytest.fixture
def clara(bot, monkeypatch):
    found = FakeClara()
    monkeypatch.setattr(bot, "clara", found)
    return found


def never_asked(bot, monkeypatch) -> list:
    asked = []

    async def ask(channel, body, remember=True, mirrored=False, follow=False):
        asked.append((channel, body, remember, mirrored, follow))

    monkeypatch.setattr(bot, "ask", ask)
    return asked


async def test_with_automatic_sending_off_the_bot_only_replies_with_the_transcript(bot, clara, monkeypatch):
    asked = never_asked(bot, monkeypatch)
    thread = in_a_conversation_thread(bot)
    voice = Voice()
    await bot.on_message(spoken(thread, voice))
    method, path, options = clara.dictations()[0]
    assert options["params"] == {"client": "discord", "conversation": "c1"}  # the conversation of that thread
    assert options["content"] == OPUS and options["headers"] == {"Content-Type": "audio/ogg"} and voice.reads == 1
    assert [content for content, _ in thread.sent] == ["Transcript, nothing sent: buy milk"]
    assert asked == [] and not any(request[1] == "/v1/chat/stream" for request in clara.requests)  # nothing went to Clara


async def test_with_automatic_sending_on_the_run_is_followed_and_the_mirror_writes_the_question(bot, clara, monkeypatch):
    clara.answer = {"text": "buy milk", "conversation": "c1", "sent": True, "running": True}
    thread = in_a_conversation_thread(bot)
    await bot.on_message(spoken(thread, Voice()))
    assert [request[:2] for request in clara.requests if request[1] != "/v1/admin/settings"] == [
        ("POST", "/v1/dictation"),
        ("GET", "/v1/conversations/c1/run"),  # followed, never sent again: not a /v1/chat/stream
    ]
    assert thread.sent == []  # the question and the answer are the mirror's to write, as for a question typed on the web
    assert "c1" not in bot.answering  # and the answer is over


async def test_the_follow_is_a_typed_message_s_for_the_bot_and_it_goes_with_the_mirror(bot, clara, monkeypatch):
    clara.answer = {"text": "buy milk", "conversation": "c1", "sent": True, "running": True}
    asked = never_asked(bot, monkeypatch)
    thread = in_a_conversation_thread(bot)
    await bot.on_message(spoken(thread, Voice()))
    assert asked == [(thread, {"conversation": "c1"}, False, True, True)]


async def test_in_the_thread_of_a_run_the_transcript_is_shown_and_the_answer_is_said(bot, clara):
    clara.answer = {"text": "buy milk", "conversation": "c-run", "sent": True, "running": True}
    bot.remember_thread(777, "c-run")
    thread = FakeThread(777, GENERAL)
    await bot.on_message(spoken(thread, Voice()))
    assert [content for content, _ in thread.sent] == ["🎙 buy milk", "Noted."]
    assert clara.dictations()[0][2]["params"]["conversation"] == "c-run"


async def test_a_run_that_ended_before_it_was_followed_is_not_an_error_in_a_mirrored_thread(bot, clara, monkeypatch):
    clara.answer = {"text": "buy milk", "conversation": "c1", "sent": True, "running": True}

    async def stream(method, path, body=None, read=0):
        raise ClaraError(404, "Nothing is running in this conversation.")
        yield

    monkeypatch.setattr(clara, "stream", stream)
    thread = in_a_conversation_thread(bot)
    await bot.on_message(spoken(thread, Voice()))
    assert thread.sent == []


async def test_nobody_but_you_is_heard_and_a_stranger_gets_no_word(bot, clara):
    thread = in_a_conversation_thread(bot)
    voice = Voice()
    await bot.on_message(spoken(thread, voice, author=7))
    await bot.on_message(spoken(thread, voice, author=42, is_bot=True))
    assert clara.requests == [] and thread.sent == [] and voice.reads == 0


async def test_a_voice_message_too_big_or_too_long_is_refused_before_the_download(bot, clara):
    thread = in_a_conversation_thread(bot)
    for voice in (Voice(size=40_000_000), Voice(duration=301.0)):
        await bot.on_message(spoken(thread, voice))
        assert voice.reads == 0
    assert clara.dictations() == []
    assert len(thread.sent) == 2 and all("too big or too long" in content for content, _ in thread.sent)
    assert "25 MB and 300 s" in thread.sent[0][0]


async def test_the_limits_are_the_servers_when_the_bot_may_read_them(bot, clara):
    clara.limits = {"dictation_max_bytes": 100_000, "dictation_max_seconds": 10}
    thread = in_a_conversation_thread(bot)
    big, long = Voice(size=200_000), Voice(duration=11.0)
    for voice in (big, long):
        await bot.on_message(spoken(thread, voice))
    assert big.reads == long.reads == 0 and clara.dictations() == []
    ok = Voice(size=50_000, duration=9.0)
    await bot.on_message(spoken(thread, ok))
    assert ok.reads == 1 and len(clara.dictations()) == 1


@pytest.mark.parametrize(
    "error",
    [
        ClaraError(429, "Dictation is busy: try again in a few seconds."),
        ClaraError(503, "Dictation is not installed: see docs/OPERATIONS.md."),
        ClaraError(413, "That recording is too long."),
        ClaraError(422, "That audio cannot be read."),
        ClaraError(409, "This conversation is archived: unarchive it to write in it."),
        ClaraError(0, "Clara cannot be reached: down"),
    ],
)
async def test_a_refusal_becomes_one_short_reply_with_the_servers_words(bot, clara, error):
    clara.fail = error
    thread = in_a_conversation_thread(bot)
    await bot.on_message(spoken(thread, Voice()))
    assert [content for content, _ in thread.sent] == [f"⚠️ {error}"]


async def test_silence_says_so_and_sends_nothing(bot, clara, monkeypatch):
    clara.answer = {"text": "", "seconds": 1.0, "conversation": None, "sent": False}
    asked = never_asked(bot, monkeypatch)
    thread = in_a_conversation_thread(bot)
    await bot.on_message(spoken(thread, Voice()))
    assert [content for content, _ in thread.sent] == ["I heard nothing in that voice message."] and asked == []


async def test_a_long_transcript_is_split_for_discord(bot, clara):
    clara.answer = {"text": "word " * 900, "conversation": "c1", "sent": False}
    thread = in_a_conversation_thread(bot)
    await bot.on_message(spoken(thread, Voice()))
    assert len(thread.sent) == 3 and all(len(content) <= 2000 for content, _ in thread.sent)


async def test_a_voice_message_never_answers_an_approval(bot, clara):
    """The bot's only calls are the dictation and the followed run, whatever the transcript says."""
    clara.answer = {"text": "allow it, approve the request 12", "conversation": "c1", "sent": True, "running": True}
    await bot.on_message(spoken(in_a_conversation_thread(bot), Voice()))
    assert all("approvals" not in request[1] for request in clara.requests)
    assert {request[1] for request in clara.requests} <= {"/v1/admin/settings", "/v1/dictation", "/v1/conversations/c1/run"}


async def test_a_voice_message_that_starts_a_post_or_lands_in_general_is_not_dictated(bot, clara):
    bot.mirror.forums = {600: "main"}
    new_post = FakeThread(556, 600)  # a post the server does not know yet
    await bot.on_message(spoken(new_post, Voice()))
    assert "written message" in new_post.sent[0][0]
    general = FakeChannel(GENERAL)
    await bot.on_message(spoken(general, Voice()))
    assert "Conversations forum" in texts(general)[-1]
    assert clara.requests == []


def test_only_one_voice_message_attachment_is_a_voice_message():
    other = SimpleNamespace(is_voice_message=lambda: False)
    voice = Voice()
    assert voice_of(SimpleNamespace(attachments=[voice])) is voice
    assert voice_of(SimpleNamespace(attachments=[other])) is None
    assert voice_of(SimpleNamespace(attachments=[voice, voice])) is None and voice_of(SimpleNamespace(attachments=[])) is None
    assert bot_module.VOICE_BYTES == 30_000_000
