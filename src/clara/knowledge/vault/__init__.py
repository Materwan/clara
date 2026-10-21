"""The slim second brain: an Obsidian vault with git sync, FTS5 search and seven tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .git import GitSync
from .tools import OPEN_VAULT, VAULT_TOOLS, vault_tools
from .vault import CLOSED_BLOCK, Vault, VaultError

if TYPE_CHECKING:
    from ...services import Services

__all__ = ["CLOSED_BLOCK", "OPEN_VAULT", "VAULT_TOOLS", "GitSync", "Vault", "VaultError", "install", "vault_tools"]


def install(s: Services) -> None:
    """Adds the vault when CLARA_VAULT_PATH names a folder."""
    path = s.settings.vault_path
    if path is None:
        return
    if not path.is_dir():
        s.alerts.raise_("vault", "The vault is missing", f"CLARA_VAULT_PATH={path} is not a folder.")
        return
    git = GitSync(
        path,
        push=s.settings.vault_push,
        pull_every=float(s.settings.vault_pull_seconds),
        token=s.secrets.get("vault_git_token") or "",
    )
    git.on_problem = lambda text: s.alerts.raise_("vault_push", "The vault could not be synced", text)
    git.on_success = lambda: s.alerts.clear("vault_push")
    vault = Vault(path, s.db, git, s.settings.timezone)
    vault.reindex_sync()
    s.vault = vault
    for tool in vault_tools():
        s.toolbox.add(tool)
    s.agent.vault_prompt = vault.prompt_block
    s.agent.vault_closed_block = CLOSED_BLOCK
    s.agent.vault_tools = VAULT_TOOLS
    s.agent.vault_opener = OPEN_VAULT
