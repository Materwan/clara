"""Typed settings, read once at start, checked strictly.

Settings come from the process environment and from one `.env` file (`CLARA_ENV_FILE`, default `.env` in the working
directory); a value already in the environment wins. The file holds **non-secret settings only**: provider keys and
tokens live in the encrypted secrets store (secrets/), and the key that opens it comes from a systemd credential.

Things you change while Clara runs (the default model, fallbacks, the tainted-turn rule, the debug switch) are not
settings: they live in the database's `settings` table and are changed from the console or the Admin page.

Every problem found is reported at once (`SettingsError` lists them all), and the server refuses to start.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .timeutil import ZoneError, system_zone_name, zone

PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
HOSTED_PROVIDERS = ("cloud", "gemini", "deepseek", "mistral", "opencode-go")
RESERVED_PROVIDER_IDS = frozenset(HOSTED_PROVIDERS)
TAILSCALE_PORTS = (443, 8443, 10000)
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmg]?)b?$", re.IGNORECASE)


class SettingsError(ValueError):
    """One or more settings are wrong; the message lists each problem on its own line."""

    def __init__(self, problems: list[str]):
        super().__init__("Invalid settings:\n" + "\n".join(f"- {problem}" for problem in problems))
        self.problems = problems


@dataclass(frozen=True)
class OllamaHost:
    """An Ollama server on a machine you own: this one, or your PC over the tailnet. Always a *local* provider."""

    id: str
    url: str


@dataclass(frozen=True)
class Settings:
    # --- core -----------------------------------------------------------------------------------------------
    data_dir: Path
    port: int = 8765
    timezone: str = "UTC"
    persona_file: Path | None = None
    env_file: Path | None = None
    debug: bool = False
    secret_key_file: Path | None = None
    secret_key: str | None = field(default=None, repr=False)  # development only: prefer the credential file
    check_permissions: bool = True

    # --- providers ------------------------------------------------------------------------------------------
    ollama_hosts: tuple[OllamaHost, ...] = (OllamaHost("local", "http://127.0.0.1:11434"),)
    ollama_context: int = 32_768
    cloud_host: str = "https://ollama.com"
    gemini_host: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    deepseek_host: str = "https://api.deepseek.com"
    mistral_host: str = "https://api.mistral.ai/v1"
    opencode_go_host: str = "https://opencode.ai/inference/go"  # chat is under /openai/v1, the model list under /v1
    opencode_zen_host: str = "https://opencode.ai/zen/v1"  # its free models are added to those of Go
    deepseek_thinking: bool = True
    hosted_context: Mapping[str, int] = field(
        default_factory=lambda: {"cloud": 131_072, "gemini": 1_048_576, "deepseek": 131_072, "mistral": 131_072,
                                 "opencode-go": 131_072}
    )

    # --- conversation engine --------------------------------------------------------------------------------
    history_turns: int = 20
    max_tool_rounds: int = 20
    chat_slots: int = 2
    background_slots: int = 1
    first_token_timeout: float = 300.0
    idle_timeout: float = 120.0
    answer_budget: float = 900.0
    client_tool_timeout: float = 900.0
    compact_percent: int = 80
    compact_tokens: int = 40_000
    keep_recent_turns: int = 2
    purge_summarised: bool = False
    facts_token_budget: int = 2_000
    approval_wait: float = 600.0
    approval_expire: int = 86_400
    notify_long_turn: int = 120
    reminder_ai_timeout: float = 60.0
    task_max_reminders: int = 10
    incognito_hours: int = 24

    # --- workspace ------------------------------------------------------------------------------------------
    workspace: Path | None = None
    workspace_max_bytes: int = 5 * 1024**3
    workspace_max_file_bytes: int = 100 * 1024**2
    run: str = "auto"  # auto: on when rootless Podman works; off
    podman: str = "podman"
    sandbox_image: str = "localhost/clara-sandbox:latest"
    run_timeout: int = 120
    run_max_timeout: int = 900
    run_memory: str = "1g"
    run_cpus: float = 2.0
    run_pids: int = 256
    run_slots: int = 2
    run_output_bytes: int = 64 * 1024

    # --- knowledge ------------------------------------------------------------------------------------------
    project_max_chars: int = 20_000_000
    project_max_files: int = 5_000
    project_inline_percent: int = 40
    project_inline_tokens: int = 20_000
    vault_path: Path | None = None
    vault_push: bool = True
    vault_pull_seconds: int = 30

    # --- connectors -----------------------------------------------------------------------------------------
    searxng_url: str = "http://127.0.0.1:8888"
    fetch_max_bytes: int = 2_000_000
    public_url: str = ""
    google_client_id: str = ""
    google_project_number: str = ""

    # --- operations -----------------------------------------------------------------------------------------
    tailscale: str = "serve"
    tailscale_port: int = 443
    tailscale_bin: str = "tailscale"
    traffic_log_days: int = 7
    backup_dir: Path | None = None
    backup_time: str = "03:30"
    backup_keep_daily: int = 14
    backup_keep_weekly: int = 8
    restic_repository: str = ""
    restic_bin: str = "restic"
    release_repo: str = ""
    releases_dir: Path = Path("/opt/clara/releases")
    release_pubkey: str = ""
    drain_seconds: int = 120
    disk_alert_percent: int = 85
    late_alert_seconds: int = 300
    discord_expected: bool = False
    discord_autostart: bool = False
    retention_days: int = 400
    events_max_days: int = 90

    # --- derived paths --------------------------------------------------------------------------------------
    @property
    def database(self) -> Path:
        return self.data_dir / "clara.db"

    @property
    def workspace_dir(self) -> Path:
        return self.workspace or self.data_dir / "workspace"

    @property
    def backups_dir(self) -> Path:
        return self.backup_dir or self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def run_dir(self) -> Path:
        """Small runtime files: the instance lock, the boot record the launcher reads, pending updates."""
        return self.data_dir / "run"

    @property
    def lock_file(self) -> Path:
        return self.run_dir / "clara.lock"

    def zone(self):
        return zone(self.timezone)


# ----------------------------------------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------------------------------------
class _Reader:
    """Reads typed values out of the merged environment, collecting every problem instead of stopping at the first."""

    def __init__(self, values: Mapping[str, str]):
        self.values = values
        self.problems: list[str] = []

    def raw(self, name: str) -> str | None:
        value = self.values.get(name)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def text(self, name: str, default: str) -> str:
        return self.raw(name) or default

    def integer(self, name: str, default: int, low: int | None = None, high: int | None = None) -> int:
        raw = self.raw(name)
        if raw is None:
            return default
        try:
            value = int(raw.replace("_", ""))
        except ValueError:
            self.problems.append(f"{name}={raw!r} is not a whole number.")
            return default
        return self._bounded(name, value, low, high, default)

    def number(self, name: str, default: float, low: float | None = None, high: float | None = None) -> float:
        raw = self.raw(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            self.problems.append(f"{name}={raw!r} is not a number.")
            return default
        return self._bounded(name, value, low, high, default)

    def _bounded(self, name, value, low, high, default):
        if (low is not None and value < low) or (high is not None and value > high):
            bounds = f"between {low} and {high}" if high is not None else f"at least {low}"
            self.problems.append(f"{name}={value} must be {bounds}.")
            return default
        return value

    def flag(self, name: str, default: bool) -> bool:
        raw = self.raw(name)
        if raw is None:
            return default
        lowered = raw.lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        self.problems.append(f"{name}={raw!r} is not true or false.")
        return default

    def choice(self, name: str, default: str, choices: tuple[str, ...]) -> str:
        raw = (self.raw(name) or default).lower()
        if raw not in choices:
            self.problems.append(f"{name}={raw!r} must be one of {', '.join(choices)}.")
            return default
        return raw

    def path(self, name: str, default: Path | None = None) -> Path | None:
        raw = self.raw(name)
        return Path(raw).expanduser() if raw else default

    def size(self, name: str, default: int) -> int:
        """A size in bytes: `5g`, `100m`, `64k`, `1048576`."""
        raw = self.raw(name)
        if raw is None:
            return default
        match = _SIZE.match(raw)
        if match is None:
            self.problems.append(f"{name}={raw!r} is not a size like 100m or 5g.")
            return default
        return int(float(match.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2).lower()])

    def url(self, name: str, default: str) -> str:
        value = self.text(name, default).rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            self.problems.append(f"{name}={value!r} must start with http:// or https://.")
            return default
        return value


def _ollama_hosts(reader: _Reader) -> tuple[OllamaHost, ...]:
    raw = reader.raw("CLARA_OLLAMA_HOSTS")
    if raw is None:
        return Settings.__dataclass_fields__["ollama_hosts"].default  # type: ignore[return-value]
    if raw.lower() == "none":
        return ()
    hosts: list[OllamaHost] = []
    for part in (piece.strip() for piece in raw.split(",")):
        if not part:
            continue
        name, separator, url = part.partition("=")
        name, url = name.strip().lower(), url.strip().rstrip("/")
        if not separator or not PROVIDER_ID.match(name):
            reader.problems.append(f"CLARA_OLLAMA_HOSTS: {part!r} must be id=url, the id in lower case (local=http://127.0.0.1:11434).")
            continue
        if name in RESERVED_PROVIDER_IDS:
            reader.problems.append(f"CLARA_OLLAMA_HOSTS: {name!r} is the id of a hosted provider; choose another.")
            continue
        if not url.startswith(("http://", "https://")):
            reader.problems.append(f"CLARA_OLLAMA_HOSTS: the address of {name!r} must start with http:// or https://.")
            continue
        if any(host.id == name for host in hosts):
            reader.problems.append(f"CLARA_OLLAMA_HOSTS: {name!r} is given twice.")
            continue
        hosts.append(OllamaHost(name, url))
    return tuple(hosts)


def _credential(name: str, environ: Mapping[str, str]) -> Path | None:
    """A file systemd hands the service with LoadCredential=/LoadCredentialEncrypted=."""
    directory = environ.get("CREDENTIALS_DIRECTORY")
    if directory:
        candidate = Path(directory) / name
        if candidate.exists():
            return candidate
    return None


def default_env_file(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get("CLARA_ENV_FILE") or ".env").expanduser()


def load_settings(environ: Mapping[str, str] | None = None, env_file: Path | None = None, *, use_file: bool = True) -> Settings:
    """Settings from the environment over the `.env` file. SettingsError lists every problem found."""
    environ = dict(os.environ if environ is None else environ)
    env_file = env_file or default_env_file(environ)
    file_values: dict[str, str] = {}
    if use_file and env_file.exists():
        file_values = {key: value for key, value in dotenv_values(env_file).items() if value is not None}
    merged = {**file_values, **environ}
    r = _Reader(merged)

    data_dir = r.path("CLARA_DATA_DIR") or (Path(environ["STATE_DIRECTORY"]) if environ.get("STATE_DIRECTORY") else Path("/var/lib/clara"))
    timezone = r.text("CLARA_TIMEZONE", "") or system_zone_name()
    try:
        zone(timezone)
    except ZoneError as error:
        r.problems.append(f"CLARA_TIMEZONE: {error}")
        timezone = "UTC"
    for name, value in merged.items():
        if (name.startswith("CLARA_") and any(word in name for word in ("_API_KEY", "_PASSWORD", "_SECRET"))
                and name not in ("CLARA_SECRET_KEY", "CLARA_SECRET_KEY_FILE")):
            if value.strip():
                r.problems.append(f"{name}: secrets do not belong in the environment; store it with `clara secrets set`.")

    hosted_context = dict(Settings.__dataclass_fields__["hosted_context"].default_factory())  # type: ignore[misc]
    for provider in HOSTED_PROVIDERS:
        name = f"CLARA_{provider.upper().replace('-', '_')}_CONTEXT"
        hosted_context[provider] = r.integer(name, hosted_context[provider], 1024)

    backup_time = r.text("CLARA_BACKUP_TIME", "03:30")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", backup_time):
        r.problems.append(f"CLARA_BACKUP_TIME={backup_time!r} must be HH:MM.")
        backup_time = "03:30"
    release_repo = r.text("CLARA_RELEASE_REPO", "")
    if release_repo and not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", release_repo):
        r.problems.append(f"CLARA_RELEASE_REPO={release_repo!r} must be owner/name.")
        release_repo = ""
    tailscale_port = r.integer("CLARA_TAILSCALE_PORT", 443)
    if tailscale_port not in TAILSCALE_PORTS:
        r.problems.append(f"CLARA_TAILSCALE_PORT must be one of {', '.join(map(str, TAILSCALE_PORTS))}.")
        tailscale_port = 443
    run_timeout = r.integer("CLARA_RUN_TIMEOUT", 120, 1)
    run_max_timeout = r.integer("CLARA_RUN_MAX_TIMEOUT", 900, 1, 86_400)
    if run_timeout > run_max_timeout:
        r.problems.append("CLARA_RUN_TIMEOUT cannot be above CLARA_RUN_MAX_TIMEOUT.")
    secret_key = environ.get("CLARA_SECRET_KEY") or file_values.get("CLARA_SECRET_KEY")
    if secret_key and "CLARA_SECRET_KEY" in file_values:
        r.problems.append("CLARA_SECRET_KEY must not be written in the .env file: use a systemd credential or CLARA_SECRET_KEY_FILE.")

    settings = Settings(
        data_dir=data_dir,
        port=r.integer("CLARA_PORT", 8765, 1, 65_535),
        timezone=timezone,
        persona_file=r.path("CLARA_PERSONA_FILE"),
        env_file=env_file if env_file.exists() else None,
        debug=r.flag("CLARA_DEBUG", False),
        secret_key_file=r.path("CLARA_SECRET_KEY_FILE") or _credential("clara-secret-key", environ),
        secret_key=secret_key or None,
        check_permissions=r.flag("CLARA_CHECK_PERMISSIONS", True),
        ollama_hosts=_ollama_hosts(r),
        ollama_context=r.integer("CLARA_OLLAMA_CONTEXT", 32_768, 2048),
        cloud_host=r.url("CLARA_CLOUD_HOST", "https://ollama.com"),
        gemini_host=r.url("CLARA_GEMINI_HOST", Settings.gemini_host),
        deepseek_host=r.url("CLARA_DEEPSEEK_HOST", Settings.deepseek_host),
        mistral_host=r.url("CLARA_MISTRAL_HOST", Settings.mistral_host),
        opencode_go_host=r.url("CLARA_OPENCODE_GO_HOST", Settings.opencode_go_host),
        opencode_zen_host=r.url("CLARA_OPENCODE_ZEN_HOST", Settings.opencode_zen_host),
        deepseek_thinking=r.flag("CLARA_DEEPSEEK_THINKING", True),
        hosted_context=hosted_context,
        history_turns=r.integer("CLARA_HISTORY_TURNS", 20, 1, 500),
        max_tool_rounds=r.integer("CLARA_MAX_TOOL_ROUNDS", 20, 1, 200),
        chat_slots=r.integer("CLARA_CHAT_SLOTS", 2, 1, 64),
        background_slots=r.integer("CLARA_BACKGROUND_SLOTS", 1, 1, 64),
        first_token_timeout=r.number("CLARA_FIRST_TOKEN_TIMEOUT", 300.0, 1),
        idle_timeout=r.number("CLARA_IDLE_TIMEOUT", 120.0, 1),
        answer_budget=r.number("CLARA_ANSWER_BUDGET", 900.0, 10),
        client_tool_timeout=r.number("CLARA_CLIENT_TOOL_TIMEOUT", 900.0, 1),
        compact_percent=r.integer("CLARA_COMPACT_PERCENT", 80, 0, 95),
        compact_tokens=r.integer("CLARA_COMPACT_TOKENS", 40_000, 0),
        keep_recent_turns=r.integer("CLARA_KEEP_RECENT_TURNS", 2, 0, 50),
        purge_summarised=r.flag("CLARA_PURGE_SUMMARISED", False),
        facts_token_budget=r.integer("CLARA_FACTS_TOKEN_BUDGET", 2_000, 0),
        approval_wait=r.number("CLARA_APPROVAL_WAIT", 600.0, 0),
        approval_expire=r.integer("CLARA_APPROVAL_EXPIRE", 86_400, 60),
        notify_long_turn=r.integer("CLARA_NOTIFY_LONG_TURN", 120, 0),
        reminder_ai_timeout=r.number("CLARA_REMINDER_AI_TIMEOUT", 60.0, 0),
        task_max_reminders=r.integer("CLARA_TASK_MAX_REMINDERS", 10, 1, 100),
        incognito_hours=r.integer("CLARA_INCOGNITO_HOURS", 24, 1, 24 * 30),
        workspace=r.path("CLARA_WORKSPACE"),
        workspace_max_bytes=r.size("CLARA_WORKSPACE_MAX_SIZE", 5 * 1024**3),
        workspace_max_file_bytes=r.size("CLARA_WORKSPACE_MAX_FILE", 100 * 1024**2),
        run=r.choice("CLARA_RUN", "auto", ("auto", "off")),
        podman=r.text("CLARA_PODMAN", "podman"),
        sandbox_image=r.text("CLARA_SANDBOX_IMAGE", "localhost/clara-sandbox:latest"),
        run_timeout=run_timeout,
        run_max_timeout=run_max_timeout,
        run_memory=r.text("CLARA_RUN_MEMORY", "1g"),
        run_cpus=r.number("CLARA_RUN_CPUS", 2.0, 0.1, 256),
        run_pids=r.integer("CLARA_RUN_PIDS", 256, 16, 65_536),
        run_slots=r.integer("CLARA_RUN_SLOTS", 2, 1, 32),
        run_output_bytes=r.size("CLARA_RUN_OUTPUT", 64 * 1024),
        project_max_chars=r.integer("CLARA_PROJECT_MAX_CHARS", 20_000_000, 1000),
        project_max_files=r.integer("CLARA_PROJECT_MAX_FILES", 5_000, 1),
        project_inline_percent=r.integer("CLARA_PROJECT_INLINE_PERCENT", 40, 0, 90),
        project_inline_tokens=r.integer("CLARA_PROJECT_INLINE_TOKENS", 20_000, 0),
        vault_path=r.path("CLARA_VAULT_PATH"),
        vault_push=r.flag("CLARA_VAULT_PUSH", True),
        vault_pull_seconds=r.integer("CLARA_VAULT_PULL_SECONDS", 30, 0),
        searxng_url=r.url("CLARA_SEARXNG_URL", "http://127.0.0.1:8888"),
        fetch_max_bytes=r.size("CLARA_FETCH_MAX_SIZE", 2_000_000),
        public_url=r.url("CLARA_PUBLIC_URL", ""),
        google_client_id=r.text("CLARA_GOOGLE_CLIENT_ID", ""),
        google_project_number=r.text("CLARA_GOOGLE_PROJECT_NUMBER", ""),
        tailscale=r.choice("CLARA_TAILSCALE", "serve", ("serve", "off")),
        tailscale_port=tailscale_port,
        tailscale_bin=r.text("CLARA_TAILSCALE_BIN", "tailscale"),
        traffic_log_days=r.integer("CLARA_TRAFFIC_LOG_DAYS", 7, 0, 365),
        backup_dir=r.path("CLARA_BACKUP_DIR"),
        backup_time=backup_time,
        backup_keep_daily=r.integer("CLARA_BACKUP_KEEP_DAILY", 14, 1, 365),
        backup_keep_weekly=r.integer("CLARA_BACKUP_KEEP_WEEKLY", 8, 0, 520),
        restic_repository=r.text("CLARA_RESTIC_REPOSITORY", ""),
        restic_bin=r.text("CLARA_RESTIC_BIN", "restic"),
        release_repo=release_repo,
        releases_dir=r.path("CLARA_RELEASES_DIR", Path("/opt/clara/releases")) or Path("/opt/clara/releases"),
        release_pubkey=r.text("CLARA_RELEASE_PUBKEY", ""),
        drain_seconds=r.integer("CLARA_DRAIN_SECONDS", 120, 1, 3600),
        disk_alert_percent=r.integer("CLARA_DISK_ALERT_PERCENT", 85, 1, 100),
        late_alert_seconds=r.integer("CLARA_LATE_ALERT_SECONDS", 300, 30),
        discord_expected=r.flag("CLARA_DISCORD_EXPECTED", False),
        discord_autostart=r.flag("CLARA_DISCORD_AUTOSTART", False),
        retention_days=r.integer("CLARA_RETENTION_DAYS", 400, 1),
        events_max_days=r.integer("CLARA_EVENTS_MAX_DAYS", 90, 1),
    )
    if r.problems:
        raise SettingsError(r.problems)
    return settings
