"""`clara-chat`: talk to Clara from a terminal.

    clara-chat                     a new conversation (each run starts one)
    clara-chat --resume            go on with the last conversation started from the terminal
    clara-chat --conversation ID   go on with that one
    clara-chat --incognito         nothing remembered, deleted after a day idle
    clara-chat "a question"        one question, then exit
    clara-chat --save-token        store your token (hidden input) in ~/.config/clara/token, mode 0600

While idle it shows reminders, notifications, alerts and approval requests as they come. Times you type are read on
this machine's clock zone (its IANA name is sent, never an offset).

`--local-tools` offers Clara two read-only tools that run here, on this machine, inside the folder you started in:
listing a folder and reading a file. Each call is shown and needs your yes (unless `--yes`).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from ..timeutil import system_zone_name, zone
from .common import DEFAULT_URL, Api, ClientError, read_token, save_token, token_file, utf8_console

CLIENT = "cli"
REPEATS = ("daily", "weekly", "monthly")
ANSWERS = {"o": "allow_once", "a": "allow_always", "r": "refuse_once", "n": "refuse_always"}

HELP = """\
/new [incognito] [local]   start a new conversation
/resume [id]               the conversations of the terminal (with an id: go on with it)
/conversations [words]     search every conversation
/facts                     what Clara remembers about you
/remember <text>           store a fact          /forget <id>     delete one
/remind [daily|weekly|monthly] [@clients] <when> <text>
                           <when>: +30m, +2h, +3d | 09:30 | tomorrow 09:30 | 2026-10-05 09:30
