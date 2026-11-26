"""What is known of an OpenCode model without asking it: the API it speaks and what it can do.

OpenCode's lists name the models and nothing else, and its gateway answers a model of another API with an error only
once asked. The table (`opencode_models.py`, written from models.dev by scripts/refresh_opencode_models.py) is what lets
Clara keep those models out of the pickers, and refuse a picture for a model that only reads text, before asking.
A model the table does not have is unknown: it is offered, and the service answers for it. The table gives the window
of each model too (`window`), which OpenCode's lists do not.
"""

from __future__ import annotations

from collections.abc import Mapping

from .opencode_models import MODELS
from .types import Capabilities, Known


def known(model: str) -> Known | None:
    entry = MODELS.get(model)
    if entry is None:
        return None
    protocol, pictures, tools, thinking, _window = entry
    return Known(protocol, Capabilities(thinking=thinking, tools=tools, vision=pictures))


def window(model: str, overrides: Mapping[str, int]) -> int | None:
    """The context window of a model: what you set for it (runtime setting `opencode_go_windows`), else what models.dev
    says, else None: nothing says, and the provider's default stands in (it is then shown as assumed)."""
    entry = MODELS.get(model)
    return overrides.get(model) or (entry and entry[4]) or None
