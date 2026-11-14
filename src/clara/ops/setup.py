"""Adds the operations to a server: Tailscale publishing, the events door, backups, the updater, the Discord bot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .backups import Backups
from .discord_bot import DiscordBot
from .door import EventsDoor
from .tailscale import Tailscale, door_of
from .updater import Updater

if TYPE_CHECKING:
    from ..services import Services


def install(s: Services) -> None:
    settings = s.settings
    if settings.events == "on":
        s.events_door = EventsDoor(s)  # served by the main server's lifespan, published by Tailscale when it serves
    if settings.tailscale == "serve":
        s.tailscale = Tailscale(
            settings.tailscale_port,
            f"http://127.0.0.1:{settings.port}",
            settings.tailscale_bin,
            alert=s.alerts.raise_,
            clear=s.alerts.clear,
            door=door_of(settings),
            door_up=lambda: s.events_door is not None and s.events_door.up,
        )
    s.backups = Backups(s)
    s.updater = Updater(s)
    s.updater.report()
    if settings.discord_autostart:
        s.discord = DiscordBot(settings, s.alerts.raise_)
