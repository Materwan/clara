"""Voice dictation: audio in, text out, on this machine.

A client sends the recorded audio to `POST /v1/dictation/transcribe`; a child process (`worker.py`) decodes and transcribes it
with faster-whisper and the server returns the text. The audio lives in memory only: it reaches the child through its stdin
pipe, is never written to a file by Clara, and is dropped whatever the outcome. One dictation runs at a time (a second one
gets a 429 at once), and the child is killed on a timeout, when the client goes away and when the server stops.

`Dictation` is the service (limits, one at a time, reading the body as it comes); a `Transcriber` does the work: the real one
(`process.ProcessTranscriber`) spawns the child, a scripted one stands in for it in the tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .clients import Clients

if TYPE_CHECKING:
    from ..services import Services

UPLOAD_SECONDS = 120  # what a client may take to send the audio once the request is accepted
LANGUAGE = re.compile(r"[a-z]{2,3}")
INSTALL_HELP = "See docs/OPERATIONS.md, « Voice dictation »."


class DictationError(Exception):
    """A refusal or a failure the client is told about: `status` and `code` are those of the HTTP answer."""

    status = 502
    code = "transcriber_failed"


class NotInstalled(DictationError):
    status, code = 503, "dictation_not_installed"


class ModelMissing(DictationError):
    status, code = 503, "model_not_installed"


class Busy(DictationError):
    status, code = 429, "busy"


class TooLarge(DictationError):
    status, code = 413, "too_large"


class TooLong(DictationError):
    status, code = 413, "too_long"


class BadAudio(DictationError):
    status, code = 422, "bad_audio"


class BadLanguage(DictationError):
    status, code = 422, "bad_language"


class TimedOut(DictationError):
    status, code = 504, "timeout"


class ClientGone(DictationError):
    status, code = 499, "client_gone"


@dataclass(frozen=True)
class Transcript:
    text: str
    seconds: float  # the real length of the audio, as decoded


class Transcriber(Protocol):
    def ready(self, model: str) -> None:
        """Raise NotInstalled or ModelMissing when a dictation cannot run: asked before the audio is read."""

    async def transcribe(
        self, audio: bytes, *, model: str, language: str, max_seconds: int, timeout: int, memory_mb: int
    ) -> Transcript:
        """Language '' means detect it. Must stop whatever it started when it is cancelled."""


async def read_limited(chunks: AsyncIterator[bytes], limit: int) -> bytes:
    """The body as it arrives, refused the moment it passes `limit`: a Content-Length is not trusted, nothing is buffered beyond it."""
    audio = bytearray()
    async for chunk in chunks:
        audio += chunk
        if len(audio) > limit:
            raise TooLarge(f"This audio is too big: at most {limit // 1_000_000} MB.")
    if not audio:
        raise BadAudio("The request holds no audio.")
    return bytes(audio)


class Dictation:
    def __init__(self, s: Services, engine: Transcriber):
        self.s = s
        self.engine = engine
        self.busy = False
        self.clients = Clients(s)

    async def transcribe(
        self,
        chunks: AsyncIterator[bytes],
        language: str | None = None,
        gone: Callable[[], Awaitable[None]] | None = None,
    ) -> Transcript:
        """`language`: None takes the setting, '' detects it. `gone()` returns when the client has left (watched while it works)."""
        runtime = self.s.runtime
        self.engine.ready(runtime.get("dictation_model"))
        if self.busy:
            raise Busy("Clara is already transcribing another dictation: try again in a moment.")
        self.busy = True  # no await since the check above: one dictation at a time, no queue
        try:
            try:
                async with asyncio.timeout(UPLOAD_SECONDS):
                    audio = await read_limited(chunks, runtime.get("dictation_max_bytes"))
            except TimeoutError:
                raise TimedOut(f"The audio took more than {UPLOAD_SECONDS} seconds to arrive.") from None
            work = asyncio.ensure_future(
                self.engine.transcribe(
                    audio,
                    model=runtime.get("dictation_model"),
                    language=runtime.get("dictation_language") if language is None else language,
                    max_seconds=runtime.get("dictation_max_seconds"),
                    timeout=runtime.get("dictation_timeout"),
                    memory_mb=runtime.get("dictation_memory_mb"),
                )
            )
            del audio  # the engine holds the only copy now
            watcher = asyncio.ensure_future(gone()) if gone is not None else None
            try:
                await asyncio.wait([work, *([watcher] if watcher else [])], return_when=asyncio.FIRST_COMPLETED)
                if not work.done():
                    raise ClientGone("The client left before the dictation was done.")
                return work.result()
            finally:
                for task in (work, watcher):
                    if task is not None:
                        task.cancel()
                        with contextlib.suppress(
                            BaseException
                        ):  # the work's own error is raised above, from `result()`
                            await task
        finally:
            self.busy = False
