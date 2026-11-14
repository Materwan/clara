"""The events door: a second listener that serves `POST /v1/events/<source id>` and nothing else, the guard that lets Funnel publish that
one door and nothing more, and the settings that turn it on. Nothing here opens a port or runs a real `tailscale`: the app is called
through httpx's ASGI transport, a fake `tailscale` (a script, and a runner that remembers what it was asked) stands for the real one."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import shutil
import socket
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from uvicorn.server import ServerState

from clara import console
from clara.api.door import ADDRESS, create_door_app
from clara.api.door import Door as DoorApp
from clara.app import StartError, build_services, create_app, extend, open_database, prepare
from clara.app import main as server_main
from clara.automations import SourceEvent
from clara.automations.sources import MAX_WAITING
from clara.db import InstanceLock
from clara.ops import door as door_module
from clara.ops.door import CONCURRENCY, DoorError, DoorProtocol, DoorServer, EventsDoor
from clara.ops.status import events_door, status
from clara.ops.tailscale import Door, FunnelOn, Output, Tailscale, check_funnel_now, door_of, funnel_refused
from clara.settings import SettingsError, load_settings
from clara.timeutil import iso, utcnow
from test_event_sources import Ticks, bearer, made, watch, work
from test_github_webhook import deliver, webhook

ID = "k" + "a" * 32  # a well-formed address nobody was given
MAIN, DOOR = "http://127.0.0.1:8765", "http://127.0.0.1:8766"


@pytest.fixture
async def door(services):
    """What reaches the events door from outside: no token, no cookie."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_door_app(services)), base_url="http://door.test") as http:
        yield http


@pytest.fixture
def ticks(services) -> Ticks:
    clock = Ticks()
    services.event_sources._ticks = clock
    services.event_sources.limiter._clock = clock
    return clock


@pytest.fixture
def events_services(environ, llm):
    """Services of an installation that turned the door on (Tailscale off, as in every test)."""
    s = _events_services({**environ, "CLARA_EVENTS": "on"}, llm)
    yield s
    s.traffic.close()
    s.db.close()


def _events_services(environ, llm):
    settings = load_settings(environ, use_file=False)
    s = build_services(settings, open_database(settings), provider_factory=llm.factory)
    extend(s)
    s.runtime.set("default_model", "local:fake")
    s.runtime.set("reader", False)
    return s


# ----------------------------------------------------------------------
# It serves one route and says nothing about anything else
# ----------------------------------------------------------------------
NOT_THE_ROUTE = [
    ("GET", "/"), ("GET", "/index.html"), ("GET", "/favicon.ico"), ("GET", "/health"), ("GET", "/v1/health"), ("GET", "/v1/conversations"),
    ("POST", "/v1/chat"), ("POST", "/v1/chat/stream"), ("GET", "/v1/auth/me"), ("POST", "/v1/auth/web-login"), ("GET", "/v1/admin/status"),
    ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"), ("GET", "/static/app.js"), ("GET", "/assets/index.js"),
    ("POST", "/v1/events/ack"), ("GET", "/v1/events/ack"), ("GET", "/v1/events"), ("GET", "/v1/events/stream"),
    ("POST", "/v1/events/nope"), ("POST", "/v1/events/k" + "0" * 31), ("POST", "/v1/events/k" + "0" * 33), ("POST", "/v1/events/K" + "0" * 32),
    ("POST", "/v1/events/k" + "g" * 32), ("POST", f"/v1/events/{ID}/"), ("POST", f"/v1/events/{ID}/x"), ("POST", f"//v1/events/{ID}"),
    ("POST", f"/v1/events/{ID}%0A"), ("POST", f"/x/v1/events/{ID}"), ("GET", "/v1/event-sources"), ("POST", "/v1/event-sources"),
    ("POST", f"/v1/event-sources/{ID}/fire"), ("GET", "/v1/connections/google/callback"),
]
CALLERS = [{}, {"X-Clara-Client": "web"}, {"Cookie": "clara_session=abc", "X-Clara-Web": "1"}]


@pytest.mark.parametrize("method_and_path", NOT_THE_ROUTE, ids=lambda item: f"{item[0]} {item[1]!r}")
async def test_every_other_path_is_a_bare_404_with_or_without_a_token(door, token, method_and_path):
    method, path = method_and_path
    for extra in (*CALLERS, {"Authorization": f"Bearer {token}"}):
        answer = await door.request(method, path, headers=extra, content=b"{}" if method == "POST" else None)
        assert answer.status_code == 404 and answer.content == b"", (path, extra)
        assert set(answer.headers) == {"content-length", "connection"}, answer.headers  # no server, no type, no cookie, nothing
        assert answer.headers["content-length"] == "0" and answer.headers["connection"] == "close"
        assert "clara" not in str(answer.headers).lower()


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def test_every_other_method_on_the_route_is_a_bare_405_and_does_not_say_whether_the_address_exists(services, door, token, method):
    source, _ = made(services)
    for address in (source.address, f"/v1/events/{ID}"):  # one that exists and one that does not: the same answer
        for extra in (*CALLERS, {"Authorization": f"Bearer {token}"}):
            answer = await door.request(method, address, headers=extra)
            assert answer.status_code == 405 and answer.content == b"" and answer.headers["allow"] == "POST"
            assert set(answer.headers) == {"content-length", "connection", "allow"}
    assert services.event_sources.describe(source)["failures"] == 0 and services.event_sources.limiter._failures == {}


def test_the_address_is_matched_whole_even_with_a_newline_after_it(services):
    assert ADDRESS.fullmatch(f"/v1/events/{ID}") and not ADDRESS.fullmatch(f"/v1/events/{ID}\n") and not ADDRESS.fullmatch(f"/v1/events/{ID}/")
    sent: list = []

    async def send(message):
        sent.append(message)

    asyncio.run(create_door_app(services)({"type": "http", "method": "POST", "path": f"/v1/events/{ID}\n", "headers": []}, None, send))
    assert sent[0]["status"] == 404 and sent[1]["body"] == b""  # a `$` would have let it through to the route