/reminders                 reminders to come     /unremind <id>   cancel one
/tasks [all|done]          your to-do list       /task <id>       one task in full
/task add [due <when>] [remind <when>]... <title> [| <description>]
/task sub <id> ...         a sub task            /task done|reopen|delete <id>
/task move <id> <parent-id>|none                 /task set <id> title|description|due|remind <value>
/notify [@clients] <text>  send yourself a notification
/model [provider:model | default]   this conversation's model
/tools [<pack> [on|off]]   the special tools of this conversation (listed; or switch one)
/approvals                 requests waiting      /allow|/always|/refuse|/never <id>  answer one
/undo                      revert Clara's last workspace change
/quit                      leave"""


def local_zone() -> str:
    name = os.environ.get("TZ", "").lstrip(":") or system_zone_name()
    try:
        zone(name)
        return name
    except ValueError:
        return "UTC"


def _clock(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d\d)", text)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"Not a time of day: {text!r} (use HH:MM).")
    return int(match[1]), int(match[2])


def take_when(words: list[str], now: datetime) -> tuple[datetime, list[str]]:
    """The wall-clock moment the words start with, and the words after it. `now` is naive local time."""
    head = words[0].lower() if words else ""
    relative = re.fullmatch(r"\+(\d+)([mhd])", head)
    if relative:
        unit = {"m": "minutes", "h": "hours", "d": "days"}[relative[2]]
        return now + timedelta(**{unit: int(relative[1])}), words[1:]
    if head == "tomorrow" and len(words) > 1:
        hour, minute = _clock(words[1])
        return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0), words[2:]
    if re.fullmatch(r"\d{4}-\d\d-\d\d", head) and len(words) > 1:
        hour, minute = _clock(words[1])
        try:
            return datetime.fromisoformat(head).replace(hour=hour, minute=minute), words[2:]
        except ValueError:
            raise ValueError(f"Not a date: {head!r}.") from None
    if ":" in head:
        hour, minute = _clock(head)
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return (due if due > now else due + timedelta(days=1)), words[1:]
    raise ValueError(f"Cannot read the time {head!r}: use +30m, 09:30, tomorrow 09:30 or 2026-10-05 09:30.")


def take_targets(argument: str) -> tuple[list[str], str]:
    words = argument.split()
    for index, word in enumerate(words[:2]):
        if word.startswith("@"):
            del words[index]
            return [name for name in word[1:].lower().split(",") if name], " ".join(words)
    return [], argument


def iso(moment: datetime) -> str:
    return moment.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def now_here(zone_name: str) -> datetime:
    return datetime.now(zone(zone_name)).replace(tzinfo=None)


def parse_task(argument: str, now: datetime) -> tuple[str, str, datetime | None, list[datetime]]:
    words = argument.split()
    due: datetime | None = None
    reminders: list[datetime] = []
    while words and words[0].lower() in ("due", "remind"):
        keyword = words.pop(0).lower()
        if not words:
            raise ValueError(f"{keyword} needs a time")
        moment, words = take_when(words, now)
        if keyword == "due":
            due = moment
        else:
            reminders.append(moment)
    title, _, description = " ".join(words).partition("|")
    if not title.strip():
        raise ValueError("A task needs a title.")
    return title.strip(), description.strip(), due, reminders


def parse_when_list(text: str, now: datetime) -> list[datetime]:
    if text.strip().lower() in ("none", "off", "-"):
        return []
    moments = []
    for part in text.split(","):
        moment, rest = take_when(part.split(), now)
        if rest:
            raise ValueError(f"Unexpected after the time: {' '.join(rest)!r} (separate several times with commas).")
        moments.append(moment)
    return moments


def local(moment: str | None, zone_name: str) -> str:
    if not moment:
        return "-"
    return datetime.fromisoformat(moment).astimezone(zone(zone_name)).strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------
# Local tools (the client-side tools protocol)
# ----------------------------------------------------------------------
LOCAL_TOOLS = [
    {"type": "function", "function": {
        "name": "local_list", "description": "List a folder on the person's own machine (inside the folder clara-chat "
        "was started in). The person confirms each call.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative path."}}}}},
    {"type": "function", "function": {
        "name": "local_read", "description": "Read a text file on the person's own machine (inside the folder clara-chat "
        "was started in), up to 40,000 characters from start_line. The person confirms each call.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}},
                       "required": ["path"]}}},
]


class LocalTools:
    def __init__(self, root: Path, confirm: bool):
        self.root = root.resolve()
        self.confirm = confirm

    def _path(self, relative: str) -> Path:
        candidate = (self.root / (relative or ".")).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"{relative} is outside {self.root}")
        return candidate

    def run(self, name: str, arguments: dict) -> str:
        try:
            path = self._path(str(arguments.get("path") or "."))
            if self.confirm:
                answer = input(f"\n  Clara wants to {name.removeprefix('local_')} {path}. Allow? [y/N] ").strip().lower()
                if answer not in ("y", "yes", "o", "oui"):
                    return "The person refused this."
            if name == "local_list":
                entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]
                return "\n".join(f"{entry.name}{'/' if entry.is_dir() else ''}" for entry in entries) or "(empty)"
            if name == "local_read":
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                start = max(1, int(arguments.get("start_line") or 1))
                out, used = [], 0
                for number in range(start, len(lines) + 1):
                    line = f"{number:>5}  {lines[number - 1]}"
                    if used + len(line) > 40_000:
                        out.append(f"[read on with start_line={number}]")
                        break
                    out.append(line)
                    used += len(line) + 1
                return "\n".join(out) or "(empty file)"
        except (OSError, ValueError) as error:
            return f"Error: {error}"
        return f"Unknown tool {name}."


# ----------------------------------------------------------------------
# Showing events
# ----------------------------------------------------------------------
class Notices:
    """Events from the listener thread, never printed in the middle of an answer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._waiting: list[str] = []

    def say(self, text: str) -> None:
        with self._lock:
            if self._busy:
                self._waiting.append(text)
            else:
                print(f"\n{text}\n\nyou> ", end="", flush=True)

    @contextmanager
    def busy(self) -> Iterator[None]:
        with self._lock:
            self._busy = True
        try:
            yield
        finally:
            with self._lock:
                self._busy = False
                waiting, self._waiting = self._waiting, []
            for text in waiting:
                print(f"\n{text}")


def describe_event(event: dict) -> str | None:
    kind = event.get("type")
    if kind == "reminder":
        return f"\a⏰ {event['text']}"
    if kind in ("notification", "task", "schedule", "ask"):
        return f"\a🔔 {event.get('title') + ': ' if event.get('title') else ''}{event['text']}"
    if kind == "alert":
        return f"\a⚠ {event.get('title')}: {event['text']}"
    if kind == "approval":
        approval = event["payload"]["approval"]
        return (f"\a🔐 Clara asks: {approval['summary']} [{approval['level']}] — /allow {approval['id']}, "
                f"/always {approval['id']}, /refuse {approval['id']} or /never {approval['id']}")
    return None


def listen(api: Api, notices: Notices, stop: threading.Event) -> None:
    state = ""
    while not stop.is_set():
        try:
            for event in api.stream("GET", "/v1/events/stream", read_timeout=60):
                if event.get("type") == "server":
                    new = event.get("state") or event.get("payload", {}).get("state", "")
                    if new and new != state:
                        if state:
                            notices.say({"running": "Clara is running again.", "stopping": "Clara is stopping.",
                                         "stopped": "Clara stopped."}.get(new, new))
                        state = new
                    continue
                text = describe_event(event)
                if text:
                    notices.say(text)
        except (ClientError, OSError, ValueError):
            pass
        if state not in ("", "down"):
            notices.say("Clara is not reachable.")
            state = "down"
        stop.wait(5)


