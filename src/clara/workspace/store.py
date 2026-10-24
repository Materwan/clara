"""The workspace: one folder (CLARA_WORKSPACE, default <data>/workspace), a git repository, the only place on the
machine where Clara writes or runs anything.

Every write, edit, move or delete made by a tool is committed at once, with the tool's name, a one-line summary and
the turn; what a script run changes is committed after the run, as one commit with its log. Any commit can be
reverted (`undo`): a new commit, so nothing is lost. Limits: CLARA_WORKSPACE_MAX_SIZE (5 GB) in all and
CLARA_WORKSPACE_MAX_FILE (100 MB) per file.

Git runs with no system or user configuration, no prompt, and a private home, so nothing of the machine's own git
setup leaks in.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import PathError, clean, open_read, open_write, resolve

log = logging.getLogger(__name__)

AUTHOR = ("Clara", "clara@localhost")
READ_MAX_LINES = 1_000
SEARCH_MAX_MATCHES = 80
SEARCH_MAX_FILE = 2_000_000
SEARCH_LINE = 240
LIST_MAX = 500
SIZE_CACHE_SECONDS = 60.0
TEXT_PEEK = 8192


class WorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    path: str
    action: str  # created, updated, replaced, moved, deleted
    size: int
    commit: str


_SURROGATES = re.compile("[\ud800-\udfff]")


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:TEXT_PEEK]


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024  # type: ignore[assignment]
    return f"{size} B"


class Workspace:
    def __init__(self, root: Path, max_bytes: int, max_file_bytes: int, git: str = "git"):
        self.root = root
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self.git_bin = git
        self._lock = asyncio.Lock()
        self._size: tuple[float, int] | None = None
        self._home = root.parent / ".clara-git-home"

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------
    def _env(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self._home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": AUTHOR[0],
            "GIT_AUTHOR_EMAIL": AUTHOR[1],
            "GIT_COMMITTER_NAME": AUTHOR[0],
            "GIT_COMMITTER_EMAIL": AUTHOR[1],
            "LC_ALL": "C",
        }

    def _argv(self, *args: str) -> list[str]:
        """The git command line. Scripts write into this tree, so a repository they plant inside it must not be able
        to make git run a command: `core.fsmonitor` in a nested `.git/config` is run by the outer `add` and `status`."""
        return [self.git_bin, "-C", str(self.root), "-c", "core.fsmonitor=false", *args]

    def git_sync(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(self._argv(*args), capture_output=True, text=True, env=self._env(), timeout=120)
        if check and result.returncode != 0:
            raise WorkspaceError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()[:500]}")
        return result.stdout

    async def git(self, *args: str, check: bool = True) -> str:
        process = await asyncio.create_subprocess_exec(
            *self._argv(*args),
            env=self._env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(), 120)
        if check and process.returncode != 0:
            raise WorkspaceError(f"git {args[0]} failed: {(err or out).decode(errors='replace').strip()[:500]}")
        return out.decode(errors="replace")

    def ensure(self) -> None:
        """Create the workspace and its repository at first start."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise WorkspaceError(f"{self.root} is a symbolic link: the workspace must be a real folder.")
        self._home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (self.root / ".git").is_dir():
            self.git_sync("init", "--quiet", "--initial-branch=main")
            (self.root / "notes").mkdir(exist_ok=True)
            (self.root / "README.md").write_text(
                "# Clara's workspace\n\nEvery change here is a git commit. `notes/` holds the documents Clara writes for "
                "you; `runs/` the logs of the scripts she ran.\n",
                encoding="utf-8",
            )
            self.git_sync("add", "-A")
            self.git_sync("commit", "--quiet", "-m", "Workspace created")

    def recover(self) -> None:
        """At start: changes left uncommitted by a crash are committed, so the history stays whole."""
        try:
            if self.git_sync("status", "--porcelain").strip():
                self.git_sync("add", "-A")
                self.git_sync("commit", "--quiet", "-m", "Changes found at start (the server had stopped mid-change)")
                log.warning("workspace: committed changes left by an unclean stop")
        except WorkspaceError:
            log.exception("workspace: could not check for left changes")

    async def commit(self, message: str) -> str:
        """Commit everything changed; the new commit's id ('' when nothing changed)."""
        await self.git("add", "-A")
        if not (await self.git("status", "--porcelain")).strip():
            return ""
        await self.git("commit", "--quiet", "--no-verify", "-m", message)
        self._size = None
        return (await self.git("rev-parse", "--short=12", "HEAD")).strip()

    @staticmethod
    def message(tool: str, summary: str, turn_id: str = "", conversation_id: str = "") -> str:
        lines = [f"{tool}: {summary}"[:200]]
        trailer = [f"Turn: {turn_id}" if turn_id else "", f"Conversation: {conversation_id}" if conversation_id else ""]
        trailer = [line for line in trailer if line]
        return "\n\n".join([lines[0], "\n".join(trailer)]) if trailer else lines[0]

    # ------------------------------------------------------------------
    # Sizes
    # ------------------------------------------------------------------
    def size_bytes(self) -> int:
        now = time.monotonic()
        if self._size is not None and now - self._size[0] < SIZE_CACHE_SECONDS:
            return self._size[1]
        total = 0
        for folder, dirs, files in os.walk(self.root):
            dirs[:] = [name for name in dirs if not (folder == str(self.root) and name == ".git")]
            for name in files:
                try:
                    total += os.lstat(os.path.join(folder, name)).st_size
                except OSError:
                    continue
        self._size = (now, total)
        return total

    def _room(self, adding: int, replacing: int = 0) -> None:
        if adding > self.max_file_bytes:
            raise WorkspaceError(f"A file is {human_size(self.max_file_bytes)} at most.")
        if self.size_bytes() - replacing + adding > self.max_bytes:
            raise WorkspaceError(
                f"The workspace is full ({human_size(self.max_bytes)} at most): delete something first."
            )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def exists(self, relative: str) -> bool:
        try:
            return resolve(self.root, relative).exists()
        except PathError:
            return False

    def is_dir(self, relative: str) -> bool:
        try:
            return resolve(self.root, relative).is_dir()
        except PathError:
            return False

    @staticmethod
    def _read(path: Path, limit: int) -> bytes | None:
        """The bytes of a regular file, or None when it is longer than `limit` (read no further than that)."""
        with os.fdopen(open_read(path), "rb") as handle:
            data = handle.read(limit + 1)
        return data if len(data) <= limit else None

    def read_bytes(self, relative: str) -> bytes:
        """A file's bytes. A script can write files bigger than the limit for a file, so a read has that limit too."""
        path = resolve(self.root, relative, must_exist=True)
        if path.is_dir():
            raise WorkspaceError(f"{clean(relative)} is a folder.")
        data = self._read(path, self.max_file_bytes)
        if data is None:
            raise WorkspaceError(f"{clean(relative)} is bigger than {human_size(self.max_file_bytes)}: it is not read.")
        return data

    def read_text(self, relative: str) -> str:
        data = self.read_bytes(relative)
        if looks_binary(data):
            raise WorkspaceError(f"{clean(relative)} is not a text file ({human_size(len(data))}).")
        return data.decode("utf-8", errors="replace")

    def listing(self, relative: str = "", depth: int = 2) -> list[dict]:
        base = resolve(self.root, relative, must_exist=True)
        if not base.is_dir():
            raise WorkspaceError(f"{clean(relative)} is not a folder.")
        found: list[dict] = []
        start = len(self.root.parts)

        def walk(folder: Path, level: int) -> None:
            try:
                entries = sorted(
                    os.scandir(folder), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())
                )
            except OSError:
                return
            for entry in entries:
                if len(found) >= LIST_MAX:
                    return
                if entry.name == ".git" and Path(folder) == self.root:
                    continue
                path = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                kind = "link" if stat.S_ISLNK(info.st_mode) else "dir" if stat.S_ISDIR(info.st_mode) else "file"
                found.append(
                    {
                        "path": "/".join(path.parts[start:]),
                        "kind": kind,
                        "size": info.st_size,
                        "modified": info.st_mtime,
                        "depth": level,
                    }
                )
                if kind == "dir" and level + 1 < depth:
                    walk(path, level + 1)

        walk(base, 0)
        return found

    def search(self, query: str, relative: str = "", regex: bool = False, glob: str = "") -> list[str]:
        if not query.strip():
            raise WorkspaceError("query is empty.")
        try:
            pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        except re.error as error:
            raise WorkspaceError(f"not a valid regular expression ({error}).") from None
        base = resolve(self.root, relative, must_exist=True)
        matches: list[str] = []
        for folder, dirs, files in os.walk(base):
            dirs[:] = sorted(
                name
                for name in dirs
                if not (Path(folder) == self.root and name == ".git") and not os.path.islink(os.path.join(folder, name))
            )
            for name in sorted(files):
                path = Path(folder, name)
                shown = "/".join(path.parts[len(self.root.parts) :])
                if glob and not fnmatch.fnmatch(shown, glob) and not fnmatch.fnmatch(name, glob):
                    continue
                try:
                    if path.is_symlink():
                        continue
                    data = self._read(path, SEARCH_MAX_FILE)  # a named pipe has size 0: it is the open that refuses it
                except (OSError, PathError):
                    continue
                if data is None:
                    continue
                if looks_binary(data):
                    continue
                for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        text = line.strip()
                        matches.append(
                            f"{shown}:{number}: {text if len(text) <= SEARCH_LINE else text[: SEARCH_LINE - 1] + '…'}"
                        )
                        if len(matches) >= SEARCH_MAX_MATCHES:
                            return matches
        return matches

    async def log(self, relative: str = "", limit: int = 20) -> list[dict]:
        args = ["log", f"-n{max(1, min(limit, 200))}", "--format=%h%x1f%aI%x1f%an%x1f%s", "--"]
        if relative:
            args.append(clean(relative))
        out = await self.git(*args)
        entries = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                entries.append({"commit": parts[0], "at": parts[1], "author": parts[2], "message": parts[3]})
        return entries

    async def conversation_files(self, conversation_id: str, limit: int = 100) -> list[dict]:
        """The files that tools changed in a conversation and that are still here, newest first: `path`, `created` (the
        first change made in that conversation), `updated` and `size`. Found from the `Conversation:` line every
        commit carries; the logs of the scripts (`runs/`, written by the host) are left out."""
        if not re.fullmatch(r"c[0-9a-f]{16}", conversation_id):
            return []
        out = await self.git(
            "-c",
            "core.quotepath=false",
            "log",
            "--fixed-strings",
            f"--grep=Conversation: {conversation_id}",
            "--format=%x1e%aI",
            "--name-status",
            "--no-renames",
        )
        seen: dict[str, dict] = {}  # newest commit first
        for block in out.split("\x1e")[1:]:
            lines = block.splitlines()
            when = lines[0]
            for line in lines[1:]:
                status, _, path = line.partition("\t")
                if not path or path.startswith(('"', "runs/")) or status == "D":
                    continue
                found = seen.setdefault(path, {"path": path, "created": when, "updated": when})
                found["created"] = when  # going back in time: ends as the first change
        files = []
        for found in seen.values():
            try:
                found["size"] = resolve(self.root, found["path"], must_exist=True).stat().st_size
            except (PathError, OSError):
                continue  # deleted or moved since, in this conversation or another
            files.append(found)
        files.sort(key=lambda item: item["created"], reverse=True)
        return files[: max(1, limit)]

    async def show(self, commit: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{4,40}", commit):
            raise WorkspaceError("A commit is given by its id (hex).")
        return await self.git("show", "--stat", "--format=%h %aI %s%n%n%b", commit)

    # ------------------------------------------------------------------
    # Writing (each one a commit)
    # ------------------------------------------------------------------
    def _make_parents(self, path: Path) -> None:
        relative = path.relative_to(self.root)
        current = self.root
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise PathError(f"{'/'.join(current.relative_to(self.root).parts)} is a symbolic link.")
            if not current.exists():
                current.mkdir(mode=0o755)
            elif not current.is_dir():
                raise WorkspaceError(f"{'/'.join(current.relative_to(self.root).parts)} is a file, not a folder.")

    async def write(self, relative: str, content: str | bytes, *, overwrite: bool, message: str) -> Change:
        name = clean(relative)
        if not name:
            raise WorkspaceError("Give a file path.")
        data = content.encode("utf-8") if isinstance(content, str) else content
        async with self._lock:
            path = resolve(self.root, name)
            existed = path.exists()
            if existed and path.is_dir():
                raise WorkspaceError(f"{name} is a folder.")
            if existed and not overwrite:
                raise WorkspaceError(f"{name} exists already: use overwrite (it replaces the whole file) or ws_edit.")
            self._room(len(data), path.stat().st_size if existed else 0)
            self._make_parents(path)
            descriptor = open_write(path, create_only=not existed)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            commit = await self.commit(message)
        return Change(name, "replaced" if existed else "created", len(data), commit)

    async def edit(self, relative: str, old: str, new: str, *, replace_all: bool, message: str) -> tuple[Change, int]:
        name = clean(relative)
        async with self._lock:
            raw = self.read_bytes(name)
            if looks_binary(raw):
                raise WorkspaceError(f"{name} is not a text file ({human_size(len(raw))}).")
            if _SURROGATES.search(old) or _SURROGATES.search(new):
                raise WorkspaceError("old_text and new_text must be plain text.")
            # Bytes that are not UTF-8 come back as lone surrogates and go out as the same bytes, so what the edit does
            # not touch stays as it was (decoding with "replace" and writing back would turn them into U+FFFD).
            text = raw.decode("utf-8", errors="surrogateescape")
            if not old:
                raise WorkspaceError("old_text is empty: give the exact passage to replace.")
            count = text.count(old)
            if count == 0:
                raise WorkspaceError(f"old_text is not in {name}: read the file again, it must match exactly.")
            if count > 1 and not replace_all:
                raise WorkspaceError(f"old_text is in {name} {count} times: add the lines around it, or replace_all.")
            updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
            data = updated.encode("utf-8", errors="surrogateescape")
            self._room(len(data), len(raw))
            path = resolve(self.root, name, must_exist=True)
            descriptor = open_write(path)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            commit = await self.commit(message)
        return Change(name, "updated", len(data), commit), count if replace_all else 1

    async def move(self, source: str, destination: str, *, overwrite: bool, message: str) -> Change:
        old, new = clean(source), clean(destination)
        if not old or not new:
            raise WorkspaceError("Give both paths.")
        async with self._lock:
            src = resolve(self.root, old, must_exist=True)
            dst = resolve(self.root, new)
            if dst.exists() and not overwrite:
                raise WorkspaceError(f"{new} exists already: overwrite replaces it.")
            if dst.exists() and dst.is_dir():
                raise WorkspaceError(f"{new} is a folder.")
            self._make_parents(dst)
            os.replace(src, dst)
            commit = await self.commit(message)
        return Change(new, "moved", 0, commit)

    async def delete(self, relative: str, *, message: str) -> Change:
        name = clean(relative)
        if not name:
            raise WorkspaceError("The workspace itself cannot be deleted.")
        async with self._lock:
            path = resolve(self.root, name, must_exist=True)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            commit = await self.commit(message)
        return Change(name, "deleted", 0, commit)

    async def undo(self, commit: str | None = None) -> str:
        """Revert a commit (by default the last one): a new commit, so the undone change stays in the history."""
        async with self._lock:
            target = commit or (await self.git("rev-parse", "--short=12", "HEAD")).strip()
            if not re.fullmatch(r"[0-9a-f]{4,40}", target):
                raise ValueError("A commit is given by its id (hex).")
            parents = (await self.git("rev-list", "--parents", "-n", "1", target, check=False)).split()
            if len(parents) < 2:
                raise ValueError("The first commit of the workspace cannot be undone.")
            out = await self.git("revert", "--no-edit", target, check=False)
            if "CONFLICT" in out or not (await self.git("status", "--porcelain")).strip() == "":
                await self.git("revert", "--abort", check=False)
                raise ValueError(f"{target} cannot be undone cleanly: later changes touch the same lines.")
            self._size = None
            head = (await self.git("rev-parse", "--short=12", "HEAD")).strip()
        return f"{target} (as {head})"
