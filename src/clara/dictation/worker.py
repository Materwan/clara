"""The child process of one dictation: `python -m clara.dictation.worker --model-dir=... --language=fr ...`.

It reads the audio from stdin, decodes it (PyAV, which bundles FFmpeg), refuses it when its REAL decoded length passes the cap,
transcribes it with faster-whisper from a local model folder (no network), and writes ONE JSON line on the real stdout:
`{"ok": true, "text": "...", "seconds": 13.7}` or `{"ok": false, "error": "<code>", "message": "..."}`. Whatever else a library
prints goes to stderr, which the server logs and never shows. The first thing it does is limit itself, before any byte of the
audio is read: address space, CPU time, no core dump (a crash must not write the audio to disk) and no file bigger than zero
bytes (it can write none).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import resource
import signal
import sys
import time

RATE = 16_000  # what Whisper reads
# A segment above this no_speech_prob is dropped on that alone: the library also wants avg_logprob < -1.0,
# and the noise hallucinations measured on 2026-11-23 never got that low.
NO_SPEECH_MAX = 0.6


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def limit_self(memory_mb: int, cpu_seconds: int) -> None:
    signal.signal(
        signal.SIGXFSZ, signal.SIG_IGN
    )  # a write over the limit then fails with an error instead of killing us
    for kind, soft, hard in (
        (resource.RLIMIT_AS, memory_mb * 1024 * 1024, memory_mb * 1024 * 1024),
        (resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 5),  # SIGXCPU at the soft limit, SIGKILL at the hard one
        (resource.RLIMIT_CORE, 0, 0),
        (resource.RLIMIT_FSIZE, 0, 0),
    ):
        resource.setrlimit(kind, (soft, hard))


def decode(data: bytes, max_seconds: int):
    """The audio as 16 kHz mono floats. It stops at the first sample past the cap: a small file can decode to hours."""
    import av
    import numpy

    limit = max_seconds * RATE
    pieces: list = []
    count = 0
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            for frame in _frames(container):
                for part in resampler.resample(frame):
                    pieces.append(part.to_ndarray().reshape(-1))
                    count += pieces[-1].size
                    if count > limit:
                        raise Refused("too_long", f"This dictation is longer than {max_seconds} seconds, the limit.")
    except Refused:
        raise
    except Exception as error:  # anything the decoder cannot read: not an audio file, cut short, no audio track
        raise Refused("bad_audio", f"This audio cannot be read ({type(error).__name__}).") from None
    if not count:
        raise Refused("bad_audio", "This audio holds no sound.")
    return numpy.concatenate(pieces).astype(numpy.float32) / 32768.0


def _frames(container):
    yield from container.decode(audio=0)
    yield None  # flushes the resampler


def is_speech(segment) -> bool:
    """False for a segment Whisper itself judges to be silence or noise: its text is then a hallucination ("Merci.", "You")."""
    return segment.no_speech_prob <= NO_SPEECH_MAX


def transcribe(audio, args, started: float) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(args.model_dir, device="cpu", compute_type="int8", local_files_only=True)
    try:
        segments, _ = model.transcribe(audio, language=args.language or None, beam_size=5, vad_filter=True)
    except ValueError as error:  # an unknown language code
        raise Refused("bad_language", str(error)[:200]) from None
    parts = []
    for segment in segments:  # the work happens here, one segment at a time
        if time.monotonic() - started > args.deadline:
            raise Refused("timeout", f"The transcription took more than {args.deadline} seconds.")
        if is_speech(segment):
            parts.append(segment.text.strip())
    return " ".join(part for part in parts if part)


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--language", default="")
    parser.add_argument("--max-seconds", type=int, required=True)
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    args = parser.parse_args(argv)
    answer = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)  # anything a library prints lands in stderr, not in the answer
    limit_self(args.memory_mb, args.cpu_seconds)
    try:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            raise Refused("not_installed", "faster-whisper is not installed.") from None
        if not os.path.isfile(os.path.join(args.model_dir, "model.bin")):
            raise Refused("no_model", "The dictation model is not installed.")
        data = sys.stdin.buffer.read()
        audio = decode(data, args.max_seconds)
        del data
        text = transcribe(audio, args, started)
        said = {"ok": True, "text": text, "seconds": round(audio.size / RATE, 2)}
    except Refused as refusal:
        said = {"ok": False, "error": refusal.code, "message": str(refusal)}
    answer.write(json.dumps(said, ensure_ascii=False) + "\n")
    answer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
