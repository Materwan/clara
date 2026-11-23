"""Where the speech model lives, and the one place that fetches it.

The model is fetched only by the console command `/dictation fetch-model`, never by a request: the first fetch downloads from
Hugging Face and the owner must not get a network call as a surprise. The worker then loads it from this folder with
`local_files_only=True`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
from pathlib import Path

from . import INSTALL_HELP, DictationError, NotInstalled


def installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def folder(root: Path, name: str) -> Path:
    return root / "whisper" / name


def present(root: Path, name: str) -> bool:
    return (folder(root, name) / "model.bin").is_file()


def _download(root: Path, name: str) -> Path:
    from faster_whisper.utils import download_model

    final = folder(root, name)
    partial = final.with_name(f".{name}.partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True, mode=0o700)
    try:
        download_model(name, output_dir=str(partial))
        os.replace(partial, final)  # a half-fetched model is never where the worker looks
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    return final


async def fetch(root: Path, name: str) -> str:
    """Download the model into `root`. The answer is the text the console shows."""
    if not installed():
        raise NotInstalled(
            f"Dictation is not installed: the `dictation` extra (faster-whisper) is missing. {INSTALL_HELP}"
        )
    if present(root, name):
        return f"The model {name!r} is already in {folder(root, name)}. Delete that folder to fetch it again."
    try:
        final = await asyncio.to_thread(_download, root, name)
    except Exception as error:  # an unknown name, no network, a full disk: the console says which
        raise DictationError(
            f"Could not fetch the model {name!r}: {type(error).__name__}: {str(error)[:300]}"
        ) from None
    return f"Fetched the model {name!r} into {final}."
