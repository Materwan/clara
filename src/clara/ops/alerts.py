"""Alerts to you: an `alert` event, shown in every client and sent as a Discord DM by the bot.

The same alert (same key) is not repeated within its cool-down. What raises one:

    provider_key:<id>    a provider refused its key (401/403)          provider_payment:<id>  it wants payment (402)
    vault_push           the vault could not be pushed                 disk                   the disk is above 85%
    late:<what>          a reminder or schedule ran more than 5 min late
    backup               a backup (local or remote) failed             discord                the Discord bot is gone
    tailscale            Tailscale is not serving Clara                update                 an update rolled back
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..events import Events

log = logging.getLogger(__name__)

DEFAULT_COOLDOWN = 6 * 3600.0


class Alerts:
    def __init__(self, events: Events, clock: Callable[[], float] = time.monotonic):
        self.events = events
        self._clock = clock
        self._sent: dict[str, float] = {}
        self.active: dict[str, str] = {}  # key -> the last text, until cleared: /v1/admin/status shows them

    def raise_(self, key: str, title: str, text: str, cooldown: float = DEFAULT_COOLDOWN) -> bool:
        """Raise an alert unless the same one was sent within `cooldown` seconds. True when sent."""
        self.active[key] = f"{title}: {text}"
        now = self._clock()
        last = self._sent.get(key)
        if last is not None and now - last < cooldown:
            return False
        self._sent[key] = now
        log.warning("alert %s: %s: %s", key, title, text)
        try:
            self.events.publish("alert", title, text, payload={"key": key})
        except Exception:
            log.exception("could not publish alert %s", key)
            return False
        return True

    def clear(self, key: str) -> None:
        """The condition is over: the next occurrence alerts again at once."""
        self.active.pop(key, None)
        self._sent.pop(key, None)
