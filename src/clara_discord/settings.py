"""The bot's settings. Its two secrets come from its own systemd credentials, never from Clara's database:

    discord-token       the Discord bot token          (or DISCORD_BOT_TOKEN_FILE)
    clara-token         a Clara client token, client `discord` (or CLARA_TOKEN_FILE)

    CLARA_DISCORD_USER_ID   your Discord user id: the only one the bot answers
    CLARA_URL               Clara (default http://127.0.0.1:8765)
    CLARA_TIMEZONE          the zone times are read in (default: the machine's)
    CLARA_DISCORD_STATE     a small file remembering the current conversation (default ./discord-state.json)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class SettingsError(ValueError):
    pass


def _secret(name: str, file_variable: str, env: Mapping[str, str]) -> str:
    candidates = []
    if env.get("CREDENTIALS_DIRECTORY"):
        candidates.append(Path(env["CREDENTIALS_DIRECTORY"]) / name)
    if env.get(file_variable):
        candidates.append(Path(env[file_variable]).expanduser())
    for path in candidates:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    raise SettingsError(f"No {name}: give it as the systemd credential {name}, or in the file named by {file_variable}.")


@dataclass(frozen=True)
class Settings:
    discord_token: str = field(repr=False)
    clara_token: str = field(repr=False)
    owner_id: int
    clara_url: str
    timezone: str
    state_file: Path

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        owner = env.get("CLARA_DISCORD_USER_ID", "").strip()
        if not owner.isdigit():
            raise SettingsError("CLARA_DISCORD_USER_ID must be your Discord user id (a number): the only one answered.")
        from clara.timeutil import system_zone_name, zone

        timezone = env.get("CLARA_TIMEZONE", "").strip() or system_zone_name()
        zone(timezone)
        return cls(
            discord_token=_secret("discord-token", "DISCORD_BOT_TOKEN_FILE", env),
            clara_token=_secret("clara-token", "CLARA_TOKEN_FILE", env),
            owner_id=int(owner),
            clara_url=(env.get("CLARA_URL") or "http://127.0.0.1:8765").rstrip("/"),
            timezone=timezone,
            state_file=Path(env.get("CLARA_DISCORD_STATE") or env.get("STATE_DIRECTORY", ".") + "/discord-state.json"),
        )
