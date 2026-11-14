"""Tailscale: Clara publishes itself with `tailscale serve`, inside your tailnet only, never with Funnel. One exception, off by
default: with `CLARA_EVENTS=on`, **one door for events** may be public.

The server listens on 127.0.0.1 and nowhere else; tailscaled terminates HTTPS and proxies to it. At start Clara asks
`tailscale serve status --json` whether Funnel is on for its HTTPS port, and **refuses to start** if it is. Then it
sets the `serve` mapping (retrying while tailscaled is not up), and checks every minute that it still serves and that
Funnel was not turned on meanwhile: if Funnel appears, Clara turns it off and raises an alert; if serving stops, an
alert too. The mapping is removed when the server stops.

**The events door** (ops/door.py: a second listener on 127.0.0.1 that serves `POST /v1/events/<source id>` and nothing else) is the
one thing Funnel may publish, and the guard changes, it does not go. With `CLARA_EVENTS=on`, Funnel is allowed on exactly one port,
`CLARA_EVENTS_PORT` (8443 or 10000, never Clara's own), and only while that port maps `/` to the door and to nothing else. Funnel on
Clara's own port, on the events port mapped elsewhere, or on any other port that maps to Clara (her main listener or the door) is
refused: at start Clara will not run, while running she turns it off (`tailscale funnel --https=<port> off`) and raises the
`funnel` alert. A Funnel on a port that maps to something else of yours is not Clara's business and is left alone. Clara makes the
events mapping herself (`tailscale funnel --bg --https=<events port> http://127.0.0.1:<door port>`, only while the door listens and
Tailscale serves her), checks it every minute with the rest, removes **only that mapping** when the server stops or `CLARA_EVENTS`
is off, and never runs `tailscale funnel reset` or touches another port. When Tailscale refuses (the tailnet's policy has no `funnel`
in `nodeAttrs`), she raises the `events` alert and the door stays on 127.0.0.1. With `CLARA_EVENTS=off` none of this exists: the
guard is the one above, byte for byte.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

COMMAND_TIMEOUT = 20.0
CHECK_SECONDS = 60.0
ALERT_AFTER = 120.0  # tailscaled may need a moment at boot: only a failure that lasts is an alert


class FunnelOn(RuntimeError):
    """Funnel is on for Clara's port: the server would be on the public internet."""


@dataclass(frozen=True)
class Output:
    code: int | None
    text: str


Runner = Callable[[list[str], float], Awaitable[Output]]


