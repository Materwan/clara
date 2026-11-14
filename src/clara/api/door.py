"""The events door: the one application a public port may reach. It serves `POST /v1/events/<source id>` and **nothing else**.

It is a second, separate ASGI application (ops/door.py runs it as a second listener on 127.0.0.1, which `tailscale funnel` publishes
on its own port): it is not the main application with a filter in front. It shares the `Services` and the handler of the route
(`api/event_sources.py::receive`, one implementation: the same checks, the same limits, the same answers as the route on the main
listener), and has none of the rest. No other `/v1` route, no web site, no console, no `/health`, no docs, no OpenAPI file, no
static file: **every other path is a bare 404 and every other method on the route a bare 405**, with no body and no header that says
what answers (no `Server`, no cookie, no JSON). No token and no cookie is read anywhere here: an `Authorization` header is the
source's secret or nothing, and an API token sent to it is a wrong secret like any other.

What an outsider can make this do is what the route lets a source's secret (or GitHub's signature) do: store events for that source,
60 a minute, 64 KB a body, 1,000 waiting, 5,000 kept, ten wrong proofs a minute and the source is blocked for a minute. Failures are
counted per source id, never per address, and no forwarded header is read: behind Funnel the address of the caller is not to be trusted.
The traffic log is not written from here (a flood of wrong calls must not fill the disk): the source's own counters are.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI

from ..automations.sources import ID_PATTERN
from . import event_sources as event_sources_api
from .common import install_error_handlers
from .middleware import BodyLimitMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..services import Services

# matched whole: nothing before it, nothing after it, not even a newline
ADDRESS = re.compile(rf"/v1/events/{ID_PATTERN}")


async def _bare(
    send: Callable[[dict], Awaitable[None]], status: int, headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    """An answer with nothing in it. `Connection: close`, so a scanner's connection is not kept (and a body nobody read is not waited for)."""
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-length", b"0"), (b"connection", b"close"), *(headers or [])],
        }
    )
    await send({"type": "http.response.body", "body": b""})


class Door:
    """The whole application: the one request it takes goes on to the route, everything else is refused here, before any routing."""

    def __init__(self, route: Any):
        self.route = route

    async def __call__(self, scope: dict, receive: Callable, send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            return  # a WebSocket or a lifespan: not served (uvicorn closes it)
        if ADDRESS.fullmatch(scope["path"]) is None:
            await _bare(send, 404)
        elif scope["method"] != "POST":
            await _bare(send, 405, [(b"allow", b"POST")])
        else:
            await self.route(scope, receive, send)


def create_door_app(s: Services) -> Door:
    route = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    route.state.services = s
    install_error_handlers(route)
    router = APIRouter(prefix="/v1")
    router.add_api_route("/events/{source_id}", event_sources_api.receive, methods=["POST"], status_code=202)
    route.include_router(router)
    # 64 KB: answered 413 from the declared length before a byte is read, else by counting
    route.add_middleware(BodyLimitMiddleware)
    return Door(route)
