"""The bot's side of the Discord mirror, against fake channels: what it makes for a place, a conversation and an answer
being written, what it reports, and what it stops at (Discord's limits)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from clara_discord.mirror import Mirror, Stopped, split


def refusal(code: int) -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"), {"code": code, "message": "no"})


class FakeChannel:
    def __init__(self, channel_id: int, name: str):
        self.id = channel_id
        self.name = name
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class FakeMessage:
    def __init__(self, thread: FakeThread, message_id: int, content: str):
        self.thread = thread
        self.id = message_id
        self.content = content

    async def edit(self, content: str) -> None:
        self.content = content
        self.thread.edits.append(content)

    async def delete(self) -> None:
        self.thread.deleted.append(self.id)


class FakeThread(FakeChannel):
    def __init__(self, channel_id: int, name: str, forum: FakeChannel | None = None, archived: bool = False):
        super().__init__(channel_id, name)
        self.archived = archived
        self.parent = forum
        self.sent: list[str] = []  # what was posted, in order (an edit is in `edits`)
        self.edits: list[str] = []
        self.deleted: list[int] = []
        self.counter = channel_id * 100

    async def send(self, content=None, file=None):
        self.counter += 1
        self.sent.append(content or "")
        return FakeMessage(self, self.counter, content or "")

    def get_partial_message(self, message_id: int):
        return FakeMessage(self, message_id, "")

    async def edit(self, **fields) -> None:
        self.name = fields.get("name", self.name)
        self.archived = fields.get("archived", self.archived)


class FakeForum(FakeChannel):
    def __init__(self, channel_id: int, name: str, category=None):
        super().__init__(channel_id, name)
        self.category = category
        self.threads: list[FakeThread] = []
        self.refuse_with: int | None = None

    def create_thread(self, name: str, content: str, auto_archive_duration: int = 0):
        async def made():
            if self.refuse_with:
                raise refusal(self.refuse_with)
            thread = FakeThread(self.id * 10 + len(self.threads) + 1, name, forum=self)
            starter = SimpleNamespace(id=thread.id * 10 + 1)
            thread.sent.append(content)
            self.threads.append(thread)
            return SimpleNamespace(thread=thread, message=starter)

        return made()


class FakeCategory(FakeChannel):
    def __init__(self, channel_id: int, name: str):
        super().__init__(channel_id, name)

    async def edit(self, name: str) -> None:
        self.name = name


class FakeGuild:
    def __init__(self):
        self.channels: dict[int, FakeChannel] = {}
        self.next_id = 500
        self.refuse_categories: int | None = None

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    async def create_category(self, name: str):
        if self.refuse_categories:
            raise refusal(self.refuse_categories)
        self.next_id += 1
        category = FakeCategory(self.next_id, name)
        self.channels[category.id] = category
        return category

    async def create_forum(self, name: str, category):
        self.next_id += 1
        forum = FakeForum(self.next_id, name, category)
        self.channels[forum.id] = forum
        return forum


class FakeBot:
    def __init__(self, guild: FakeGuild):
        self.guild = guild
        self.guild_id = 9

    def get_guild(self, guild_id: int):
        return self.guild

    def get_channel(self, channel_id: int):
        for thread in [item for item in self.guild.channels.values() if isinstance(item, FakeForum)]:
            for found in thread.threads:
                if found.id == channel_id:
                    return found
        return self.guild.get_channel(channel_id)

    async def fetch_channel(self, channel_id: int):
        found = self.get_channel(channel_id)
        if found is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), {"code": 10003, "message": "no"})
        return found


class FakeClara:
    def __init__(self):
        self.sync_payload: dict = {}
        self.reports: list[dict] = []
        self.events: list[dict] = []

    async def call(self, method: str, path: str, **options) -> dict:
        if path == "/v1/discord/sync":
            return self.sync_payload
        if path == "/v1/discord/report":
            self.reports.append(options["json"])
            return {"ok": True}
        if path == "/v1/discord/event":
            self.events.append(options["json"])
            return {}
        raise AssertionError(path)


@pytest.fixture
def world():
    guild = FakeGuild()
    bot = FakeBot(guild)
    clara = FakeClara()
    return SimpleNamespace(guild=guild, bot=bot, clara=clara, mirror=Mirror(bot, clara))


def place(key: str = "main", name: str = "Clara", category: str = "", forum: str = "") -> dict:
    return {"key": key, "name": name, "category_id": category, "forum_id": forum}


def conversation(number: str = "c1", turns: list | None = None, thread: dict | None = None, **over) -> dict:
    found = {"id": number, "title": "Plan", "archived": False, "place": "main", "thread": thread, "turns": turns or [],
             "posted": {}}
    found.update(over)
    return found


def turn(key: str = "t1", question: str = "Bonjour", reply: str | None = "Salut", ref: str = "") -> dict:
    return {"key": key,
            "user": {"text": question, "ref": ref, "digest": "ref" if ref else f"d-{question}"},
            "reply": None if reply is None else {"text": reply, "digest": f"d-{reply}"}}


def test_a_split_is_on_discord_s_2000_characters_at_a_boundary_where_it_can_be():
    parts = split("mot " * 1000)
    assert all(len(part) <= 2000 for part in parts) and "".join(parts).replace(" ", "") == ("mot" * 1000)
    assert split("") == ["…"]


async def test_a_place_is_made_once_and_its_ids_are_reported(world):
    world.clara.sync_payload = {"places": [place()], "dropped": [], "trash": [], "conversations": [], "cursor": 3,
                                "stopped": None}
    await world.mirror.sync_once()
    assert [call for call in world.clara.reports if "places" in call][0]["places"][0]["key"] == "main"
    assert world.mirror.cursor == 3
    made = len(world.guild.channels)
    assert made == 2  # the category and its forum
    world.clara.reports.clear()
    world.clara.sync_payload["places"] = [place(category="501", forum="502")]  # the server now holds their ids
    await world.mirror.sync_once()
    assert len(world.guild.channels) == made and world.clara.reports == []  # nothing changed: nothing made or reported


async def test_a_conversation_gets_a_thread_started_by_its_question_and_its_answer_posted(world):
    world.clara.sync_payload = {
        "places": [place()], "dropped": [], "trash": [], "cursor": 4, "stopped": None,
        "conversations": [conversation(turns=[turn(question="Bonjour", reply="Salut")])],
    }
    await world.mirror.sync_once()
    forum = next(item for item in world.guild.channels.values() if isinstance(item, FakeForum))
    (thread,) = forum.threads
    assert thread.name == "Plan" and thread.sent == ["Bonjour", "Salut"]
    posts = [post for call in world.clara.reports for post in call.get("posts", [])]
    assert [(post["side"], post["digest"]) for post in posts] == [("user", "d-Bonjour"), ("reply", "d-Salut")]


async def test_a_question_that_came_from_discord_is_not_posted_again(world):
    world.mirror.places = {"main": {"category": 1, "forum": 2}}
    forum = FakeForum(2, "Conversations")
    world.guild.channels[2] = forum
    thread = FakeThread(55, "Plan", forum=forum)
    forum.threads.append(thread)
    world.clara.sync_payload = {
        "places": [place(category="1", forum="2")], "dropped": [], "trash": [], "cursor": 5, "stopped": None,
        "conversations": [conversation(turns=[turn(question="déjà là", reply="Oui", ref="777")],
                                       thread={"thread_id": "55", "place": "main", "archived": False})],
    }
    await world.mirror.sync_once()
    assert "déjà là" not in thread.sent and thread.sent == ["Oui"]


async def test_an_answer_that_changed_is_edited_in_place(world):
    world.mirror.places = {"main": {"category": 1, "forum": 2}}
    forum = FakeForum(2, "Conversations")
    world.guild.channels[2] = forum
    thread = FakeThread(55, "Plan", forum=forum)
    forum.threads.append(thread)
    posted = {"reply": {"ids": ["8001"], "digest": "old"}}
    world.clara.sync_payload = {
        "places": [place(category="1", forum="2")], "dropped": [], "trash": [], "cursor": 6, "stopped": None,
        "conversations": [conversation(turns=[turn(question="Q", reply="nouvelle réponse")],
                                       thread={"thread_id": "55", "place": "main", "archived": False},
                                       posted={"t1": {**posted, "user": {"ids": ["8000"], "digest": "d-Q"}}})],
    }
    await world.mirror.sync_once()
    assert thread.edits == ["nouvelle réponse"]
    posts = [post for call in world.clara.reports for post in call.get("posts", [])]
    assert posts == [{"conversation": "c1", "turn": "t1", "side": "reply", "ids": ["8001"], "digest": "d-nouvelle réponse"}]


async def test_a_limit_of_discord_stops_the_mirror_and_says_so(world):
    world.guild.refuse_categories = 30030
    world.clara.sync_payload = {"places": [place()], "dropped": [], "trash": [], "conversations": [], "cursor": 1,
                                "stopped": None}
    with pytest.raises(Stopped) as stop:
        await world.mirror.sync_once()
    assert "categories as Discord allows (50)" in stop.value.reason
    assert world.mirror.cursor == 0  # nothing is counted as done


async def test_a_thread_deleted_on_discord_is_told_to_the_server_and_not_made_again(world):
    world.mirror.places = {"main": {"category": 1, "forum": 2}}
    forum = FakeForum(2, "Conversations")
    world.guild.channels[2] = forum
    world.clara.sync_payload = {
        "places": [place(category="1", forum="2")], "dropped": [], "trash": [], "cursor": 7, "stopped": None,
        "conversations": [conversation(turns=[turn()], thread={"thread_id": "9999", "place": "main", "archived": False})],
    }
    await world.mirror.sync_once()
    assert world.clara.events == [{"kind": "thread_deleted", "thread": "9999"}]
    assert {"conversation": "c1", "thread_id": "gone", "place": "", "archived": False} in [
        item for call in world.clara.reports for item in call.get("threads", [])
    ]


async def test_an_answer_is_written_as_it_comes_and_ends_with_what_was_stored(world):
    world.mirror.places = {"main": {"category": 1, "forum": 2}}
    forum = FakeForum(2, "Conversations")
    world.guild.channels[2] = forum
    thread = FakeThread(55, "Plan", forum=forum)
    forum.threads.append(thread)
    world.mirror.threads["c1"] = "55"
    world.mirror.conversations[55] = "c1"
    await world.mirror.on_live({"type": "start", "conversation": "c1", "turn": "t9", "title": "Plan", "text": "Quoi ?",
                                "digest": "dq", "ref": "", "place": "main", "thread": "55"})
    assert thread.sent == ["Quoi ?", "…"]
    await world.mirror.on_live({"type": "summary", "conversation": "c1", "turn": "t9", "text": "🔧 Read a page · 1 actions"})
    await world.mirror.on_live({"type": "token", "conversation": "c1", "turn": "t9", "text": "Ça"})
    await world.mirror.on_live({"type": "done", "conversation": "c1", "turn": "t9",
                                "text": "🔧 Read a page · 1 actions\nÇa va.", "digest": "dr", "files": []})
    assert thread.edits[-1] == "🔧 Read a page · 1 actions\nÇa va."
    assert world.clara.reports[-1]["posts"] == [{"conversation": "c1", "turn": "t9", "side": "reply",
                                                 "ids": ["5502"], "digest": "dr"}]
    assert "t9" not in world.mirror.streams


async def test_a_conversation_with_no_thread_is_made_whole_when_a_turn_starts_in_it(world):
    """Its place was not shown before, or the bot was off: the thread starts with its first question, the turns that came
    before follow in order, and only then the question being asked."""
    world.mirror.places = {"main": {"category": 1, "forum": 2}}
    forum = FakeForum(2, "Conversations")
    world.guild.channels[2] = forum
    await world.mirror.on_live({"type": "start", "conversation": "c1", "turn": "t3", "title": "Plan", "text": "Q3",
                                "digest": "d-Q3", "ref": "", "place": "main", "thread": "",
                                "earlier": [turn("t1", "Q1", "A1"), turn("t2", "Q2", "A2")]})
    (thread,) = forum.threads
    assert thread.sent == ["Q1", "A1", "Q2", "A2", "Q3", "…"]
    posts = [(post["turn"], post["side"]) for call in world.clara.reports for post in call.get("posts", [])]
    assert posts == [("t1", "user"), ("t1", "reply"), ("t2", "user"), ("t2", "reply"), ("t3", "user"), ("t3", "reply")]
    assert world.mirror.threads["c1"] == str(thread.id) and "t3" in world.mirror.streams


async def test_a_new_conversation_still_starts_its_thread_with_the_question_being_asked(world):
    world.mirror.places = {"main": {"category": 1, "forum": 2}}
    forum = FakeForum(2, "Conversations")
    world.guild.channels[2] = forum
    await world.mirror.on_live({"type": "start", "conversation": "c1", "turn": "t1", "title": "Plan", "text": "Q1",
                                "digest": "d-Q1", "ref": "", "place": "main", "thread": "", "earlier": []})
    (thread,) = forum.threads
    assert thread.sent == ["Q1", "…"]
    posts = [(post["turn"], post["side"]) for call in world.clara.reports for post in call.get("posts", [])]
    assert posts == [("t1", "user"), ("t1", "reply")]


async def test_a_conversations_thread_can_be_waited_for_until_the_bot_has_made_it(world):
    forum = FakeForum(700, "Bilan")
    thread = FakeThread(7001, "Plan", forum)
    forum.threads.append(thread)
    world.guild.channels[700] = forum
    assert await world.mirror.thread_of("c1") is None and await world.mirror.thread_of("c1", 0.1) is None

    async def made_later():
        await asyncio.sleep(0.2)
        world.mirror.remember_thread("7001", "c1")

    maker = asyncio.create_task(made_later())
    assert await world.mirror.thread_of("c1", 5) is thread
    await maker
    assert await world.mirror.thread_of("c1") is thread
