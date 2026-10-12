"""Which failures of a model are worth asking another model about (its service is overloaded or unreachable; Gemini
often is). The next model of the administrators' list is asked (agent._model, models.py).

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
