"""Voice dictation: audio in, text out (src/clara/dictation/).

POST /v1/dictation/transcribe   The body is the audio itself, any format the decoder reads (webm or ogg with opus, m4a, wav...),
                                sent as `audio/*` or `application/octet-stream`. `?language=fr` overrides the setting (`auto`
                                detects it). The answer is `{"text": "...", "seconds": 13.7}`. Nothing goes into a conversation
                                and the audio is never kept. One dictation at a time: a second one gets a 429 at once.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from starlette.requests import ClientDisconnect

from ..dictation import LANGUAGE, DictationError
from .common import ApiError, services

router = APIRouter(prefix="/v1/dictation", tags=["dictation"])


async def _gone(request: Request) -> None:
    """Returns when the client has left: once the body is read, the server's next message on the request is its disconnection."""
    while (await request.receive())["type"] != "http.disconnect":
        pass


@router.post("/transcribe")
async def transcribe(request: Request, language: str | None = Query(default=None, max_length=8)) -> dict:
    kind = request.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
    if not (kind.startswith("audio/") or kind == "application/octet-stream"):
        raise ApiError(
            415, "Send the audio as the body, with the type audio/* or application/octet-stream.", "bad_media_type"
        )
    if language is not None:
        language = "" if language.lower() == "auto" else language.lower()
        if language and not LANGUAGE.fullmatch(language):
            raise ApiError(422, "language: two or three letters such as fr or en, or auto.", "bad_language")
    try:
        found = await services(request).dictation.transcribe(request.stream(), language, lambda: _gone(request))
    except ClientDisconnect:
        raise ApiError(499, "The client left while sending the audio.", "client_gone") from None
    except DictationError as error:
        raise ApiError(
            error.status, str(error), error.code, {"Retry-After": "5"} if error.status == 429 else None
        ) from None
    return {"text": found.text, "seconds": found.seconds}
