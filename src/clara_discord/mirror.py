"""The bot's side of the Discord mirror: it makes in your server what the server asks for (a category and a forum for each
project, schedule and hook shown in the history, one for the conversations outside them, one for incognito ones; a thread
for each conversation; the messages of its turns), and it writes an answer while Clara writes it.

The server says what should be there (`GET /v1/discord/sync`, from a revision: only what changed since the last look is
sent); this module makes it so and reports the ids Discord gave (`POST /v1/discord/report`). What Discord changes by
itself (an archived thread, a deleted thread or forum, a message of yours edited) goes to the server
(`POST /v1/discord/event`), which asks you before it deletes anything. Discord's limits (500 channels, 50 categories) stop
the mirror: nothing is made until you resume it on the Admin page.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
from dataclasses import dataclass, field

import discord

from .api import ClaraError

log = logging.getLogger("clara_discord.mirror")

LIMIT = 2000
SYNC_EVERY = 5.0  # seconds between two looks at what changed
EDIT_EVERY = 1.5  # seconds between two edits of an answer being written (Discord allows a few edits every few seconds)
SHOWN = 1900  # the end of an answer is what is shown while it is written: the rest comes when it ends
ARCHIVE_AFTER = 10080  # minutes: a thread goes to the archive after a week without a message (the server follows)
STOP_CODES = {
    30013: "the server has as many channels as Discord allows (500): delete some, then resume the mirror",
    30030: "the server has as many categories as Discord allows (50): delete some, then resume the mirror",
}
FORUM_NAMES = {"incognito": "Incognito"}
DEFAULT_FORUM = "Conversations"


class Stopped(Exception):
    """Discord refused for good (a limit): the mirror stops and says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Discord's 2,000 characters: cut at a paragraph, a line or a word where it can."""
    text = text.strip() or "…"
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


@dataclass
class Stream:
    """An answer being written: where it is, what it says so far, and when its message was last edited."""

    conversation: str
    thread: discord.Thread
    message: discord.Message
    summary: str = ""
    text: str = ""
    last: float = field(default_factory=time.monotonic)

    def shown(self) -> str:
        tail = self.text[-SHOWN:] if len(self.text) > SHOWN else self.text
        return "\n".join(part for part in (self.summary, tail.strip()) if part) or "…"