async def test_an_api_token_or_a_cookie_sent_to_the_door_is_a_wrong_secret_like_any_other(services, door, token):
    source, secret = made(services)
    for headers in ({"Authorization": f"Bearer {token}"}, {"Cookie": "clara_session=abc", "X-Clara-Web": "1"}, {"X-Clara-Token": token}):
        answer = await door.post(source.address, json={}, headers=headers)
        assert answer.status_code == 401 and answer.json() == {"error": {"code": "unauthorized", "message": "Not authorized."}}
    assert services.event_sources.describe(source)["failures"] == 3 and services.limiter._failures == {}
    assert (await door.post(source.address, json={"key": "a"}, headers=bearer(secret))).status_code == 202


async def test_the_door_has_no_session_cookie_no_security_headers_of_the_site_and_no_server_name(services, door):
    source, secret = made(services)
    answer = await door.post(source.address, json={"key": "a"}, headers=bearer(secret))
    assert answer.status_code == 202 and "set-cookie" not in answer.headers and "server" not in answer.headers
    assert set(answer.headers) <= {"content-length", "content-type"}


def test_a_websocket_or_anything_that_is_not_http_is_not_served(services):
    app = create_door_app(services)
    sent: list = []

    async def run():
        async def send(message):
            sent.append(message)

        await app({"type": "websocket", "path": f"/v1/events/{ID}"}, None, send)
        await app({"type": "lifespan"}, None, send)

    asyncio.run(run())
    assert sent == [] and isinstance(app, DoorApp)


async def test_the_main_listener_still_answers_its_routes_and_the_door_is_not_among_them(services, client):
    assert (await client.get("/health")).json() == {"status": "ok"} and (await client.get("/v1/auth/me")).status_code == 200
    assert (await client.get("/v1/event-sources")).status_code == 200


# ----------------------------------------------------------------------
# Same Services, same engine, same answers
# ----------------------------------------------------------------------
async def test_an_event_accepted_on_the_door_starts_a_run_through_the_same_engine(services, door, llm):
    source, secret = made(services)
    automation = watch(services, source.kind, "Door", prompt="Look at the door.", per_run=True)
    llm.replies = ["Seen."]
    answer = await door.post(source.address, json={"key": "d1", "title": "The door", "data": {"state": "open"}}, headers=bearer(secret))
    assert answer.status_code == 202
    assert [run["status"] for run in services.automations.runs(automation.id)] == ["queued"]
    await work(services)
    assert [run["status"] for run in services.automations.runs(automation.id)] == ["ok"] and llm.seen
    again = await door.post(source.address, json={"key": "d1", "title": "The door", "data": {"state": "open"}}, headers=bearer(secret))
    assert (again.status_code, again.json()) == (200, {"duplicate": True})
    assert services.automations.event(answer.json()["id"]).title == "The door"  # the event is in the database the main listener reads


async def test_a_github_webhook_on_the_door_starts_a_run_too(services, door, llm):
    source, secret = webhook(services)
    automation = watch(services, "github.push", "Pushes", prompt="Look.", per_run=True)
    llm.replies = ["Seen."]
    push = {"ref": "refs/heads/main", "before": "a" * 40, "after": "b" * 40, "commits": [{"id": "b" * 40, "message": "Fix", "author": {"name": "alice", "username": "alice"}}],
            "repository": {"full_name": "Materwan/test"}}
    assert (await deliver(door, source, secret, "push", push)).status_code == 202
    await work(services)
    assert [run["status"] for run in services.automations.runs(automation.id)] == ["ok"]


