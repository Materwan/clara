"""`clara-admin`: the admin console over HTTP (history, Tab completion of the commands).

    clara-admin                 the prompt
    clara-admin /status         one command, then exit
    clara-admin --save-token    store a console token (hidden input)

`/secrets set <name>` without a value asks for it hidden and sends it straight to the store: it is never on your
screen, in your shell history or in the console's history.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from .common import DEFAULT_URL, Api, ClientError, read_token, save_token, token_file, utf8_console


def run_line(api: Api, line: str) -> str:
    words = line.strip().split()
    if len(words) == 3 and words[0].lstrip("/") == "secrets" and words[1] == "set":
        value = getpass.getpass(f"Value of {words[2]} (hidden): ").strip()
        if not value:
            return "Nothing changed."
        api.put(f"/v1/admin/secrets/{words[2]}", {"value": value})
        return f"{words[2]}: set"
    return api.post("/v1/admin/command", {"line": line})["output"]


def history_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    path = Path(base) / "clara" / "admin_history"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(prog="clara-admin", description="Clara's admin console.")
    parser.add_argument("--url", default=os.getenv("CLARA_URL", DEFAULT_URL))
    parser.add_argument("--token-file", default=os.getenv("CLARA_ADMIN_TOKEN_FILE"))
    parser.add_argument("--save-token", action="store_true")
    parser.add_argument("command", nargs="*", help="one command, then exit")
    args = parser.parse_args(argv)
    path = token_file(args.token_file)
    try:
        if args.save_token:
            save_token(path)
            return 0
        api = Api(args.url, read_token(path), "console")
        if args.command:
            print(run_line(api, " ".join(args.command)))
            return 0
        names = ["/" + item["name"] for item in api.get("/v1/admin/commands")["commands"]] + ["/help"]
    except ClientError as error:
        print(f"! {error}", file=sys.stderr)
        return 1
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory

    session = PromptSession(history=FileHistory(str(history_file())), completer=WordCompleter(names, sentence=True))
    print("Clara admin console — /help for commands, Ctrl+D to leave.")
    while True:
        try:
            line = session.prompt("clara> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not line:
            continue
        if line.lstrip("/") in ("quit", "exit"):
            return 0
        try:
            print(run_line(api, line))
        except ClientError as error:
            print(f"! {error}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
