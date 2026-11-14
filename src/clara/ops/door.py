"""The events door as a listener: a second `uvicorn.Server` on 127.0.0.1, in the same event loop and with the same `Services` as the
main one, serving the application of api/door.py (`POST /v1/events/<source id>` and nothing else).

It exists only with `CLARA_EVENTS=on`. Without it no second server, socket or task is made and the process is what it was. The port
(`CLARA_EVENTS_LOCAL_PORT`) is taken at start, before anything is served, and a port that is taken is a start-up error that names it.
The server runs once the main one is up (its lifespan starts it, after the loops) and stops with it, in the same drain: while Clara
is stopping the route answers 503 (`refuse_when_stopping`), and the listener closes when the main server's lifespan ends.
`ops/tailscale.py` publishes it with Funnel, only while it listens.

What makes it hard to hold open: uvicorn's `limit_concurrency` (a 503 beyond), `timeout_keep_alive`, small headers, `server_header` off,
no access log (an address is a secret-ish id), no forwarded header believed (`proxy_headers` off), no WebSocket, no lifespan, and
**a read deadline** that uvicorn has not: a connection that sends nothing, or a byte a minute, is cut after `READ_DEADLINE` seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import TYPE_CHECKING

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from ..api.door import create_door_app

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

READ_DEADLINE = (
    15.0  # seconds a request has from the connection (or the last answer) to its answer; GitHub itself waits 10
)
CONCURRENCY = 64  # connections or running requests at once: more get a 503
KEEP_ALIVE = 2  # seconds an idle connection is kept between two requests
HEADERS = 16 * 1024  # bytes of request line and headers
BACKLOG = 128
GRACE = 3  # seconds the open requests get when the listener stops


class DoorError(RuntimeError):
    """The listener cannot start: the message says why and what to do."""


class DoorProtocol(H11Protocol):
    """HTTP/1.1 as uvicorn speaks it, with a deadline per request: it starts when the connection opens and again at each answer, and when
    it runs out the connection is dropped, whatever the client was doing (nothing, the headers a byte at a time, a body that never ends)."""

    _deadline: asyncio.TimerHandle | None = None

    def connection_made(self, transport) -> None:  # type: ignore[no-untyped-def,override]
        super().connection_made(transport)
        self._arm()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        self._arm()

    def connection_lost(self, exc: Exception | None) -> None:
        self._disarm()
        super().connection_lost(exc)

    def _arm(self) -> None:
        self._disarm()
        if not self.transport.is_closing():
            self._deadline = self.loop.call_later(READ_DEADLINE, self._expired)

    def _disarm(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None

    def _expired(self) -> None:
        self._deadline = None
        self.transport.abort()


class DoorServer(uvicorn.Server):
    """It does not take the signals: the main server's own handler stops Clara, and this one stops with it."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[no-untyped-def]
        yield


class EventsDoor:
    def __init__(self, services: Services):
        self.s = services
        self.port = services.settings.events_local_port
        self.sock: socket.socket | None = None
        self.server: DoorServer | None = None
        self.problem = ""
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def up(self) -> bool:
        """The listener is accepting."""
        return self.server is not None and self.server.started and self._task is not None and not self._task.done()

    def config(self) -> uvicorn.Config:
        """How the listener is made: what it serves and what it limits."""
        return uvicorn.Config(
            create_door_app(self.s),
            host="127.0.0.1",
            port=self.port,
            http=DoorProtocol,
            ws="none",
            lifespan="off",
            interface="asgi3",
            log_config=None,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            server_header=False,
            limit_concurrency=CONCURRENCY,
            timeout_keep_alive=KEEP_ALIVE,
            timeout_graceful_shutdown=GRACE,
            h11_max_incomplete_event_size=HEADERS,
            backlog=BACKLOG,
        )

    def bind(self) -> None:
        """Take the port now, before anything is served: DoorError (naming it) when it cannot be had."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
            sock.listen(BACKLOG)
            sock.setblocking(False)
        except OSError as error:
            sock.close()
            raise DoorError(
                f"CLARA_EVENTS_LOCAL_PORT={self.port}: the events listener cannot take 127.0.0.1:{self.port} "
                f"({error.strerror or error}). Another program uses that port: set another CLARA_EVENTS_LOCAL_PORT, or "
                "CLARA_EVENTS=off."
            ) from None
        self.sock = sock

    def start(self) -> None:
        """Serve, once the main server is up (called from its lifespan, in its event loop)."""
        try:
            if self.sock is None:
                self.bind()
            assert self.sock is not None
            self.server = DoorServer(self.config())
            self._task = asyncio.ensure_future(self._serve(self.server, self.sock))
        except Exception as error:
            self._failed(f"the events listener did not start: {error}")
            return
        self._task.add_done_callback(self._ended)
        log.info("the events listener is on http://127.0.0.1:%d (POST /v1/events/<id> only)", self.port)

    @staticmethod
    async def _serve(server: DoorServer, sock: socket.socket) -> None:
        try:
            await server.serve(sockets=[sock])
        except SystemExit as error:
            # uvicorn exits by raising: that must not leave the task and stop the main loop with it
            raise RuntimeError(f"uvicorn exited ({error.code})") from None

    def _ended(self, task: asyncio.Task) -> None:
        if self._stopping or task.cancelled():
            return
        error = task.exception()
        self._failed(f"the events listener stopped{f' ({error})' if error else ''}")

    def _failed(self, problem: str) -> None:
        self.problem = problem
        log.error("%s", problem)
        self.s.alerts.raise_(
            "events",
            "The events listener is down",
            "It does not answer: nothing reaches Clara through the events door until it is back (restart Clara; the log says why).",
        )

    async def stop(self) -> None:
        """Stop accepting, let what runs finish (a few seconds), close the port."""
        self._stopping = True
        if self.server is not None:
            self.server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, GRACE + 2)  # on a timeout it cancels the task
            except TimeoutError:
                log.warning("the events listener did not stop in time: cut")
            except asyncio.CancelledError:
                if not self._task.cancelled():  # it is this call that was cancelled
                    raise
            except Exception:
                log.debug("the events listener had already failed", exc_info=True)  # reported when it ended
        if self.sock is not None:
            self.sock.close()
            self.sock = None