async def test_an_address_nobody_was_given_costs_nothing_on_the_door_either(services, door, caplog, monkeypatch):
    source, secret = made(services)
    seen: list = []
    monkeypatch.setattr(services.traffic, "record", lambda kind, **fields: seen.append(kind))
    monkeypatch.setattr(services.secrets, "get", lambda name: seen.append(name))
    with caplog.at_level(logging.DEBUG):
        for n in range(30):
            answer = await door.post(f"/v1/events/k{n:032x}", json={}, headers=bearer(secret))
            assert answer.status_code == 404
    assert seen == [] and [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert services.event_sources.limiter._failures == {} and services.event_sources._sent == {} and services.alerts.active == {}


async def test_the_door_writes_no_traffic_line_whatever_a_stranger_sends(services, door, monkeypatch):
    source, secret = made(services)
    seen: list = []
    monkeypatch.setattr(services.traffic, "record", lambda kind, **fields: seen.append(kind))
    await door.post(source.address, json={"key": "a"}, headers=bearer(secret))
    await door.post(source.address, json={"key": "a"}, headers=bearer("wrong"))
    await door.get("/v1/chat")
    assert seen == []  # a flood of wrong calls must not fill the disk: the source's own counters are the record
    assert services.event_sources.describe(source)["failures"] == 1


async def test_wrong_secrets_are_counted_per_source_and_no_forwarded_header_is_believed(services, door, ticks):
    first, first_secret = made(services, "door")
    second, second_secret = made(services, "lamp")
    for number in range(10):
        wrong = await door.post(first.address, json={}, headers={**bearer("wrong"), "X-Forwarded-For": f"203.0.113.{number}", "Forwarded": f"for=192.0.2.{number}"})
        assert wrong.status_code == 401
    blocked = await door.post(first.address, json={"key": "a"}, headers={**bearer(first_secret), "X-Forwarded-For": "198.51.100.9"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0  # whatever address it says it comes from
    assert (await door.post(second.address, json={"key": "a"}, headers={**bearer(second_secret), "X-Forwarded-For": "203.0.113.1"})).status_code == 202
    assert set(services.event_sources.limiter._failures) <= {first.id} and services.limiter._failures == {} and services.limiter._blocked == {}
    ticks.now += 61
    assert (await door.post(first.address, json={"key": "a"}, headers=bearer(first_secret))).status_code == 202


async def test_the_door_takes_sixty_events_a_minute_per_source_and_a_thousand_waiting(services, door, ticks):
    source, secret = made(services)
    for n in range(60):
        assert (await door.post(source.address, json={"key": f"e{n}"}, headers=bearer(secret))).status_code == 202
    over = await door.post(source.address, json={"key": "e60"}, headers=bearer(secret))
    assert over.status_code == 429 and int(over.headers["retry-after"]) >= 1
    ticks.now += 61
    watch(services, source.kind, "Digest", catch_up="digest")
    old = utcnow() - timedelta(hours=2)
    services.automations.ingest([SourceEvent(source.kind, source.kind, f"old{n}", iso(old), title="old", data={}) for n in range(MAX_WAITING + 1)])
    full = await door.post(source.address, json={"key": "new"}, headers=bearer(secret))
    assert full.status_code == 429 and "wait for a run" in full.json()["error"]["message"]


async def test_the_door_refuses_a_body_over_64_kb_before_reading_it_and_counts_what_has_no_length(services, door):
    source, secret = made(services)
    app = create_door_app(services)
    reads: list = []
    sent: list = []

    async def receive():
        reads.append("read")
        return {"type": "http.request", "body": b"x", "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": source.address, "headers": [(b"content-length", b"70000"), (b"content-type", b"application/json"), (b"authorization", f"Bearer {secret}".encode())]}
    await app(scope, receive, send)
    assert reads == [] and sent[0]["status"] == 413  # answered from the declared length: not a byte was read
    assert json.loads(sent[1]["body"])["error"]["message"] == "This request is too big: at most 64 KB."

    async def chunks():
        for _ in range(100):
            yield b"a" * 1024

    json_type = {**bearer(secret), "content-type": "application/json"}
    assert (await door.post(source.address, content=chunks(), headers=json_type)).status_code == 413  # no length declared: counted
    assert (await door.post(f"/v1/events/k{'0' * 32}", content=b" " * 70_000, headers=json_type)).status_code == 413  # an unknown address too
    assert (await door.post(source.address, content=b"{}", headers={**bearer(secret), "content-type": "text/plain"})).status_code == 415
    assert services.automations.recent_events() == []


# ----------------------------------------------------------------------
# The listener: what it is made of, what it limits
# ----------------------------------------------------------------------
def test_the_listener_is_made_with_the_limits_of_a_door_and_none_of_a_site(events_services):
    config = EventsDoor(events_services).config()
    assert (config.host, config.port, config.limit_concurrency, config.timeout_keep_alive) == ("127.0.0.1", 8766, CONCURRENCY, 2)
    assert (config.server_header, config.proxy_headers, config.access_log, config.ws, config.lifespan) == (False, False, False, "none", "off")
    assert config.h11_max_incomplete_event_size == 16 * 1024 and config.timeout_graceful_shutdown == 3 and config.http is DoorProtocol
    config.load()
    assert config.http_protocol_class is DoorProtocol and isinstance(config.loaded_app, DoorApp)  # no proxy middleware, no logger in front


def test_the_door_server_leaves_the_signals_to_the_main_server(events_services):
    server = DoorServer(EventsDoor(events_services).config())
    with server.capture_signals():
        pass
    assert server._captured_signals == []


def test_with_the_door_off_no_second_listener_and_no_mapping_exist(services, settings):
    assert settings.events == "off" and (settings.events_port, settings.events_local_port) == (8443, 8766)
    assert services.events_door is None and services.tailscale is None
    assert events_door(services) == {"on": False, "local_port": 8766, "public_port": 8443, "listening": False, "published": False, "url": "", "problem": ""}


def test_with_the_door_on_the_listener_exists_but_listens_nowhere_until_the_server_starts_it(events_services):
    door = events_services.events_door
    assert isinstance(door, EventsDoor) and door.port == 8766 and door.sock is None and door.up is False


def test_a_taken_port_is_a_start_up_error_that_names_it(events_services, monkeypatch):
    made_sockets: list = []

    class Taken:
        def __init__(self, *args):
            self.calls: list = []
            self.closed = False
            made_sockets.append(self)

        def setsockopt(self, *args):
            self.calls.append("setsockopt")

        def bind(self, address):
            self.calls.append(("bind", address))
            raise OSError(errno.EADDRINUSE, "Address already in use")

        def close(self):
            self.closed = True

    fake = SimpleNamespace(socket=Taken, AF_INET=2, SOCK_STREAM=1, SOL_SOCKET=1, SO_REUSEADDR=2)
    monkeypatch.setattr(door_module, "socket", fake)
    door = events_services.events_door
    with pytest.raises(DoorError) as error:
        door.bind()
    assert "CLARA_EVENTS_LOCAL_PORT=8766" in str(error.value) and "127.0.0.1:8766" in str(error.value) and "Address already in use" in str(error.value)
    assert made_sockets[0].closed and ("bind", ("127.0.0.1", 8766)) in made_sockets[0].calls and door.sock is None


def test_a_free_port_is_taken_on_the_loopback_address_only(events_services, monkeypatch):
    class Free:
        def __init__(self, *args):
            self.calls: list = []

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            self.calls.append(("bind", address))

        def listen(self, backlog):
            self.calls.append(("listen", backlog))

        def setblocking(self, flag):
            self.calls.append(("setblocking", flag))

        def close(self):
            self.calls.append("close")

    monkeypatch.setattr(door_module, "socket", SimpleNamespace(socket=Free, AF_INET=2, SOCK_STREAM=1, SOL_SOCKET=1, SO_REUSEADDR=2))
    door = events_services.events_door
    door.bind()
    assert door.sock.calls == [("bind", ("127.0.0.1", 8766)), ("listen", door_module.BACKLOG), ("setblocking", False)]


def test_the_server_does_not_start_when_the_events_port_is_taken_and_gives_back_the_lock(tmp_path, monkeypatch, caplog):
    for name, value in {
        "CLARA_DATA_DIR": str(tmp_path / "data"), "CLARA_SECRET_KEY": "ab" * 32, "CLARA_TIMEZONE": "Europe/Paris", "CLARA_TAILSCALE": "off", "CLARA_RUN": "off",
        "CLARA_CHECK_PERMISSIONS": "false", "CLARA_ENV_FILE": str(tmp_path / "none.env"), "CLARA_DEFAULT_MODE_FILE": str(tmp_path / "none.toml"),
        "CLARA_EVENTS": "on", "CLARA_EVENTS_LOCAL_PORT": "8766",
    }.items():
        monkeypatch.setenv(name, value)

    def taken(self):
        raise DoorError("CLARA_EVENTS_LOCAL_PORT=8766: the events listener cannot take 127.0.0.1:8766 (Address already in use).")

    monkeypatch.setattr(EventsDoor, "bind", taken)
    with caplog.at_level(logging.ERROR, logger="clara"):
        assert server_main([]) == 3
    assert "CLARA_EVENTS_LOCAL_PORT=8766" in caplog.text
    lock = InstanceLock(tmp_path / "data" / "run" / "clara.lock")
    lock.acquire()  # the server that did not start holds nothing
    lock.release()


# ----------------------------------------------------------------------
# A request has a deadline
# ----------------------------------------------------------------------
class Wire(asyncio.Transport):
    """A connection that is not one: what the protocol is told, and whether it was cut."""

    def __init__(self) -> None:
        super().__init__()
        self.aborted = False
        self.closing = False

    def get_extra_info(self, name, default=None):
        return {"peername": ("127.0.0.1", 40000), "sockname": ("127.0.0.1", 8766)}.get(name, default)

    def is_closing(self):
        return self.closing

    def abort(self):
        self.aborted = self.closing = True

    def close(self):
        self.closing = True

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass

    def write(self, data):
        pass


def protocol(services, monkeypatch, seconds: float) -> tuple[DoorProtocol, Wire]:
    monkeypatch.setattr(door_module, "READ_DEADLINE", seconds)
    config = EventsDoor(services).config()
    config.load()
    wire = Wire()
    return DoorProtocol(config=config, server_state=ServerState(), app_state={}, _loop=asyncio.get_running_loop()), wire


async def test_a_connection_that_sends_nothing_is_cut_at_the_deadline(services, monkeypatch):
    found, wire = protocol(services, monkeypatch, 0.05)
    found.connection_made(wire)
    await asyncio.sleep(0.02)
    assert not wire.aborted
    await asyncio.sleep(0.15)
    assert wire.aborted  # what uvicorn alone would keep for ever


async def test_headers_sent_a_byte_at_a_time_do_not_stretch_the_deadline(services, monkeypatch):
    found, wire = protocol(services, monkeypatch, 0.1)
    found.connection_made(wire)
    for byte in b"POST /v1/events/k HTTP/1.1\r\nHost: door\r\nX-Slow: ":
        if wire.aborted:
            break
        found.data_received(bytes([byte]))
        await asyncio.sleep(0.01)
    assert wire.aborted


async def test_a_connection_that_closes_by_itself_leaves_no_timer_behind(services, monkeypatch):
    found, wire = protocol(services, monkeypatch, 0.05)
    found.connection_made(wire)
    found.connection_lost(None)
    await asyncio.sleep(0.15)
    assert not wire.aborted and found._deadline is None


async def test_each_answer_gives_the_next_request_a_new_deadline(services, monkeypatch):
    found, wire = protocol(services, monkeypatch, 0.12)
    found.connection_made(wire)
    await asyncio.sleep(0.08)
    found.on_response_complete()  # an answer was sent: the clock starts again
    await asyncio.sleep(0.08)
    assert not wire.aborted  # 0.16 s since the connection, 0.08 since the answer
    await asyncio.sleep(0.12)
    assert wire.aborted
    found.connection_lost(None)


# ----------------------------------------------------------------------
# The real listener, on a UNIX socket in a temporary folder (no network port, no TCP): uvicorn, the protocol and the application together
# ----------------------------------------------------------------------
@pytest.fixture
async def listening(events_services):
    """The events listener, served by uvicorn on a UNIX socket that only this test reaches."""
    folder = Path(tempfile.mkdtemp(prefix="cd"))  # a short path: a UNIX socket's name is limited to about a hundred bytes
    path = str(folder / "s")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.listen(8)
    sock.setblocking(False)
    s = events_services
    s.events_door.sock = sock
    s.events_door.start()
    for _ in range(200):
        if s.events_door.up:
            break
        await asyncio.sleep(0.01)
    assert s.events_door.up
    yield SimpleNamespace(path=path, services=s, door=s.events_door)
    await s.events_door.stop()
    shutil.rmtree(folder, ignore_errors=True)


def over(path: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=path), base_url="http://door.test")


async def raw(path: str, data: bytes = b"", wait: float = 2.0) -> bytes:
    """Send these bytes down a connection and return what the listener says first: b"" when it closes the connection, `<silent>` when it
    says nothing for `wait` seconds."""
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(data)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(65536), wait)
    except TimeoutError:
        return b"<silent>"
    finally:
        writer.close()


async def test_uvicorn_serves_the_door_and_only_the_door(listening):
    s = listening.services
    source, secret = made(s)
    async with over(listening.path) as http:
        stored = await http.post(source.address, json={"key": "k1", "data": {"state": "open"}}, headers=bearer(secret))
        assert stored.status_code == 202 and "server" not in stored.headers and "set-cookie" not in stored.headers
        assert s.automations.event(stored.json()["id"]).data == {"state": "open"}  # through the engine of the main listener
        for method, path in (("GET", "/"), ("GET", "/health"), ("GET", "/docs"), ("GET", "/v1/conversations"), ("POST", "/v1/chat"), ("GET", "/v1/events/ack")):
            answer = await http.request(method, path)
            assert (answer.status_code, answer.content) == (404, b""), path
            assert "server" not in answer.headers and answer.headers["connection"] == "close"
        assert (await http.get(source.address)).status_code == 405
        assert (await http.post(source.address, json={}, headers=bearer("wrong"))).status_code == 401
    assert s.event_sources.describe(source)["failures"] == 1 and s.limiter._failures == {}


async def test_uvicorn_refuses_a_body_over_64_kb_and_headers_over_16_kb_on_the_door(listening):
    source, secret = made(listening.services)
    async with over(listening.path) as http:
        assert (await http.post(source.address, content=b" " * 70_000, headers={**bearer(secret), "content-type": "application/json"})).status_code == 413
    said = await raw(listening.path, b"POST " + source.address.encode() + b" HTTP/1.1\r\nHost: x\r\nX-Big: " + b"a" * 20_000)  # headers that never end
    assert said.startswith(b"HTTP/1.1 400") and b"uvicorn" not in said.lower() and b"server:" not in said.lower()
    assert listening.services.automations.recent_events() == []


async def test_uvicorn_drops_a_connection_that_sends_nothing_or_sends_a_byte_at_a_time(listening, monkeypatch):
    # the deadline of the real protocol (its value is read when a connection opens: the next ones are short)
    monkeypatch.setattr(door_module, "READ_DEADLINE", 0.3)
    started = asyncio.get_running_loop().time()
    assert await raw(listening.path, b"", wait=3.0) == b""  # closed by the listener, not by us
    assert asyncio.get_running_loop().time() - started < 2.0
    reader, writer = await asyncio.open_unix_connection(listening.path)
    closed_at = None
    for byte in b"POST /v1/events/k HTTP/1.1\r\nHost: door\r\nX-Slow: abcdefghijklmnopqrstuvwxyz":
        try:
            writer.write(bytes([byte]))
            await writer.drain()
        except (ConnectionError, OSError):
            closed_at = byte
            break
        await asyncio.sleep(0.02)
        if reader.at_eof():
            closed_at = byte
            break
    assert await asyncio.wait_for(reader.read(), 2.0) == b""  # it never got its request through
    writer.close()
    assert closed_at is not None or reader.at_eof()


async def test_uvicorn_answers_503_beyond_the_concurrency_limit_and_a_door_that_stops_stops_listening(listening, monkeypatch):
    monkeypatch.setattr(listening.door.server.config, "limit_concurrency", 1)  # the protocol reads it per connection
    held = [await asyncio.open_unix_connection(listening.path) for _ in range(2)]  # two connections open and silent
    async with over(listening.path) as http:
        answer = await http.get("/")
        assert answer.status_code == 503
    for _, writer in held:
        writer.close()
    await listening.door.stop()
    assert not listening.door.up and listening.door.sock is None
    with pytest.raises((ConnectionError, FileNotFoundError, OSError)):
        await asyncio.open_unix_connection(listening.path)  # nothing listens any more


# ----------------------------------------------------------------------
# It starts with the main server, stops with it, and says when it dies
# ----------------------------------------------------------------------
class Pretend:
    """A socket and a server that only remember: no port is opened."""

    closed = False

    def close(self):
        self.closed = True


def fake_listener(monkeypatch, door: EventsDoor, order: list, dies: bool = False):
    door.sock = Pretend()

    async def serve(server, sock):
        server.started = True
        order.append("serving")
        if dies:
            raise RuntimeError("it broke")
        for _ in range(500):  # until it is told to stop
            if server.should_exit:
                break
            await asyncio.sleep(0.01)
        order.append("served")

    monkeypatch.setattr(EventsDoor, "_serve", staticmethod(serve))
    real_start, real_stop = EventsDoor.start, EventsDoor.stop

    def start(self):
        order.append("door.start")
        real_start(self)

    async def stop(self):
        order.append("door.stop")
        await real_stop(self)

    monkeypatch.setattr(EventsDoor, "start", start)
    monkeypatch.setattr(EventsDoor, "stop", stop)


async def test_the_listener_starts_with_the_main_server_before_tailscale_publishes_it_and_stops_after_tailscale_unpublishes(events_services, monkeypatch):
    s, order = events_services, []
    fake_listener(monkeypatch, s.events_door, order)

    async def tailscale_stop():
        order.append("tailscale.stop")

    s.tailscale = SimpleNamespace(start=lambda: order.append("tailscale.start"), stop=tailscale_stop, describe=lambda: "x", events_url=None, events_problem="")
    monkeypatch.setattr("clara.app.notify", lambda *_: None)
    app = create_app(s)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        assert s.events_door.up and order[:2] == ["door.start", "tailscale.start"] and "serving" in order
    assert order.index("tailscale.stop") < order.index("door.stop") < order.index("served")  # unpublished, then closed
    assert not s.events_door.up and s.events_door.sock is None  # the port is closed and nothing is left running


async def test_a_listener_that_dies_raises_the_events_alert_and_is_not_up(events_services, monkeypatch):
    s, order = events_services, []
    fake_listener(monkeypatch, s.events_door, order, dies=True)
    s.events_door.start()
    await asyncio.sleep(0.05)
    assert not s.events_door.up and "events" in s.alerts.active and "it broke" in s.events_door.problem
    assert events_door(s)["listening"] is False and "it broke" in events_door(s)["problem"]
    await s.events_door.stop()


async def test_a_listener_that_cannot_start_is_an_alert_not_a_crash_of_the_server(events_services, monkeypatch):
    s = events_services

    def broken(self):
        raise DoorError("CLARA_EVENTS_LOCAL_PORT=8766: taken")

    monkeypatch.setattr(EventsDoor, "bind", broken)
    s.events_door.start()  # called from the lifespan, which must go on
    assert "events" in s.alerts.active and not s.events_door.up and "taken" in s.events_door.problem


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"CLARA_EVENTS": "maybe"}, "CLARA_EVENTS='maybe' must be one of off, on"),
        ({"CLARA_EVENTS": "on", "CLARA_EVENTS_LOCAL_PORT": "8765"}, "CLARA_EVENTS_LOCAL_PORT=8765 is the port of CLARA_PORT"),
        ({"CLARA_EVENTS": "on", "CLARA_PORT": "9000", "CLARA_EVENTS_LOCAL_PORT": "9000"}, "CLARA_EVENTS_LOCAL_PORT=9000 is the port of CLARA_PORT"),
        ({"CLARA_EVENTS": "on", "CLARA_EVENTS_PORT": "443"}, "CLARA_EVENTS_PORT=443 must be one of 8443, 10000"),
        ({"CLARA_EVENTS": "on", "CLARA_EVENTS_PORT": "8080"}, "CLARA_EVENTS_PORT=8080 must be one of 8443, 10000"),
        ({"CLARA_EVENTS": "off", "CLARA_EVENTS_PORT": "443"}, "CLARA_EVENTS_PORT=443 must be one of 8443, 10000"),
        ({"CLARA_EVENTS": "on", "CLARA_TAILSCALE": "serve", "CLARA_TAILSCALE_PORT": "8443"}, "CLARA_EVENTS_PORT=8443 is the port of CLARA_TAILSCALE_PORT"),
        ({"CLARA_EVENTS": "on", "CLARA_EVENTS_PORT": "10000", "CLARA_TAILSCALE": "serve", "CLARA_TAILSCALE_PORT": "10000"}, "CLARA_EVENTS_PORT=10000 is the port of CLARA_TAILSCALE_PORT"),
        ({"CLARA_EVENTS": "on", "CLARA_EVENTS_LOCAL_PORT": "70000"}, "CLARA_EVENTS_LOCAL_PORT=70000 must be between 1 and 65535"),
        ({"CLARA_EVENTS": "on", "CLARA_EVENTS_LOCAL_PORT": "abc"}, "is not a whole number"),
    ],
)
def test_the_events_settings_are_checked_at_start_with_clear_messages(environ, env, message):
    with pytest.raises(SettingsError) as error:
        load_settings({**environ, **env}, use_file=False)
    assert any(message in problem for problem in error.value.problems), error.value.problems


