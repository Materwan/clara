"""`/dictate` for clara-chat: record from the microphone with an EXTERNAL recorder, no Python audio library.

The recorder is an argv list (never a shell): `arecord` (ALSA) or `parecord` (PulseAudio, PipeWire), whichever is installed.
It writes a 16 kHz mono WAV to its standard output, which is read into memory; Enter stops it, the maximum length stops it,
and it is killed on every way out (Ctrl-C and errors too). No file is written: the audio only goes to the server.
"""

from __future__ import annotations

import select
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from .common import ClientError

# program -> its arguments: WAV, 16-bit, 16 kHz, one channel (32 KB a second), on standard output. Tried in this order.
RECORDERS = {
    "arecord": ["-q", "-t", "wav", "-f", "S16_LE", "-r", "16000", "-c", "1"],
    "parecord": ["--file-format=wav", "--format=s16le", "--rate=16000", "--channels=1", "/dev/stdout"],
}
BYTES_PER_SECOND = 32_000
DEFAULT_SECONDS = 300  # the server's own default for `dictation_max_seconds`, used when the setting cannot be read
HEADER = 44
MISSING = (
    "No recorder found: dictation needs `arecord` (package alsa-utils) or `parecord` (package pulseaudio-utils, which also "
    "works on PipeWire). Install one, for example `sudo apt install alsa-utils`."
)


def pick_recorder(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    for program, arguments in RECORDERS.items():
        found = which(program)
        if found:
            return [found, *arguments]
    raise ClientError(MISSING)


def wait_for_enter(seconds: float, alive: Callable[[], bool]) -> None:
    """Returns at Enter, after `seconds`, or when the recorder is gone."""
    end = time.monotonic() + seconds
    while alive() and time.monotonic() < end:
        if select.select([sys.stdin], [], [], 0.2)[0]:
            sys.stdin.readline()
            return


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def fix_header(data: bytes) -> bytes:
    """A recorder writing to a pipe cannot go back to say how long the audio is: its header carries a placeholder."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    at = data.find(b"data", 12, 200)
    if at < 0:
        return data
    fixed = bytearray(data)
    struct.pack_into("<I", fixed, 4, len(data) - 8)
    struct.pack_into("<I", fixed, at + 4, len(data) - at - 8)
    return bytes(fixed)


def record(
    argv: list[str], seconds: float, wait: Callable[[float, Callable[[], bool]], None] = wait_for_enter
) -> bytes:
    """The WAV the recorder heard until `wait` returned. The recorder is always stopped before this returns or raises."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise ClientError(f"Cannot start the recorder {argv[0]}: {error}") from None
    output: list[tuple[bytes, bytes]] = []
    reader = threading.Thread(target=lambda: output.append(process.communicate()), daemon=True)
    reader.start()
    ended = None
    try:
        wait(seconds, reader.is_alive)
        ended = process.poll()  # not None: it stopped by itself, before Enter and before the time was up
    finally:
        stop(process)
        reader.join(5)
    data, errors = output[0] if output else (b"", b"")
    if ended not in (None, 0):
        reason = errors.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no reason given"]
        raise ClientError(f"The recorder stopped: {reason[0][:200]}")
    if len(data) <= HEADER:
        raise ClientError("Nothing was recorded.")
    return fix_header(data)
