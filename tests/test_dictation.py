"""Voice dictation (src/clara/dictation/): the route, its limits, the child process and the console command.

The service and the route run against a scripted transcriber (`FakeTranscriber`); the child process is exercised with stand-in
scripts (so these tests need neither faster-whisper nor a model). The tests of the real worker skip when the `dictation` extra
is missing, and the one that needs a model skips unless CLARA_TEST_WHISPER_ROOT names a folder that holds `whisper/small`."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx
import pytest

from clara import console
from clara.app import create_app
from clara.dictation import (
    BadAudio,
    ClientGone,
    DictationError,
    ModelMissing,
    NotInstalled,
    TimedOut,
    TooLarge,
    TooLong,
    Transcript,
    read_limited,
    worker,
)
from clara.dictation import models as speech_models
from clara.dictation.process import ProcessTranscriber
from fakes import FakeTranscriber

AUDIO = b"RIFF....fake audio, the route never looks inside"
URL = "/v1/dictation/transcribe"
OPTIONS = {"model": "small", "language": "fr", "max_seconds": 300, "timeout": 300, "memory_mb": 4096}


# ---------------------------------------------------------------------------------------------- the route
async def test_the_route_returns_the_text_and_the_length(client, transcriber):
    transcriber.replies.append(Transcript("Bonjour Clara", 13.7))
    answer = await client.post(URL, content=AUDIO, headers={"Content-Type": "audio/webm;codecs=opus"})
    assert answer.status_code == 200 and answer.json() == {"text": "Bonjour Clara", "seconds": 13.7}
    assert transcriber.calls == [{"size": len(AUDIO), **OPTIONS}]  # the defaults: small, fr, 5 minutes, 4 GB of address space


@pytest.mark.parametrize("kind", ["audio/ogg", "audio/mp4", "application/octet-stream", None])
async def test_any_audio_type_and_an_untyped_body_are_accepted(client, transcriber, kind):
    answer = await client.post(URL, content=AUDIO, headers={"Content-Type": kind} if kind else {})
    assert answer.status_code == 200


@pytest.mark.parametrize("kind", ["application/json", "text/plain", "image/png"])
async def test_another_type_is_refused(client, transcriber, kind):
    answer = await client.post(URL, content=AUDIO, headers={"Content-Type": kind})
    assert answer.status_code == 415 and "audio/*" in answer.json()["error"]["message"] and not transcriber.calls


async def test_the_route_needs_a_token(services, transcriber):
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test") as anonymous:
        assert (await anonymous.post(URL, content=AUDIO)).status_code == 401
        assert (await anonymous.post(URL, content=AUDIO, headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert not transcriber.calls


async def test_the_language_follows_the_setting_and_the_query(client, services, transcriber):
    await client.post(URL, content=AUDIO)
    await client.post(URL + "?language=EN", content=AUDIO)
    await client.post(URL + "?language=auto", content=AUDIO)
    services.runtime.set("dictation_language", "")
    await client.post(URL, content=AUDIO)
    services.runtime.set("dictation_language", "de")
    await client.post(URL, content=AUDIO)
    assert [call["language"] for call in transcriber.calls] == ["fr", "en", "", "", "de"]


async def test_a_bad_language_is_refused(client, transcriber):
    answer = await client.post(URL + "?language=fr_FR", content=AUDIO)
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "bad_language" and not transcriber.calls


async def test_the_settings_are_passed_on(client, services, transcriber):
    for key, value in (("dictation_model", "base"), ("dictation_max_seconds", 60), ("dictation_timeout", 45), ("dictation_memory_mb", 8192)):
        services.runtime.set(key, value)
    await client.post(URL, content=AUDIO)
    assert transcriber.calls[0] | {"size": 0} == {"size": 0, "model": "base", "language": "fr", "max_seconds": 60, "timeout": 45, "memory_mb": 8192}


def test_the_settings_are_checked(services):
    for key, bad in (("dictation_language", "fr_FR"), ("dictation_language", "français"), ("dictation_model", "../x"), ("dictation_model", ""),
                     ("dictation_max_seconds", 0), ("dictation_max_bytes", 200_000_000), ("dictation_timeout", 1), ("dictation_memory_mb", 5)):
        with pytest.raises(ValueError):
            services.runtime.set(key, bad)
    assert services.runtime.get("dictation_model") == "small" and services.runtime.get("dictation_max_bytes") == 30_000_000


async def test_an_empty_body_is_refused(client, transcriber):
    answer = await client.post(URL, content=b"")
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "bad_audio" and not transcriber.calls


async def test_bad_audio_is_a_clean_422(client, transcriber):
    transcriber.replies.append(BadAudio("This audio cannot be read (InvalidDataError)."))
    answer = await client.post(URL, content=b"not audio at all")
    assert answer.status_code == 422 and answer.json()["error"] == {"code": "bad_audio", "message": "This audio cannot be read (InvalidDataError)."}


async def test_a_duration_over_the_limit_is_refused_with_a_message(client, transcriber):
    transcriber.replies.append(TooLong("This dictation is longer than 300 seconds, the limit."))
    answer = await client.post(URL, content=AUDIO)
    assert answer.status_code == 413 and answer.json()["error"]["code"] == "too_long" and "300 seconds" in answer.json()["error"]["message"]


async def test_a_transcription_that_takes_too_long_is_a_504(client, transcriber):
    transcriber.replies.append(TimedOut("The transcription took more than 300 seconds and was stopped."))
    assert (await client.post(URL, content=AUDIO)).status_code == 504


async def test_a_failure_of_the_transcriber_is_a_502_with_no_detail(client, transcriber):
    transcriber.replies.append(DictationError("The transcription failed (see the server's log)."))
    answer = await client.post(URL, content=AUDIO)
    assert answer.status_code == 502 and "log" in answer.json()["error"]["message"]


# ------------------------------------------------------------------------------------------------ limits
async def test_the_size_limit_is_enforced_while_the_body_is_read(client, services, transcriber):
    services.runtime.set("dictation_max_bytes", 100_000)
    sent = 0

    async def body():
        nonlocal sent
        for _ in range(1000):
            sent += 1
            yield b"a" * 50_000  # no Content-Length: the declared size cannot be trusted

    answer = await client.post(URL, content=body())
    assert answer.status_code == 413 and answer.json()["error"]["code"] == "too_large" and "0 MB" in answer.json()["error"]["message"]
    assert sent <= 4 and not transcriber.calls  # refused as it arrived: not the 50 MB the client wanted to send


async def test_read_limited_stops_at_the_first_chunk_over_the_limit():
    pulled = []

    async def chunks():
        for index in range(10):
            pulled.append(index)
            yield b"x" * 10

    with pytest.raises(TooLarge):
        await read_limited(chunks(), 25)
    assert pulled == [0, 1, 2]
    assert await read_limited(_one(b"abc"), 3) == b"abc"


async def _one(data: bytes):
    yield data


async def test_a_second_dictation_gets_a_429_at_once(client, services, transcriber):
    transcriber.hold = asyncio.Event()
    first = asyncio.create_task(client.post(URL, content=AUDIO))
    await asyncio.wait_for(transcriber.started.wait(), 5)
    second = await asyncio.wait_for(client.post(URL, content=AUDIO), 5)  # no queue: it answers while the first still works
    assert second.status_code == 429 and second.json()["error"]["code"] == "busy" and "already" in second.json()["error"]["message"]
    assert second.headers["retry-after"] == "5" and len(transcriber.calls) == 1
    transcriber.hold.set()
    assert (await first).status_code == 200
    assert (await client.post(URL, content=AUDIO)).status_code == 200  # the slot is free again
    assert not services.dictation.busy


async def test_the_slot_is_freed_after_a_failure(client, services, transcriber):
    transcriber.replies.append(DictationError("boom"))
    assert (await client.post(URL, content=AUDIO)).status_code == 502
    assert (await client.post(URL, content=AUDIO)).status_code == 200 and not services.dictation.busy


async def test_a_client_that_leaves_stops_the_work(services):
    fake = services.dictation.engine = FakeTranscriber(hold=asyncio.Event())

    async def gone() -> None:
        await fake.started.wait()

    with pytest.raises(ClientGone):
        await asyncio.wait_for(services.dictation.transcribe(_one(AUDIO), None, gone), 5)
    assert fake.cancelled == 1 and not services.dictation.busy


async def test_a_cancelled_request_stops_the_work(services):
    fake = services.dictation.engine = FakeTranscriber(hold=asyncio.Event())
    task = asyncio.create_task(services.dictation.transcribe(_one(AUDIO), None))
    await asyncio.wait_for(fake.started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.cancelled == 1 and not services.dictation.busy


# ------------------------------------------------------------------------------- not installed, no model
async def test_dictation_not_installed_says_how_to_install_it(client, services, transcriber):
    transcriber.problem = NotInstalled("Dictation is not installed: the `dictation` extra (faster-whisper) is missing.")
    answer = await client.post(URL, content=AUDIO)
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "dictation_not_installed"
    assert "dictation" in answer.json()["error"]["message"] and not transcriber.calls


async def test_the_real_transcriber_says_so_without_the_extra_and_without_a_model(client, services, monkeypatch):
    def fetched(*args):
        raise AssertionError("a request must never fetch the model")

    monkeypatch.setattr(speech_models, "_download", fetched)
    monkeypatch.setattr(speech_models, "installed", lambda: False)
    answer = await client.post(URL, content=AUDIO)
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "dictation_not_installed"
    assert "docs/OPERATIONS.md" in answer.json()["error"]["message"]
    monkeypatch.setattr(speech_models, "installed", lambda: True)
    answer = await client.post(URL, content=AUDIO)
    message = answer.json()["error"]["message"]
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "model_not_installed"
    assert "/dictation fetch-model" in message and "'small'" in message
    assert not services.dictation.busy


async def test_the_model_is_looked_for_in_the_data_folder(services, monkeypatch):
    monkeypatch.setattr(speech_models, "installed", lambda: True)
    engine = services.dictation.engine
    assert engine.models_dir == services.settings.data_dir / "models"
    with pytest.raises(ModelMissing):
        engine.ready("small")
    model = speech_models.folder(engine.models_dir, "small")
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"x")
    engine.ready("small")
    with pytest.raises(ModelMissing):
        engine.ready("medium")


# ---------------------------------------------------------------------------------- the audio is never kept
async def test_the_audio_is_never_written_to_disk(client, services, transcriber, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    marker = os.urandom(64)
    transcriber.replies.extend([Transcript("ok", 1.0), BadAudio("no"), TooLong("long"), DictationError("boom")])
    codes = [(await client.post(URL, content=marker * 100)).status_code for _ in range(4)]
    assert codes == [200, 422, 413, 502]
    services.runtime.set("dictation_max_bytes", 100_000)
    assert (await client.post(URL, content=marker * 2000)).status_code == 413  # refused while reading
    assert list(scratch.iterdir()) == []
    for folder in (services.settings.data_dir, scratch, Path(services.settings.workspace_dir)):
        for path in folder.rglob("*"):
            if path.is_file():
                assert marker not in path.read_bytes(), path


# ----------------------------------------------------------------------------- the child process (stand-ins)
def stand_in(tmp_path: Path, script: str, name: str = "worker.py") -> ProcessTranscriber:
    path = tmp_path / name
    path.write_text(script, encoding="utf-8")
    return ProcessTranscriber(tmp_path / "models", command=[sys.executable, str(path)])


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_for_pid(path: Path) -> int:
    for _ in range(200):
        if path.exists() and path.read_text():
            return int(path.read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("the child never started")


def sleeper(tmp_path: Path) -> ProcessTranscriber:
    pidfile = tmp_path / "pid"
    return stand_in(tmp_path, f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(120)\n")


async def test_the_child_is_killed_on_a_timeout(tmp_path):
    engine = sleeper(tmp_path)
    with pytest.raises(TimedOut):
        await engine.transcribe(AUDIO, **{**OPTIONS, "timeout": 2})
    assert not alive(int((tmp_path / "pid").read_text()))


async def test_the_child_is_killed_on_a_cancellation(tmp_path):
    engine = sleeper(tmp_path)
    task = asyncio.create_task(engine.transcribe(AUDIO, **OPTIONS))
    pid = await wait_for_pid(tmp_path / "pid")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not alive(pid)


async def test_the_child_is_killed_when_the_service_is_cancelled(services, tmp_path):
    services.dictation.engine = engine = sleeper(tmp_path)
    engine.ready = lambda model: None  # no model in this test: the stand-in is not the worker
    task = asyncio.create_task(services.dictation.transcribe(_one(AUDIO), None))
    pid = await wait_for_pid(tmp_path / "pid")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not alive(pid) and not services.dictation.busy


async def test_the_child_gets_the_audio_and_the_limits_and_nothing_of_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CLARA_SECRET_KEY", "must-not-reach-the-child")
    engine = stand_in(
        tmp_path,
        "import sys, os, json\nsize = len(sys.stdin.buffer.read())\n"
        "print(json.dumps({'ok': True, 'text': json.dumps([size, sys.argv[1:], sorted(os.environ)]), 'seconds': 1.5}))\n",
    )
    found = await engine.transcribe(AUDIO * 50_000, **{**OPTIONS, "language": "", "timeout": 100})  # 2 MB through the pipe
    size, argv, environment = json.loads(found.text)
    assert found.seconds == 1.5 and size == len(AUDIO) * 50_000
    assert f"--model-dir={tmp_path / 'models' / 'whisper' / 'small'}" in argv
    assert {"--language=", "--max-seconds=300", "--deadline=95", "--memory-mb=4096", "--cpu-seconds=800"} <= set(argv)
    assert "CLARA_SECRET_KEY" not in environment and "HF_HUB_OFFLINE" in environment


@pytest.mark.parametrize(
    ("code", "error"),
    [("bad_audio", BadAudio), ("too_long", TooLong), ("timeout", TimedOut), ("no_model", ModelMissing), ("not_installed", NotInstalled)],
)
async def test_what_the_child_refuses_becomes_the_matching_error(tmp_path, code, error):
    engine = stand_in(tmp_path, f"import json\nprint(json.dumps({{'ok': False, 'error': {code!r}, 'message': 'said so'}}))\n")
    with pytest.raises(error, match="said so"):
        await engine.transcribe(AUDIO, **OPTIONS)


async def test_a_crashing_child_gives_a_clean_error_and_its_stderr_stays_in_the_log(tmp_path, caplog):
    engine = stand_in(tmp_path, "import sys\nprint('PRIVATE-DETAIL-IN-STDERR', file=sys.stderr)\nsys.exit(3)\n")
    with caplog.at_level(logging.WARNING, logger="clara.dictation"), pytest.raises(DictationError) as caught:
        await engine.transcribe(AUDIO, **OPTIONS)
    assert "PRIVATE-DETAIL" not in str(caught.value) and type(caught.value) is DictationError
    assert "PRIVATE-DETAIL-IN-STDERR" in caplog.text


async def test_a_child_that_floods_its_output_cannot_fill_the_server(tmp_path):
    engine = stand_in(tmp_path, "import sys\nsys.stdout.write('x' * 20_000_000)\nsys.stderr.write('y' * 20_000_000)\n")
    with pytest.raises(DictationError):
        await engine.transcribe(AUDIO, **OPTIONS)


async def test_a_child_that_answers_without_reading_the_audio_is_fine(tmp_path):
    engine = stand_in(tmp_path, "import json\nprint(json.dumps({'ok': False, 'error': 'bad_audio', 'message': 'no'}))\n")
    with pytest.raises(BadAudio):
        await engine.transcribe(b"x" * 5_000_000, **OPTIONS)  # more than a pipe holds: the write meets a closed pipe


# ------------------------------------------------------------------------------------- the silence filter (no model)
class Seg:
    def __init__(self, text, no_speech_prob, avg_logprob=-0.2):
        self.text, self.no_speech_prob, self.avg_logprob = text, no_speech_prob, avg_logprob


def run_transcribe(monkeypatch, *segments):
    """`worker.transcribe` against a stand-in faster_whisper module that yields these segments."""
    import types

    class Model:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio, **k): return iter(segments), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    args = types.SimpleNamespace(model_dir="x", language="fr", deadline=60)
    return worker.transcribe(None, args, time.monotonic())


def test_speech_is_kept_and_a_high_no_speech_prob_is_dropped():
    assert worker.is_speech(Seg("Bonjour", 0.03)) and worker.is_speech(Seg("Merci beaucoup", 0.55, -0.5))
    assert not worker.is_speech(Seg("Merci.", 0.64, -0.4))  # measured: noise hallucinations sit at 0.64 and above, never under 1.0 logprob
    assert not worker.is_speech(Seg("Sous-titres", 0.9, -1.5))


def test_the_threshold_itself_is_kept_and_just_above_is_dropped():
    assert worker.is_speech(Seg("x", worker.NO_SPEECH_MAX)) and not worker.is_speech(Seg("x", worker.NO_SPEECH_MAX + 0.001))


def test_the_filter_looks_at_no_speech_prob_alone():  # the library's own rule also needs avg_logprob < -1.0
    assert worker.is_speech(Seg("x", 0.1, -3.0)) and not worker.is_speech(Seg("x", 0.7, -0.1))


def test_transcribe_joins_what_is_speech_and_drops_the_rest(monkeypatch):
    got = run_transcribe(monkeypatch, Seg(" Bonjour Clara. ", 0.03), Seg("Merci.", 0.8), Seg("À demain.", 0.05))
    assert got == "Bonjour Clara. À demain."


def test_a_transcription_where_every_segment_is_dropped_is_empty(monkeypatch):
    assert run_transcribe(monkeypatch, Seg("Merci.", 0.7), Seg("Sous-titres réalisés par la communauté d'Amara.org", 0.9)) == ""
    assert run_transcribe(monkeypatch) == ""


# ------------------------------------------------------------------------------------- the real worker
def wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(16000)
        sound.writeframes(b"\x00\x10" * int(16000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def real_worker(tmp_path):
    pytest.importorskip("faster_whisper")
    pytest.importorskip("av")
    model = tmp_path / "models" / "whisper" / "small"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"")  # enough for the checks that come before the model is loaded
    return ProcessTranscriber(tmp_path / "models")


async def test_the_worker_refuses_what_is_not_audio(real_worker):
    with pytest.raises(BadAudio):
        await real_worker.transcribe(b"this is not audio", **OPTIONS)


async def test_the_worker_checks_the_real_decoded_duration(real_worker):
    with pytest.raises(TooLong, match="longer than 2 seconds"):
        await real_worker.transcribe(wav(3), **{**OPTIONS, "max_seconds": 2})


def test_the_worker_limits_itself_before_reading_anything(real_worker):
    code = "import resource, sys; sys.argv=['w']\nfrom clara.dictation.worker import limit_self\nlimit_self(2048, 7)\n" \
           "print([resource.getrlimit(k)[0] for k in (resource.RLIMIT_AS, resource.RLIMIT_CPU, resource.RLIMIT_CORE, resource.RLIMIT_FSIZE)])"
    import subprocess

    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert done.stdout.strip() == f"[{2048 * 1024 * 1024}, 7, 0, 0]"


@pytest.fixture
def real_model():
    root = os.environ.get("CLARA_TEST_WHISPER_ROOT")
    pytest.importorskip("faster_whisper")
    if not root or not (Path(root) / "whisper" / "small" / "model.bin").is_file():
        pytest.skip("CLARA_TEST_WHISPER_ROOT does not name a folder that holds whisper/small")
    return ProcessTranscriber(Path(root))


async def test_the_real_model_transcribes_in_a_child(real_model):
    found = await real_model.transcribe(wav(3), **OPTIONS)
    assert found.seconds == 3.0 and isinstance(found.text, str)


async def test_the_real_model_refuses_an_unknown_language(real_model):
    from clara.dictation import BadLanguage

    with pytest.raises(BadLanguage):
        await real_model.transcribe(wav(3), **{**OPTIONS, "language": "zz"})


# -------------------------------------------------------------------------------------------- console
async def run(services, line: str) -> str:
    return await console.run(services, line, services.tokens.find("tester"))


async def test_the_console_reports_the_state(services, token, monkeypatch):
    monkeypatch.setattr(speech_models, "installed", lambda: False)
    text = await run(services, "/dictation")
    assert "Installed: no" in text and "not fetched" in text and "Language fr" in text and "Idle." in text
    assert await run(services, "/dictation status") == text
    assert (await run(services, "/dictation fetch")).startswith("Usage:")


async def test_fetching_the_model_needs_the_extra(services, token, monkeypatch):
    monkeypatch.setattr(speech_models, "installed", lambda: False)
    assert "`dictation` extra" in await run(services, "/dictation fetch-model")


async def test_fetching_the_model_downloads_it_into_the_data_folder(services, token, monkeypatch):
    monkeypatch.setattr(speech_models, "installed", lambda: True)
    asked = []

    def download(root, name):
        asked.append((root, name))
        final = speech_models.folder(root, name)
        final.mkdir(parents=True)
        (final / "model.bin").write_bytes(b"x")
        return final

    monkeypatch.setattr(speech_models, "_download", download)
    text = await run(services, "/dictation fetch-model")
    assert asked == [(services.settings.data_dir / "models", "small")] and text.startswith("Fetched the model 'small'")
    assert "fetched, in" in await run(services, "/dictation")
    assert "already" in await run(services, "/dictation fetch-model") and len(asked) == 1


async def test_a_failed_fetch_says_why_and_leaves_no_model(services, token, monkeypatch):
    monkeypatch.setattr(speech_models, "installed", lambda: True)

    def download(root, name):
        raise OSError("no network")

    monkeypatch.setattr(speech_models, "_download", download)
    text = await run(services, "/dictation fetch-model")
    assert "Could not fetch" in text and "no network" in text
    assert not speech_models.present(services.settings.models_dir, "small")


def test_a_partial_download_is_never_where_the_worker_looks(tmp_path, monkeypatch):
    utils = pytest.importorskip("faster_whisper.utils")

    def broken(name, output_dir=None):
        (Path(output_dir) / "model.bin").write_bytes(b"half")
        raise OSError("connection lost")

    monkeypatch.setattr(utils, "download_model", broken)
    with pytest.raises(OSError):
        speech_models._download(tmp_path, "small")
    assert not speech_models.present(tmp_path, "small") and not list((tmp_path / "whisper").iterdir())
