"""What the Discord bot tells Clara in its heartbeats, for the Discord page of the web site: its name, the channels it
found (their names, and the rights it lacks in each) and how the last test message went.

Kept in memory only: the bot repeats it every minute, and a restart of either side starts it afresh. It is data from a
client, so it is limited in size and shown as text, never trusted for anything else.
"""

from __future__ import annotations

import time
from collections import deque

from ..events.routing import CHANNELS
from ..timeutil import now_iso

TESTS_PER_MINUTE = 6
MAX_NAME = 100
MAX_RIGHTS = 12


class DiscordReport:
    def __init__(self) -> None:
        self.bot = ""
        self.channels: dict[str, dict] = {}
        self.test: dict | None = None
        self._tests: deque[float] = deque()

    def update(self, bot: str, channels: dict[str, tuple[str, list[str]]], test: tuple[int, bool, str] | None) -> None:
        """A heartbeat: `channels` maps the bot's channel names to (name in Discord, rights it lacks)."""
        self.bot = bot[:MAX_NAME]
        if channels:
            self.channels = {name: {"name": found[:MAX_NAME], "missing": [right[:MAX_NAME] for right in lacking[:MAX_RIGHTS]]}
                             for name, (found, lacking) in channels.items() if name in CHANNELS}
        if test is not None:
            event, ok, error = test
            self.test = {"event": event, "ok": ok, "error": error[:300], "at": now_iso()}

    def clear(self) -> None:
        """The bot said goodbye."""
        self.bot, self.channels, self.test = "", {}, None

    def allow_test(self) -> bool:
        now = time.monotonic()
        while self._tests and self._tests[0] < now - 60:
            self._tests.popleft()
        if len(self._tests) >= TESTS_PER_MINUTE:
            return False
        self._tests.append(now)
        return True