def test_the_events_settings_that_are_allowed(environ):
    base = load_settings(environ, use_file=False)
    assert (base.events, base.events_port, base.events_local_port) == ("off", 8443, 8766)  # off by default, on the usual ports
    assert load_settings({**environ, "CLARA_EVENTS": "on"}, use_file=False).events == "on"
    wide = load_settings({**environ, "CLARA_EVENTS": "on", "CLARA_EVENTS_PORT": "10000", "CLARA_EVENTS_LOCAL_PORT": "9100", "CLARA_TAILSCALE_PORT": "8443"}, use_file=False)
    assert (wide.events_port, wide.events_local_port, wide.tailscale_port) == (10000, 9100, 8443)
    # on with Tailscale off is allowed: the listener answers on 127.0.0.1 only and nothing is published
    solo = load_settings({**environ, "CLARA_EVENTS": "on", "CLARA_TAILSCALE": "off", "CLARA_TAILSCALE_PORT": "8443"}, use_file=False)
    assert solo.events == "on" and solo.tailscale == "off"
    assert door_of(solo) == Door(True, 8443, 8766)


def test_the_status_says_what_is_on_and_why_it_is_not_public(events_services):
    s = events_services
    assert events_door(s) == {"on": True, "local_port": 8766, "public_port": 8443, "listening": False, "published": False, "url": "", "problem": "the events listener is not running"}
    s.events_door.server = SimpleNamespace(started=True)
    s.events_door._task = SimpleNamespace(done=lambda: False)
    assert events_door(s)["listening"] is True and "CLARA_TAILSCALE is off" in events_door(s)["problem"]  # nothing is published, and it says so
    s.tailscale = SimpleNamespace(events_url=None, events_problem="Tailscale would not publish it: add funnel to nodeAttrs of your tailnet policy")
    assert "nodeAttrs" in events_door(s)["problem"] and events_door(s)["published"] is False
    s.tailscale = SimpleNamespace(events_url="https://box.ts.net:8443", events_problem=None)
    assert events_door(s) == {"on": True, "local_port": 8766, "public_port": 8443, "listening": True, "published": True, "url": "https://box.ts.net:8443", "problem": ""}


