# Clara

Clara is a personal AI agent for **one person**, running on **one Linux machine**, reachable **only from that
person's own devices over Tailscale**. She chats from a web site, a terminal, an admin console, a Discord server of
your own or your own scripts; she remembers what matters, keeps your reminders, tasks and scheduled jobs, reads and
writes files in her own workspace, runs scripts in isolated containers, and works with GitHub, Google Drive, your
Gmail, your Obsidian vault and the web. Every action she takes goes through rules you set: allowed, asked, or refused.

There are no accounts, no users, no credits and no public exposure: Clara 1.0 is a rewrite from zero of an earlier,
broader server, following [the rebuild plan](clara-rebuild-plan.md) and answering
[its critical review](docs/REVIEW-RESPONSE.md).

- **What it does, precisely:** [FEATURES.md](FEATURES.md) (every tool, route, setting, rule default, alert and limit;
  a test keeps it in step with the code).
- **What it deliberately does not do:** [docs/NON-GOALS.md](docs/NON-GOALS.md).
- **Running it:** [docs/OPERATIONS.md](docs/OPERATIONS.md).
- **Security:** [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md), [docs/DATA-MAP.md](docs/DATA-MAP.md),
  [SECURITY.md](SECURITY.md).
- **Why it is built this way:** [docs/DECISIONS.md](docs/DECISIONS.md).
- **How the web site is drawn:** [docs/WEB-DESIGN.md](docs/WEB-DESIGN.md) (tokens, components, accessibility rules).
- **Model providers and their terms:** [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **Changes:** [CHANGELOG.md](CHANGELOG.md).

## How it fits together

```text
  Web site        clara-chat       clara-admin      your scripts     clara-discord
  (Svelte)        (terminal)       (console)        (HTTP API)       (Discord, own process)
       |               |                |                |                 |
       +---------------+--------- one token each --------+-----------------+
                                        |
                  tailscale serve: HTTPS inside your tailnet only (Funnel refused)
                                        |
  +-------------------------------------v----------------------------------------+
  | clara-server on 127.0.0.1, started by clara-launcher (updates and rollback)   |
  |                                                                              |
  |  token check -> agent (prompt, memory, compaction) ----> model providers     |
  |                    |                                     Ollama (local, PC), |
  |                    v                                     Ollama cloud,       |
  |        RULE GATE: allow / ask / refuse every tool call   Gemini, DeepSeek,   |
  |        (approvals, audit log, tainted turns)             Mistral             |
  |                    |                                                         |
  |                    v                                                         |
  |   tools: memory, time, projects, files, forms, vault, web (Brave, SearXNG),         |
  |          GitHub, Drive, Gmail, workspace, scripts (rootless Podman)          |
  |   time: reminders, tasks, schedules -> unattended turns through the gate     |
  |   ops: events, alerts, backups (SQLite + restic), traffic and usage logs     |
  +---------+---------------------------+---------------------------+------------+
            |                           |                           |
     SQLite (WAL, FTS5)          workspace: a git repo        secrets: AES-GCM,
     /var/lib/clara              (a commit per change)        key in a systemd credential
```

A message arrives with a client token. Clara builds the prompt (her fixed persona, your facts, the conversation, the
project), calls the model and streams the answer. Each tool call is checked by the rule gate: it runs, waits for your
answer (in the chat, on another client, or as Discord buttons), or is refused. Reminders and schedules start turns
from inside the server, through the same gate, as "unattended".

## Requirements

- A Linux machine with systemd (Ubuntu 24.04 or later tested), always on.
- [Tailscale](https://tailscale.com) on it and on your devices.
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for Clara), `git`.
- Rootless [Podman](https://podman.io) for scripts (cgroup v2; `clara doctor` checks it). Without it, scripts stay
  off; everything else works.
- Models: [Ollama](https://ollama.com) on this machine or another of your tailnet, and/or keys for Ollama cloud,
  Gemini, DeepSeek, Mistral or OpenCode Go.
- Optional: a SearXNG instance (`deploy/searxng/`), [restic](https://restic.net) and another tailnet machine for remote
  backups, a Google Cloud OAuth client for Drive and Gmail, a GitHub fine-grained
  token, a Discord bot.

## Install

From a release (built by `.github/workflows/release.yml`):

```bash
curl -LO https://github.com/<owner>/clara/releases/download/v1.0.0/clara-v1.0.0.tar.gz
mkdir clara-v1.0.0 && tar -xzf clara-v1.0.0.tar.gz -C clara-v1.0.0
sudo clara-v1.0.0/deploy/install.sh clara-v1.0.0.tar.gz
```

The installer creates the `clara` system user (with what rootless Podman needs), `/opt/clara/releases/<tag>` and the
`current` link, `/etc/clara/clara.env`, the secrets key as an encrypted systemd credential, the systemd units, makes
`clara` the Tailscale operator, prints three first tokens (console, terminal, web) and runs `clara doctor`. Then:

1. Edit `/etc/clara/clara.env` (your zone, your Ollama hosts, `CLARA_RELEASE_REPO`). Every setting is in
   [FEATURES.md](FEATURES.md#14-settings).
2. Pull or build the sandbox image as `clara` (`clara doctor --pull`, or `podman build -t localhost/clara-sandbox:latest sandbox/`).
3. `sudo systemctl enable --now clara`, then open `https://<machine>.<tailnet>.ts.net` and paste the web token.

The full procedure, with SearXNG, Drive, GitHub, the vault, Discord and backups, is in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## First steps

```bash
clara-chat --save-token                  # paste the terminal token once (kept 0600 in ~/.config/clara/token)
clara-chat                               # chat; /help lists the commands
clara-chat "what is on my list today?"   # one question, then exit
clara-admin --save-token                 # the console token; then /status, /models, /rules, /approvals…
```

On the web site, under **Settings**: choose your default model on **Models**, look at **Rules & approvals** to see
what Clara may do alone, connect GitHub, Drive and Gmail on **Connections** if you want them.

## Development

```bash
uv sync --all-extras                     # Python 3.12 virtualenv with the dev tools and discord.py
uv run pytest -q                         # the test suite (a Podman test runs when rootless Podman is there)
uv run ruff check src tests scripts      # lint
scripts/build-web.sh                     # the web site, in a pinned Node container (svelte-check, then vite build)
scripts/build-release.sh v1.0.0          # a release, checked the way the updater checks it
```

Running a development server against a throwaway folder:

```bash
export CLARA_DATA_DIR=$PWD/.dev/data CLARA_TAILSCALE=off CLARA_RUN=off CLARA_ENV_FILE=$PWD/.dev/env
mkdir -p .dev/data && chmod 700 .dev .dev/data && touch .dev/env && chmod 600 .dev/env
uv run clara secrets new-key > .dev/key && chmod 600 .dev/key && export CLARA_SECRET_KEY_FILE=$PWD/.dev/key
uv run clara token create dev web        # prints the token
uv run clara-server                      # http://127.0.0.1:8765
```

| Folder | What is there |
| --- | --- |
| `src/clara/` | The server: `app.py` (wiring), `settings.py`, `db/` (schema, migrations, lock), `secrets/`, `auth/`, `llm/` (providers, catalogue, router), `agent/` (turn loop, prompt, compaction, model runner), `memory/`, `knowledge/` (projects, files, vault), `timekeeping/`, `triggers/` (hooks: what Clara does when a mail arrives, or a pull request or a push is made), `events/` (the event store and which Discord channel carries each kind), `gate/` (rules, approvals, audit), `tools/`, `workspace/` (files, git, sandbox), `connectors/` (web, GitHub, Drive, Gmail, forms), `api/`, `ops/` (lifecycle, Tailscale, backups, updater, launcher, logs, alerts), `cli/` |
| `src/clara_discord/` | The Discord bot |
| `web/` | The Svelte site (built into `src/clara/web/dist`) |
| `tests/` | The test suite (a fake model provider in `fakes.py`) |
| `deploy/` | Installer, systemd units, settings example, SearXNG |
| `sandbox/` | The script container image |
| `scripts/` | Web and release builds, release signing, the refresh of the OpenCode model table |
| `docs/` | Decisions, web design system, review response, threat model, data map, non-goals, providers, operations |

## Licence

All rights reserved: see [LICENSE](LICENSE). The source is public to read; no permission to use, copy, modify or
distribute it is granted.
