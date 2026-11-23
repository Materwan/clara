"""`/dictate` in clara-chat: an external recorder chosen by availability and run as an argv list, Enter or the maximum length
stops it, it is always killed, and the text goes on the prompt line, or its run is followed when the server sent it."""

from __future__ import annotations

import os
import struct
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from clara.cli import chat as terminal
from clara.cli import dictate
from clara.cli.common import Api, ClientError

RECORDER = '''
import os, signal, struct, sys, time
marker, pidfile, mode = sys.argv[1:4]
open(pidfile, "w").write(str(os.getpid()))
if mode == "deaf":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "fail":
    sys.stderr.write("arecord: main:831: audio open error: No such file or directory\\n")
    sys.exit(1)
big = struct.pack("<I", 0x7FFFFFFF)  # what a recorder writing to a pipe puts in its header
header = b"RIFF" + big + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + big
sys.stdout.buffer.write(header + b"\\0\\0" * 1600)
sys.stdout.buffer.flush()
open(marker, "w").close()
time.sleep(60)
'''


class Stand:
    """A stand-in recorder (a script that writes WAV bytes, then waits), and a stand-in for the Enter key."""

    def __init__(self, tmp_path, mode: str = "ok"):
        script = tmp_path / "recorder.py"
        script.write_text(RECORDER)
        self.marker, self.pidfile = tmp_path / "marker", tmp_path / "pid"
        self.argv = [sys.executable, str(script), str(self.marker), str(self.pidfile), mode]

    def enter(self, seconds, alive):
        """Returns once the recorder has written its audio: what pressing Enter does."""
        end = time.monotonic() + 10
        while not self.marker.exists() and alive() and time.monotonic() < end:
            time.sleep(0.01)
        self.marker.unlink(missing_ok=True)  # the next recording waits for its own audio

    def gone(self) -> bool:
        try:
            os.kill(int(self.pidfile.read_text()), 0)
        except ProcessLookupError:
            return True
        return False


@pytest.fixture
def stand(tmp_path):
    return Stand(tmp_path)


def test_the_recorder_is_chosen_by_availability_in_a_fixed_order_and_is_an_argv_list():
    both = {"arecord": "/usr/bin/arecord", "parecord": "/usr/bin/parecord"}
    first = dictate.pick_recorder(both.get)
    assert isinstance(first, list) and first[:2] == ["/usr/bin/arecord", "-q"] and "16000" in first
    only = dictate.pick_recorder({"parecord": "/usr/bin/parecord"}.get)
    assert only[0] == "/usr/bin/parecord" and "--rate=16000" in only
    assert all(isinstance(word, str) and " " not in word for word in first + only)  # nothing a shell would split


def test_without_a_recorder_the_error_names_both_and_how_to_install_them(monkeypatch):
    monkeypatch.setattr(dictate.shutil, "which", lambda name: None)
    api = FakeApi()
    chat = terminal.Chat(api, "UTC", None, False, False, None)
    with pytest.raises(ClientError) as error:
        terminal.command(chat, "/dictate")
    assert "arecord" in str(error.value) and "parecord" in str(error.value) and "alsa-utils" in str(error.value)
    assert api.sent == [] and chat.pending == ""  # nothing was asked of the server


def test_a_recorder_that_cannot_start_is_an_error_not_a_crash(tmp_path):
    with pytest.raises(ClientError, match="Cannot start the recorder"):
        dictate.record([str(tmp_path / "nothing-here")], 1)


def test_enter_stops_the_recorder_and_the_audio_comes_back_with_a_true_header(stand):
    audio = dictate.record(stand.argv, 30, wait=stand.enter)
    assert stand.gone()
    assert audio[:4] == b"RIFF" and len(audio) == 44 + 3200
    assert struct.unpack("<I", audio[4:8])[0] == len(audio) - 8 and struct.unpack("<I", audio[40:44])[0] == 3200


def test_the_maximum_length_stops_it(stand, monkeypatch):
    monkeypatch.setattr(dictate.sys, "stdin", open(os.devnull))  # nobody presses Enter
    monkeypatch.setattr(dictate.select, "select", lambda *a: ([], [], []))
    started = time.monotonic()
    audio = dictate.record(stand.argv, 1.0)
    assert 0.9 < time.monotonic() - started < 6 and stand.gone() and len(audio) > 44


def test_enter_is_read_from_the_terminal(monkeypatch):
    read, write = os.pipe()
    with os.fdopen(read) as stdin:
        monkeypatch.setattr(dictate.sys, "stdin", stdin)
        started = time.monotonic()
        dictate.wait_for_enter(0.4, lambda: True)  # no Enter: the time is up
        assert time.monotonic() - started >= 0.4
        os.write(write, b"\n")
        started = time.monotonic()
        dictate.wait_for_enter(30, lambda: True)
        assert time.monotonic() - started < 5
        dictate.wait_for_enter(30, lambda: False)  # the recorder is gone: no waiting
    os.close(write)


