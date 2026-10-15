"""Adds the workspace and the script runner to a server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .sandbox import Sandbox
from .store import Workspace
from .tools import workspace_tools

if TYPE_CHECKING:
    from ..services import Services


def install(s: Services) -> None:
    settings = s.settings
    workspace = Workspace(settings.workspace_dir, settings.workspace_max_bytes, settings.workspace_max_file_bytes)
    workspace.ensure()
    s.workspace = workspace
    image = s.runtime.get("sandbox_image") or settings.sandbox_image
    s.sandbox = Sandbox(settings, workspace, s.db, image)
    s.sandbox.recover()
    for tool in workspace_tools():
        s.toolbox.add(tool)
    s.agent.tool_conditions["ws_run"] = lambda state: bool(s.sandbox and s.sandbox.ready)
