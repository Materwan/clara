from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from clara.app import build_services, create_app, extend, open_database
from clara.services import Services
from clara.settings import load_settings
from fakes import FakeLLM

KEY = "ab" * 32


@pytest.fixture
def environ(tmp_path) -> dict[str, str]:
    return {
        "CLARA_DATA_DIR": str(tmp_path / "data"),
        "CLARA_SECRET_KEY": KEY,
        "CLARA_TIMEZONE": "Europe/Paris",
        "CLARA_TAILSCALE": "off",
        "CLARA_RUN": "off",
        "CLARA_CHECK_PERMISSIONS": "false",
        "CLARA_OLLAMA_HOSTS": "local=http://127.0.0.1:9,pc=http://127.0.0.1:8",
        "CLARA_REMINDER_AI_TIMEOUT": "5",
        "CLARA_APPROVAL_WAIT": "2",
        "CLARA_WORKSPACE": str(tmp_path / "workspace"),
    }


@pytest.fixture
def settings(environ):
    return load_settings(environ, use_file=False)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def services(settings, llm) -> Services:
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    extend(s)
    s.runtime.set("default_model", "local:fake")
    yield s
    s.traffic.close()
    db.close()


@pytest.fixture
def token(services) -> str:
    return services.tokens.create("tester", "cli")[1]


@pytest.fixture
async def client(services, token) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {token}"}) as http:
        yield http


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