@dataclass(frozen=True)
class Door:
    """The events door as Tailscale sees it: `on` is `CLARA_EVENTS`, `public_port` the Funnel port (`CLARA_EVENTS_PORT`) and `local_port`
    where the listener answers on 127.0.0.1 (`CLARA_EVENTS_LOCAL_PORT`)."""

    on: bool
    public_port: int
    local_port: int

    @property
    def target(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"


async def run(args: list[str], timeout: float) -> Output:
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        out, _ = await process.communicate()
        return Output(None, out.decode(errors="replace").strip())
    return Output(process.returncode, out.decode(errors="replace").strip())


def funnel_ports(status: dict) -> set[int]:
    ports = set()
    for key, allowed in (status.get("AllowFunnel") or {}).items():
        if allowed and ":" in key and key.rsplit(":", 1)[1].isdigit():
            ports.add(int(key.rsplit(":", 1)[1]))
    return ports


def handlers(status: dict, port: int) -> dict[str, str]:
    """What the HTTPS port serves, mount by mount: the address a mount proxies to, or what else it serves (a path, a text)."""
    found: dict[str, str] = {}
    for key, web in (status.get("Web") or {}).items():
        if key.endswith(f":{port}"):
            for mount, handler in ((web or {}).get("Handlers") or {}).items():
                handler = handler or {}
                found[mount] = str(handler.get("Proxy") or handler.get("Path") or handler.get("Text") or "").rstrip("/")
    forward = ((status.get("TCP") or {}).get(str(port)) or {}).get("TCPForward")
    if forward:
        found["tcp"] = str(forward)
    return found


def reaches(target: str, ports: set[int]) -> bool:
    """Does a proxy target (`http://127.0.0.1:8765`, `localhost:8765`, a TCP forward) name one of these ports on this machine?"""
    try:
        found = urlsplit(target if "://" in target else f"//{target}")
        return found.hostname in ("127.0.0.1", "localhost", "::1") and found.port in ports
    except ValueError:
        return False


def funnel_refused(status: dict, own: int, door: Door | None, clara: int) -> list[int]:
    """The ports where Funnel must not be. Without the door (`CLARA_EVENTS` off, or no `door`): Clara's own port, as it always was. With
    it: Clara's own port; the events port unless it maps `/` to the door and to nothing else; and every other port that maps to Clara
    (her main listener, `clara`, or the door). A port that maps to something else is not Clara's."""
    ports = sorted(funnel_ports(status))
    if door is None or not door.on:
        return [own] if own in ports else []
    mine = {clara, door.local_port}
    refused = []
    for port in ports:
        if port == own:
            refused.append(port)
        elif port == door.public_port:
            if handlers(status, port) != {"/": door.target}:
                refused.append(port)
        elif any(reaches(target, mine) for target in handlers(status, port).values()):
            refused.append(port)
    return refused


def door_of(settings) -> Door:  # type: ignore[no-untyped-def]
    return Door(settings.events == "on", settings.events_port, settings.events_local_port)


def door_published(status: dict, door: Door) -> bool:
    """Funnel is on for the events port and it maps `/` to the door, and to nothing else."""
    return door.public_port in funnel_ports(status) and handlers(status, door.public_port) == {"/": door.target}


def serving(status: dict, port: int, target: str) -> bool:
    for key, web in (status.get("Web") or {}).items():
        if key.endswith(f":{port}"):
            handler = ((web or {}).get("Handlers") or {}).get("/") or {}
            if str(handler.get("Proxy", "")).rstrip("/") == target:
                return True
    return False


def check_funnel_now(binary: str, port: int, door: Door | None = None, clara: int = 0) -> None:
    """At start, before anything is served: FunnelOn when Funnel is on for the port (with the events door on: when it is where it must
    not be, see `funnel_refused`; `clara` is the port of her main listener). A missing tailscale is not an error here (the monitor
    reports it)."""
    try:
        done = subprocess.run(
            [binary, "serve", "status", "--json"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    try:
        status = json.loads(done.stdout or "{}")
    except ValueError:
        return
    if door is not None and door.on:
        refused = funnel_refused(status, port, door, clara)
        if refused:
            raise FunnelOn(
                f"Tailscale Funnel is on for port {', '.join(map(str, refused))}, where it must not be: only the events port "
                f"{door.public_port}, mapped to the events listener ({door.target}) and nothing else, may be public. Clara would be on "
                "the public internet. Turn it off "
                f"({'; '.join(f'tailscale funnel --https={each} off' for each in refused)}) and start again."
            )
        return
    if port in funnel_ports(status):
        raise FunnelOn(
            f"Tailscale Funnel is on for port {port}: Clara would be on the public internet. Turn it off "
            f"(tailscale funnel --https={port} off) and start again."
        )


class Tailscale:
    def __init__(
        self,
        https_port: int,
        target: str,
        binary: str = "tailscale",
        runner: Runner = run,
        alert: Callable[[str, str, str], object] | None = None,
        clear: Callable[[str], object] | None = None,
        door: Door | None = None,
        door_up: Callable[[], bool] = lambda: True,
    ):
        self.https_port = https_port
        self.target = target
        self.binary = binary
        self._run = runner
        self._alert = alert
        self._clear = clear
        self.door = door  # the events door: published with Funnel when `door.on`, its leftover mapping removed when not
        self._door_up = door_up  # is the door's listener accepting? Nothing is published before it does
        self.url: str | None = None
        self.events_url: str | None = None  # the public address of the door, while it is published
        self.events_problem: str | None = None
        self.problem: str | None = None
        self._task: asyncio.Task | None = None
        self._failing_since: float | None = None
        self._events_logged = ""
        self._mapped = False  # the door's Funnel mapping is there and is Clara's: it is removed when she stops

    def describe(self) -> str:
        if self.url:
            return f"serving {self.url} (your tailnet only)"
        return f"NOT SERVING: {self.problem}" if self.problem else "starting…"

    def start(self) -> None:
        self._task = asyncio.ensure_future(self.watch())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        if self._mapped and self.door is not None:
            await self._command(["funnel", f"--https={self.door.public_port}", "off"])  # only the door's mapping
            self._mapped = False
        self.events_url = None
        if self.url:
            await self._command(["serve", f"--https={self.https_port}", "off"])
            self.url = None

    async def _command(self, args: list[str]) -> Output:
        try:
            return await self._run([self.binary, *args], COMMAND_TIMEOUT)
        except FileNotFoundError:
            return Output(127, f"{self.binary} is not installed")

    async def status(self) -> tuple[dict | None, str]:
        out = await self._command(["status", "--json"])
        if out.code != 0:
            return None, out.text or "tailscale status failed"
        try:
            found = json.loads(out.text)
        except ValueError:
            return None, "tailscale status did not answer JSON"
        if found.get("BackendState") != "Running":
            return None, f"Tailscale is not running (state {found.get('BackendState')}): sudo tailscale up"
        return found, ""

    async def once(self) -> None:
        found, problem = await self.status()
        if found is None:
            self._fail(problem)
            return
        name = str((found.get("Self") or {}).get("DNSName") or "").rstrip(".")
        if not name:
            self._fail("this machine has no Tailscale DNS name: turn on MagicDNS and HTTPS certificates")
            return
        serve = await self._serve_status()
        for port in funnel_refused(serve, self.https_port, self.door, self._clara_port()):
            await self._command(["funnel", f"--https={port}", "off"])
            if self._alert:
                self._alert("funnel", "Funnel was turned on", f"Funnel was on for port {port}: Clara turned it off.")
        if not serving(serve, self.https_port, self.target):
            out = await self._command(["serve", "--bg", f"--https={self.https_port}", self.target])
            if out.code != 0:
                hint = (
                    " (the service user may need: sudo tailscale set --operator=clara)"
                    if "denied" in out.text.lower()
                    else ""
                )
                self._fail(f"tailscale serve failed: {out.text or out.code}{hint}")
                return
        self.url = f"https://{name}" + ("" if self.https_port == 443 else f":{self.https_port}")
        self.problem = None
        self._failing_since = None
        if self._clear:
            self._clear("tailscale")
        if self.door is not None:
            await self._events(serve, name)

    async def _serve_status(self) -> dict:
        out = await self._command(["serve", "status", "--json"])
        try:
            return json.loads(out.text or "{}") if out.code == 0 else {}
        except ValueError:
            return {}

    def _clara_port(self) -> int:
        """The local port of her main listener, from the address `tailscale serve` proxies to."""
        return urlsplit(self.target).port or 0

    async def _events(self, serve: dict, name: str) -> None:
        """The events door, after what was found on the way in: published (and kept so) when it is on, its mapping removed when it was
        on and is not any more. Only the events port is ever touched here, and only with `funnel`, never `funnel reset`."""
        door = self.door
        if door is None:
            return
        if not door.on:
            if door_published(serve, door):  # what a run with `CLARA_EVENTS=on` left: Clara's own, exactly
                await self._command(["funnel", f"--https={door.public_port}", "off"])
                log.info("Tailscale: the events mapping on port %d was removed (CLARA_EVENTS is off)", door.public_port)
            return
        if not self._door_up():
            self._events_down("the events listener is not running")
            return
        status = serve
        refused = door.public_port in funnel_refused(serve, self.https_port, door, self._clara_port())
        if refused or not door_published(serve, door):
            out = await self._command(["funnel", "--bg", f"--https={door.public_port}", door.target])
            status = await self._serve_status() if out.code == 0 else {}
            if out.code != 0 or not door_published(status, door):
                if out.text != self._events_logged:
                    self._events_logged = out.text
                    log.warning("Tailscale: tailscale funnel for the events door failed: %s", out.text or out.code)
                self._events_down(
                    "Tailscale would not publish it: add funnel to nodeAttrs of your tailnet policy, and check that HTTPS is "
                    "enabled (docs/OPERATIONS.md)",
                    alert=True,
                )
                return
        self._mapped = True
        self.events_url = f"https://{name}:{door.public_port}"
        self.events_problem = None
        self._events_logged = ""
        if self._clear:
            self._clear("events")

    def _events_down(self, problem: str, alert: bool = False) -> None:
        self.events_url = None
        self.events_problem = problem
        if alert and self._alert:
            self._alert(
                "events",
                "The events door is not public",
                f"{problem}. The listener answers on 127.0.0.1 only, so nothing from the internet reaches Clara.",
            )

    def _fail(self, problem: str) -> None:
        self.problem = problem
        self.url = None
        self.events_url = None
        log.warning("Tailscale: %s", problem)
        now = time.monotonic()
        self._failing_since = self._failing_since or now
        if self._alert and now - self._failing_since >= ALERT_AFTER:
            self._alert("tailscale", "Tailscale is not serving Clara", problem)

    async def watch(self) -> None:
        wait = 5.0
        while True:
            try:
                await self.once()
            except Exception:
                log.exception("tailscale check failed")
            await asyncio.sleep(CHECK_SECONDS if self.url else wait)
            wait = min(wait * 2, CHECK_SECONDS)
