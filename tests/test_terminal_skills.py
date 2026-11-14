"""The terminal's side of skills: a `/name` that is a skill of the conversation is sent as typed (the server expands it), `/skills`
lists them, Tab completes the commands and the skills read, and a skill never takes one of the terminal's own words."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

from clara.cli import chat as terminal
from clara.cli.common import ClientError
from clara.skills import RESERVED

ROOT = Path(__file__).resolve().parent.parent
REVIEW = {"name": "review-pr", "description": "Review a pull request.\n  Read it whole.", "argument_hint": "[number]", "packs": []}
PROJECT = {"name": "thesis", "description": "Only for the thesis.", "argument_hint": "", "packs": []}


class SkillsApi:
    """An Api stand-in: the skills of each project, the project of the conversation, and what was sent."""

    def __init__(self, skills=None, project=None, fail=None):
        self.skills = {"none": [REVIEW]} if skills is None else skills
        self.project = project
        self.fail = fail
        self.listed: list[str] = []  # the project of each skill list asked for
        self.streamed: list[dict] = []  # the bodies of the messages sent

    def get(self, path, **params):
        if path == "/v1/skills":
            self.listed.append(params["project"])
            if self.fail:
                raise self.fail
            return {"skills": self.skills.get(params["project"], [])}
        if path.startswith("/v1/conversations/"):
            return {"id": path.rsplit("/", 1)[1], "project": self.project}
        if path == "/v1/tasks":
            return {"tasks": []}
        raise AssertionError(path)

    def stream(self, method, path, body=None, read_timeout=None):
        self.streamed.append(body)
        yield {"type": "turn", "conversation": "c-new"}
        yield {"type": "done"}


def terminal_chat(api, conversation=None):
    return terminal.Chat(api, "UTC", conversation, False, False, None)


def test_a_visible_skill_is_sent_as_typed_and_the_server_expands_it(capsys):
    api = SkillsApi()
    chat = terminal_chat(api)
    assert terminal.command(chat, "/review-pr 42 now") is True
    assert api.streamed == [{"message": "/review-pr 42 now", "timezone": "UTC", "incognito": False, "local_only": False}]
    assert chat.conversation == "c-new"


def test_a_word_that_is_no_skill_prints_the_help_and_sends_nothing(capsys):
    api = SkillsApi()
    assert terminal.command(terminal_chat(api), "/nope 1") is True
    assert api.streamed == [] and "/new [incognito] [local]" in capsys.readouterr().out


def test_the_terminals_own_commands_win_over_a_skill_of_the_same_name(capsys):
    api = SkillsApi(skills={"none": [REVIEW] + [{**REVIEW, "name": word} for word in ("tasks", "help", "remember")]})
    chat = terminal_chat(api)
    terminal.command(chat, "/tasks")  # the terminal's own command runs
    assert "(no task)" in capsys.readouterr().out
    terminal.command(chat, "/help")
    terminal.command(chat, "/remember")  # without its text it falls through to the help, not to the skill
    assert api.streamed == [] and capsys.readouterr().out.count("/new [incognito] [local]") == 2


def test_skills_lists_one_line_each_and_cuts_the_description(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")
    api = SkillsApi(skills={"none": [REVIEW, {**REVIEW, "name": "long", "description": "x" * 200, "argument_hint": ""}]})
    terminal.command(terminal_chat(api), "/skills")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "/review-pr [number]  Review a pull request. Read it whole."
    assert lines[1].startswith("/long  x") and lines[1].endswith("…") and len(lines[1]) == 59


def test_no_skill_says_so(capsys):
    terminal.command(terminal_chat(SkillsApi(skills={"none": []})), "/skills")
    assert capsys.readouterr().out.startswith("No skill for this conversation.")


def test_a_list_that_cannot_be_read_only_disables_the_skills(capsys):
    for failure in (ClientError("403: forbidden"), httpx.ReadTimeout("slow")):
        api = SkillsApi(fail=failure)
        chat = terminal_chat(api)
        assert terminal.command(chat, "/review-pr 1") is True  # no exception: the loop goes on
        assert api.streamed == [] and "/new [incognito] [local]" in capsys.readouterr().out
        terminal.command(chat, "/skills")
        assert capsys.readouterr().out.startswith("No skill for this conversation.")
    assert chat.listed == []


def test_the_skills_are_those_of_the_conversations_project_read_once_per_project(capsys):
    api = SkillsApi(skills={"none": [REVIEW], "3": [PROJECT]}, project=3)
    chat = terminal_chat(api, conversation="c-thesis")
    terminal.command(chat, "/thesis")
    terminal.command(chat, "/thesis again")
    terminal.command(chat, "/review-pr")  # a skill of no project: not visible in this one
    assert api.listed == ["3"] and len(api.streamed) == 2
    assert [item["name"] for item in chat.listed] == ["thesis"]


def test_a_skill_of_another_project_is_not_sent_outside_it(capsys):
    api = SkillsApi(skills={"none": [REVIEW], "3": [PROJECT]})
    terminal.command(terminal_chat(api), "/thesis")
    assert api.streamed == [] and "/new [incognito] [local]" in capsys.readouterr().out


def test_tab_completes_the_commands_and_the_skills_read(monkeypatch):
    api = SkillsApi()
    chat = terminal_chat(api)
    chat.visible_skills()
    monkeypatch.setattr(terminal, "readline", SimpleNamespace(get_begidx=lambda: 0))
    complete = terminal.completer(chat)
    assert complete("/rev", 0) == "/review-pr" and complete("/rev", 1) is None
    assert complete("/re", 0) == "/refuse"  # commands and skills are sorted together
    assert complete("/ne", 0) == "/new" and complete("/ne", 1) is None and complete("/no", 0) == "/notify"
    assert complete("hello", 0) is None
    monkeypatch.setattr(terminal, "readline", SimpleNamespace(get_begidx=lambda: 3))
    assert complete("/re", 0) is None  # not the first word


def test_the_terminal_starts_without_readline(monkeypatch):
    monkeypatch.setitem(sys.modules, "readline", None)  # importing it fails, as on Windows
    spec = importlib.util.spec_from_file_location("clara.cli._chat_without_readline", terminal.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.readline is None


def test_every_command_of_the_terminal_is_its_own_and_reserved_for_skills():
    chat = (ROOT / "src/clara/cli/chat.py").read_text(encoding="utf-8")
    found: set[str] = set()
    for line in chat.splitlines():
        if re.match(r"\s+(el)?if name (==|in) ", line):
            found |= set(re.findall(r'"/([a-z-]+)"', line))
    assert {"skills", "tasks", "quit", "allow"} <= found, "the scan above found nothing: it is out of date"
    assert not found - terminal.OWN, "a command missing from OWN in cli/chat.py"
    assert terminal.OWN <= RESERVED, "an own command that a skill could take: add it to RESERVED in skills/parse.py"


def test_the_help_names_skills():
    assert "/skills" in terminal.HELP and "/name arguments" in terminal.HELP
