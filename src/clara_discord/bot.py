"""`clara-discord`: the Discord bot, a separate process and a client of Clara like any other.

* It connects with **direct-message intents only**: no server messages, no member list, no message content from
  servers. It answers **one** Discord user id (yours) and ignores every other one, in DMs and in commands.
* Your DMs go to Clara in the current conversation (`/new` starts another), with their files (PDF, Word, text,
  pictures); notes Clara writes in `notes/` come back as attachments.
* Approval requests come as DMs with buttons: allow once, allow always, refuse once, refuse always. The bot's token
  may answer every level but *destructive* (deletions, overwrites, merges): those say to answer on the web or with
  clara-admin. The server enforces it, whatever the bot shows.
* Reminders, notifications, task reminders, schedule summaries and alerts arrive as DMs (the event stream; what was
  missed while the bot was away arrives when it reconnects).
* A heartbeat every minute lets the server alert you when the bot is gone.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import re
import sys

import discord
from discord import app_commands

from .api import Clara, ClaraError
from .settings import Settings, SettingsError

log = logging.getLogger("clara_discord")

LIMIT = 2000
MAX_FILES = 4
MAX_FILE_BYTES = 25_000_000
MAX_TOTAL_BYTES = 34_000_000  # what Clara takes for one message in all
HEARTBEAT = 60.0
RETRY = (2, 5, 15, 30, 60)
ANSWERS = (("allow_once", "Allow once", discord.ButtonStyle.success), ("allow_always", "Allow always", discord.ButtonStyle.primary),
           ("refuse_once", "Refuse once", discord.ButtonStyle.secondary), ("refuse_always", "Refuse always", discord.ButtonStyle.danger))
ICONS = {"reminder": "⏰", "notification": "🔔", "task": "📋", "schedule": "🗓", "alert": "⚠️", "update": "⬆️"}


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


class ApprovalButton(discord.ui.DynamicItem[discord.ui.Button],
                     template=r"clara:approval:(?P<id>[0-9]+):(?P<answer>allow_once|allow_always|refuse_once|refuse_always)"):
    def __init__(self, approval_id: int, answer: str):
        label, style = next((label, style) for key, label, style in ANSWERS if key == answer)
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"clara:approval:{approval_id}:{answer}"))
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
            settled = await bot.clara.call("POST", f"/v1/approvals/{self.approval_id}/decide", json={"answer": self.answer})
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
        intents.dm_messages = True  # DMs only: no guilds, no members, no message content from servers
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.clara = Clara(settings.clara_url, settings.clara_token)
        self.tree = app_commands.CommandTree(self)
        self.asked: dict[int, discord.Message] = {}  # approval id -> its DM
        self._tasks: list[asyncio.Task] = []
        self._register_commands()

    # ------------------------------------------------------------------
    # State: the current conversation
    # ------------------------------------------------------------------
    def conversation(self) -> str | None:
        try:
            return json.loads(self.settings.state_file.read_text(encoding="utf-8")).get("conversation")
        except (OSError, ValueError):
            return None

    def set_conversation(self, conversation: str | None) -> None:
        self.settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.state_file.write_text(json.dumps({"conversation": conversation}), encoding="utf-8")

    async def owner(self) -> discord.User:
        return self.get_user(self.settings.owner_id) or await self.fetch_user(self.settings.owner_id)

    # ------------------------------------------------------------------
    # Life
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        self.add_dynamic_items(ApprovalButton)
        await self.tree.sync()
        self._tasks.append(asyncio.create_task(self.relay(), name="relay"))
        self._tasks.append(asyncio.create_task(self.heartbeat(), name="heartbeat"))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        with contextlib.suppress(ClaraError):
            await self.clara.call("POST", "/v1/clients/heartbeat", json={"state": "disconnected"})
        await self.clara.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("connected as %s; answering only Discord user %s", self.user, self.settings.owner_id)

    async def heartbeat(self) -> None:
        while True:
            state = "connected" if self.is_ready() and not self.is_closed() else "disconnected"
            try:
                await self.clara.call("POST", "/v1/clients/heartbeat", json={"state": state})
            except ClaraError as error:
                log.warning("heartbeat: %s", error)
            await asyncio.sleep(HEARTBEAT)

    # ------------------------------------------------------------------
    # Your messages
    # ------------------------------------------------------------------
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id != self.settings.owner_id:
            return  # every other id is ignored, without a word
        if not isinstance(message.channel, discord.DMChannel):
            return
        files = []
        if len(message.attachments) > MAX_FILES:
            await message.channel.send(f"{MAX_FILES} files at most per message: send the others in another one.")
            return
        if sum(attachment.size for attachment in message.attachments) > MAX_TOTAL_BYTES:
            await message.channel.send(f"The files of one message may weigh {MAX_TOTAL_BYTES // 1_000_000} MB in all.")
            return
        for attachment in message.attachments:
            if attachment.size > MAX_FILE_BYTES:
                await message.channel.send(f"{attachment.filename} is too big (25 MB at most).")
                return
            files.append({"name": attachment.filename, "data": base64.b64encode(await attachment.read()).decode()})
        if not message.content.strip() and not files:
            return
        body = {"message": message.content, "attachments": files, "timezone": self.settings.timezone}
        if self.conversation():
            body["conversation"] = self.conversation()
        await self.ask(message.channel, body)

    async def ask(self, channel: discord.abc.Messageable, body: dict) -> None:
        reply = ""
        done: dict = {}
        try:
            async with channel.typing():
                async for event in self.clara.stream("POST", "/v1/chat/stream", body):
                    kind = event["type"]
                    if kind == "turn" and event.get("conversation"):
                        self.set_conversation(event["conversation"])
                    elif kind == "approval":
                        await self.show_approval(event["approval"])
                    elif kind == "done":
                        done = event
                        reply = event.get("reply") or ""
                    elif kind == "error":
                        reply = f"⚠️ {event['message']}"
        except ClaraError as error:
            if error.status == 404 and "conversation" in str(error).lower():
                self.set_conversation(None)
            reply = f"⚠️ {error}"
        for part in split_message(reply or "(no answer)"):
            await channel.send(part)
        for card in done.get("files", []):
            try:
                data = await self.clara.file(card["path"])
                await channel.send(file=discord.File(io.BytesIO(data), filename=card["path"].rsplit("/", 1)[-1]))
            except (ClaraError, discord.HTTPException) as error:
                log.warning("cannot send %s: %s", card.get("path"), error)

    async def show_approval(self, approval: dict) -> None:
        if approval["id"] in self.asked or approval.get("status", "pending") != "pending":
            return
        text = f"🔐 Clara asks (#{approval['id']}, {approval['level']}): {approval['summary']}"
        if approval.get("reason"):
            text += f"\n_{approval['reason']}_"
        user = await self.owner()
        if approval["level"] == "destructive":
            self.asked[approval["id"]] = await user.send(text + "\nThis one is answered on the web site or with clara-admin.")
        else:
            self.asked[approval["id"]] = await user.send(text, view=approval_view(approval["id"]))

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
                    await message.edit(content=f"🔐 #{approval['id']} {approval['summary']}: **{approval['status']}**", view=None)
            return
        icon = ICONS.get(str(kind))
        if icon is None:
            return
        title = event.get("title") or ""
        text = f"{icon} **{title}**\n{event.get('text', '')}" if title and kind != "reminder" else f"{icon} {event.get('text', '')}"
        user = await self.owner()
        for part in split_message(text):
            await user.send(part)

    # ------------------------------------------------------------------
    # Slash commands, in DMs only, for you only
    # ------------------------------------------------------------------
    def _register_commands(self) -> None:
        tree = self.tree
        dms = app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)

        async def only_you(interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.settings.owner_id:
                await interaction.response.send_message("Not for you.", ephemeral=True)
                return False
            return True

        @tree.command(name="new", description="Start a new conversation with Clara")
        @dms
        async def new(interaction: discord.Interaction) -> None:
            if await only_you(interaction):
                self.set_conversation(None)
                await interaction.response.send_message("New conversation: your next message starts it.")

        @tree.command(name="status", description="Clara's state")
        @dms
        async def status(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                info = await self.clara.call("GET", "/v1/admin/status")
                lines = [f"Clara {info['version']}, {info['state']}; model {info['models']['default'] or '(none)'}",
                         f"pending approvals: {info['approvals_pending']}"]
                lines += [f"⚠️ {text}" for text in info["alerts"].values()]
                await interaction.response.send_message("\n".join(lines))
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="tasks", description="Your open tasks")
        @dms
        async def tasks(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                found = (await self.clara.call("GET", "/v1/tasks"))["tasks"]
                text = "\n".join(f"[{t['id']}] {t['title']}" + (f" — due {t['due_at'][:16]}" if t["due_at"] else "")
                                 for t in found) or "No open task."
                await interaction.response.send_message(split_message(text)[0])
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="reminders", description="Reminders to come")
        @dms
        async def reminders(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                found = (await self.clara.call("GET", "/v1/reminders"))["reminders"]
                text = "\n".join(f"[{r['id']}] {r['local']} {r['text']}" for r in found if not r["system"]) or "None."
                await interaction.response.send_message(split_message(text)[0])
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")

        @tree.command(name="approvals", description="Requests waiting for your answer")
        @dms
        async def approvals(interaction: discord.Interaction) -> None:
            if not await only_you(interaction):
                return
            try:
                found = (await self.clara.call("GET", "/v1/approvals", params={"status": "pending"}))["approvals"]
            except ClaraError as error:
                await interaction.response.send_message(f"⚠️ {error}")
                return
            await interaction.response.send_message(f"{len(found)} waiting." if found else "Nothing waits.")
            for approval in found:
                self.asked.pop(approval["id"], None)
                await self.show_approval(approval)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        settings = Settings.load()
    except (SettingsError, ValueError) as error:
        log.error("%s", error)
        return 2
    bot = ClaraBot(settings)
    bot.run(settings.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
