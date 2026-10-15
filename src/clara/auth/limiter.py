"""A failure limiter: an address that sends too many wrong tokens is refused for a while, even with a right one.

Every connection reaches the server from 127.0.0.1 (`tailscale serve` proxies it), so the address that counts is the
one `tailscale serve` puts in `X-Forwarded-For`; that header is believed only from 127.0.0.1 / ::1.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def client_address(peer: str | None, forwarded_for: str | None) -> str:
    """The address a request comes from: the peer, or the first forwarded address when the peer is the machine."""
    peer = peer or "unknown"
    if peer in LOOPBACK and forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    return peer


class Blocked(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Too many failed sign-ins from this address: try again in {retry_after} s.")
        self.retry_after = retry_after


class FailureLimiter:
    def __init__(self, max_failures: int = 10, window: float = 60.0, block: float = 300.0,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.block = block
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._blocked: dict[str, float] = {}

    def check(self, address: str) -> None:
        until = self._blocked.get(address)
        if until is None:
            return
        now = self._clock()
        if now >= until:
            del self._blocked[address]
            return
        raise Blocked(int(until - now) + 1)

    def fail(self, address: str) -> None:
        now = self._clock()
        failures = self._failures.setdefault(address, deque())
        failures.append(now)
        while failures and failures[0] <= now - self.window:
            failures.popleft()
        if len(failures) >= self.max_failures:
            self._blocked[address] = now + self.block
            failures.clear()
        if len(self._failures) > 10_000:  # a flood of addresses must not grow memory without end
            for key in [key for key, value in self._failures.items() if not value]:
                del self._failures[key]
