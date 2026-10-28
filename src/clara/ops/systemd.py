"""Talking to systemd without a dependency: `READY=1` once serving, `WATCHDOG=1` while every loop turns, `STOPPING=1`
(not by a server run by the launcher: it may only be handing over to another release, and systemd knows when the unit stops).

The unit sets `NotifyAccess=all` because the server runs as a child of clara-launcher, not as the main process.
"""

from __future__ import annotations

import os
import socket


def notify(message: str) -> bool:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
    except OSError:
        return False
    return True


def watchdog_seconds() -> float | None:
    """Half the watchdog interval systemd set, or None when there is no watchdog."""
    value = os.environ.get("WATCHDOG_USEC")
    if not value or not value.isdigit():
        return None
    return int(value) / 2_000_000
