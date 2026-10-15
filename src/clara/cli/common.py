"""What clara-chat and clara-admin share: where the server is, the token file, and a small HTTP client.

The token is read from a file only you can read (`~/.config/clara/token`, or `--token-file`, or `CLARA_TOKEN_FILE`);
a file others can read is refused. `--save-token` asks for a token (hidden) and writes the file with mode 0600.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx

DEFAULT_URL = "http://127.0.0.1:8765"


class ClientError(Exception):
    pass


def token_file(given: str | None = None) -> Path:
    if given:
        return Path(given).expanduser()
    if os.environ.get("CLARA_TOKEN_FILE"):
        return Path(os.environ["CLARA_TOKEN_FILE"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "clara" / "token"


def read_token(path: Path) -> str:
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ClientError(f"No token file at {path}: run with --save-token first (or give --token-file).") from None
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ClientError(f"{path} can be read by others: chmod 600 {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ClientError(f"{path} is empty.")
    return token


def save_token(path: Path) -> None:
    token = getpass.getpass("Token (input hidden): ").strip()
    if not token.startswith("clara_"):
        raise ClientError("That does not look like a Clara token (clara_<id>_<secret>).")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.chmod(path, 0o600)
    print(f"Saved to {path} (readable by you only).")


def problem(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        return f"{error.get('message') or response.text} ({response.status_code})"
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:300]}"


class Api:
    def __init__(self, url: str, token: str, client: str):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "X-Clara-Client": client}
        self.http = httpx.Client(base_url=self.url, headers=self.headers, timeout=httpx.Timeout(10.0, read=600.0))

    def call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.ConnectError:
            raise ClientError(f"Cannot reach Clara at {self.url}.") from None
        if response.is_error:
            raise ClientError(problem(response))
        return response.json() if response.content else {}

    def get(self, path: str, **params) -> dict:
        return self.call("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, body: dict | None = None) -> dict:
        return self.call("POST", path, json=body or {})

    def patch(self, path: str, body: dict) -> dict:
        return self.call("PATCH", path, json=body)

    def put(self, path: str, body: dict) -> dict:
        return self.call("PUT", path, json=body)

    def delete(self, path: str) -> dict:
        return self.call("DELETE", path)

    def stream(self, method: str, path: str, body: dict | None = None, read_timeout: float = 600.0) -> Iterator[dict]:
        """Server-sent events, as dicts."""
        timeout = httpx.Timeout(10.0, read=read_timeout)
        try:
            with httpx.Client(base_url=self.url, headers=self.headers, timeout=timeout) as http, \
                    http.stream(method, path, json=body) as response:
                if response.is_error:
                    response.read()
                    raise ClientError(problem(response))
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line[6:])
        except httpx.ConnectError:
            raise ClientError(f"Cannot reach Clara at {self.url}.") from None


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
