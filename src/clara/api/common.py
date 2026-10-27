"""What the routers share: the error shape, who is calling, server-sent events, and the mapping of failures to HTTP.

Every error is `{"error": {"code": "...", "message": "..."}}`. Requests carry no identity fields: the token says which
client is calling, and the `X-Clara-Client` header may label what the request comes from (web, cli, console,
discord, script).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from ..auth import CLIENTS, TokenInfo
from ..connectors.forms import CLIENTS as FORM_CLIENTS

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger("clara.api")

KEEPALIVE_SECONDS = 15.0
CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal",
    502: "model_failed",
    503: "unavailable",
    504: "timeout",
}


class ApiError(HTTPException):
    def __init__(self, status: int, message: str, code: str | None = None, headers: dict | None = None):
        super().__init__(status, message, headers)
        self.code = code or CODES.get(status, "error")


def error_body(status: int, message: str, code: str | None = None) -> dict:
    return {"error": {"code": code or CODES.get(status, "error"), "message": message}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = getattr(error, "code", None)
        return JSONResponse(
            error_body(error.status_code, str(error.detail), code),
            error.status_code,
            headers=getattr(error, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ())[1:]) or 'body'}: {item.get('msg')}"
            for item in error.errors()
        )
        return JSONResponse(error_body(422, problems or "Invalid request."), 422)

    @app.exception_handler(Exception)
    async def crash(request: Request, error: Exception) -> JSONResponse:
        log.exception("%s %s failed", request.method, request.url.path)
        return JSONResponse(error_body(500, "Something went wrong on the server (see its log)."), 500)


@dataclass(frozen=True)
class Caller:
    """Who sent a request: the token (by its name), the client label, and whether it came through the browser."""

    token: TokenInfo
    client: str
    web_session: int | None = None

    @property
    def name(self) -> str:
        return self.token.name

    @property
    def may_answer_questions(self) -> bool:
        """Is this caller shown Clara's questions (`ask_questions`) and allowed to answer them? The web site, and a script
        token that may answer approvals (the desktop island, the phone app): one with `approvals: none` is not."""
        return self.client in FORM_CLIENTS or (self.token.client == "script" and self.token.approvals != "none")


def caller(request: Request) -> Caller:
    found = getattr(request.state, "caller", None)
    if found is None:
        raise ApiError(401, "Sign in first: send Authorization: Bearer <token>.")
    return found


def services(request: Request) -> Services:
    return request.app.state.services


def client_label(header: str | None, token: TokenInfo) -> str:
    label = (header or "").strip().lower()
    return label if label in CLIENTS else token.client


def refuse_when_stopping(request: Request) -> None:
    if services(request).lifecycle.stopping:
        raise ApiError(503, "Clara is stopping and takes no new question.", "stopping")


def failure(error: Exception, what: str) -> ApiError:
    """The HTTP answer to a failure of the agent or a model."""
    from ..agent.model import ModelTimeout
    from ..agent.turn import (
        ClientToolTimeout,
        NoSuchConversation,
        NothingToCompact,
        NothingToTitle,
        PromptTooLarge,
        ServerStopping,
    )
    from ..llm import NoModel
    from ..timeutil import ZoneError

    if isinstance(error, (NothingToCompact, NothingToTitle)):
        return ApiError(409, str(error))
    if isinstance(error, NoSuchConversation):
        return ApiError(404, str(error))
    if isinstance(error, PromptTooLarge):
        return ApiError(413, str(error))
    if isinstance(error, (ValueError, ZoneError)):
        return ApiError(422, str(error))
    if isinstance(error, ServerStopping):
        return ApiError(503, str(error), "stopping")
    if isinstance(error, NoModel):
        return ApiError(503, str(error), "no_model")
    if isinstance(error, (ModelTimeout, ClientToolTimeout)):
        return ApiError(504, str(error))
    log.exception("%s failed", what)
    return ApiError(502, f"The language model failed ({type(error).__name__}).")


def error_event(error: Exception, what: str) -> dict:
    problem = failure(error, what)
    return {"type": "error", "status": problem.status_code, "code": problem.code, "message": str(problem.detail)}


# ----------------------------------------------------------------------
# Server-sent events
# ----------------------------------------------------------------------
def sse(event: dict, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def with_keepalive(
    events: AsyncIterator[dict], interval: float = KEEPALIVE_SECONDS
) -> AsyncIterator[dict | None]:
    """The events, and a None every `interval` seconds of silence (proxies drop idle connections)."""
    iterator = events.__aiter__()
    pending = asyncio.ensure_future(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield None
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield event
            pending = asyncio.ensure_future(iterator.__anext__())
    finally:
        pending.cancel()
        with contextlib.suppress(BaseException):
            await pending
        with contextlib.suppress(Exception):
            await iterator.aclose()  # type: ignore[attr-defined]


def event_stream(lines: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        lines, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