def test_a_recorder_that_ignores_the_stop_is_killed(tmp_path):
    deaf = Stand(tmp_path, "deaf")
    dictate.record(deaf.argv, 30, wait=deaf.enter)
    assert deaf.gone()


def test_it_is_killed_on_ctrl_c_and_on_any_error(stand):
    def interrupted(seconds, alive):
        stand.enter(seconds, alive)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        dictate.record(stand.argv, 30, wait=interrupted)
    assert stand.gone()

    def broken(seconds, alive):
        stand.enter(seconds, alive)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        dictate.record(stand.argv, 30, wait=broken)
    assert stand.gone()


def test_a_recorder_that_fails_says_why(tmp_path):
    failing = Stand(tmp_path, "fail")
    with pytest.raises(ClientError, match="The recorder stopped: arecord: main:831: audio open error"):
        dictate.record(failing.argv, 30, wait=lambda seconds, alive: time.sleep(0.5))


def test_a_recording_with_no_audio_is_refused_before_anything_is_sent(tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("import time; time.sleep(60)")
    with pytest.raises(ClientError, match="Nothing was recorded"):
        dictate.record([sys.executable, str(empty)], 30, wait=lambda seconds, alive: time.sleep(0.3))


# ----------------------------------------------------------------------
# The terminal
# ----------------------------------------------------------------------
class FakeApi:
    def __init__(self, answer=None, settings=None, fail=None):
        self.answer = answer if answer is not None else {"text": "Hello there", "conversation": "c-dict", "sent": False}
        self.settings = settings
        self.fail = fail
        self.sent: list[tuple] = []  # every call made, whatever the verb
        self.audio: list[bytes] = []

    def get(self, path, **params):
        self.sent.append(("GET", path))
        if path == "/v1/admin/settings" and self.settings is not None:
            return {"settings": [{"key": key, "value": value} for key, value in self.settings.items()]}
        raise ClientError("403: not for this token")

    def call(self, method, path, **options):
        self.sent.append((method, path, options["params"], options["headers"]))
        self.audio.append(options["content"])
        if self.fail:
            raise self.fail
        return self.answer

    def stream(self, method, path, body=None, read_timeout=None):
        self.sent.append((method, path))
        yield {"type": "resume", "message": "Hello there", "attachments": []}
        yield {"type": "token", "text": "Hi!"}
        yield {"type": "done"}


@pytest.fixture
def dictating(stand, monkeypatch):
    """A terminal whose recorder is the stand-in and whose Enter is pressed at once."""
    monkeypatch.setattr(terminal, "record", lambda argv, seconds: dictate.record(argv, seconds, wait=stand.enter))

    def make(api, conversation=None):
        return terminal.Chat(api, "UTC", conversation, False, False, None, recorder=stand.argv)

    return make


def test_with_automatic_sending_off_the_text_waits_on_the_prompt_line(dictating, stand, capsys):
    api = FakeApi({"text": " Hello \x1b[2J there ", "conversation": "c-dict", "sent": False})
    chat = dictating(api)
    assert terminal.command(chat, "/dictate") is True
    method, path, params, headers = api.sent[-1]
    assert (method, path, params) == ("POST", "/v1/dictation", {"client": "cli"})  # no conversation open: the server's
    assert headers == {"Content-Type": "audio/wav"} and api.audio[0][:4] == b"RIFF"
    assert chat.pending == "Hello [2J there" and chat.conversation == "c-dict"  # the dictation conversation is now open
    assert not any(call[1].endswith("/run") for call in api.sent)  # nothing was sent, nothing is followed
    assert stand.gone() and "Recording" in capsys.readouterr().out


def test_it_dictates_into_the_open_conversation(dictating):
    api = FakeApi({"text": "Hello", "conversation": "c-open", "sent": False})
    chat = dictating(api, "c-open")
    chat.dictate()
    assert api.sent[-1][2] == {"client": "cli", "conversation": "c-open"} and chat.pending == "Hello"


def test_with_automatic_sending_on_the_run_is_followed_and_printed(dictating, capsys):
    api = FakeApi({"text": "Hello there", "conversation": "c-dict", "sent": True, "running": True})
    chat = dictating(api)
    chat.dictate()
    assert api.sent[-1] == ("GET", "/v1/conversations/c-dict/run") and chat.pending == ""
    out = capsys.readouterr().out
    assert "you> Hello there" in out and "Hi!" in out and chat.conversation == "c-dict"


def test_silence_sends_nothing_and_keeps_the_conversation(dictating, capsys):
    api = FakeApi({"text": "", "conversation": None, "sent": False})
    chat = dictating(api, "c-open")
    chat.dictate()
    assert chat.pending == "" and chat.conversation == "c-open" and "Nothing heard" in capsys.readouterr().out


def test_a_server_refusal_reaches_the_prompt_and_the_recorder_is_gone(dictating, stand):
    chat = dictating(FakeApi(fail=ClientError("Dictation is busy (429)")))
    with pytest.raises(ClientError, match="busy"):
        chat.dictate()
    assert stand.gone() and chat.pending == ""


def test_ctrl_c_while_recording_cancels_and_asks_nothing(stand, monkeypatch, capsys):
    def interrupted(argv, seconds):
        dictate.record(argv, seconds, wait=lambda s, alive: (stand.enter(s, alive), (_ for _ in ()).throw(KeyboardInterrupt)))

    monkeypatch.setattr(terminal, "record", interrupted)
    api = FakeApi()
    chat = terminal.Chat(api, "UTC", None, False, False, None, recorder=stand.argv)
    assert terminal.command(chat, "/dictate") is True
    assert api.audio == [] and stand.gone() and "Cancelled" in capsys.readouterr().out


def test_the_taint_notice_is_shown_once_and_the_transcript_is_never_a_command(dictating, capsys):
    api = FakeApi({"text": "/allow 3", "conversation": "c-dict", "sent": False})
    chat = dictating(api)
    chat.dictate()
    chat.dictate()
    out = capsys.readouterr().out
    assert out.count("marked so for good") == 1
    assert chat.pending == "allow 3"  # no leading slash: it cannot run a command
    assert not any("/v1/approvals" in str(call) for call in api.sent)  # a dictation never touches an approval


def test_the_longest_recording_is_what_the_server_allows_else_300_seconds():
    chat = terminal.Chat(FakeApi(settings={"dictation_max_seconds": 120, "dictation_max_bytes": 30_000_000}), "UTC", None, False, False, None)
    assert chat.dictation_seconds() == 120
    small = {"dictation_max_seconds": 3600, "dictation_max_bytes": 3_200_000}  # the byte limit comes first
    assert terminal.Chat(FakeApi(settings=small), "UTC", None, False, False, None).dictation_seconds() == 99
    assert terminal.Chat(FakeApi(), "UTC", None, False, False, None).dictation_seconds() == 300  # setting not readable


def test_the_audio_reaches_the_route_as_audio_wav_through_the_real_client(stand, monkeypatch):
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "ok", "conversation": "c1", "sent": False})

    api = Api("http://clara.test", "clara_1_x", "cli")
    api.http = httpx.Client(base_url="http://clara.test", headers=api.headers, transport=httpx.MockTransport(serve))
    monkeypatch.setattr(terminal, "record", lambda argv, seconds: dictate.record(argv, seconds, wait=stand.enter))
    chat = terminal.Chat(api, "UTC", None, False, False, None, recorder=stand.argv)
    monkeypatch.setattr(chat, "dictation_seconds", lambda: 30)
    chat.dictate()
    request = seen[0]
    assert request.url.path == "/v1/dictation" and request.url.params["client"] == "cli"
    assert request.headers["content-type"] == "audio/wav" and request.content[:4] == b"RIFF"
    assert request.headers["authorization"] == "Bearer clara_1_x"


def test_the_text_is_put_on_the_line_where_readline_can_and_shown_where_it_cannot(monkeypatch, capsys):
    hook = []
    typed = []
    fake = SimpleNamespace(
        set_pre_input_hook=lambda found=None: hook.append(found),
        insert_text=lambda text: typed.append(text),
        redisplay=lambda: None,
    )
    monkeypatch.setattr(terminal, "readline", fake)

    def input_(prompt):
        hook[0]()  # readline calls the hook just before it shows the line
        return "".join(typed) + " edited"

    monkeypatch.setattr("builtins.input", input_)
    assert terminal.read_line("Hello there") == "Hello there edited" and hook[-1] is None  # the hook is taken off again
    monkeypatch.setattr(terminal, "readline", None)  # Windows, or libedit: no prefill
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert terminal.read_line("Hello there") == "Hello there"  # Enter alone sends the transcript
    assert "Transcript: Hello there" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt: "no, this")
    assert terminal.read_line("Hello there") == "no, this"
    monkeypatch.setattr("builtins.input", lambda prompt: "plain")
    assert terminal.read_line("") == "plain"


def test_the_help_and_the_features_name_the_command():
    assert "/dictate" in terminal.HELP and "dictate" in terminal.OWN
