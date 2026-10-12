"""Which failures of a model are worth asking another model about (its service is overloaded or unreachable; Gemini
often is, or the model was retired). The next model of the owner's list is asked (agent._model, models.py).

Only a failure before the model has said anything counts: once a piece of the answer has been passed on,
asking again would repeat it.
"""

from __future__ import annotations

import httpx

RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def retryable(error: BaseException) -> bool:
    """Is this failure the kind that passes: the service is busy or could not be reached?"""
    status = getattr(error, "status", None) or getattr(error, "status_code", None)  # LlmError, ollama.ResponseError
    if isinstance(status, int):
        return status in RETRY_STATUSES
    if isinstance(error, httpx.TimeoutException):
        return isinstance(error, httpx.ConnectTimeout)  # a model that went silent is not asked again
    return isinstance(error, (httpx.TransportError, ConnectionError))


def moves_on(error: BaseException, fallback: bool) -> bool:
    """Should the next model of the list be asked after this failure? Always when the service is busy or
    unreachable, or when the model is gone (HTTP 404). A model of the list (`fallback`) that fails for any other
    reason (it takes no tools, it was retired, it refuses the prompt) is skipped too, so that one bad entry does not
    stop the others; the first model's other refusals (a bad key, a prompt too long) end the turn."""
    if retryable(error) or getattr(error, "status", None) == 404:
        return True
    return fallback
