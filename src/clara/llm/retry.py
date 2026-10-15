"""Which failures of a model send the turn to the next model of the fallback list.

Only a failure before the model has said anything counts: once a piece of the answer was passed on, asking again
would repeat it (agent/model.py checks that).
"""

from __future__ import annotations

import httpx

RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
KEY_STATUSES = frozenset({401, 403})  # the key was refused: an alert, and the next model
PAYMENT_STATUS = 402  # the balance is spent: an alert, and the next model


def status_of(error: BaseException) -> int | None:
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


def retryable(error: BaseException) -> bool:
    """The service is busy or could not be reached."""
    status = status_of(error)
    if status is not None:
        return status in RETRY_STATUSES
    if isinstance(error, httpx.TimeoutException):
        return isinstance(error, httpx.ConnectTimeout)  # a model that went silent is not asked again
    return isinstance(error, (httpx.TransportError, ConnectionError))


def moves_on(error: BaseException, fallback: bool) -> bool:
    """Ask the next model? Always when the service is busy, unreachable, the model is gone (404), the key was
    refused or the balance is spent (both also raise an alert). A model of the fallback list that fails for any other
    reason is skipped too, so one bad entry does not stop the others; the first model's other refusals (a prompt too
    long, a bad request) end the turn."""
    status = status_of(error)
    if retryable(error) or status == 404 or status == PAYMENT_STATUS or status in KEY_STATUSES:
        return True
    return fallback
