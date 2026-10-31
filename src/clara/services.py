"""Everything the server is made of, built once at start (app.build_services) and shared by the API, the agent and the
tools. Optional parts (the workspace runner, the vault, connectors) are None when they are off."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .mirror.service import Mirror
from .ops.discord_report import DiscordReport

if TYPE_CHECKING:
    from .agent.turn import Agent
    from .auth import FailureLimiter, Tokens, WebSessions
    from .connectors.asks import Questions
    from .connectors.quizzes import Quizzes
    from .db import Database
    from .events import Events
    from .gate import Approvals, Audit, Gate, Grants, Levels, Rules
    from .gate.allowances import Allowances
    from .knowledge.conversation_files import ConversationFiles
    from .knowledge.projects import Projects
    from .llm import Catalog, Providers, Router
    from .memory.conversations import Conversations
    from .memory.facts import Facts
    from .memory.retention import AutomationRetention
    from .ops.alerts import Alerts
    from .ops.lifecycle import Lifecycle
    from .ops.logstream import LogHub
    from .ops.traffic import TrafficLog
    from .ops.usage import UsageLog
    from .runtime import RuntimeSettings
    from .secrets import SecretsStore
    from .settings import Settings
    from .timekeeping import Reminders, Schedules, Tasks
    from .tools.base import Toolbox
    from .tools.folder_tools import FolderGrants


@dataclass
class Services:
    settings: Settings
    db: Database
    runtime: RuntimeSettings
    secrets: SecretsStore
    tokens: Tokens
    web_sessions: WebSessions
    limiter: FailureLimiter
    events: Events
    alerts: Alerts
    traffic: TrafficLog
    logs: LogHub
    usage: UsageLog
    facts: Facts
    conversations: Conversations
    conversation_files: ConversationFiles
    projects: Projects
    reminders: Reminders
    tasks: Tasks
    schedules: Schedules
    rules: Rules
    gate: Gate
    levels: Levels
    audit: Audit
    approvals: Approvals
    grants: Grants
    questions: Questions
    quizzes: Quizzes
    providers: Providers
    catalog: Catalog
    router: Router
    allowances: Allowances
    folders: FolderGrants
    retention: AutomationRetention = None  # type: ignore[assignment]
    toolbox: Toolbox = None  # type: ignore[assignment]
    agent: Agent = None  # type: ignore[assignment]
    lifecycle: Lifecycle = None  # type: ignore[assignment]
    workspace: Any = None
    sandbox: Any = None
    vault: Any = None
    packs: Any = None
    web: Any = None
    connectors: Any = None
    triggers: Any = None
    connections: Any = None
    github_snapshots: Any = None
    tailscale: Any = None
    backups: Any = None
    updater: Any = None
    discord: Any = None
    monitors: Any = None
    clients: dict[str, float] = field(default_factory=dict)  # client heartbeats: name -> monotonic time
    discord_report: DiscordReport = field(default_factory=DiscordReport)  # what the bot's heartbeats said
    mirror: Mirror | None = None  # the conversations kept in Discord
    boot_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.monotonic)

    def due_loops(self) -> list:
        return [self.reminders, self.tasks, self.schedules, *([self.triggers] if self.triggers is not None else [])]

    def reminders_firing(self) -> bool:
        return self.reminders.busy_firing or self.tasks.busy_firing
