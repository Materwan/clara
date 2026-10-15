"""Tailscale: Clara publishes itself with `tailscale serve`, inside your tailnet only, never with Funnel.

The server listens on 127.0.0.1 and nowhere else; tailscaled terminates HTTPS and proxies to it. At start Clara asks
`tailscale serve status --json` whether Funnel is on for its HTTPS port, and **refuses to start** if it is. Then it
sets the `serve` mapping (retrying while tailscaled is not up), and checks every minute that it still serves and that
Funnel was not turned on meanwhile: if Funnel appears, Clara turns it off and raises an alert; if serving stops, an
alert too. The mapping is removed when the server stops.
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


async def run(args: list[str], timeout: float) -> Output:
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
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


def serving(status: dict, port: int, target: str) -> bool:
    for key, web in (status.get("Web") or {}).items():
        if key.endswith(f":{port}"):
            handler = ((web or {}).get("Handlers") or {}).get("/") or {}
            if str(handler.get("Proxy", "")).rstrip("/") == target:
                return True
    return False


def check_funnel_now(binary: str, port: int) -> None:
    """At start, before anything is served: FunnelOn when Funnel is on for the port. A missing tailscale is not an
    error here (the monitor reports it)."""
    try:
        done = subprocess.run([binary, "serve", "status", "--json"], capture_output=True, text=True,
                              timeout=COMMAND_TIMEOUT, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    try:
        status = json.loads(done.stdout or "{}")
    except ValueError:
        return
    if port in funnel_ports(status):
        raise FunnelOn(f"Tailscale Funnel is on for port {port}: Clara would be on the public internet. Turn it off "
                       f"(tailscale funnel --https={port} off) and start again.")


class Tailscale:
    def __init__(self, https_port: int, target: str, binary: str = "tailscale", runner: Runner = run,
                 alert: Callable[[str, str, str], object] | None = None, clear: Callable[[str], object] | None = None):
        self.https_port = https_port
        self.target = target
        self.binary = binary
        self._run = runner
        self._alert = alert
        self._clear = clear
        self.url: str | None = None
        self.problem: str | None = None
        self._task: asyncio.Task | None = None
        self._failing_since: float | None = None

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
        out = await self._command(["serve", "status", "--json"])
        try:
            serve = json.loads(out.text or "{}") if out.code == 0 else {}
        except ValueError:
            serve = {}
        if self.https_port in funnel_ports(serve):
            await self._command(["funnel", f"--https={self.https_port}", "off"])
            if self._alert:
                self._alert("funnel", "Funnel was turned on", f"Funnel was on for port {self.https_port}: Clara turned it off.")
        if not serving(serve, self.https_port, self.target):
            out = await self._command(["serve", "--bg", f"--https={self.https_port}", self.target])
            if out.code != 0:
                hint = " (the service user may need: sudo tailscale set --operator=clara)" if "denied" in out.text.lower() else ""
                self._fail(f"tailscale serve failed: {out.text or out.code}{hint}")
                return
        self.url = f"https://{name}" + ("" if self.https_port == 443 else f":{self.https_port}")
        self.problem = None
        self._failing_since = None
        if self._clear:
            self._clear("tailscale")

    def _fail(self, problem: str) -> None:
        self.problem = problem
        self.url = None
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
