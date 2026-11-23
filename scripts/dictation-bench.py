#!/usr/bin/env python3
"""Measures dictation on THIS machine: the real worker, on an audio file you give.

    uv run python scripts/dictation-bench.py recording.webm [--model small] [--language fr] [--runs 3] [--models-dir DIR]

For each run it prints the time the whole dictation took (the child starts, loads the model, decodes, transcribes), the length of
the audio, the real-time factor (time / audio length: 0.47 means twice as fast as speech) and the peak memory of the child. It
needs the `dictation` extra and the model fetched (`/dictation fetch-model`). No audio sample is part of the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import resource
import sys
import time
from pathlib import Path

from clara.dictation import DictationError
from clara.dictation.process import ProcessTranscriber
from clara.settings import load_settings


async def once(engine: ProcessTranscriber, audio: bytes, args: argparse.Namespace) -> tuple[float, float, str]:
    started = time.perf_counter()
    found = await engine.transcribe(
        audio, model=args.model, language=args.language, max_seconds=3600, timeout=args.timeout, memory_mb=args.memory_mb
    )
    return time.perf_counter() - started, found.seconds, found.text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="fr", help="'' to detect it")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--models-dir", type=Path, help="default: the models folder of the data folder in .env")
    args = parser.parse_args()
    engine = ProcessTranscriber(args.models_dir or load_settings().models_dir)
    try:
        engine.ready(args.model)
        audio = args.audio.read_bytes()
        for run in range(1, args.runs + 1):
            took, length, text = asyncio.run(once(engine, audio, args))
            peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024  # the largest child so far, in MB
            print(f"run {run}: {took:.1f} s for {length:.1f} s of audio, real-time factor {took / length:.2f}, peak memory {peak:.0f} MB")
        print(f"text: {text}")
    except (DictationError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
