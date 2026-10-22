"""Checks, on your real Discord server, that the bot may do what the mirror needs: a category, a forum, a thread, a message,
an edit, an archive, and the deletions of each. It makes items named `clara-check-…` and removes them again; nothing else is
touched. Run it once, with the bot's token and the server's id in the environment:

    DISCORD_TOKEN=… DISCORD_GUILD_ID=… uv run python scripts/discord_live_check.py

Exit code 0 when every step passed. Forum channels need the server's Community features switched on.
"""

from __future__ import annotations

import asyncio
import os
import sys

import discord

STAMP = "clara-check"
RIGHTS = (
    "manage_channels",
    "manage_threads",
    "create_public_threads",
    "send_messages_in_threads",
    "read_message_history",
    "attach_files",
    "view_channel",
)


async def check(token: str, guild_id: int) -> bool:
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    results: list[bool] = []
    made: list[object] = []  # what was created, removed again if a step fails

    def step(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(("PASS" if ok else "FAIL"), name, detail, flush=True)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
            permissions = guild.me.guild_permissions
            for right in RIGHTS:
                step(f"the bot may {right.replace('_', ' ')}", permissions.administrator or getattr(permissions, right))
            category = await guild.create_category(name=f"{STAMP}-category")
            made.append(category)
            step("create a category", True)
            forum = await guild.create_forum(name=f"{STAMP}-forum", category=category)
            made.append(forum)
            step("create a forum in it", True)
            post = await forum.create_thread(name=f"{STAMP}-thread", content="check: the first message")
            made.append(post.thread)
            step("create a thread (a forum post)", True)
            await post.message.edit(content="check: the first message, edited")
            step("edit a message", True)
            await post.thread.send("check: a second message")
            step("send a message in the thread", True)
            await post.thread.edit(archived=True)
            await post.thread.edit(archived=False)
            step("archive and unarchive a thread", True)
            await post.thread.delete()
            made.remove(post.thread)
            step("delete a thread", True)
            await forum.delete()
            made.remove(forum)
            step("delete a forum", True)
            await category.delete()
            made.remove(category)
            step("delete a category", True)
        except discord.HTTPException as error:
            step("the check stopped", False, f"Discord said {error.status} (code {error.code}): {error.text}")
        finally:
            for item in reversed(made):  # leave nothing behind
                try:
                    await item.delete()
                except discord.HTTPException:
                    pass
            await client.close()

    await client.start(token)
    return bool(results) and all(results)


def main() -> int:
    token = os.environ.get("DISCORD_TOKEN", "")
    guild = os.environ.get("DISCORD_GUILD_ID", "")
    if not token or not guild.isdigit():
        print("Set DISCORD_TOKEN (the bot's token) and DISCORD_GUILD_ID (the server's id).", file=sys.stderr)
        return 2
    return 0 if asyncio.run(check(token, int(guild))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
