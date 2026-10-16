"""The live log: a ring in memory, the server's log and the traffic records as sources, secrets masked, nothing
sensitive let through."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

import pytest

from clara.ops.logstream import LEVELS, RING, SOURCES, LogHub
from clara.secretscan import mask

ALL = frozenset(SOURCES)


def texts(lines) -> list[str]:
    return [line.text for line in lines]


def test_secrets_are_masked_not_shown():
    token = "clara_1_" + "A" * 43
    assert mask(f"token {token} used") == "token [hidden] used"
    assert mask("password: hunter22222") == "[hidden]"
    assert mask("nothing to hide") == "nothing to hide"
    # a key's body is on the lines after its header
    assert mask("failed\n-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----") == "failed\n[hidden]"


def test_the_ring_keeps_the_last_lines_and_filters_by_source_and_level():
    hub = LogHub(size=3)
    hub.add("app", "INFO", "clara", "one")
    hub.add("traffic", "WARNING", "http", "two")
    hub.add("app", "ERROR", "clara", "three")
    hub.add("app", "INFO", "clara", "four")
    assert texts(hub.recent()) == ["two", "three", "four"]
    assert texts(hub.recent(frozenset({"app"}))) == ["three", "four"]
    assert texts(hub.recent(ALL, LEVELS["WARNING"])) == ["two", "three"]
    assert [line.id for line in hub.recent()] == [2, 3, 4]
    assert RING >= 500


def test_a_long_or_secret_line_is_cut_and_masked_when_kept():
    hub = LogHub()
    hub.add("app", "INFO", "clara", "key AIza" + "x" * 35 + " " + "y" * 9000)
    (line,) = hub.recent()
    assert "AIza" not in line.text and line.text.startswith("key [hidden] yyy") and len(line.text) < 4100


async def test_a_viewer_gets_the_ring_then_the_new_lines_in_order():
    hub = LogHub()
    hub.add("app", "INFO", "clara", "before")
    got: list[str] = []

    async def watch() -> None:
        async with contextlib.aclosing(hub.listen(ALL, LEVELS["INFO"])) as lines:
            async for line in lines:
                got.append(line.text)
                if len(got) == 3:
                    return

    task = asyncio.create_task(watch())
    await asyncio.sleep(0)
    # lines from another thread, as the traffic writer and the model threads do
    worker = threading.Thread(target=lambda: [hub.add("app", "INFO", "clara", name) for name in ("one", "two")])
    worker.start()
    worker.join()
    await asyncio.wait_for(task, 2)
    assert got == ["before", "one", "two"]
    assert not hub._watchers  # the viewer is gone with its generator


async def test_a_viewer_only_gets_what_it_asked_for():
    hub = LogHub()
    got: list[str] = []

    async def watch() -> None:
        async for line in hub.listen(frozenset({"traffic"}), LEVELS["WARNING"]):
            got.append(line.text)
            return

    task = asyncio.create_task(watch())
    await asyncio.sleep(0)
    hub.add("app", "ERROR", "clara", "wrong source")
    hub.add("traffic", "INFO", "http", "too quiet")
    hub.add("traffic", "WARNING", "http", "this one")
    await asyncio.wait_for(task, 2)
    assert got == ["this one"]


async def test_a_viewer_that_fell_behind_loses_lines_not_the_server(monkeypatch):
    monkeypatch.setattr("clara.ops.logstream.WATCH_QUEUE", 2)
    hub = LogHub()
    listener = hub.listen(ALL, LEVELS["INFO"])
    first = asyncio.create_task(listener.__anext__())
    await asyncio.sleep(0)
    for number in range(10):
        hub.add("app", "INFO", "clara", str(number))
    await asyncio.sleep(0)
    assert (await first).text == "0"
    await listener.aclose()


def test_the_server_log_is_taken_from_info_up_and_secrets_are_masked():
    hub = LogHub()
    logger = logging.getLogger("clara.test.logstream")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    hub.attach(logger)
    try:
        logger.debug("a debug line, possibly with content")
        logger.info("started on port %d", 8765)
        logger.error("refused with sk-%s", "a" * 40)
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("it failed")
    finally:
        hub.detach()
    logger.info("after detach")
    lines = hub.recent()
    assert [line.level for line in lines] == ["INFO", "ERROR", "ERROR"]
    assert lines[0].text == "started on port 8765" and lines[1].text == "refused with [hidden]"
    assert "Traceback" in lines[2].text and "ValueError: boom" in lines[2].text
    assert {line.source for line in lines} == {"app"} and lines[0].name == "clara.test.logstream"


def test_the_traffic_log_feeds_the_stream_without_the_debug_content_or_addresses(services):
    traffic = services.traffic
    traffic.record("http", method="GET", path="/v1/chat/{id}", status=200, ms=12, client="laptop", label="web")
    traffic.record("http", method="POST", path="/v1/chat", status=503, ms=3, client="laptop")
    traffic.record("model", provider="local", model="fake", use="chat", client="web", prompt_tokens=10,
                   completion_tokens=5, cached_tokens=0, ms=40, outcome="ok")
    traffic.record("auth_failure", path="/v1/auth/web-login", address="203.0.113.9")
    traffic.content("http", method="GET", path="/v1/x", query="secret=1")
    traffic.flush()
    lines = services.logs.recent()
    assert [(line.level, line.text) for line in lines] == [
        ("INFO", "GET /v1/chat/{id} 200 12 ms · laptop"),
        ("ERROR", "POST /v1/chat 503 3 ms · laptop"),
        ("INFO", "model local:fake (chat) ok 40 ms · 10+5 tokens"),
        ("WARNING", "sign-in refused on /v1/auth/web-login"),
    ]
    assert all(line.source == "traffic" for line in lines)
    assert "203.0.113.9" not in " ".join(texts(lines)) and "secret=1" not in " ".join(texts(lines))


def test_the_live_log_works_when_the_log_files_are_off(environ, tmp_path, llm):
    from clara.app import build_services, open_database
    from clara.settings import load_settings

    settings = load_settings({**environ, "CLARA_DATA_DIR": str(tmp_path / "off"), "CLARA_TRAFFIC_LOG_DAYS": "0"}, use_file=False)
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    try:
        s.traffic.record("http", method="GET", path="/health", status=200, ms=1)
        s.traffic.flush()
        assert texts(s.logs.recent()) == ["GET /health 200 1 ms"]
        assert not list((tmp_path / "off").rglob("traffic-*.jsonl"))
    finally:
        s.traffic.close()
        db.close()


@pytest.mark.parametrize("query", ["level=DEBUG", "source=debug", "level=info"])
async def test_the_stream_refuses_a_filter_it_does_not_know(client, query):
    response = await client.get(f"/v1/admin/logs/stream?{query}")
    assert response.status_code == 422


async def test_the_stream_needs_a_token(client):
    response = await client.get("/v1/admin/logs/stream?level=BAD", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
