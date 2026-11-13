"""Alerts to you: an `alert` event, shown in every client and posted in #alerts by the Discord bot.

The same alert (same key) is not repeated within its cool-down. When the problem is over (`clear`) an `alert_resolved`
event tells the web site, which drops its alert badge, and the Discord bot, which removes the alert's message from
#alerts. What raises one:

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
        self._cleared: set[str] = set()  # the keys cleared since this server started

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
        """The condition is over: the next occurrence alerts again at once. The web site and Discord are told
        (`alert_resolved`) when the alert was active, and the first time this run that the key is cleared at all:
        `active` is empty after a restart, so an alert raised before it, and fixed since, would otherwise stay in
        #alerts (and on the site's badge). Callers clear on every good poll; after that first time a key that is not
        active says nothing."""
        was_active = self.active.pop(key, None) is not None
        self._sent.pop(key, None)
        if not was_active and key in self._cleared:
            return
        self._cleared.add(key)
        try:
            self.events.publish("alert_resolved", "Alert over", key, payload={"key": key}, targets=("web", "discord"))
        except Exception:
            log.exception("could not publish the end of alert %s", key)
