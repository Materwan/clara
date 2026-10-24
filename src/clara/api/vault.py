"""The vault for you: its state, search, a note, sync and reindex."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..knowledge.vault import VaultError
from .common import ApiError, services

router = APIRouter(prefix="/v1/vault", tags=["vault"])


def _vault(request: Request):
    vault = services(request).vault
    if vault is None:
        raise ApiError(404, "No vault (CLARA_VAULT_PATH is not set).")
    return vault


@router.get("")
def status(request: Request) -> dict:
    return _vault(request).status()


@router.get("/notes")
def notes(request: Request, folder: str = Query("", max_length=400)) -> dict:
    vault = _vault(request)
    prefix = folder.strip("/")
    return {"notes": [path for path in vault.notes() if not prefix or path.startswith(prefix + "/")]}


@router.get("/search")
async def search(request: Request, q: str = Query(min_length=1, max_length=300)) -> dict:
    try:
        return {"results": await _vault(request).search(q)}
    except VaultError as error:
        raise ApiError(422, str(error)) from None


@router.get("/note")
def note(request: Request, path: str = Query(min_length=1, max_length=400)) -> dict:
    vault = _vault(request)
    try:
        found = vault.find(path)
    except VaultError as error:
        raise ApiError(404, str(error)) from None
    return {"path": found, "content": (vault.root / found).read_text(encoding="utf-8", errors="replace")}


@router.post("/sync")
async def sync(request: Request) -> dict:
    return {"message": await _vault(request).sync_now()}


@router.post("/reindex")
async def reindex(request: Request) -> dict:
    return {"indexed": await _vault(request).reindex(full=True)}
