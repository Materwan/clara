"""Path safety in the workspace.

A path from a tool is joined to the workspace root, then checked part by part: absolute paths, `..`, `.git` and any
symbolic link on the way are refused, and files are opened with O_NOFOLLOW. Nothing outside the workspace can be named,
and the workspace's own git history is out of reach.
"""

from __future__ import annotations

import errno
import os
import stat
import unicodedata
from pathlib import Path, PurePosixPath

MAX_PATH = 400


class PathError(ValueError):
    pass


def clean(relative: str) -> str:
    """A workspace path as stored and shown: forward slashes, no leading slash, normalised; '' is the root."""
    text = unicodedata.normalize("NFC", str(relative or "")).replace("\\", "/").strip()
    if len(text) > MAX_PATH:
        raise PathError(f"The path is longer than {MAX_PATH} characters.")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise PathError(f"{relative}: give a path inside the workspace, not an absolute one.")
    if "\x00" in text:
        raise PathError("A path cannot hold a NUL character.")
    parts = [part for part in PurePosixPath(text).parts if part not in ("", ".")]
    for part in parts:
        if part == "..":
            raise PathError(f"{relative}: a path may not go up (..).")
        if part == ".git":
            raise PathError(f"{relative}: the workspace's .git folder is not reachable.")
    return "/".join(parts)


def resolve(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """The real path of a workspace path, refusing links on the way. The root itself must not be a link either."""
    name = clean(relative)
    current = root
    parts = name.split("/") if name else []
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if must_exist:
                raise PathError(f"{name} does not exist.") from None
            # what does not exist yet cannot be a link; the rest of the path is checked when it is made
            return root.joinpath(*parts)
        if stat.S_ISLNK(info.st_mode):
            shown = "/".join(parts[: index + 1])
            raise PathError(f"{shown} is a symbolic link: links are not followed in the workspace.")
    return current


def _open_regular(path: Path, flags: int, mode: int = 0o777) -> int:
    """Open a regular file. A script can leave a named pipe in the workspace, and opening one that nobody is on the
    other end of blocks for good: O_NONBLOCK makes the open return (or fail with ENXIO) at once, and anything that is
    not a file is refused."""
    refused = PathError(f"{path.name} is not a regular file (a named pipe, a socket or a device): it is not opened.")
    try:
        descriptor = os.open(path, flags | os.O_NONBLOCK, mode)
    except OSError as error:
        if error.errno == errno.ENXIO:  # a pipe with no reader, or a socket
            raise refused from None
        raise
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise refused
    return descriptor


def open_read(path: Path) -> int:
    return _open_regular(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)


def open_write(path: Path, *, create_only: bool = False) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_EXCL if create_only else os.O_TRUNC)
    return _open_regular(path, flags, 0o644)
