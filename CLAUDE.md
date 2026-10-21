# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Clara is a single-user AI agent server (Python 3.12+, FastAPI, SQLite) with a Svelte web client and a separate Discord bot. [README.md](README.md) covers what it is, how to install it and how to run a throwaway dev server; [FEATURES.md](FEATURES.md) is the exact list of tools, routes, settings and limits; [docs/DECISIONS.md](docs/DECISIONS.md) records why things are built the way they are (read it before changing a behaviour it names) and [docs/NON-GOALS.md](docs/NON-GOALS.md) what is deliberately left out.

## Commands

```bash
uv sync --all-extras                                  # venv with dev tools and discord.py
uv run pytest -q                                      # whole suite (~25 s)
uv run pytest -q tests/test_gate.py                   # one file
uv run pytest -q tests/test_gate.py::test_name        # one test (-k "words" also works)
uv run ruff check src tests scripts                   # lint: exactly what CI runs
uv run clara-server --check                           # start-up checks only (settings, permissions, DB, key), then exit
scripts/build-web.sh [--quick]                        # web/ -> src/clara/web/dist, in a pinned Node container (podman or docker)
scripts/build-release.sh v1.0.0                       # builds a release and checks it the way the updater does
CLARA_REQUIRE_SANDBOX=1 uv run pytest -q tests/test_workspace.py -k sees_no_network   # real rootless-Podman test; skipped without Podman
```

- CI runs lint and tests on Python 3.12 and 3.13, the web build, the Podman sandbox test and a release dry run.
- Ruff selects `E,F,I,B,UP,S,ASYNC,G,RUF100` at line length 120. `ASYNC` is on, so blocking calls (`subprocess.run`, `open`) inside an `async def` fail lint: put them in a sync helper. `RUF100` fails on a `noqa` for a rule that is not selected.
- `ruff format` has been applied to `src/` only; `tests/` keeps its original compact layout, so do not format whole test files.
- mypy, pip-audit, pytest-cov and vulture are dev dependencies but not enforced (mypy has ~86 existing errors).
- `src/clara/web/dist/` is the gitignored build output of `web/` (shipped in the wheel and releases). The build and `svelte-check` run in the container script; no Node is assumed on the host.

## Architecture

**One `Services` object, built in two phases.** `app.build_services()` creates the core (db, secrets, tokens, events, memory, time, gate, providers, agent, a toolbox with the memory/time/knowledge tools) and `wire()` connects the parts that call each other. `app.extend()` then calls each optional subsystem's `install(s)` (workspace + sandbox, connectors, vault, packs, triggers, ops), which adds its tools to `s.toolbox` and sets its `Services` attribute, `None` when that part is off. Tests need both calls (see the `services` fixture in `tests/conftest.py`). The API layer (`api/*.py`), the agent and the tools only ever receive `Services`.

**Every capability is a `Tool`, and every tool call goes through the rule gate.** A `Tool` (`tools/base.py`) pairs a function with a `describe()` that returns an `Action` (tool, level, action class, `prefix:target` targets, summary) *before* anything runs. `Toolbox.call` asks `gate.decide(action, situation)`: floor (non-overridable) → user rules (most specific wins) → default of the action class (`gate/actions.py`, different for chat vs unattended) → raises that can only make it stricter (tainted turn, secrets leaving). The result is run, refused, or held as an approval request. A new tool needs a sensible class/level/target, not just a function. Tool failures come back to the model as text, never as a crashed turn; tools not offered in a turn are not run whatever the model asks.

**Approvals have two possible executors; exactly one must run an approved action.** In a chat, the turn holds its requests from the moment they are created (`TurnState.waits_for_approvals`, `Approvals.hold`) so `decide()` hands the answer to the turn; with nobody holding it (unattended, client gone), `decide()` runs the action itself and a follow-up turn tells the model. `Approvals.release` runs from the turn's `finally`.

**`Agent.turn()` (`agent/turn.py`) is the single path for all conversation**, from any client and from the server's own unattended runs (reminders, tasks, schedules, hooks, approval follow-ups). It yields event dicts; streaming clients forward them, `Agent.answer()` runs a turn to its end and returns the reply. Prompts are never silently truncated (summarise, then drop with a `warning` event, else `PromptTooLarge`). `mode="unattended"` turns never wait for approvals.

**Time-driven services share one loop shape** (`timekeeping/dueloop.py`): reminders, tasks, schedules and triggers each fire what is due, sleep until the next item, and catch their own errors; the systemd watchdog is fed only while every loop turns. `triggers/` (hooks) write events, planned runs and the source cursor in one transaction so a crash neither loses nor repeats work.

**Persistence.** One SQLite connection (`db/connection.py`), shared by the event loop and worker threads behind a lock; call it directly from async code, and use `transaction()` (BEGIN IMMEDIATE) for writes that must stand together. Schema changes are append-only numbered files in `db/migrations/` (`PRAGMA user_version`; a backup is taken first; a DB newer than the code is refused). Three separate stores of configuration, never mixed: `settings.py` (env / `.env`, read once, strict, non-secret), `runtime.py` (live knobs in the `settings` table, changed from the console/Admin page; unknown keys refused), and `secrets/` (AES-GCM in the DB, key from a systemd credential).

**The workspace is a tree that sandboxed scripts write into, so the host treats it as untrusted.** `workspace/store.py` keeps it as a git repo with a commit per change; `workspace/paths.py` is the only door to files (no links, no `.git`, regular files only, reads capped at `CLARA_WORKSPACE_MAX_FILE`); every host git call passes `-c core.fsmonitor=false`. `workspace/sandbox.py` runs scripts in rootless Podman with an empty environment and no fallback to a plain process. Apply the same suspicion to anything else that reads files or archives a script or an attacker could shape (`knowledge/ingest.py` counts real bytes, not zip headers).

**Processes.** systemd starts `clara-launcher` (`ops/launcher.py`), which runs the release the `current` link points to and handles updates and rollback through exit codes (75 = update ready, 76 = restart). `clara-server` binds 127.0.0.1 only (Tailscale serves it). The Discord bot (`src/clara_discord/`) is a separate process and an ordinary API client with its own token; the server can spawn it. Auth is enforced in `api/middleware.py` before routing, so a new `/v1` route is protected by default.

## Conventions that bite

- **FEATURES.md is tested against the code** (`tests/test_docs.py`). Adding a tool, route, `CLARA_*` setting, console command, secret, runtime knob, action class, event kind or alert without naming it in FEATURES.md (in backticks) fails the suite, and the tool count line (`N tools.`) must be updated.
- Tests use `FakeLLM` (`tests/fakes.py`: scripted replies, `calls(("tool", {...}))` for tool calls) and `tests/mailworld.py` (a stand-in Gmail). The fixtures set `CLARA_RUN=off` and `CLARA_APPROVAL_WAIT=2`. `asyncio_mode = auto`; an unraisable exception (a leaked or un-awaited task) is configured to fail the test.
- Subprocesses are always argv lists, never a shell. A child that is awaited with a timeout must be stopped on timeout *and* on cancellation (see `ops/backups.py::_stop`).