# ----------------------------------------------------------------------
# The conversation
# ----------------------------------------------------------------------
class Chat:
    def __init__(self, api: Api, zone_name: str, conversation: str | None, incognito: bool, local_only: bool,
                 tools: LocalTools | None):
        self.api = api
        self.zone = zone_name
        self.conversation = conversation
        self.incognito = incognito
        self.local_only = local_only
        self.tools = tools

    def ask(self, message: str) -> None:
        body: dict = {"message": message, "timezone": self.zone}
        if self.conversation:
            body["conversation"] = self.conversation
        else:
            body.update(incognito=self.incognito, local_only=self.local_only)
        if self.tools:
            body["tools"] = LOCAL_TOOLS
        for event in self.api.stream("POST", "/v1/chat/stream", body):
            kind = event["type"]
            if kind == "turn" and event.get("conversation"):
                self.conversation = event["conversation"]
            elif kind == "token":
                print(event["text"], end="", flush=True)
            elif kind == "tool_start":
                print(f"\n  [{event['name']}]", end="", flush=True)
            elif kind == "approval":
                self.answer_inline(event["approval"])
            elif kind == "tool_requests":
                self.run_local(event)
            elif kind in ("warning", "retrying"):
                print(f"\n  ({event['message']})", end="", flush=True)
            elif kind == "compacted":
                print(f"\n  (summarised: {event['before']:.0f}% → {event['after']:.0f}% of the context)", end="")
            elif kind == "error":
                print(f"\n! {event['message']}")
        print()

    def answer_inline(self, approval: dict) -> None:
        print(f"\n  🔐 {approval['summary']} [{approval['level']}; {approval['reason']}]")
        choice = input("  Allow? [o]nce, [a]lways, [r]efuse, [n]ever, Enter: decide later > ").strip().lower()
        if choice[:1] in ANSWERS:
            try:
                settled = self.api.post(f"/v1/approvals/{approval['id']}/decide", {"answer": ANSWERS[choice[:1]]})
                print(f"  → {settled['status']}")
            except ClientError as error:
                print(f"  ! {error}")

    def run_local(self, event: dict) -> None:
        assert self.tools is not None
        results = [{"id": call["id"], "content": self.tools.run(call["name"], call.get("arguments") or {})}
                   for call in event["calls"]]
        self.api.post(f"/v1/turns/{event['turn']}/tool-results", {"results": results})


def task_line(task: dict, zone_name: str) -> str:
    parts = [f"[{task['id']}] {task['title']}"]
    if task["status"] == "done":
        parts.append("done")
    if task.get("parent_id"):
        parts.append(f"sub task of [{task['parent_id']}]")
    if task.get("subtasks", {}).get("total"):
        parts.append(f"{task['subtasks']['done']}/{task['subtasks']['total']} sub tasks done")
    if task.get("due_at"):
        parts.append(f"due {local(task['due_at'], zone_name)}")
    parts.append(f"{task['reminders_sent']} reminder{'s' if task['reminders_sent'] != 1 else ''} sent")
    if task["status"] == "open":
        parts.append(f"next {local(task['next_reminder'], zone_name)}" if task["next_reminder"] else "no reminder to come")
    return "  ·  ".join(parts)


