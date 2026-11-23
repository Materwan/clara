"""The real transcriber: a child process per dictation.

The decoder (FFmpeg, through PyAV) reads bytes a client sent: a bug in it must not take Clara down, and the model's memory is
given back when the child ends. The child is `python -m clara.dictation.worker` (an argv list, never a shell). It gets the audio
on its stdin and answers with one JSON line on its stdout; its stderr goes to the server log and is never shown to the client.

Its limits are set by the child itself, as the first thing it does, before it reads a byte (`worker.py`): a `preexec_fn` is not
safe in a threaded server, and nothing a client sent has been read at that point.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import (
    INSTALL_HELP,
    BadAudio,
    BadLanguage,
    DictationError,
    ModelMissing,
    NotInstalled,
    TimedOut,
    TooLong,
    Transcript,
)
from . import models as speech_models

log = logging.getLogger("clara.dictation")

ANSWER_LIMIT = 100_000  # what is kept of the child's output: an answer is a few kilobytes of text
KEPT_ENV = ("PATH", "LD_LIBRARY_PATH")
COMMAND = (sys.executable, "-I", "-m", "clara.dictation.worker")


async def _drain(stream: asyncio.StreamReader, keep: int) -> bytes:
    """Read to the end, keep the start: a child that floods its pipe cannot fill the server's memory."""
    kept = bytearray()
    while chunk := await stream.read(65536):
        if len(kept) < keep:
            kept += chunk[: keep - len(kept)]
    return bytes(kept)


async def _feed(process: asyncio.subprocess.Process, audio: bytes) -> None:
    assert process.stdin is not None
    with contextlib.suppress(
        BrokenPipeError, ConnectionResetError
    ):  # the child may answer before it has read everything
        process.stdin.write(audio)
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Killed at once, not asked to stop: it holds nothing that needs a clean exit, and it holds the memory of a model."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await process.wait()


class ProcessTranscriber:
    def __init__(self, models_dir: Path, command: Sequence[str] = COMMAND):
        self.models_dir = models_dir
        self.command = list(command)  # the tests give a stand-in for the worker

    def ready(self, model: str) -> None:
        if not speech_models.installed():
            raise NotInstalled(
                f"Dictation is not installed: the `dictation` extra (faster-whisper) is missing. {INSTALL_HELP}"
            )
        if not speech_models.present(self.models_dir, model):
            raise ModelMissing(
                f"The dictation model {model!r} is not installed. Fetch it once from the console, which downloads it "
                f"(about 500 MB for `small`) from Hugging Face: /dictation fetch-model. {INSTALL_HELP}"
            )

    async def transcribe(
        self, audio: bytes, *, model: str, language: str, max_seconds: int, timeout: int, memory_mb: int
    ) -> Transcript:
        argv = [
            *self.command,
            f"--model-dir={speech_models.folder(self.models_dir, model)}",
            f"--language={language}",
            f"--max-seconds={max_seconds}",
            f"--deadline={int(timeout * 0.95)}",
            f"--memory-mb={memory_mb}",
            f"--cpu-seconds={timeout * 8}",  # a backstop for a child left behind when the server dies; threads add up
        ]
        env = {key: os.environ[key] for key in KEPT_ENV if key in os.environ} | {"HF_HUB_OFFLINE": "1"}
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        assert process.stdout is not None and process.stderr is not None
        try:
            async with asyncio.timeout(timeout):
                out, err, _ = await asyncio.gather(
                    _drain(process.stdout, ANSWER_LIMIT), _drain(process.stderr, 4000), _feed(process, audio)
                )
                await process.wait()
        except TimeoutError:
            await _stop(process)
            raise TimedOut(f"The transcription took more than {timeout} seconds and was stopped.") from None
        except BaseException:  # the request was cancelled (the client left, the server stops)
            await _stop(process)
            raise
        return self._answer(process.returncode, out, err)

    @staticmethod
    def _answer(code: int | None, out: bytes, err: bytes) -> Transcript:
        lines = out.decode("utf-8", errors="replace").strip().splitlines()
        try:
            said = json.loads(lines[-1]) if lines else None
        except ValueError:
            said = None
        if not isinstance(said, dict):
            log.warning("the dictation worker exited %s with no answer: %s", code, err.decode(errors="replace")[-1000:])
            raise DictationError("The transcriber stopped without an answer (see the server's log).")
        if said.get("ok") is True and isinstance(said.get("text"), str):
            return Transcript(said["text"], float(said.get("seconds", 0)))
        message = str(said.get("message") or "The transcription failed.")[:300]
        match said.get("error"):
            case "bad_audio":
                raise BadAudio(message)
            case "too_long":
                raise TooLong(message)
            case "bad_language":
                raise BadLanguage(message)
            case "timeout":
                raise TimedOut(message)
            case "not_installed":
                raise NotInstalled(f"{message} {INSTALL_HELP}")
            case "no_model":
                raise ModelMissing(message)
        log.warning("the dictation worker failed (%s): %s", said.get("error"), err.decode(errors="replace")[-1000:])
        raise DictationError("The transcription failed (see the server's log).")
