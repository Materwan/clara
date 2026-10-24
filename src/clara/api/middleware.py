"""ASGI middlewares, outermost first: the traffic log, the body limit, authentication, and security headers.

**Authentication** is checked here, before any route runs, so a new route is protected by default. Every `/v1` path
needs a token, except signing the browser in (which checks the token itself) and the page Google redirects to after a
Drive consent (it only shows a page that finishes the flow from the signed-in browser). `/health` answers
`{"status": "ok"}` to anyone and nothing more. `/docs` and the OpenAPI file exist only with CLARA_DEBUG.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

from ..auth import COOKIE, Blocked, client_address
from ..auth.web import HEADER
from .common import Caller, client_label, error_body

if TYPE_CHECKING:
    from ..services import Services

MEGABYTE = 1_000_000
DEFAULT_LIMIT = 16 * MEGABYTE
LIMITS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^/v1/projects/\d+/files$"), 90 * MEGABYTE),
    (re.compile(r"^/v1/workspace/upload$"), 110 * MEGABYTE),
    (re.compile(r"^/v1/documents/extract$"), 41 * MEGABYTE),
    (re.compile(r"^/v1/turns/[^/]+/tool-results$"), 64 * MEGABYTE),
    (re.compile(r"^/v1/chat(/stream)?$"), 48 * MEGABYTE),
)
OPEN_PATHS = frozenset({"/health", "/v1/auth/web-login", "/v1/connections/google/callback"})
DEBUG_PATHS = ("/docs", "/openapi.json", "/redoc")


def limit_for(path: str) -> int:
    for pattern, size in LIMITS:
        if pattern.match(path):
            return size
    return DEFAULT_LIMIT


async def _json(send: Callable, status: int, body: dict, headers: list | None = None) -> None:
    data = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": data})


def _headers(scope: dict) -> dict[str, str]:
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}


class AuthMiddleware:
    def __init__(self, app: Any, services: Services):
        self.app = app
        self.s = services

    async def __call__(self, scope: dict, receive: Callable, send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith(DEBUG_PATHS) and not self.s.settings.debug:
            await _json(send, 404, error_body(404, "Not found."))
            return
        if not path.startswith("/v1/") or path in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        peer = (scope.get("client") or ("unknown", 0))[0]
        address = client_address(peer, headers.get("x-forwarded-for"))
        try:
            self.s.limiter.check(address)
        except Blocked as error:
            await _json(send, 429, error_body(429, str(error)), [(b"retry-after", str(error.retry_after).encode())])
            return
        found = self.authenticate(headers)
        if found is None:
            self.s.limiter.fail(address)
            self.s.traffic.record("auth_failure", path=path, address=address)
            await _json(
                send,
                401,
                error_body(401, "Sign in first: send Authorization: Bearer <token>."),
                [(b"www-authenticate", b"Bearer")],
            )
            return
        token, session_id = found
        scope.setdefault("state", {})["caller"] = Caller(
            token, client_label(headers.get("x-clara-client"), token), session_id
        )
        await self.app(scope, receive, send)

    def authenticate(self, headers: dict[str, str]):
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = self.s.tokens.verify(authorization[7:].strip())
            return (token, None) if token else None
        if headers.get(HEADER.lower()) != "1":
            return None  # a cookie alone is never enough: another site could have made the browser send it
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(COOKIE)
        if morsel is None:
            return None
        verified = self.s.web_sessions.verify(morsel.value)
        if verified is None:
            return None
        session_id, token = verified
        return token, session_id


class BodyLimitMiddleware:
    """A body has a size limit however it comes (a Content-Length, or chunks with none)."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"])
        declared = _headers(scope).get("content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                await _json(send, 400, error_body(400, "Bad Content-Length."))
                return
            if size > limit:
                await _json(send, 413, error_body(413, f"This request is too big: at most {limit // MEGABYTE} MB."))
                return
        seen = 0
        started = refused = False

        async def counted() -> dict:
            nonlocal seen, refused
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit and not refused:
                    refused = True
                    if not started:
                        await _json(
                            send, 413, error_body(413, f"This request is too big: at most {limit // MEGABYTE} MB.")
                        )
                    return {"type": "http.disconnect"}
            return message

        async def watched(message: dict) -> None:
            nonlocal started
            if refused:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, counted, watched)


class TrafficMiddleware:
    """One metadata line per request: method, route (never the query string), status, duration, client, sizes."""

    def __init__(self, app: Any, services: Services):
        self.app = app
        self.s = services

    async def __call__(self, scope: dict, receive: Callable, send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http" or scope["path"] == "/health" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        begun = time.monotonic()
        status = 0
        sent = 0
        received = 0

        async def counted() -> dict:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b"")) if message["type"] == "http.request" else 0
            return message

        async def watched(message: dict) -> None:
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, counted, watched)
        finally:
            caller = scope.get("state", {}).get("caller")
            self.s.traffic.record(
                "http",
                method=scope["method"],
                path=_route(scope["path"]),
                status=status,
                ms=int((time.monotonic() - begun) * 1000),
                client=caller.name if caller else None,
                label=caller.client if caller else None,
                bytes_in=received,
                bytes_out=sent,
            )
            if caller is not None and self.s.traffic.debugging:
                self.s.traffic.content(
                    "http",
                    method=scope["method"],
                    path=scope["path"],
                    query=scope.get("query_string", b"").decode("latin-1"),
                )


_ID = re.compile(r"/(?:c[0-9a-f]{16}|\d+|[0-9a-f]{32})(?=/|$)")


def _route(path: str) -> str:
    """A path with its ids replaced, so the log says which route, not which conversation."""
    return _ID.sub("/{id}", path)


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _ in message.get("headers", [])}
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        *(header for header in SECURITY_HEADERS if header[0] not in names),
                    ],
                }
            await send(message)

        await self.app(scope, receive, with_headers)