async def test_the_admin_status_and_the_console_show_the_door(events_services):
    s = events_services
    info = await status(s)
    assert info["events_door"]["on"] is True and "listeners" in info["events"]  # the event bus of the clients is another thing
    admin = s.tokens.create("admin", "console")[0]
    text = await console.run(s, "/status", admin)
    assert "Events door: on 127.0.0.1:8766 only, NOT public" in text
    s.tailscale = SimpleNamespace(events_url="https://box.ts.net:8443", events_problem=None, describe=lambda: "serving x")
    s.events_door.server = SimpleNamespace(started=True)
    s.events_door._task = SimpleNamespace(done=lambda: False)
    assert "Events door: PUBLIC at https://box.ts.net:8443" in await console.run(s, "/status", admin)


async def test_the_console_says_nothing_about_a_door_that_is_off(services):
    admin = services.tokens.create("admin", "console")[0]
    assert "Events door" not in await console.run(services, "/status", admin)


# ----------------------------------------------------------------------
# The guard: Funnel may publish the door, and nothing else
# ----------------------------------------------------------------------
class Node:
    """A fake `tailscale`: what it serves, where Funnel is on, and every argument list it was given."""

    def __init__(self, handlers=None, funnel=()):
        self.handlers: dict[int, dict[str, str]] = {443: {"/": MAIN}} if handlers is None else {443: {"/": MAIN}, **handlers}
        self.funnel = set(funnel)
        self.calls: list[list[str]] = []
        self.allow_funnel = True
        self.silent_funnel = False  # a funnel command that says yes and changes nothing

    def serve_status(self) -> dict:
        return {
            "Web": {f"box.ts.net:{port}": {"Handlers": {mount: {"Proxy": target} for mount, target in mounts.items()}} for port, mounts in self.handlers.items()},
            "AllowFunnel": {f"box.ts.net:{port}": True for port in self.funnel},
        }

    async def __call__(self, args: list[str], timeout: float) -> Output:
        rest = args[1:]
        self.calls.append(rest)
        port = next((int(item.split("=")[1]) for item in rest if item.startswith("--https=")), None)
        if rest == ["status", "--json"]:
            return Output(0, json.dumps({"BackendState": "Running", "Self": {"DNSName": "box.ts.net."}}))
        if rest == ["serve", "status", "--json"]:
            return Output(0, json.dumps(self.serve_status()))
        if rest[0] == "serve" and "--bg" in rest:
            self.handlers[port] = {"/": rest[-1]}
            return Output(0, "")
        if rest[0] == "serve" and rest[-1] == "off":
            self.handlers.pop(port, None)
            self.funnel.discard(port)
            return Output(0, "")
        if rest[0] == "funnel" and rest[-1] == "off":
            self.funnel.discard(port)
            return Output(0, "")
        if rest[0] == "funnel" and rest[:2] == ["funnel", "--bg"]:
            if not self.allow_funnel:
                return Output(1, "Funnel not available; \"funnel\" node attribute not set. See https://tailscale.com/s/no-funnel")
            if not self.silent_funnel:
                self.handlers[port] = {"/": rest[-1]}
                self.funnel.add(port)
            return Output(0, "")
        raise AssertionError(f"Clara ran a tailscale command nobody expected: {rest}")


