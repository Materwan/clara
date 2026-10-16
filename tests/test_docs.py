"""FEATURES.md is the precise list of what Clara does: everything the code offers must be named there."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clara import console
from clara.app import build_services, create_app, extend, open_database
from clara.events.store import KINDS as EVENT_KINDS
from clara.gate import CLASSES
from clara.runtime import KNOBS
from clara.secrets import KNOWN_SECRETS
from clara.settings import load_settings

ROOT = Path(__file__).resolve().parent.parent
FEATURES = (ROOT / "FEATURES.md").read_text(encoding="utf-8")
SOURCE = ROOT / "src" / "clara"


def missing(names) -> list[str]:
    return sorted(name for name in names if f"`{name}`" not in FEATURES)


@pytest.fixture
def everything(environ, tmp_path, llm):
    """Services with every optional part on (the vault too), as on a configured machine."""
    (tmp_path / "vault").mkdir()
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(tmp_path / "vault")}, use_file=False)
    db = open_database(settings)
    s = build_services(settings, db, provider_factory=llm.factory)
    extend(s)
    yield s
    s.traffic.close()
    db.close()


def test_every_tool_is_listed(everything):
    names = everything.toolbox.names
    assert not missing(names)
    assert f"\n{len(names)} tools." in FEATURES, f"FEATURES.md should say how many tools there are ({len(names)})"


def test_every_route_is_listed(services):
    app = create_app(services)
    routes = [f"{method.upper()} {path}" for path, ops in app.openapi()["paths"].items() for method in ops]
    assert not missing(routes + ["GET /health", "GET /v1/connections/google/callback"])


def test_every_setting_is_listed():
    names = set()
    for path in [SOURCE / "settings.py", ROOT / "src" / "clara_discord" / "settings.py"]:
        names |= set(re.findall(r'"(CLARA_[A-Z0-9_]+)"', path.read_text(encoding="utf-8")))
    names |= {f"CLARA_{provider.upper().replace('-', '_')}_CONTEXT"
              for provider in ("cloud", "gemini", "deepseek", "mistral", "opencode-go")}
    assert not missing(names)


def test_every_console_command_secret_knob_class_and_event_is_listed():
    assert not [command.name for command in console.COMMANDS if f"`/{command.name}" not in FEATURES]
    assert not missing(KNOWN_SECRETS)
    assert not missing(KNOBS)
    assert not missing(CLASSES)
    assert not missing(EVENT_KINDS)


def test_every_alert_is_listed():
    keys = set()
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        keys |= {match.split(":")[0] for match in re.findall(r'raise_\(\s*f?"([a-z_]+(?::)?)', text)}
        keys |= set(re.findall(r'_alert\("([a-z_]+)"', text))
    found = {key.rstrip(":") for key in keys}
    assert found, "no alert found: the pattern above is out of date"
    listed = set(re.findall(r"^\s*\| `([a-z_]+)(?::<[a-z]+>)?` \|", FEATURES, re.MULTILINE))
    assert not sorted(found - listed)
