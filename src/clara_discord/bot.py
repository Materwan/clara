"""`clara-discord`: the Discord bot, a separate process and a client of Clara like any other. It lives in your private
Discord server (you and the bot, nobody else) and uses four text channels, given by id; it has no direct-message
function: DMs are ignored.

* It connects with the guild and message-content intents, no member list. It answers **one** Discord user id
  (yours), in **one** channel (#general) and in commands, and ignores every other user and channel.
* What you write in #general goes to Clara in the current conversation (`/new` starts another), with its files (PDF,
  Word, text, pictures); Clara's answer, and notes she writes in `notes/`, come back there.
* Approval requests go to #approval with buttons: allow once, allow always, refuse once, refuse always (#general
  says where to answer). The bot's token may answer every level but *destructive* (deletions, overwrites, merges):
  those say to answer on the web or with clara-admin. The server enforces it, whatever the bot shows.
* Alerts go to #alerts; reminders, notifications, task reminders, schedule summaries and update news go to
  #notification (the event stream; what was missed while the bot was away arrives when it reconnects). The server
  says the channel of each event (you choose it on the web site, or switch the kind off); ROUTES below is what the bot
  does with a server that does not say.
* An automation that delivers to Discord lives in one forum thread (its conversation, in its automation's forum) and
  puts nothing in #general: when a run starts the bot posts its link in the channel the server names (#notification
  by default), the run's questions, reminder and end are written in the thread, and you answer there. The answer itself
  is already in the thread (the mirror writes it), so the `said` message is not posted a second time.
* When any other scheduled run asks you something (an `ask` event) the question is posted in #general, with a thread under
  it when the run has the Discord thread pack: what you write in that thread goes to the run's conversation, whatever the
  current conversation is. If the bot may not create threads, or the run has no thread, the run's conversation becomes
  the current one instead. When the run ends (or is closed without your answer) the bot says so in #general and
  deletes the thread (it archives it when it may not delete); the conversation stays in Clara. A run can also open,
  write in and remove a thread by itself.
* An automation that delivers to Discord, or Clara herself (`discord_say`), puts a message in #general (`ask` event,
  stage `said`): its conversation becomes the current one, so what you write next goes to it.
* A heartbeat every minute lets the server alert you when the bot is gone. It also tells the channels the bot found and
  the rights it lacks in them, for the Discord page of the web site, which can have a test message posted in each.

Exit codes: 0 stopped, 2 a setting, the token, the channels or an intent is wrong (starting again will not help),
3 another bot already runs with the same state file.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import io
import json
import logging
import re
import signal
import sys
from datetime import UTC, datetime
from typing import Literal

import discord
from discord import app_commands

from clara.timeutil import ZoneError, human, parse_iso, zone

from .api import Clara, ClaraError
from .mirror import Mirror
from .settings import Settings, SettingsError

log = logging.getLogger("clara_discord")

LIMIT = 2000
MAX_FILES = 4
MAX_FILE_BYTES = 25_000_000
MAX_TOTAL_BYTES = 34_000_000  # what Clara takes for one message in all
HEARTBEAT = 60.0
TEST_MAX_AGE = 120.0  # a test message older than this (the bot was away) is dropped: it would only confuse
STARTED_MAX_AGE = 900.0  # so is the notice that a run began, when the bot was away longer than this
STARTED_SOURCE = "automations"  # the source the server gives that notice
THREAD_WAIT = 10.0  # seconds a line for a run's forum thread waits for the thread to be made
RETRY = (2, 5, 15, 30, 60)
EXIT_SETTINGS = 2
EXIT_RUNNING = 3
ANSWERS = (
    ("allow_once", "Allow once", discord.ButtonStyle.success),
    ("allow_always", "Allow always", discord.ButtonStyle.primary),
    ("refuse_once", "Refuse once", discord.ButtonStyle.secondary),
    ("refuse_always", "Refuse always", discord.ButtonStyle.danger),
)
THREADS_KEPT = 30
THREAD_ARCHIVE_MINUTES = 1440
ICONS = {"reminder": "⏰", "notification": "🔔", "task": "📋", "schedule": "🗓", "alert": "⚠️", "update": "⬆️"}
# event kind -> channel (approvals have their own path: buttons in #approval; replies to you go to #general)
ROUTES = {
    "reminder": "notification",
    "notification": "notification",
    "task": "notification",
    "schedule": "notification",
    "update": "notification",
    "alert": "alerts",
}
CHOICES = ("general", "notification", "alerts")  # the channels the server may send an event to (or "off")
TEST_SOURCE = "discord-test"


class ChannelError(RuntimeError):
    """A configured channel is missing, is not a text channel, or the four are not in one server."""


def due_text(task: dict, fallback_zone: str) -> str:
    """A task's deadline on the clock of its own time zone. The server sends it in UTC: cut as it came, it would read
    as local time, hours off."""
    try:
        tz = zone(task.get("timezone") or fallback_zone)
    except ZoneError:
        tz = zone(fallback_zone)
    return human(parse_iso(task["due_at"]), tz)


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Discord's 2,000 characters: cut at paragraph, then line, then word boundaries."""
    text = text.strip() or "(empty)"
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


class ApprovalButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"clara:approval:(?P<id>[0-9]+):(?P<answer>allow_once|allow_always|refuse_once|refuse_always)",
):
    def __init__(self, approval_id: int, answer: str):
        label, style = next((label, style) for key, label, style in ANSWERS if key == answer)
        super().__init__(
            discord.ui.Button(label=label, style=style, custom_id=f"clara:approval:{approval_id}:{answer}")
        )
        self.approval_id = approval_id
        self.answer = answer

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /):
        return cls(int(match["id"]), match["answer"])

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: ClaraBot = interaction.client  # type: ignore[assignment]
        if interaction.user.id != bot.settings.owner_id:
            await interaction.response.send_message("This is not yours to answer.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            settled = await bot.clara.call(
                "POST", f"/v1/approvals/{self.approval_id}/decide", json={"answer": self.answer}
            )
            text = f"🔐 #{self.approval_id} {settled['summary']}: **{settled['status']}**"
        except ClaraError as error:
            text = f"🔐 #{self.approval_id}: {error}"
        with contextlib.suppress(discord.HTTPException):
            await interaction.message.edit(content=text, view=None)


def approval_view(approval_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for answer, _, _ in ANSWERS:
        view.add_item(ApprovalButton(approval_id, answer))
    return view


class ClaraBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True  # the channels of your server
        intents.guild_messages = True
        intents.message_content = True  # privileged: switched on in the developer portal; no members, no DMs
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.clara = Clara(settings.clara_url, settings.clara_token)
        self.mirror = Mirror(self, self.clara)  # the conversations kept in forums, in your server
        self.tree = app_commands.CommandTree(self)
        self.channels: dict[str, discord.TextChannel] = {}  # general, notification, approval, alerts
        self.guild_id: int | None = None
        self.asked: dict[int, discord.Message] = {}  # approval id -> its message in #approval
        self.answering: set[str] = set()  # conversations an answer is being written in
        self.finished: dict[
            str, dict
        ] = {}  # conversation -> its "recap written" event, held until that answer is posted
        self._tasks: list[asyncio.Task] = []
        self._register_commands()

    # ------------------------------------------------------------------
    # State: the current conversation
    # ------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            data = json.loads(self.settings.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, **changes) -> None:
        self.settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.state_file.write_text(json.dumps({**self._state(), **changes}), encoding="utf-8")

    def conversation(self) -> str | None:
        return self._state().get("conversation")

    def set_conversation(self, conversation: str | None) -> None:
        self._save(conversation=conversation)

    def threads(self) -> dict[str, str]:
        """The threads of scheduled runs: Discord thread id -> Clara conversation."""
        found = self._state().get("threads")
        return found if isinstance(found, dict) else {}

    def remember_thread(self, thread_id: int, conversation: str) -> None:
        kept = {key: value for key, value in self.threads().items() if value != conversation}
        kept[str(thread_id)] = conversation
        self._save(threads=dict(list(kept.items())[-THREADS_KEPT:]))

    def forget_thread(self, thread_id: int) -> None:
        self._save(threads={key: value for key, value in self.threads().items() if key != str(thread_id)})

    def thread_of(self, conversation: str | None) -> int | None:
        return (
            next((int(key) for key, value in self.threads().items() if value == conversation), None)
            if conversation
            else None
        )

    def bot_forum(self, key: str) -> discord.ForumChannel | None:
        place = self.mirror.places.get(key)
        channel = self.get_channel(place["forum"]) if place else None
        return channel if isinstance(channel, discord.ForumChannel) else None

    async def resolve_channels(self) -> None:
        """Find the four channels by id. A missing one is refused: there is no direct-message fallback."""
        wrong = []
        for name, channel_id in self.settings.channels.items():
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                wrong.append(f"#{name} ({channel_id}) does not exist, or the bot cannot see it")
                continue
            if not isinstance(channel, discord.TextChannel):
                wrong.append(f"#{name} ({channel_id}) is not a text channel")
                continue
            self.channels[name] = channel
        if wrong:
            raise ChannelError(
                "; ".join(wrong) + ". Check the CLARA_DISCORD_CHANNEL_* ids and that the bot is in the server."
            )
        guilds = {channel.guild.id for channel in self.channels.values()}
        if len(guilds) != 1:
            raise ChannelError("The four channels must all be in one server.")
        self.guild_id = guilds.pop()

    def lacking_rights(self) -> dict[str, tuple[str, list[str]]]:
        """For each channel the bot can look at: its name in Discord and the rights it lacks there."""
        guild = self.get_guild(self.guild_id) if self.guild_id else None
        if guild is None:
            return {}
        found: dict[str, tuple[str, list[str]]] = {}
        for name, channel in self.channels.items():
            live = self.get_channel(channel.id)
            if live is None:
                continue
            allowed = live.permissions_for(guild.me)
            wanted = ("view_channel", "send_messages", "attach_files")
            if name == "general":  # a run's thread, opened and then removed
                wanted += ("create_public_threads", "send_messages_in_threads", "manage_threads")
            found[name] = (live.name, [right for right in wanted if not getattr(allowed, right)])
        return found

    def check_permissions(self) -> None:
        for name, (_, lacking) in self.lacking_rights().items():
            if lacking:
                log.warning("in #%s the bot lacks: %s", name, ", ".join(lacking))

    # ------------------------------------------------------------------
    # Life
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        await self.resolve_channels()
        with contextlib.suppress(NotImplementedError, RuntimeError):  # SIGTERM (the server stopping us) closes cleanly
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(self.close()))
        self.add_dynamic_items(ApprovalButton)
        await self.tree.sync()
        self._tasks.append(asyncio.create_task(self.relay(), name="relay"))
        self._tasks.append(asyncio.create_task(self.heartbeat(), name="heartbeat"))
        self._tasks.append(asyncio.create_task(self.mirror.run(), name="mirror"))
        self._tasks.append(asyncio.create_task(self.mirror.live(), name="mirror-live"))

    async def close(self) -> None:
        if self.is_closed():  # discord.py and the SIGTERM handler may both close: the second one has nothing left to do
            return
        for task in self._tasks:
            task.cancel()
        with contextlib.suppress(ClaraError):
            await self.clara.call("POST", "/v1/clients/heartbeat", json={"state": "disconnected"})
        await self.clara.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info(
            "connected as %s; answering only Discord user %s, in #general (%s) of server %s",
            self.user,
            self.settings.owner_id,
            self.settings.channels["general"],
            self.guild_id,
        )
        self.check_permissions()

    async def beat(self, test: dict | None = None) -> None:
        """One heartbeat. Connected, it says who the bot is and what it sees in each channel; `test` tells how a test
        message went."""
        ready = self.is_ready() and not self.is_closed()
        body: dict = {"state": "connected" if ready else "disconnected"}
        if ready:
            body["bot"] = str(self.user)
            body["channels"] = {
                name: {"name": found, "missing": lacking} for name, (found, lacking) in self.lacking_rights().items()
            }
            if test is not None:
                body["test"] = test
        try:
            await self.clara.call("POST", "/v1/clients/heartbeat", json=body)
        except ClaraError as error:
            log.warning("heartbeat: %s", error)

    async def heartbeat(self) -> None:
        while True:
            await self.beat()
            await asyncio.sleep(HEARTBEAT)

    # ------------------------------------------------------------------
    # Your messages (#general only)
    # ------------------------------------------------------------------
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id != self.settings.owner_id:
            return  # every other id is ignored, without a word
        channel = message.channel
        general = self.settings.channels["general"]
        if isinstance(channel, discord.Thread) and channel.parent_id in self.mirror.forums:
            await self.mirror_message(message)
            return
        if channel.id == general:  # #general is not a chat any more: the conversations are threads in their forums
            await channel.send(
                "Write in the Conversations forum (each conversation is a thread there), or `/new` to start one."
            )
            return
        if isinstance(channel, discord.Thread) and channel.parent_id == general:
            conversation = self.threads().get(str(channel.id))  # a thread of a scheduled run
            if conversation is None:
                return
            files = await self.attachments_of(message)
            if files is None:
                return
            if not message.content.strip() and not files:
                return
            await self.ask(
                channel,
                {
                    "message": message.content,
                    "attachments": files,
                    "timezone": self.settings.timezone,
                    "conversation": conversation,
                },
                remember=False,
            )
        else:
            return  # the other channels, other servers and DMs are not for chatting

    async def attachments_of(self, message: discord.Message) -> list[dict] | None:
        """The files of a message, read, or None when one of them is refused (the refusal is said in the channel)."""
        if len(message.attachments) > MAX_FILES:
            await message.channel.send(f"{MAX_FILES} files at most per message: send the others in another one.")
            return None
        if sum(attachment.size for attachment in message.attachments) > MAX_TOTAL_BYTES:
            await message.channel.send(f"The files of one message may weigh {MAX_TOTAL_BYTES // 1_000_000} MB in all.")
            return None
        files = []
        for attachment in message.attachments:
            if attachment.size > MAX_FILE_BYTES:
                await message.channel.send(f"{attachment.filename} is too big (25 MB at most).")
                return None
            files.append({"name": attachment.filename, "data": base64.b64encode(await attachment.read()).decode()})
        return files

    async def mirror_message(self, message: discord.Message) -> None:
        """What you write in a conversation's thread (or start as a new post of a forum) goes to Clara, in that
        conversation, as the web site would send it. Its answer is written in the thread by the mirror as it comes."""
        channel = message.channel
        key = self.mirror.forums[channel.parent_id]
        conversation = self.mirror.conversation_of(channel.id)
        files = await self.attachments_of(message)
        if files is None or (not message.content.strip() and not files):
            return
        body: dict = {
            "message": message.content,
            "attachments": files,
            "timezone": self.settings.timezone,
            "ref": str(message.id),
        }
        if conversation is None:
            if channel.id != message.id:
                return  # a thread the server does not know: not a conversation
            if key.startswith("project:"):
                body["project"] = int(key.split(":", 1)[1])
            elif key == "incognito":
                body["incognito"] = True
            elif key != "main":
                await channel.send("Automations are Clara's: a new post starts a conversation in Conversations.")
                return
            body["mirror_thread"] = str(channel.id)
        else:
            body["conversation"] = conversation
            if channel.archived and not await self.reopen(conversation, channel):
                return
        await self.ask(channel, body, remember=False, mirrored=True)

    async def reopen(self, conversation: str, thread: discord.Thread) -> bool:
        """A message in an archived thread: Discord reopened the thread, so Clara follows when the archive came from
        Discord (its idle week); one the web site made stays archived, and the message is said to be refused."""
        try:
            info = await self.clara.call("GET", f"/v1/conversations/{conversation}")
        except ClaraError as error:
            await thread.send(f"⚠️ {error}")
            return False
        if not info.get("archived"):  # Clara does not have it archived: Discord's state is the one that was wrong
            return True
        if info.get("archived_by") == "discord":
            await self.clara.call("PATCH", f"/v1/conversations/{conversation}", json={"archived": False})
            return True
        await thread.edit(archived=True)
        await thread.send("This conversation is archived: unarchive it on the web site to write in it.")
        return False

    async def ask(
        self, channel: discord.abc.Messageable, body: dict, remember: bool = True, mirrored: bool = False
    ) -> None:
        """Send a message to Clara. `remember`: the conversation becomes the current one (not for a check-in thread, which
        has its own). `mirrored`: the answer is written by the mirror as it comes (text and files), so only approvals and
        failures are said here."""
        reply = ""
        done: dict = {}
        conversation = body.get("conversation")
        if conversation:
            self.answering.add(conversation)
        try:
            async with channel.typing():
                async for event in self.clara.stream("POST", "/v1/chat/stream", body):
                    kind = event["type"]
                    if kind == "turn" and event.get("conversation") and remember:
                        self.set_conversation(event["conversation"])
                    elif kind == "approval":
                        if await self.show_approval(event["approval"]):
                            await channel.send(
                                f"🔐 Waiting for your answer in <#{self.settings.channels['approval']}>."
                            )
                    elif kind == "done":
                        done = event
                        reply = event.get("reply") or ""
                    elif kind == "error":
                        reply = f"⚠️ {event['message']}"
        except ClaraError as error:
            if error.status == 404 and "conversation" in str(error).lower() and remember:
                self.set_conversation(None)
            reply = f"⚠️ {error}"
        finally:
            self.answering.discard(conversation)
        if mirrored and reply.startswith("⚠️"):
            await channel.send(reply[:LIMIT])
        elif not mirrored:
            for part in split_message(reply or "(no answer)"):
                await channel.send(part)
            for card in done.get("files", []):
                try:
                    data = await self.clara.file(card["path"])
                    await channel.send(file=discord.File(io.BytesIO(data), filename=card["path"].rsplit("/", 1)[-1]))
                except (ClaraError, discord.HTTPException) as error:
                    log.warning("cannot send %s: %s", card.get("path"), error)
        if conversation in self.finished:  # the recap was written during this answer: now the thread can go
            await self.close_ask(self.finished.pop(conversation))

    # ------------------------------------------------------------------
    # What Discord changes by itself: archived, edited, deleted
    # ------------------------------------------------------------------
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        await self.mirror.thread_changed(before, after)

    async def on_thread_delete(self, thread: discord.Thread) -> None:
        await self.mirror.thread_deleted(thread)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.mirror.forum_deleted(channel)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Your message in a conversation's thread was edited: the server asks Clara again, when it is your last question."""
        data = payload.data
        if str(data.get("author", {}).get("id", "")) != str(self.settings.owner_id) or "content" not in data:
            return
        channel = self.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread) or channel.parent_id not in self.mirror.forums:
            return
        try:
            await self.clara.call(
                "POST",
                "/v1/discord/event",
                json={"kind": "edit", "message": str(payload.message_id), "text": data["content"][:2000]},
            )
        except ClaraError as error:
            await channel.send(f"⚠️ {error}")

    async def tools_text(self, pack: str | None, state: str | None, current: str | None = None) -> str:
        """The packs of the current conversation (a new one starts with web search), after switching one if asked. The
        conversation decides: what is on here is on wherever it is continued."""
        listing = (await self.clara.call("GET", "/v1/packs", params={"where": "chat"}))["packs"]
        current = current or self.conversation()
        active = (await self.clara.call("GET", f"/v1/conversations/{current}"))["packs"] if current else ["web"]
        note = ""
        if pack:
            wanted = pack.strip().lower()
            match = next((item for item in listing if wanted in (item["id"], item["label"].lower())), None)
            if match is None:
                return f"No tool called {pack!r}: " + ", ".join(item["id"] for item in listing) + "."
            on = (match["id"] not in active) if state is None else state == "on"
            active = [item for item in active if item != match["id"]] + ([match["id"]] if on else [])
            if current:
                await self.clara.call("PATCH", f"/v1/conversations/{current}", json={"packs": active})
            else:
                made = await self.clara.call("POST", "/v1/conversations", json={"packs": active})
                self.set_conversation(made["id"])
                note = "\n(A new conversation was started for it.)"
        lines = [
            f"{'✅' if item['id'] in active else '▫️'} `{item['id']}` — {item['label']}"
            + ("" if item["available"] else f" ({item['reason']})")
            for item in listing
        ]
        return "\n".join(lines) + note + "\n`/tools pack:<id> state:on|off` switches one."

    async def level_text(self, value: str | None, current: str | None = None) -> str:
        """The auto level of the current conversation, after setting it if asked (`follow`: none of its own)."""
        current = current or self.conversation()
        ladder = await self.clara.call("GET", "/v1/levels")
        names = ", ".join(f"`{level['position']}` {level['name']}" for level in ladder["levels"])
        if value:
            wanted = value.strip().lower()
            if wanted != "follow" and not (wanted.isdecimal() and int(wanted) <= ladder["top"]):
                return f"A level is {names}, or `follow`."
            if not current:
                return "Use it inside a conversation: the level belongs to one conversation."
            await self.clara.call(
                "PUT", f"/v1/conversations/{current}/level", json={"level": None if wanted == "follow" else int(wanted)}
            )
        if not current:
            return (
                f"No conversation here. The default for a chat is level {ladder['defaults']['chat']}. Levels: {names}."
            )
        found = await self.clara.call("GET", f"/v1/conversations/{current}/level")
        return f"Level `{found['level']}` ({found['name']}), from {found['source']}.\nLevels: {names}. `/level level:<n|follow>` changes it."

    async def show_approval(self, approval: dict) -> bool:
        """Post a request in #approval, once. True when it was posted now."""
        if approval["id"] in self.asked or approval.get("status", "pending") != "pending":
            return False
        text = f"🔐 Clara asks (#{approval['id']}, {approval['level']}): {approval['summary']}"
        if approval.get("reason"):
            text += f"\n_{approval['reason']}_"
        channel = self.channels["approval"]
        if approval["level"] == "destructive":
            self.asked[approval["id"]] = await channel.send(
                text + "\nThis one is answered on the web site or with clara-admin."
            )
        else:
            self.asked[approval["id"]] = await channel.send(text, view=approval_view(approval["id"]))
        return True

    # ------------------------------------------------------------------
    # Events from Clara
    # ------------------------------------------------------------------
    async def relay(self) -> None:
        failures = 0
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                async for event in self.clara.stream("GET", "/v1/events/stream", read=60.0):
                    failures = 0
                    await self.deliver(event)
            except ClaraError as error:
                if error.status in (401, 403):
                    log.error("Clara refuses the bot's token (%s): no events on Discord", error)
                    return
                log.info("event stream: %s", error)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event stream failed")
            await asyncio.sleep(RETRY[min(failures, len(RETRY) - 1)])
            failures += 1

    async def deliver(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "server":
            log.info("Clara: %s", event.get("state") or event.get("payload", {}).get("state"))
            return
        if kind == "approval":
            await self.show_approval(event["payload"]["approval"])
            return
        if kind == "approval_resolved":
            approval = event["payload"]["approval"]
            message = self.asked.pop(approval["id"], None)
            if message is not None:
                with contextlib.suppress(discord.HTTPException):
                    await message.edit(
                        content=f"🔐 #{approval['id']} {approval['summary']}: **{approval['status']}**", view=None
                    )
            return
        if kind == "ask":
            await self.deliver_ask(event)
            return
        if event.get("source") == TEST_SOURCE:
            await self.deliver_test(event)
            return
        if event.get("source") == STARTED_SOURCE:
            await self.deliver_started(event)
            return
        icon = ICONS.get(str(kind))
        told = event.get("channel")
        route = (
            told if told in (*CHOICES, "off") else ROUTES.get(str(kind))
        )  # the server's word, else the fallback table
        if icon is None or route is None or route == "off":
            return
        title = event.get("title") or ""
        text = (
            f"{icon} **{title}**\n{event.get('text', '')}"
            if title and kind != "reminder"
            else f"{icon} {event.get('text', '')}"
        )
        for part in split_message(text):
            await self.channels[route].send(part)

    async def deliver_test(self, event: dict) -> None:
        """A test message from the web site: posted in the channel it names, whatever the routes say, then the result is
        reported at once (the page is waiting for it)."""
        channel = self.channels.get(str(event.get("channel")))
        if channel is None:
            return
        try:
            if (datetime.now(UTC) - parse_iso(str(event.get("created_at")))).total_seconds() > TEST_MAX_AGE:
                return
        except ValueError:
            return
        ok, error = True, ""
        try:
            await channel.send(f"🧪 **{event.get('title') or 'Test'}**\n{event.get('text', '')}")
        except discord.HTTPException as problem:
            ok, error = False, problem.text or f"Discord refused ({problem.status})"
        await self.beat(test={"event": int(event.get("id") or 0), "ok": ok, "error": error})

    async def deliver_started(self, event: dict) -> None:
        """A run that lives in a forum thread began: a line with the thread's link, in the channel the server names (off:
        nothing). One the bot was too late for is dropped; one whose thread is not made in time goes without the link."""
        told = event.get("channel")
        route = told if told in (*CHOICES, "off") else "notification"
        if route == "off":
            return
        try:
            if (datetime.now(UTC) - parse_iso(str(event.get("created_at")))).total_seconds() > STARTED_MAX_AGE:
                return
        except ValueError:
            return
        conversation = event.get("conversation") or (event.get("payload") or {}).get("conversation")
        thread = await self.mirror.thread_of(conversation, THREAD_WAIT) if conversation else None
        link = f"\n🔗 {thread.jump_url}" if thread is not None else ""
        await self.channels[route].send(
            f"▶️ **{event.get('title') or 'Clara'}**\n{event.get('text') or 'Started.'}{link}"
        )

    async def deliver_ask(self, event: dict) -> None:
        """What a scheduled run says to you (`ask` events), by stage:

        asked, thread_open   the message in #general, with a thread under it (when the run has the thread pack, or
                             for thread_open); the thread carries your answers to the run's conversation
        said                 the message in #general (in the conversation's thread when it has one); the conversation
                             becomes the current one, so your next message goes to it
        thread_post          a message in that thread       reminder   the same, in the thread when there is one
        finished, closed     a line in #general, then the thread is removed (after the answer being written is posted)
        thread_close         the thread is removed

        A run that lives in a forum thread (`forum`) has every line but the answer written in that thread: nothing in
        #general, nothing removed. Without its thread (history switched off, the mirror stopped) the line goes where
        it went before, and no thread is opened in #general.
        """
        payload = event.get("payload") or {}
        conversation = payload.get("conversation") or event.get("conversation")
        stage = payload.get("stage")
        # a run that lives in a forum thread has its lines written there, and #general hears nothing
        if payload.get("forum") and conversation:
            thread = await self.mirror.thread_of(conversation, THREAD_WAIT)
            if thread is not None:
                if stage not in ("said", "thread_close"):  # the answer is already there: the mirror wrote it
                    for part in split_message(f"📓 **{event.get('title') or 'Clara'}**\n{event.get('text', '')}"):
                        await thread.send(part)
                return
        if stage in ("finished", "closed", "thread_close") and conversation:
            if conversation in self.answering:
                self.finished[conversation] = event
            else:
                await self.close_ask(event)
            return
        mirrored = self.mirror.threads.get(conversation) if conversation else None
        if mirrored is not None:  # a conversation kept in Discord: what Clara says to you goes in its thread
            thread = await self.mirror.existing_thread(mirrored)
            if thread is not None:
                for part in split_message(f"📓 **{event.get('title') or 'Clara'}**\n{event.get('text', '')}"):
                    await thread.send(part)
                return
        thread_id = self.thread_of(conversation)
        existing = None
        if thread_id is not None:
            with contextlib.suppress(discord.HTTPException):
                existing = await self.fetch_channel(thread_id)  # an archived thread comes back when written to
        target: discord.abc.Messageable = existing or self.channels["general"]
        title = event.get("title") or "Clara"
        first = None
        for part in split_message(f"📓 **{title}**\n{event.get('text', '')}"):
            sent = await target.send(part)
            first = first or sent
        wants_thread = not payload.get("forum") and (
            stage == "thread_open" or (stage == "asked" and payload.get("thread"))
        )
        if existing is not None or not conversation or first is None:
            return
        if stage in ("reminder", "thread_post"):
            return
        if wants_thread:
            try:
                thread = await first.create_thread(name=title[:100], auto_archive_duration=THREAD_ARCHIVE_MINUTES)
            except (discord.Forbidden, discord.HTTPException) as error:
                log.warning("cannot open the thread (%s): the conversation becomes the current one", error)
            else:
                self.remember_thread(thread.id, conversation)
                return
        if stage in ("asked", "thread_open", "said"):  # no thread: your next message answers it
            self.set_conversation(conversation)
            await self.channels["general"].send(
                "↩️ Réponds ici : cette conversation est maintenant la conversation courante."
            )

    async def close_ask(self, event: dict) -> None:
        """A run ended: say so in #general (unless the event only removes the thread), then remove its thread (archive it
        if the bot may not delete). The conversation stays in Clara; a correction is made there."""
        payload = event.get("payload") or {}
        conversation = payload.get("conversation") or event.get("conversation")
        if payload.get("stage") != "thread_close":
            for part in split_message(f"📓 **{event.get('title') or 'Clara'}**\n{event.get('text', '')}"):
                await self.channels["general"].send(part)
        if self.conversation() == conversation:  # a run that had become the current chat is not any more
            self.set_conversation(None)
        thread_id = self.thread_of(conversation)
        if thread_id is not None and await self.remove_thread(thread_id):
            self.forget_thread(thread_id)

    async def remove_thread(self, thread_id: int) -> bool:
        """True when the thread is gone."""
        try:
            thread = await self.fetch_channel(thread_id)
        except discord.NotFound:
            return True
        except discord.HTTPException as error:
            log.warning("cannot find the check-in thread %s: %s", thread_id, error)
            return False
        try:
            await thread.delete()
            return True
        except discord.NotFound:
            return True
        except discord.HTTPException as error:  # Forbidden: the bot lacks "Manage Threads"
            log.warning("cannot delete the check-in thread (%s): archiving it instead", error)
            with contextlib.suppress(discord.HTTPException):
                await thread.edit(archived=True)
            return False

    # ------------------------------------------------------------------
    # Slash commands, in your server only, for you only
    # ------------------------------------------------------------------
    def _register_commands(self) -> None:
        tree = self.tree
        here = app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)

        async def only_you(interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.settings.owner_id or interaction.guild_id != self.guild_id:
                await interaction.response.send_message("Not for you.", ephemeral=True)
                return False
            return True

        @tree.command(name="new", description="Start a new conversation with Clara")
        @here
        async def new(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            forum = self.bot_forum("main")
            if forum is None:
                await interaction.response.send_message("The Conversations forum is not made yet.", ephemeral=True)
                return
            made = await forum.create_thread(
                name="New conversation", content="Write here: a new conversation with Clara."
            )
            await interaction.response.send_message(f"New conversation: <#{made.thread.id}>.", ephemeral=True)

        @tree.command(name="status", description="Clara's state")
        @here
        async def status(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                info = await self.clara.call("GET", "/v1/admin/status")
                lines = [
                    f"Clara {info['version']}, {info['state']}; model {info['models']['default'] or '(none)'}",
                    f"pending approvals: {info['approvals_pending']}",
                ]
                lines += [f"⚠️ {text}" for text in info["alerts"].values()]
                await interaction.response.send_message("\n".join(lines), ephemeral=True)
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="tasks", description="Your open tasks")
        @here
        async def tasks(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                found = (await self.clara.call("GET", "/v1/tasks"))["tasks"]
                text = (
                    "\n".join(
                        f"[{t['id']}] {t['title']}"
                        + (f" — due {due_text(t, self.settings.timezone)}" if t["due_at"] else "")
                        for t in found
                    )
                    or "No open task."
                )
                await interaction.response.send_message(split_message(text)[0], ephemeral=True)
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="reminders", description="Reminders to come")
        @here
        async def reminders(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                found = (await self.clara.call("GET", "/v1/reminders"))["reminders"]
                text = "\n".join(f"[{r['id']}] {r['local']} {r['text']}" for r in found if not r["system"]) or "None."
                await interaction.response.send_message(split_message(text)[0], ephemeral=True)
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="tools", description="The special tools of the current conversation (switch one on or off)")
        @app_commands.describe(
            pack="The tool to switch (its id, as listed)", state="on or off (default: the other one)"
        )
        @here
        async def tools(
            interaction: discord.Interaction, pack: str | None = None, state: Literal["on", "off"] | None = None
        ) -> None:
            if not await only_you(interaction):
                return
            try:
                current = self.mirror.conversation_of(interaction.channel_id)  # a thread: its conversation
                await interaction.response.send_message(await self.tools_text(pack, state, current), ephemeral=True)
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="level", description="How much Clara may do without asking in this conversation")
        @app_commands.describe(
            level="A level number, or follow (the project's level, else the default); empty: show it"
        )
        @here
        async def level(interaction: discord.Interaction, level: str | None = None) -> None:
            if not await only_you(interaction):
                return
            try:
                current = self.mirror.conversation_of(interaction.channel_id)  # a thread: its conversation
                await interaction.response.send_message(await self.level_text(level, current), ephemeral=True)
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="approvals", description="Requests waiting for your answer")
        @here
        async def approvals(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                found = (await self.clara.call("GET", "/v1/approvals", params={"status": "pending"}))["approvals"]
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")
                return
            await interaction.response.send_message(
                f"{len(found)} waiting." if found else "Nothing waits.", ephemeral=True
            )
            for approval in found:
                self.asked.pop(approval["id"], None)
                await self.show_approval(approval)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        settings = Settings.load()
    except (SettingsError, ValueError) as error:
        log.error("%s", error)
        return EXIT_SETTINGS
    settings.state_file.parent.mkdir(parents=True, exist_ok=True)
    lock = open(settings.state_file.with_suffix(".lock"), "w")  # held until the process ends
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error(
            "another clara-discord already runs with the state file %s: two bots would answer twice",
            settings.state_file,
        )
        return EXIT_RUNNING
    bot = ClaraBot(settings)
    try:
        bot.run(settings.discord_token, log_handler=None)
    except ChannelError as error:
        log.error("%s", error)
        return EXIT_SETTINGS
    except discord.LoginFailure:
        log.error("Discord refused the bot token (discord-token).")
        return EXIT_SETTINGS
    except discord.PrivilegedIntentsRequired:
        log.error("Turn on 'Message Content Intent' for the bot in the Discord developer portal (Bot page).")
        return EXIT_SETTINGS
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