def command(chat: Chat, line: str) -> bool:
    """Run a /command. False: leave."""
    api, zone_name = chat.api, chat.zone
    name, _, argument = line.partition(" ")
    argument = argument.strip()
    now = now_here(zone_name)
    if name in ("/quit", "/exit"):
        return False
    if name == "/new":
        chat.conversation = None
        chat.incognito = "incognito" in argument.split()
        chat.local_only = "local" in argument.split()
        print("New conversation" + (" (incognito)" if chat.incognito else "") + (" (local only)" if chat.local_only else "") + ".")
    elif name == "/resume":
        if argument:
            api.get(f"/v1/conversations/{argument}")
            chat.conversation = argument
            print(f"Going on with {argument}.")
        else:
            found = [c for c in api.get("/v1/conversations", limit=20)["conversations"] if c["client"] == CLIENT]
            print("\n".join(f"{c['id']}  {c['updated_at'][:16]}  {c['title'] or c['preview'][:60]}" for c in found) or "(none)")
    elif name == "/conversations":
        found = api.get("/v1/conversations", q=argument or None, limit=30)["conversations"]
        print("\n".join(f"{c['id']}  {c['client']:<8} {c['updated_at'][:16]}  {c['title'] or c['preview'][:60]}" for c in found) or "(none)")
    elif name == "/facts":
        facts = api.get("/v1/memory/facts")["facts"]
        print("\n".join(f"[{f['id']}] {f['text']}" + ("" if f["confirmed"] else "  (unconfirmed)") for f in facts) or "(nothing yet)")
    elif name == "/remember" and argument:
        print("Stored." if api.post("/v1/memory/facts", {"text": argument})["new"] else "Already known.")
    elif name == "/forget" and argument.isdigit():
        api.delete(f"/v1/memory/facts/{argument}")
        print("Forgotten.")
    elif name == "/remind":
        targets, argument = take_targets(argument)
        words = argument.split()
        repeat = words.pop(0).lower() if words and words[0].lower() in REPEATS else ""
        due, rest = take_when(words, now)
        if not rest:
            raise ValueError("A reminder needs a text.")
        reminder = api.post("/v1/reminders", {"text": " ".join(rest), "at": iso(due), "repeat": repeat,
                                              "timezone": zone_name, "targets": targets})
        print(f"Reminder {reminder['id']} set for {local(reminder['due_at'], zone_name)}.")
    elif name == "/reminders":
        found = api.get("/v1/reminders")["reminders"]
        print("\n".join(f"[{r['id']}] {local(r['due_at'], zone_name)}{' (' + r['repeat'] + ')' if r['repeat'] else ''}  {r['text']}"
                        for r in found) or "(none)")
    elif name == "/unremind" and argument.isdigit():
        api.delete(f"/v1/reminders/{argument}")
        print("Cancelled.")
    elif name == "/tasks":
        status = argument or "open"
        print("\n".join(task_line(t, zone_name) for t in api.get("/v1/tasks", status=status)["tasks"]) or "(no task)")
    elif name == "/task":
        task_command(chat, argument, now)
    elif name == "/notify" and take_targets(argument)[1]:
        targets, text = take_targets(argument)
        api.post("/v1/notifications", {"text": text, "targets": targets})
        print("Sent.")
    elif name == "/model":
        if not chat.conversation:
            print("Start the conversation first (send a message), then choose its model.")
        elif argument:
            ref = "" if argument == "default" else argument
            api.patch(f"/v1/conversations/{chat.conversation}", {"model": ref})
            print(f"This conversation's model: {ref or 'the default'}")
        else:
            models = api.get("/v1/models")
            print(f"Default: {models['default']}\n" + "\n".join(f"  {m['ref']}" for m in models["models"]))
    elif name == "/tools":
        print(tools_text(chat, argument))
    elif name == "/approvals":
        found = api.get("/v1/approvals", status="pending")["approvals"]
        print("\n".join(f"#{a['id']} [{a['level']}] {a['summary']}" for a in found) or "Nothing waits.")
    elif name in ("/allow", "/always", "/refuse", "/never") and argument.isdigit():
        answer = {"/allow": "allow_once", "/always": "allow_always", "/refuse": "refuse_once", "/never": "refuse_always"}[name]
        print(f"#{argument}: {api.post(f'/v1/approvals/{argument}/decide', {'answer': answer})['status']}")
    elif name == "/undo":
        print(api.post("/v1/workspace/undo", {}).get("message", "Done."))
    else:
        print(HELP)
    return True


def tools_text(chat: Chat, argument: str) -> str:
    """The packs of this conversation (a new one starts with web search), after switching one if asked: what is on
    stays on wherever the conversation is continued."""
    api = chat.api
    listing = api.get("/v1/packs", where="chat")["packs"]
    active = api.get(f"/v1/conversations/{chat.conversation}")["packs"] if chat.conversation else ["web"]
    words = argument.split()
    if words:
        match = next((item for item in listing if words[0].lower() in (item["id"], item["label"].lower())), None)
        if match is None:
            return f"No tool called {words[0]!r}: " + ", ".join(item["id"] for item in listing) + "."
        on = (match["id"] not in active) if len(words) < 2 else words[1].lower() == "on"
        active = [item for item in active if item != match["id"]] + ([match["id"]] if on else [])
        if chat.conversation:
            api.patch(f"/v1/conversations/{chat.conversation}", {"packs": active})
        else:
            chat.conversation = api.post("/v1/conversations", {"packs": active, "incognito": chat.incognito,
                                                               "local_only": chat.local_only})["id"]
    return "\n".join(f"{'[x]' if item['id'] in active else '[ ]'} {item['id']:<18} {item['label']}"
                     + ("" if item["available"] else f"  ({item['reason']})") for item in listing)


