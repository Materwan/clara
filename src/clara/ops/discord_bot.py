"""The Discord bot, started with the server (CLARA_DISCORD_AUTOSTART=true).

The bot stays a separate process (`python -m clara_discord.bot`, a client of Clara with its own token): the server
starts it when it starts, starts it again if it dies, and stops it when it stops. It is the alternative to enabling
`clara-discord.service`: with both, two bots would answer.

The bot's settings are the ones it reads itself (CLARA_DISCORD_*, DISCORD_BOT_TOKEN_FILE, CLARA_TOKEN_FILE), taken
from the environment over the `.env` file like Clara's own; its address and time zone are Clara's. No other CLARA_*
setting reaches it, so it never sees the secrets store's key.

An exit code 0 (someone stopped it) or 2 and 3 (a wrong setting, a token or an intent Discord refuses; another bot
running) is not retried: the cause is told in the log, and 2 raises the `discord` alert. Any other exit is retried,
a little later each time while it keeps failing.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence

from dotenv import dotenv_values

from ..settings import Settings

log = logging.getLogger(__name__)

RETRY = (2, 5, 15, 30, 60)
STABLE = 60.0  # a bot that ran this long starts afresh after a failure
STOP_WAIT = 10.0
EXIT_STOPPED = 0
EXIT_SETTINGS = 2
EXIT_RUNNING = 3
BOT_SETTINGS = ("DISCORD_BOT_TOKEN_FILE", "CLARA_TOKEN_FILE")
BOT_PREFIX = "CLARA_DISCORD_"


def bot_environment(settings: Settings, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """What the bot's process gets: the environment without Clara's own settings, plus the bot's."""
    environ = dict(os.environ if environ is None else environ)
    file_values: dict[str, str] = {}
    if settings.env_file is not None and settings.env_file.exists():
        file_values = {key: value for key, value in dotenv_values(settings.env_file).items() if value is not None}
    merged = {**file_values, **environ}
    env = {key: value for key, value in environ.items() if not key.startswith("CLARA_")}
    env.update({key: value for key, value in merged.items() if key.startswith(BOT_PREFIX) or key in BOT_SETTINGS})
    env["CLARA_URL"] = f"http://127.0.0.1:{settings.port}"
    env["CLARA_TIMEZONE"] = settings.timezone
    env.setdefault("CLARA_DISCORD_STATE", str(settings.data_dir / "discord-state.json"))
    return env


class DiscordBot:
    def __init__(
        self,
        settings: Settings,
        alert: Callable[[str, str, str], object],
        *,
        command: Sequence[str] | None = None,
        retry: Sequence[float] = RETRY,
        environ: Mapping[str, str] | None = None,
    ):
        self.settings = settings
        self._alert = alert  # the bot's heartbeat clears it once the bot is up
        self.command = list(command or (sys.executable, "-m", "clara_discord.bot"))
        self.retry = retry
        self._environ = environ
        self.process: asyncio.subprocess.Process | None = None
        self.problem = ""
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        self._task = asyncio.ensure_future(self.supervise())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()  # the bot closes its Discord connection on SIGTERM
                try:
                    await asyncio.wait_for(process.wait(), STOP_WAIT)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        self.process = None

    def _fail(self, problem: str) -> None:
        self.problem = problem
        log.error("The Discord bot is not running: %s", problem)
        self._alert("discord", "The Discord bot is not running", problem)

    async def supervise(self) -> None:
        if importlib.util.find_spec("discord") is None:
            self._fail("discord.py is not installed (uv sync --extra discord).")
            return
        failures = 0
        while not self._stopping:
            started = time.monotonic()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command, stdin=asyncio.subprocess.DEVNULL, env=bot_environment(self.settings, self._environ)
                )
            except OSError as error:
                self._fail(f"cannot start it: {error}")
                return
            log.info("Discord bot started (pid %d)", self.process.pid)
            code = await self.process.wait()
            self.process = None
            if self._stopping:
                return
            if code == EXIT_STOPPED:
                log.info("The Discord bot stopped; it is not started again until the server restarts.")
                return
            if code == EXIT_SETTINGS:
                self._fail("it refused its settings (the log above says which): fix them, then restart Clara.")
                return
            if code == EXIT_RUNNING:
                log.warning("Another Discord bot already runs with the same state file: not starting a second one.")
                return
            failures = 1 if time.monotonic() - started >= STABLE else failures + 1
            delay = self.retry[min(failures - 1, len(self.retry) - 1)]
            log.warning("The Discord bot exited with code %s: starting it again in %g s", code, delay)
            await asyncio.sleep(delay)