def station(node: Node, *, on: bool = True, up: bool = True, door: Door | None = None):
    alerts: list[tuple[str, str]] = []
    cleared: list[str] = []
    found = Tailscale(
        443, MAIN, runner=node, alert=lambda key, title, text: alerts.append((key, text)), clear=cleared.append,
        door=door if door is not None else Door(on, 8443, 8766), door_up=lambda: up,
    )
    return found, alerts, cleared


def publishes(port: int = 8443) -> list[str]:
    return ["funnel", "--bg", f"--https={port}", DOOR]


def off(port: int) -> list[str]:
    return ["funnel", f"--https={port}", "off"]


READ = [["status", "--json"], ["serve", "status", "--json"]]
# (what is there, Funnel on these ports, the ports that must be turned off)
ON_CASES = {
    "nothing": ({}, set(), []),
    "the door, mapped right": ({8443: {"/": DOOR}}, {8443}, []),
    "the door's port, mapped elsewhere": ({8443: {"/": "http://127.0.0.1:9999"}}, {8443}, [8443]),
    "the door's port, mapped to the main listener": ({8443: {"/": MAIN}}, {8443}, [8443]),
    "the door's port, the door and something more": ({8443: {"/": DOOR, "/x": "http://127.0.0.1:3000"}}, {8443}, [8443]),
    "the door's port, the door under another name": ({8443: {"/": "http://localhost:8766"}}, {8443}, [8443]),
    "another port, mapped to the door": ({10000: {"/": DOOR}}, {10000}, [10000]),
    "another port, mapped to the main listener": ({10000: {"/": MAIN}}, {10000}, [10000]),
    "another port, mapped to the main listener by name": ({10000: {"/": "https+insecure://localhost:8765/app"}}, {10000}, [10000]),
    "another port, mapped to something that is not Clara": ({10000: {"/": "http://127.0.0.1:3000"}}, {10000}, []),
    "Clara's own port": ({}, {443}, [443]),
    "Clara's own port and the door": ({8443: {"/": DOOR}}, {443, 8443}, [443]),
    "the door's port, mapped right, with Funnel off": ({8443: {"/": DOOR}}, set(), []),
}


