"""What is known of an OpenCode model without asking it: the API it speaks and what it can do.

OpenCode's lists name the models and nothing else, and its gateway answers a model of another API with an error only
once asked. The table (`opencode_models.py`, written from models.dev by scripts/refresh_opencode_models.py) is what lets
Clara keep those models out of the pickers, and refuse a picture for a model that only reads text, before asking.
A model the table does not have is unknown: it is offered, and the service answers for it.
"""

from __future__ import annotations

from .opencode_models import MODELS
from .types import Capabilities, Known


def known(model: str) -> Known | None:
    entry = MODELS.get(model)
    if entry is None:
        return None
    protocol, pictures, tools, thinking = entry
    return Known(protocol, Capabilities(thinking=thinking, tools=tools, vision=pictures))
