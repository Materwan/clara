"""Opening the vault from the server's settings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .embeddings import EmbeddingIndex, OllamaEmbedder
from .errors import VaultError
from .git import GitSync
from .vault import Vault

if TYPE_CHECKING:
    from ..settings import Settings
    from ..users import Users

log = logging.getLogger(__name__)


def open_vault(settings: Settings) -> Vault | None:
    """The configured vault, or None when there is none or it cannot be opened (the server then runs without it)."""
    if settings.vault_path is None:
        return None
    root = settings.vault_path
    git = None
    if settings.vault_git and (root / ".git").exists():
        git = GitSync(
            root, push=settings.vault_push, pull_every=float(settings.vault_pull_seconds),
            token=settings.vault_git_token,
        )
    elif settings.vault_git:
        log.warning("vault: %s is not a git repository: changes are not committed or synced", root)
    embeddings = None
    model = settings.vault_embed_model
    if model and model.lower() not in ("off", "none", "false"):
        host = settings.vault_embed_host or settings.local_host
        embedder = OllamaEmbedder(host, model)
        try:
            embeddings = EmbeddingIndex(settings.data_dir / "vault_index.sqlite", embedder.embed, f"{host}|{model}")
        except Exception:
            log.exception("vault: the semantic index cannot be opened, semantic search is off")
    try:
        return Vault(root, git=git, embeddings=embeddings, max_chars=settings.vault_max_chars)
    except VaultError as error:
        log.error("vault: %s", error)
        return None


def owner_check(settings: Settings, users: Users) -> Callable[[int], bool]:
    """Who may use the vault: the users named in CLARA_VAULT_OWNERS, else the owner. A person is a
    user when an account of theirs is signed in as one (a Discord account linked to the user's name counts)."""

    def allowed(person_id: int) -> bool:
        if not settings.vault_owners:
            owner = users.owner_user()
            return owner is not None and not owner.disabled and owner.person_id == person_id
        user = users.of_person(person_id)
        return user is not None and not user.disabled and user.name in settings.vault_owners

    return allowed