@pytest.mark.parametrize("case", list(ON_CASES))
async def test_with_the_door_on_funnel_is_allowed_on_the_events_port_mapped_to_the_door_and_refused_and_turned_off_anywhere_else(case):
    handlers, funnel, refused = ON_CASES[case]
    node = Node(handlers, funnel)
    assert funnel_refused(node.serve_status(), 443, Door(True, 8443, 8766), 8765) == refused
    found, alerts, cleared = station(node)
    await found.once()
    assert [call for call in node.calls if call[0] == "funnel" and call[-1] == "off"] == [off(port) for port in refused]  # exactly these, no more
    assert [key for key, _ in alerts if key == "funnel"] == ["funnel"] * len(refused)  # one alert each, with the usual key
    assert all(f"port {port}" in text for (key, text), port in zip([a for a in alerts if a[0] == "funnel"], refused, strict=True))
    if 8443 in refused or not (8443 in funnel and handlers.get(8443) == {"/": DOOR}):
        assert node.calls.count(publishes()) == 1  # Clara makes her own mapping, once
    else:
        assert publishes() not in node.calls  # it was there already: nothing is run
    assert found.events_url == "https://box.ts.net:8443" and node.funnel >= {8443} and node.handlers[8443] == {"/": DOOR}
    assert found.url == "https://box.ts.net" and "events" in cleared and not any(key == "events" for key, _ in alerts)
    assert not any("reset" in call for call in node.calls) and not any(call[:1] == ["serve"] and call[-1] == "off" for call in node.calls)
    for port in node.funnel - {8443}:  # what she left on is not hers or is not Clara
        assert port != 443 and node.handlers[port]["/"] not in (MAIN, DOOR)


@pytest.mark.parametrize("case", list(ON_CASES))
def test_at_start_the_same_cases_refuse_to_run_or_let_it_through(tmp_path, case):
    handlers, funnel, refused = ON_CASES[case]
    fake = tmp_path / "tailscale"
    fake.write_text(f"#!/bin/sh\necho '{json.dumps(Node(handlers, funnel).serve_status())}'\n")
    fake.chmod(0o755)
    if refused:
        with pytest.raises(FunnelOn) as error:
            check_funnel_now(str(fake), 443, Door(True, 8443, 8766), 8765)
        assert all(f"tailscale funnel --https={port} off" in str(error.value) for port in refused)
        assert "public internet" in str(error.value) and "8443" in str(error.value)
    else:
        check_funnel_now(str(fake), 443, Door(True, 8443, 8766), 8765)


def test_with_the_door_off_the_guard_is_exactly_what_it_was(tmp_path):
    for case, (handlers, funnel, _) in ON_CASES.items():
        node = Node(handlers, funnel)
        assert funnel_refused(node.serve_status(), 443, None, 8765) == funnel_refused(node.serve_status(), 443, Door(False, 8443, 8766), 8765) == ([443] if 443 in funnel else []), case
        fake = tmp_path / "tailscale"
        fake.write_text(f"#!/bin/sh\necho '{json.dumps(node.serve_status())}'\n")
        fake.chmod(0o755)
        for door in (None, Door(False, 8443, 8766)):
            if 443 in funnel:
                with pytest.raises(FunnelOn, match="Funnel is on for port 443: Clara would be on the public internet"):
                    check_funnel_now(str(fake), 443, door, 8765)
            else:
                check_funnel_now(str(fake), 443, door, 8765)  # a Funnel elsewhere was never this guard's business


@pytest.mark.parametrize("case", ["nothing", "Clara's own port", "another port, mapped to the main listener", "the door's port, mapped right, with Funnel off"])
async def test_with_the_door_off_nothing_is_run_that_was_not_run_before(case):
    handlers, funnel, _ = ON_CASES[case]
    before, none = Node(handlers, funnel), Node(handlers, funnel)
    old, _, _ = station(before, door=Door(False, 8443, 8766))
    plain = Tailscale(443, MAIN, runner=none)  # as it was made before there was a door
    await old.once()
    await plain.once()
    assert before.calls == none.calls  # byte for byte the same commands
    assert not any(call[0] == "funnel" and call[1:2] == ["--bg"] for call in before.calls) and old.events_url is None
    assert (before.calls.count(off(443)), before.funnel) == (1 if 443 in funnel else 0, none.funnel)
    await old.stop()
    await plain.stop()
    assert before.calls == none.calls and not any("reset" in call for call in before.calls)