def task_command(chat: Chat, argument: str, now: datetime) -> None:
    api, zone_name = chat.api, chat.zone
    word, _, rest = argument.partition(" ")
    word, rest = word.lower(), rest.strip()
    if word.isdigit() and not rest:
        task = api.get(f"/v1/tasks/{word}")
        print(task_line(task, zone_name) + f"\nDescription: {task['description'] or '(none)'}")
        for kid in task.get("children", []):
            print("  " + task_line(kid, zone_name))
    elif word in ("add", "sub"):
        parent = None
        if word == "sub":
            number, _, rest = rest.partition(" ")
            if not number.isdigit():
                raise ValueError("Usage: /task sub <id> [due <when>] [remind <when>]... <title> [| <description>]")
            parent, rest = int(number), rest.strip()
        targets, rest = take_targets(rest)
        title, description, due, reminders = parse_task(rest, now)
        task = api.post("/v1/tasks", {"title": title, "description": description, "due": iso(due) if due else None,
                                      "reminders": [iso(at) for at in reminders], "timezone": zone_name,
                                      "targets": targets, "parent_id": parent})
        print(("Sub task added: " if parent else "Task added: ") + task_line(task, zone_name))
    elif word in ("done", "reopen", "delete") and rest.isdigit():
        if word == "delete":
            api.delete(f"/v1/tasks/{rest}")
            print("Deleted.")
        else:
            print(task_line(api.patch(f"/v1/tasks/{rest}", {"status": "done" if word == "done" else "open"}), zone_name))
    elif word == "move":
        number, _, under = rest.partition(" ")
        under = under.strip().lower()
        if not number.isdigit() or not (under.isdigit() or under == "none"):
            raise ValueError("Usage: /task move <id> <parent-id>|none")
        print(task_line(api.patch(f"/v1/tasks/{number}", {"parent_id": int(under) if under.isdigit() else 0}), zone_name))
    elif word == "set":
        number, _, rest = rest.partition(" ")
        field, _, value = rest.strip().partition(" ")
        field, value = field.lower(), value.strip()
        if not number.isdigit() or field not in ("title", "description", "due", "remind") or not value:
            raise ValueError("Usage: /task set <id> title|description|due|remind <value>")
        if field == "due":
            moments = parse_when_list(value, now)
            change: dict = {"due": iso(moments[0]) if moments else ""}
        elif field == "remind":
            change = {"reminders": [iso(m) for m in parse_when_list(value, now)]}
        else:
            change = {field: value}
        print(task_line(api.patch(f"/v1/tasks/{number}", change), zone_name))
    else:
        print(HELP)


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(prog="clara-chat", description="Talk to Clara from a terminal.")
    parser.add_argument("--url", default=os.getenv("CLARA_URL", DEFAULT_URL))
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--save-token", action="store_true", help="store your token (hidden input), then exit")
    parser.add_argument("--conversation", default=None, help="go on with this conversation")
    parser.add_argument("--resume", action="store_true", help="go on with the last terminal conversation")
    parser.add_argument("--incognito", action="store_true", help="a conversation that remembers nothing")
    parser.add_argument("--local-only", action="store_true", help="a conversation that never leaves your machines")
    parser.add_argument("--local-tools", action="store_true", help="let Clara list and read files here (you confirm each)")
    parser.add_argument("--yes", action="store_true", help="with --local-tools: do not ask before each call")
    parser.add_argument("message", nargs="*", help="one question, then exit")
    args = parser.parse_args(argv)
    path = token_file(args.token_file)
    try:
        if args.save_token:
            save_token(path)
            return 0
        api = Api(args.url, read_token(path), CLIENT)
        conversation = args.conversation
        if args.resume and not conversation:
            found = [c for c in api.get("/v1/conversations", limit=20)["conversations"] if c["client"] == CLIENT]
            conversation = found[0]["id"] if found else None
    except ClientError as error:
        print(f"! {error}", file=sys.stderr)
        return 1
    tools = LocalTools(Path.cwd(), confirm=not args.yes) if args.local_tools else None
    chat = Chat(api, local_zone(), conversation, args.incognito, args.local_only, tools)

    def handle(line: str) -> bool:
        try:
            if line.startswith("/"):
                return command(chat, line)
            chat.ask(line)
        except (ClientError, ValueError) as error:
            print(f"! {error}")
        return True

    if args.message:
        handle(" ".join(args.message))
        return 0
    print(f"Clara — /help for commands, Ctrl+D to leave. Times are read in {chat.zone}.")
    notices = Notices()
    stop = threading.Event()
    threading.Thread(target=listen, args=(api, notices, stop), daemon=True, name="events").start()
    try:
        while True:
            try:
                line = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if line:
                with notices.busy():
                    if not handle(line):
                        return 0
    finally:
        stop.set()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
