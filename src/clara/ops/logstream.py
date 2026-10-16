"""The live log: what the Admin page's Logs tab shows, in memory only.

Two sources feed a ring of the last `RING` lines: the server's own log (`app`: the `logging` lines from INFO up, the
ones `journalctl` shows) and the traffic log (`traffic`: one line per request and per model call, from the same
records the daily files get). Nothing is written to disk here, and a restart empties the ring.

What is left out: the debug content log (`TrafficLog.content` never reaches this module), the address of a failed
sign-in, DEBUG lines. What is masked before a line is kept: anything `secretscan.mask` takes for a secret.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..secretscan import mask

RING = 500
WATCH_QUEUE = 1000  # a viewer that falls this far behind loses lines, never the server's time
MAX_TEXT = 4000

SOURCES = ("app", "traffic")
LEVELS = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


@dataclass(frozen=True)
class Line:
    id: int
    ts: str
    source: str
    level: str
    name: str
    text: str

    def describe(self) -> dict:
        return {"type": "log", "id": self.id, "ts": self.ts, "source": self.source, "level": self.level,
                "name": self.name, "text": self.text}


def level_name(number: int) -> str:
    return "ERROR" if number >= logging.ERROR else "WARNING" if number >= logging.WARNING else "INFO"


def traffic_text(kind: str, fields: dict[str, Any]) -> tuple[str, str]:
    """(level, text) of a traffic record. Only the fields named here are shown: a new field stays out until it is
    added on purpose, and the address of a failed sign-in is never among them."""
    if kind == "http":
        status = fields.get("status") or 0
        level = "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO"
        who = f" · {fields['client']}" if fields.get("client") else ""
        return level, f"{fields.get('method', '?')} {fields.get('path', '?')} {status} {fields.get('ms', 0)} ms{who}"
    if kind == "model":
        outcome = fields.get("outcome", "?")
        used = f"{fields.get('prompt_tokens', 0)}+{fields.get('completion_tokens', 0)} tokens"
        return ("INFO" if outcome == "ok" else "WARNING",
                f"model {fields.get('provider', '?')}:{fields.get('model', '?')} ({fields.get('use', '?')}) "
                f"{outcome} {fields.get('ms', 0)} ms · {used}")
    if kind == "auth_failure":
        return "WARNING", f"sign-in refused on {fields.get('path', '?')}"
    return "INFO", kind


class LogHub:
    def __init__(self, size: int = RING):
        self._lines: deque[Line] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._next = 1
        self._watchers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Line], frozenset[str], int]] = []
        self._handler: _Handler | None = None
        self._logger: logging.Logger | None = None

    # ------------------------------------------------------------------
    # What comes in (any thread)
    # ------------------------------------------------------------------
    def add(self, source: str, level: str, name: str, text: str) -> None:
        text = mask(text)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + " …"
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            line = Line(self._next, stamp, source, level, name, text)
            self._next += 1
            self._lines.append(line)
            for watcher in list(self._watchers):
                loop, queue, sources, minimum = watcher
                if source not in sources or LEVELS[level] < minimum:
                    continue
                try:
                    loop.call_soon_threadsafe(_offer, queue, line)
                except RuntimeError:  # its loop has closed
                    self._watchers.remove(watcher)

    def traffic(self, kind: str, fields: dict[str, Any]) -> None:
        """The sink of `TrafficLog.record`."""
        level, text = traffic_text(kind, fields)
        self.add("traffic", level, kind, text)

    def attach(self, logger: logging.Logger | None = None) -> None:
        """Start taking the server's log (the root logger unless another is given)."""
        if self._handler is not None:
            return
        self._handler = _Handler(self)
        self._handler.setLevel(logging.INFO)
        self._logger = logger or logging.getLogger()
        self._logger.addHandler(self._handler)

    def detach(self) -> None:
        if self._handler is not None and self._logger is not None:
            self._logger.removeHandler(self._handler)
        self._handler = None

    # ------------------------------------------------------------------
    # What goes out
    # ------------------------------------------------------------------
    def recent(self, sources: frozenset[str] = frozenset(SOURCES), minimum: int = logging.INFO) -> list[Line]:
        with self._lock:
            return [line for line in self._lines if line.source in sources and LEVELS[line.level] >= minimum]

    async def listen(self, sources: frozenset[str] = frozenset(SOURCES), minimum: int = logging.INFO) -> AsyncIterator[Line]:
        """The ring's lines that match, then each new one as it comes. The ring is read and the viewer registered
        under one lock, so no line is missed or doubled between the two."""
        queue: asyncio.Queue[Line] = asyncio.Queue(WATCH_QUEUE)
        watcher = (asyncio.get_running_loop(), queue, sources, minimum)
        with self._lock:
            backlog = [line for line in self._lines if line.source in sources and LEVELS[line.level] >= minimum]
            self._watchers.append(watcher)
        try:
            for line in backlog:
                yield line
            while True:
                yield await queue.get()
        finally:
            with self._lock, contextlib.suppress(ValueError):
                self._watchers.remove(watcher)


def _offer(queue: asyncio.Queue[Line], line: Line) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(line)


class _Handler(logging.Handler):
    def __init__(self, hub: LogHub):
        super().__init__()
        self.hub = hub
        self._format = logging.Formatter("%(message)s")

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:  # a line about the log must not feed itself
            return
        try:
            self.hub.add("app", level_name(record.levelno), record.name, self._format.format(record))
        except Exception:  # a log handler never raises
            self.handleError(record)
