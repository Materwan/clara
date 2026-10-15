"""The traffic log: metadata only, one JSON Lines file per UTC day in `logs/traffic-YYYY-MM-DD.jsonl`.

What it records: time, route (the path, never the query string), status, duration, the name of the client token,
model, token counts, outcome. What it never records by default: what you say, what Clara answers, tool arguments,
provider error texts, addresses (except the address of a failed sign-in, which the failure limiter needs).

**The debug switch** (console `/debug on`, Admin page) turns on a second log, `logs/debug-content.jsonl`, written
private (0600), with the full prompts sent to the models and their answers. It switches itself off after one hour
(or `/debug off`), and the file is deleted when it does.

Files older than CLARA_TRAFFIC_LOG_DAYS are deleted. Writing happens on a background thread: a request never waits
for the disk.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PREFIX = "traffic-"
DEBUG_FILE = "debug-content.jsonl"
DEBUG_HOURS = 1


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


class TrafficLog:
    def __init__(self, directory: Path, retention_days: int = 7, debug_until: Callable[[], datetime | None] = lambda: None):
        self.directory = directory
        self.retention_days = retention_days
        self._debug_until = debug_until
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._write, name="traffic-log", daemon=True)
        self._thread.start()
        self._pruned: date | None = None

    # ------------------------------------------------------------------
    # What is written
    # ------------------------------------------------------------------
    def record(self, kind: str, **fields: Any) -> None:
        if not self.retention_days:
            return
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        line = json.dumps({"ts": stamp, "kind": kind, **fields}, ensure_ascii=False, default=str)
        self._queue.put((f"{PREFIX}{stamp[:10]}.jsonl", line))

    @property
    def debugging(self) -> bool:
        until = self._debug_until()
        return until is not None and datetime.now(UTC) < until

    def content(self, kind: str, **fields: Any) -> None:
        """Full content, only while the debug switch is on."""
        if not self.debugging:
            return
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        line = json.dumps({"ts": stamp, "kind": kind, **fields}, ensure_ascii=False, default=str)
        self._queue.put((DEBUG_FILE, line))

    def end_debug(self) -> None:
        """The switch went off: the content log goes."""
        self._queue.put((DEBUG_FILE, ""))

    # ------------------------------------------------------------------
    # The writer thread
    # ------------------------------------------------------------------
    def _write(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            name, line = item
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = self.directory / name
                if name == DEBUG_FILE and not line:
                    path.unlink(missing_ok=True)
                else:
                    with open(path, "a", encoding="utf-8", opener=_private) as handle:
                        handle.write(line + "\n")
                self._prune()
            except OSError as error:
                log.warning("traffic log: %s", error)
            finally:
                self._queue.task_done()

    def _prune(self) -> None:
        today = datetime.now(UTC).date()
        if self._pruned == today:
            return
        self._pruned = today
        limit = (today - timedelta(days=self.retention_days)).isoformat()
        for path in self.directory.glob(f"{PREFIX}*.jsonl"):
            if path.stem.removeprefix(PREFIX) < limit:
                path.unlink(missing_ok=True)

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)
