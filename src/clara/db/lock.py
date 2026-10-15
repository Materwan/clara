"""One server per data folder.

`flock` on `run/clara.lock`: the kernel drops the lock when the process dies, however it dies, so a crash never leaves
a stale lock behind. The file holds the pid of the holder, for the error message only.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Another Clara server holds this data folder."""


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                holder = os.read(fd, 32).decode(errors="replace").strip() or "unknown"
            finally:
                os.close(fd)
            raise AlreadyRunning(
                f"Another Clara server (pid {holder}) is using {self.path.parent.parent}: two servers must never "
                "share one data folder."
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