async def test_a_mapping_a_run_with_the_door_left_is_removed_when_the_door_is_off_and_nothing_else_is_touched():
    node = Node({8443: {"/": DOOR}, 10000: {"/": "http://127.0.0.1:3000"}}, {8443, 10000})
    found, alerts, _ = station(node, on=False)
    await found.once()
    assert [call for call in node.calls if call[0] == "funnel"] == [off(8443)]  # exactly that mapping: no reset, no other port
    assert alerts == [] and node.funnel == {10000} and found.events_url is None
    node.calls.clear()
    await found.once()
    assert [call for call in node.calls if call[0] == "funnel"] == []  # gone: nothing more to do
    elsewhere = Node({8443: {"/": "http://127.0.0.1:9999"}}, {8443})  # on the same port but not Clara's: left alone
    other, _, _ = station(elsewhere, on=False)
    await other.once()
    assert [call for call in elsewhere.calls if call[0] == "funnel"] == [] and elsewhere.funnel == {8443}


async def test_the_exact_commands_clara_runs_for_the_door_and_never_a_reset():
    node = Node()
    found, alerts, cleared = station(node)
    await found.once()
    assert node.calls == [
        ["status", "--json"], ["serve", "status", "--json"],
        publishes(), ["serve", "status", "--json"],  # made, then looked at again: it is only published when it is there
    ]
    node.calls.clear()
    await found.once()  # a minute later: it is there, she only looks
    assert node.calls == READ and found.events_url == "https://box.ts.net:8443"
    await found.stop()
    assert node.calls[2:] == [off(8443), ["serve", "--https=443", "off"]]  # her mapping first, then the serve one: no other port, no reset
    assert found.events_url is None and found.url is None and node.funnel == set()
    assert not any("reset" in call for call in node.calls) and alerts == []


async def test_a_public_port_of_10000_is_published_on_10000():
    node = Node()
    found = Tailscale(443, MAIN, runner=node, door=Door(True, 10000, 9100), door_up=lambda: True)
    await found.once()
    assert ["funnel", "--bg", "--https=10000", "http://127.0.0.1:9100"] in node.calls and found.events_url == "https://box.ts.net:10000"
    assert node.handlers[10000] == {"/": "http://127.0.0.1:9100"} and node.handlers[443] == {"/": MAIN}  # the main mapping is not touched


async def test_when_tailscale_refuses_the_funnel_the_door_stays_on_127_0_0_1_and_the_alert_says_what_to_do(caplog):
    node = Node()
    node.allow_funnel = False
    found, alerts, cleared = station(node)
    with caplog.at_level(logging.WARNING):
        await found.once()
    assert found.events_url is None and found.url == "https://box.ts.net" and "tailscale" in cleared  # serving the tailnet goes on
    [(key, text)] = [alert for alert in alerts if alert[0] == "events"]
    assert "nodeAttrs of your tailnet policy" in text and "127.0.0.1 only" in text and "Funnel not available" not in text  # fixed words
    assert "Funnel not available" in caplog.text  # tailscale's own words are in the log
    assert found.events_problem and "nodeAttrs" in found.events_problem and "events" not in cleared
    node.allow_funnel = True
    await found.once()  # the policy was fixed: it publishes by itself
    assert found.events_url == "https://box.ts.net:8443" and found.events_problem is None and cleared[-1] == "events"


async def test_a_funnel_command_that_says_yes_and_changes_nothing_is_not_a_published_door():
    node = Node()
    node.silent_funnel = True
    found, alerts, _ = station(node)
    await found.once()
    assert found.events_url is None and [key for key, _ in alerts] == ["events"]


async def test_nothing_is_published_while_the_listener_is_not_listening():
    node = Node()
    found, alerts, _ = station(node, up=False)
    await found.once()
    assert publishes() not in node.calls and found.events_url is None and found.events_problem == "the events listener is not running"
    assert found.url == "https://box.ts.net" and alerts == []  # the door's own alert says why, not this one


async def test_nothing_of_the_door_is_run_while_tailscale_cannot_serve():
    node = Node()
    node.silent_funnel = True

    async def down(args, timeout):
        node.calls.append(args[1:])
        return Output(1, "tailscaled is not running")

    found, alerts, _ = station(node)
    found._run = down
    await found.once()
    assert found.url is None and found.events_url is None and publishes() not in node.calls


async def test_a_door_that_stopped_publishing_is_published_again_and_a_stop_without_one_removes_nothing():
    node = Node()
    found, _, _ = station(node)
    await found.once()
    node.funnel.discard(8443)  # someone turned it off by hand
    node.handlers.pop(8443)
    node.calls.clear()
    await found.once()
    assert publishes() in node.calls and found.events_url == "https://box.ts.net:8443"
    node2 = Node()
    node2.allow_funnel = False
    refused, _, _ = station(node2)
    await refused.once()
    node2.calls.clear()
    await refused.stop()
    assert node2.calls == [["serve", "--https=443", "off"]]  # there was no mapping of hers to remove


async def test_a_door_taken_for_a_prepare_at_start_refuses_a_funnel_on_the_wrong_port(environ, tmp_path):
    fake = tmp_path / "tailscale"
    fake.write_text(f"#!/bin/sh\necho '{json.dumps(Node({10000: {'/': MAIN}}, {10000}).serve_status())}'\n")
    fake.chmod(0o755)
    settings = load_settings({**environ, "CLARA_TAILSCALE": "serve", "CLARA_TAILSCALE_BIN": str(fake), "CLARA_EVENTS": "on"}, use_file=False)
    with pytest.raises(StartError, match="Funnel is on for port 10000"):
        prepare(settings)
    allowed = tmp_path / "tailscale2"
    allowed.write_text(f"#!/bin/sh\necho '{json.dumps(Node({8443: {'/': DOOR}}, {8443}).serve_status())}'\n")
    allowed.chmod(0o755)
    settings = load_settings({**environ, "CLARA_TAILSCALE": "serve", "CLARA_TAILSCALE_BIN": str(allowed), "CLARA_EVENTS": "on"}, use_file=False)
    db, lock = prepare(settings)  # what a run that did not stop cleanly left is Clara's own mapping: it is let through
    db.close()
    lock.release()