class Mirror:
    def __init__(self, bot: discord.Client, clara):
        self.bot = bot
        self.clara = clara
        self.cursor = 0
        self.lock = asyncio.Lock()
        self.stopped = ""
        self.places: dict[str, dict] = {}  # place key -> {"category": id, "forum": id}, as Discord has them
        self.forums: dict[int, str] = {}  # forum channel id -> place key
        self.threads: dict[str, str] = {}  # conversation id -> thread id
        self.conversations: dict[int, str] = {}  # thread id -> conversation id
        self.streams: dict[str, Stream] = {}  # turn key -> the answer being written
        self.removed: set[int] = set()  # channels the bot removed itself: their deletion is not news for the server

    # ------------------------------------------------------------------
    # Lookups and reports
    # ------------------------------------------------------------------
    def guild(self) -> discord.Guild | None:
        guild_id = getattr(self.bot, "guild_id", None)
        return self.bot.get_guild(guild_id) if guild_id else None

    def conversation_of(self, thread_id: int) -> str | None:
        return self.conversations.get(thread_id)

    def remember_thread(self, thread_id: str, conversation: str) -> None:
        for old in [tid for tid, cid in self.conversations.items() if cid == conversation]:
            self.conversations.pop(old, None)
        self.threads[conversation] = thread_id
        self.conversations[int(thread_id)] = conversation

    async def report(self, **body) -> None:
        try:
            await self.clara.call("POST", "/v1/discord/report", json=body)
        except ClaraError as error:
            log.warning("cannot report the mirror's results: %s", error)

    async def event(self, body: dict) -> None:
        try:
            await self.clara.call("POST", "/v1/discord/event", json=body)
        except ClaraError as error:
            log.warning("the server refused a Discord event (%s): %s", body.get("kind"), error)

    # ------------------------------------------------------------------
    # The loop: what the server asks for, made in Discord
    # ------------------------------------------------------------------
    async def run(self) -> None:
        while True:
            try:
                await self.sync_once()
            except Stopped as stop:
                self.stopped = stop.reason
                log.error("the Discord mirror stopped: %s", stop.reason)
                await self.report(stopped=stop.reason)
            except ClaraError as error:
                log.warning("the Discord mirror: %s", error)
            except discord.HTTPException as error:
                log.warning("Discord refused a change of the mirror: %s", error)
                await self.report(error=str(error)[:500])
            except Exception:
                log.exception("the Discord mirror failed")
            await asyncio.sleep(SYNC_EVERY)

    async def sync_once(self) -> None:
        data = await self.clara.call("GET", "/v1/discord/sync", params={"since": self.cursor})
        if data.get("stopped"):
            self.stopped = data["stopped"]
            return
        if self.stopped:  # the server was told to go on again
            self.stopped = ""
            await self.report(stopped="", error="")
        guild = self.guild()
        if guild is None:
            return
        async with self.lock:
            for place in data["places"]:
                await self.make_place(guild, place)
            for dropped in data["dropped"]:
                await self.drop_place(dropped)
            if data["trash"]:
                for thread_id in data["trash"]:
                    await self.remove_channel(int(thread_id))
                await self.report(cleared=data["trash"])
            for conversation in data["conversations"]:
                await self.conversation(conversation)
            self.cursor = data["cursor"]

    async def make_place(self, guild: discord.Guild, place: dict) -> None:
        """A category and a forum for a place, made where they are missing; renamed when the server's name for it changed."""
        key = place["key"]
        category = guild.get_channel(int(place["category_id"])) if place["category_id"] else None
        forum = guild.get_channel(int(place["forum_id"])) if place["forum_id"] else None
        if place["forum_id"] and forum is None:  # deleted on Discord: you are asked, and the place is made again
            log.info("the forum of %s was deleted on Discord", key)
            self.forums.pop(int(place["forum_id"]), None)
            await self.report(places=[{"key": key, "category_id": "", "forum_id": ""}])
            await self.event({"kind": "forum_deleted", "place": key})
        if category is None:
            category = await self.create(guild.create_category, name=place["name"])
            forum = None
        elif category.name != place["name"]:
            await category.edit(name=place["name"])
        if forum is None:
            forum = await self.create(guild.create_forum, name=FORUM_NAMES.get(key, DEFAULT_FORUM), category=category)
        made = {"category": category.id, "forum": forum.id}
        if self.places.get(key) != made:
            self.places[key] = made
            self.forums[forum.id] = key
            await self.report(places=[{"key": key, "category_id": str(category.id), "forum_id": str(forum.id)}])

    @staticmethod
    async def create(make, **options):
        """A channel or a category made; Discord's limits stop the mirror, any other refusal is raised as it is."""
        try:
            return await make(**options)
        except discord.HTTPException as error:
            if error.code in STOP_CODES:
                raise Stopped(STOP_CODES[error.code]) from None
            raise

    async def drop_place(self, dropped: dict) -> None:
        """A place the server no longer shows (its project was deleted, or its history was switched off for good)."""
        for channel_id in (dropped["forum_id"], dropped["category_id"]):
            if channel_id:
                await self.remove_channel(int(channel_id))
        self.places.pop(dropped["key"], None)
        await self.report(dropped=[dropped["key"]])

    async def remove_channel(self, channel_id: int) -> None:
        self.removed.add(channel_id)
        self.conversations.pop(channel_id, None)
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.NotFound:
                return
        with contextlib.suppress(discord.NotFound):
            await channel.delete()

    async def existing_thread(self, thread_id: str) -> discord.Thread | None:
        """The thread, archived or not; None when Discord no longer has it."""
        try:
            channel = self.bot.get_channel(int(thread_id)) or await self.bot.fetch_channel(int(thread_id))
        except discord.NotFound:
            return None
        return channel if hasattr(channel, "create_thread") is False and hasattr(channel, "archived") else None

    async def thread_of(self, conversation: str, wait: float = 0.0) -> discord.Thread | None:
        """The thread of a conversation; with `wait`, up to that many seconds for the bot to make it (a run's thread is made
        as its first turn starts, while the event that speaks of it may already be here)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            thread_id = self.threads.get(conversation)
            if thread_id:
                return await self.existing_thread(thread_id)
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    async def conversation(self, data: dict) -> None:
        """One conversation: its thread (made once it has a turn and is shown; renamed and archived as Clara has it), and
        its messages as they should read."""
        conversation = data["id"]
        known = data["thread"]
        if known and known["thread_id"] == "gone":
            return  # its thread was deleted on Discord: you are asked about it, and nothing is made again meanwhile
        if data["place"] is None:  # not shown on Discord any more (local-only, or its history is switched off)
            if known and known["thread_id"] and not known["archived"]:
                thread = await self.existing_thread(known["thread_id"])
                if thread is not None:
                    await thread.edit(archived=True)
                    await self.report(threads=[self.thread_state(conversation, "", thread, True)])
            return
        thread = None
        if known and known["thread_id"] and known["place"] == data["place"]:
            thread = await self.existing_thread(known["thread_id"])
            if thread is None:  # deleted on Discord without the bot having seen it: it is news for the server
                await self.thread_gone(conversation, known["thread_id"])
                return
        elif known and known["thread_id"]:  # moved to another place: the old thread goes, the new one is made
            await self.remove_channel(int(known["thread_id"]))
        if not data["turns"]:
            return
        if thread is None:
            thread, _ = await self.make_thread(conversation, data)
            if thread is None:
                return
        wanted = data["archived"]
        if thread.archived and not wanted:
            await thread.edit(archived=False)
        if thread.name != data["title"]:
            await thread.edit(name=data["title"])
        if not wanted:
            await self.write_turns(thread, data)
        if thread.archived != wanted:
            await thread.edit(archived=wanted)
        await self.report(threads=[self.thread_state(conversation, data["place"], thread, wanted)])

    def thread_state(self, conversation: str, place: str, thread: discord.Thread, archived: bool) -> dict:
        self.remember_thread(str(thread.id), conversation)
        return {"conversation": conversation, "thread_id": str(thread.id), "place": place, "archived": archived}

    async def thread_gone(self, conversation: str, thread_id: str) -> None:
        """The event goes first: the server finds the conversation by the thread's id, which "gone" would replace."""
        self.conversations.pop(int(thread_id), None)
        await self.event({"kind": "thread_deleted", "thread": thread_id})
        await self.report(threads=[{"conversation": conversation, "thread_id": "gone", "place": "", "archived": False}])

    async def make_thread(self, conversation: str, data: dict) -> tuple[discord.Thread | None, bool]:
        """A thread for a conversation, in its place's forum. Its starter is the first question (or its title). Returns
        (the thread, whether it was made now) — (None, False) while its place is not made yet."""
        known = self.places.get(data["place"])
        forum = self.bot.get_channel(known["forum"]) if known else None
        if forum is None or not hasattr(forum, "create_thread"):
            return None, False
        first = data["turns"][0] if data["turns"] else None
        question = first["user"] if first and not first["user"]["ref"] else None
        starter = question["text"] if question else data["title"]
        parts = split(starter)
        made = await self.create(
            forum.create_thread, name=data["title"], content=parts[0], auto_archive_duration=ARCHIVE_AFTER
        )
        ids = [str(made.message.id)]
        for part in parts[1:]:
            ids.append(str((await made.thread.send(part)).id))
        self.remember_thread(str(made.thread.id), conversation)
        posts = []
        if first is not None and question is not None:
            posts.append(
                {
                    "conversation": conversation,
                    "turn": first["key"],
                    "side": "user",
                    "ids": ids,
                    "digest": question["digest"],
                }
            )
            data["posted"].setdefault(first["key"], {})["user"] = {"ids": ids, "digest": question["digest"]}
        await self.report(
            threads=[
                {
                    "conversation": conversation,
                    "thread_id": str(made.thread.id),
                    "place": data["place"],
                    "archived": False,
                }
            ],
            posts=posts,
        )
        return made.thread, True

    async def write_turns(self, thread: discord.Thread, data: dict) -> None:
        """The messages of each turn as they should read: a changed one is edited, one that left the conversation is taken
        away (except a question that is Discord's own: its message is never touched)."""
        conversation = data["id"]
        wanted = {turn["key"] for turn in data["turns"]}
        refs = {int(turn["user"]["ref"]) for turn in data["turns"] if turn["user"]["ref"]}
        for turn in data["turns"]:
            posted = data["posted"].get(turn["key"], {})
            if not turn["user"]["ref"]:
                await self.write(thread, conversation, turn["key"], "user", posted.get("user"), turn["user"], refs)
            if turn["reply"] is not None and turn["key"] not in self.streams:
                await self.write(thread, conversation, turn["key"], "reply", posted.get("reply"), turn["reply"], refs)
        for key, sides in data["posted"].items():
            if key in wanted or key in self.streams:
                continue
            for side, post in sides.items():
                for message_id in post["ids"]:
                    if int(message_id) not in refs:
                        with contextlib.suppress(discord.HTTPException):
                            await thread.get_partial_message(int(message_id)).delete()
                await self.report(
                    posts=[{"conversation": conversation, "turn": key, "side": side, "ids": [], "digest": ""}]
                )

    async def write(
        self, thread, conversation: str, turn: str, side: str, posted: dict | None, body: dict, refs: set[int]
    ) -> None:
        if posted and posted["digest"] == body["digest"]:
            return
        parts = split(body["text"])
        ids = list(posted["ids"]) if posted else []
        written: list[str] = []
        for index, part in enumerate(parts):
            if index < len(ids):
                await thread.get_partial_message(int(ids[index])).edit(content=part)
                written.append(ids[index])
            else:
                written.append(str((await thread.send(part)).id))
        for leftover in ids[len(parts) :]:
            if int(leftover) not in refs:
                with contextlib.suppress(discord.HTTPException):
                    await thread.get_partial_message(int(leftover)).delete()
        await self.report(
            posts=[{"conversation": conversation, "turn": turn, "side": side, "ids": written, "digest": body["digest"]}]
        )

    # ------------------------------------------------------------------
    # The answer as it is written
    # ------------------------------------------------------------------
    async def live(self) -> None:
        """Listens to the server's live feed, and listens again when it drops."""
        failures = 0
        while True:
            try:
                async for event in self.clara.stream("GET", "/v1/discord/live", read=60.0):
                    failures = 0
                    await self.on_live(event)
            except ClaraError as error:
                if error.status in (401, 403):
                    log.error("Clara refuses the bot's token for the live feed (%s)", error)
                    return
                log.info("live feed: %s", error)
            except Exception:
                log.exception("live feed failed")
            failures += 1
            await asyncio.sleep(min(60.0, 2.0**failures))

    async def on_live(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "start":
            await self.start(event)
            return
        stream = self.streams.get(event.get("turn", ""))
        if stream is None:
            return
        if kind == "token":
            stream.text += event.get("text", "")
        elif kind == "summary":
            stream.summary = event.get("text", "")
        elif kind == "done":
            await self.finish(event)
            return
        else:
            return
        if time.monotonic() - stream.last >= EDIT_EVERY:
            await self.show(stream)

    async def start(self, event: dict) -> None:
        """A turn began: its question is in Discord (unless it came from there), and its answer starts as a message."""
        conversation, turn = event["conversation"], event["turn"]
        async with self.lock:
            thread, made = await self.thread_for(conversation, event)
            if thread is None:
                return
            if not event.get("ref") and not made:
                sent = await thread.send(split(event["text"])[0])
                await self.report(
                    posts=[
                        {
                            "conversation": conversation,
                            "turn": turn,
                            "side": "user",
                            "ids": [str(sent.id)],
                            "digest": event["digest"],
                        }
                    ]
                )
            message = await thread.send("…")
            self.streams[turn] = Stream(conversation=conversation, thread=thread, message=message)
            await self.report(
                posts=[
                    {
                        "conversation": conversation,
                        "turn": turn,
                        "side": "reply",
                        "ids": [str(message.id)],
                        "digest": "",
                    }
                ]
            )

    async def thread_for(self, conversation: str, event: dict) -> tuple[discord.Thread | None, bool]:
        """The conversation's thread, made when it has none yet. Returns (thread, whether its first message is this
        turn's question).

        A conversation that had turns before its thread (its place was not shown then, the bot was off) is made whole:
        the thread starts with its first question and the turns that came before this one follow, in order, so the
        question of this turn lands after them, where it belongs."""
        known = event.get("thread") or self.threads.get(conversation)
        if known:
            thread = await self.existing_thread(known)
            if thread is not None:
                return thread, False
        earlier = event.get("earlier") or []
        current = {
            "key": event["turn"],
            "user": {"text": event["text"], "ref": event.get("ref", ""), "digest": event["digest"]},
            "reply": None,
        }
        data = {
            "id": conversation,
            "title": event.get("title") or "Conversation",
            "place": event.get("place") or "",
            "turns": [*earlier, current],
            "posted": {},
        }
        thread, made = await self.make_thread(conversation, data)
        if thread is None or not earlier:
            return thread, made
        await self.write_turns(thread, {**data, "turns": earlier})
        return thread, False

    async def show(self, stream: Stream) -> None:
        stream.last = time.monotonic()
        with contextlib.suppress(discord.HTTPException):
            await stream.message.edit(content=stream.shown())

    async def finish(self, event: dict) -> None:
        async with self.lock:  # with the sync: the answer is posted once, by one of them
            await self.finish_stream(event)

    async def finish_stream(self, event: dict) -> None:
        turn = event["turn"]
        stream = self.streams.pop(turn, None)
        if stream is None:
            return  # nothing was shown: the next sync writes the answer as it stands
        text = event.get("text", "")
        if text.strip():
            ids = [str(stream.message.id)]
            parts = split(text)
            with contextlib.suppress(discord.HTTPException):
                await stream.message.edit(content=parts[0])
            for part in parts[1:]:
                ids.append(str((await stream.thread.send(part)).id))
        else:
            ids = []
            with contextlib.suppress(discord.HTTPException):
                await stream.message.delete()
        await self.report(
            posts=[
                {
                    "conversation": stream.conversation,
                    "turn": turn,
                    "side": "reply",
                    "ids": ids,
                    "digest": event.get("digest", ""),
                }
            ]
        )
        for path in event.get("files", []):
            try:
                data = await self.clara.file(path)
                await stream.thread.send(file=discord.File(io.BytesIO(data), filename=path.rsplit("/", 1)[-1]))
            except (ClaraError, discord.HTTPException) as error:
                log.warning("cannot send %s: %s", path, error)

    # ------------------------------------------------------------------
    # What Discord changed, told to the server
    # ------------------------------------------------------------------
    async def thread_changed(self, before: discord.Thread, after: discord.Thread) -> None:
        if before.archived == after.archived or after.id in self.removed:
            return
        if self.conversation_of(after.id) is not None:
            await self.event({"kind": "thread_archived", "thread": str(after.id), "archived": after.archived})

    async def thread_deleted(self, thread: discord.Thread) -> None:
        if thread.id in self.removed:
            self.removed.discard(thread.id)
            return
        if self.conversation_of(thread.id) is not None:
            self.conversations.pop(thread.id, None)
            await self.event({"kind": "thread_deleted", "thread": str(thread.id)})

    async def forum_deleted(self, channel: discord.abc.GuildChannel) -> None:
        if channel.id in self.removed:
            self.removed.discard(channel.id)
            return
        key = self.forums.pop(channel.id, None)
        if key is not None:
            self.places.pop(key, None)
            await self.report(places=[{"key": key, "category_id": "", "forum_id": ""}])
            await self.event({"kind": "forum_deleted", "place": key})
