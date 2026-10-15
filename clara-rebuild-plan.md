# Clara rebuild plan: a personal AI agent

14 October 2026 · Erwan Mercier

## Goal and decisions

The new Clara is one agent for one person, on one Linux machine, reachable only over your Tailscale network, with no accounts, no credits and no server bot. It is rebuilt from zero in Python, porting the proven modules of the current code after trimming them.

| Topic | Decision |
| --- | --- |
| Who uses it | You alone. No users, passwords, sessions, link codes, owner role or credit limits |
| Network | Tailscale only, published with `tailscale serve` (never Funnel); the server listens on 127.0.0.1 |
| Authentication | A secret token on every request (one per client, stored hashed, revocable), checked in constant time. You left this choice to me: Tailscale is the perimeter, the token is the second lock against other processes and other tailnet devices |
| Host | One Linux machine, one systemd service; scripts Clara runs go through rootless containers |
| Backend | Python 3.12, FastAPI, SQLite in WAL mode; modules ported from the current code where they hold up |
| Web site | Svelte with Vite, English only |
| Clients | Web site, terminal chat, admin console, HTTP API for your scripts, and a Discord bot that answers only your own user id in DMs |
| Providers | Local Ollama (on the Linux machine and on your Windows PC over Tailscale), Ollama cloud, Gemini, DeepSeek, Mistral |
| Persona | A fixed system-prompt file; no relationship score |
| Spending | Token statistics only; no caps, no credits |
| Alerts | Inside the clients, plus Discord DMs to you |
| Secrets | Encrypted store in the database; the key lives outside the data folder, with a salted slow derivation and a rotation command |
| Logging | Traffic log records metadata only; full content only while a debug switch is on |
| Updates | Tagged releases only, checked before the old process stops, with automatic rollback |
| Backups | Rotating local SQLite backups, plus an encrypted copy to another machine on your tailnet |
| Existing data | Nothing is migrated; the new Clara starts empty |

## Scope

About 3,500 of the current 30,800 source lines disappear outright, about 2,700 more (the Discord bot) are rewritten as a separate small process, and the rest is either ported or rewritten without the idea of a person.

### Kept

- **Chat engine:** the tool loop with streaming events, parallel tools, client-side tools, prompt fitting that never truncates silently, retries and model fallbacks.
- **Memory:** one global memory shared by every client and conversation: facts, conversation history with titles, automatic summaries, and a per-conversation "remember nothing" mode.
- **Time:** reminders (once, daily, weekly, monthly), tasks with sub-tasks and reminders Clara picks, scheduled prompts that run unattended, and notifications.
- **Knowledge:** projects (files, zips, GitHub repositories), markdown files, a slim second brain (Obsidian vault with git sync), and chat attachments (PDF, Word, pictures).
- **Outside world:** GitHub, Google Drive, web search and page fetching, forms (the `qcm` tool), and the approval step for risky actions.
- **Operations:** `tailscale serve` publishing, restart and update from the console, the traffic log, token usage statistics, the self-test.
- **Clients:** web site, terminal chat, admin console (a prompt over HTTP and a web Admin page), and the HTTP API.

### Removed

- **Accounts:** users, passwords, sessions, the owner role, link codes, account linking, aliases, spaces and rosters, per-user provider keys and model choices, per-person erasure.
- **Credits:** weights, daily limits, quotas, and the exemption for users on their own key.
- **Discord as a platform:** the built-in bot, servers, chime-in, observe mode, mentions, GIF replies, `/register` and `/login`.
- **Music:** the Music Assistant tools, the music site and its WebSocket relay.
- **Relationship score:** the hidden mood and the `adjust_relation` tool.
- **Old file access:** the `res_*` server-folder tools and the desktop-app "computer folders" connector.
- **Heavy vault parts:** semantic search with embeddings and most of the 20 `vault_*` tools.
- **Public exposure:** Funnel mode, web sign-up, and the shared GitHub token.

### New

- **Rule system:** every action resolves to allow, ask or refuse through editable rules (section 4).
- **Workspace:** a dedicated folder where Clara reads, writes and runs scripts in rootless containers, with a git commit for every change (section 5).
- **Discord DM bot:** a separate process that answers only your user id.
- **Safer foundations carried over from the review:** a lock so two servers cannot share one data folder, schema versions with a backup before each migration, reminder clocks always stored as zone names, a total time budget per answer, and a separate model-slot pool for background work.

## Architecture

Clara is one server process on your Linux machine, plus the small Discord bot as a second process. The server listens only on `127.0.0.1`; Tailscale publishes it to your devices and nothing else can reach it.

```text
  Web site        clara-chat       clara-admin      Scripts        clara-discord
  (Svelte+Vite)   (terminal)       (console+pages)  (HTTP API)     (DM-only, own process)
       |               |                |              |                 |
       +---------------+----------------+--------------+-----------------+
                                        |
                                        v
              tailscale serve: HTTPS inside your tailnet only, never Funnel
                                        |
  +-------------------------------------v---------------------------------------+
  | Clara server: one process, listening on 127.0.0.1                            |
  |                                                                              |
  |   Auth ------> Agent -------------------------------------> Model providers  |
  |   hashed       prompt, memory,                              Ollama local and |
  |   token per    compaction,                                  cloud, Gemini,   |
  |   client,      model fallbacks                              DeepSeek, Mistral|
  |   limiter         |                                                          |
  |                   v                                                          |
  |            RULE GATE: allow, ask or refuse every tool call                   |
  |            (approvals, audit log)                                            |
  |                   |                                                          |
  |                   v                                                          |
  |                 Tools ------------------------------------> Web search       |
  |            memory, projects, vault,                          (SearXNG)       |
  |            forms, workspace tools -------------------------> GitHub, Drive,  |
  |                                                              Obsidian vault  |
  |   Time: reminders, tasks, schedules, notifications                           |
  |         (they start turns from inside, through the same gate)                |
  |   Operations: updater, backups, alerts, traffic and usage logs               |
  +----------+-------------------------+-------------------------+---------------+
             |                         |                         |
          SQLite                   Workspace                  Secrets store
       (WAL, FTS5,              (git repo, a commit        (AES-GCM; the key lives
        nightly backups)         per change; scripts        outside the data folder)
                                 run in rootless Podman)
```

A message enters through Tailscale from any client and is checked for its token. The agent builds the prompt, calls the model and may call tools. Every tool call goes through the rule gate, which allows it, asks you, or refuses it. Reminders and schedules start turns from inside the server and use the same gate, with the situation set to unattended.

### Processes

| Process | Role | Runs as |
| --- | --- | --- |
| `clara-server` | FastAPI app, agent loop, scheduler, rule gate, updater | systemd service, with `Restart=on-failure` and a watchdog |
| `clara-discord` | DM-only bot; talks to the server over the HTTP API with its own token | separate systemd service |
| Script container | One rootless Podman container per script run, removed afterwards | started by the server |
| SearXNG | Self-hosted web search | its own service on the same machine |
| Ollama | Local models | this machine, or your PC reached over the tailnet |

### What lives where

| Place | Content |
| --- | --- |
| `/var/lib/clara/` | SQLite database, rotating backups, and `workspace/`, the git repository Clara works in |
| `releases/<tag>` and a `current` link | The program versions; an update switches the link |
| A systemd credential | The key that opens the encrypted secrets store, kept outside the data folder |
| `.env` | Non-secret settings only, checked for permissions at start |
| Log files | Metadata-only traffic log; the debug content log is a separate `0600` file that expires after one hour |

### One message, step by step

1. A client sends the message with its token over HTTPS on the tailnet.
2. The server checks the token, then builds the prompt from the fixed persona, the global memory, the conversation and the project in use.
3. The default model answers; if it fails, the next enabled provider in your fallback list is tried (a local model never falls back to a hosted one).
4. Each tool call goes through the rule gate: it runs, waits for your answer, or is refused.
5. The reply streams back as events: turn, token, tool, approval, usage and done.
6. The server saves the messages, writes a `usage_log` row with token counts only, and compacts the conversation when it is long.

### Technology

| Part | Choice |
| --- | --- |
| Language | Python 3.12 |
| Server | FastAPI, one process |
| Database | SQLite in WAL mode with FTS5, versioned by `PRAGMA user_version` |
| Web client | Svelte with Vite, built at release time, English only |
| Terminal clients | Python, ported from the current `client.py` and console |
| Scripts | Rootless Podman, pinned `clara-sandbox` image |
| Secrets | AES-GCM through `cryptography`, key derived with scrypt |
| Search | SearXNG, self-hosted |
| Network | `tailscale serve`, never Funnel |
| Process manager | systemd |
| Backups | SQLite `backup()`, then restic or an encrypted copy to another tailnet machine |
| Models | Ollama (local and cloud), Gemini, DeepSeek, Mistral |

## Access, secrets and the rule system

Every request must pass three locks in order: the Tailscale network, a client token, and then, for every side effect, the rule system. The current code already has the right shape for the last lock (one broker that every outside action goes through, three levels read / write / destructive, three answers allow / ask / deny, and a rule that the model has no tool to approve its own requests). The rebuild keeps that shape and widens what a rule can match.

### Network and token

- **Listener:** the server binds 127.0.0.1 only. At start it runs `tailscale serve` for its port and refuses to start if Funnel is enabled for it. The port is never opened on any other interface.
- **Tokens:** one random 256-bit token per client (terminal, console, web, Discord bot, each script). They are not accounts: all have the same power, each is stored as a hash with a name and a last-used time, and each can be revoked from the console at once, without a restart (today a client token cannot be revoked until the server restarts).
- **Checks:** every route except `/health` needs `Authorization: Bearer <token>`, compared in constant time. `/health` returns `{"status":"ok"}` and nothing else. A small failure limiter blocks an address after repeated wrong tokens; forwarded addresses are trusted only from 127.0.0.1.
- **Web site:** you paste a token once on a sign-in page; the server answers with an HttpOnly, Secure, SameSite=Strict cookie, and the web client must also send an `X-Clara-Web: 1` header, so another site cannot post to it. CORS stays off.
- **Surface hardening:** `/docs` and the OpenAPI file are off unless a debug switch is on.

### Secrets

- **Where:** provider keys, the GitHub token, the Google refresh token and the Discord bot token live encrypted in the database (AES-GCM through the `cryptography` package already used). The API never returns them, only "set" or "not set".
- **Key:** it comes from a systemd credential or an environment variable, never from the data folder. A passphrase goes through scrypt with N=2^17 and a random salt; a raw 32-byte key is also accepted. `clara secrets rotate` re-encrypts everything under a new key.
- **Files:** `.env` holds non-secret settings only. The server checks at start that `.env`, the data folder and the key file are readable by the service user alone.

### The rule system

A rule says allow, ask or refuse for a matching action. The model never reads or edits rules, and no tool lets it approve a request.

| Field | Meaning | Example |
| --- | --- | --- |
| Effect | `allow`, `ask` or `refuse` | `ask` |
| Tool | A tool name or glob | `ws_delete`, `github_*` |
| Target | A workspace path, a repository, a Drive folder or a web domain | `scratch/**`, `github:me/clara`, `domain:*.wikipedia.org` |
| Situation | `interactive`, `unattended`, `tainted` or any | `unattended` |
| Level | `read`, `write` or `destructive`, set by the tool | `destructive` |

- **Matching:** the most specific matching rule wins (more fields set, then the longer literal target). On a tie the stricter effect wins: refuse, then ask, then allow.
- **Built-in floor:** some refusals cannot be overridden by any rule: writing outside the workspace, reading the database, secrets or `.git`, and running a script with the server's own environment.
- **Defaults when no rule matches:** read is allowed; write inside the workspace is allowed; destructive and outside-world changes ask.
- **Unattended runs:** an `ask` never blocks a turn. The request is stored, you are alerted, and the run goes on without that action. A request expires after 24 hours and Clara is told it was not done.
- **Tainted turns:** once a turn has read untrusted content (a web page, a fetched file, a GitHub issue), any write outside the workspace or any script that asks for the network is raised to at least `ask`, whatever the rules say. This is a recommendation; it can be switched off.

| Action class | Default (chat) | Default (unattended) |
| --- | --- | --- |
| Read workspace, projects, vault, memory | allow | allow |
| Create or edit workspace files | allow (git history) | allow (git history) |
| Delete or overwrite workspace files | ask | ask |
| Run a script, no network | allow | ask |
| Run a script with network | ask | ask |
| GitHub read | allow | allow |
| GitHub issue, branch, pull request, commit | ask | ask |
| Drive read (files Clara created or you opened to her) | allow | allow |
| Drive create, update, delete | ask | ask |
| Web search | allow | allow |
| Fetch a URL you wrote or a search returned | allow | allow |
| Fetch any other URL | ask | refuse |

- **Answering an `ask`:** the prompt offers allow once, allow always, refuse once, refuse always. The two "always" answers write a rule, so the rule list grows only through your decisions.
- **Where you edit rules:** an Admin console page with the rule list, an edit form, the pending requests, and a test box (type an action, see which rule decides). The terminal admin console gets the same through `/rules` commands.
- **Audit:** every decision is written to `audit_log` with the tool, a digest of its arguments, the rule id and the outcome.

## The workspace

The workspace is one dedicated folder on the Linux machine, owned by the service user and managed as a git repository. It is the only part of the machine where Clara can write or run anything, and every change in it can be undone.

### Files and history

- **Location:** `/var/lib/clara/workspace`, created at first start with `git init`. Size limit 5 GB, single file limit 100 MB, both configurable.
- **Every change is a commit:** each write, edit, move or delete by a tool is committed at once with the tool name, a one-line summary and the turn id. Changes a script makes are committed after the run as one commit. `/undo` and a console button revert any commit.
- **Path safety:** a path is joined to the workspace root, then resolved; absolute paths, `..`, and any symlink on the way are refused, and files are opened with `O_NOFOLLOW`. The `.git` folder is not reachable by any tool or script.

### Tools Clara gets

| Tool | What it does | Level |
| --- | --- | --- |
| `ws_list`, `ws_read`, `ws_search` | Look at the tree, read a file (cut to the context budget), search text | read |
| `ws_write`, `ws_edit` | Create or replace a file, or replace an exact string in it | write, or destructive when it overwrites |
| `ws_move` | Rename or move inside the workspace | write, or destructive when the target exists |
| `ws_delete` | Delete a file or folder | destructive |
| `ws_run` | Run a command or script in a container | write; `network: true` is a separate target |
| `ws_log` | Show the git history of a path | read |

These nine tools replace the old `res_*` family, which reached folders you listed on the server.

### Running scripts

- **Isolation:** each run is one rootless Podman container, started with `--rm`, no network, a read-only root filesystem, all capabilities dropped, `no-new-privileges`, a fixed non-root user, `--pids-limit`, a memory limit, a CPU limit and a wall-clock timeout. Only the workspace is mounted, read-write, and an empty tmpfs hides `.git`.
- **Image:** a pinned `clara-sandbox` image with Python, bash and common libraries, built by the release process. Another image can be named in the settings.
- **Environment:** empty. No secret, token or host path is ever passed in.
- **Output:** the model gets the exit code and the first 64 KB of output; the full log is kept as a file in the workspace.
- **Limits (defaults, configurable):** 120 s per run (900 s at most), 1 GB memory, 2 CPUs, 2 runs at once, from a pool separate from the model slots.
- **Network:** off by default. A run that asks for it goes through the rule system as its own target, and it is `ask` by default.
- **Check:** `clara doctor` verifies that rootless containers work (subuid and subgid ranges, cgroup limits) before the feature is switched on.

## Data model

The current code defines 37 tables (the vault index included), and 15 of them exist only for people, accounts, credits, music, the desktop app, per-conversation attachments or embeddings. The new schema has about 26, in one SQLite file, and no table has a `person_id` column any more (the word "person" appears about 2,100 times in 75 current source files, so this is the largest single piece of surgery).

### Conventions

- **One file, WAL mode, foreign keys on.** Timestamps are UTC ISO text. Times a person meant on the wall clock (reminders, schedules) are stored with an IANA zone name such as `Europe/Paris`, never a bare offset, so daylight-saving changes cannot shift them.
- **Migrations:** numbered SQL files, the version kept in `PRAGMA user_version`. Before any migration the server takes a backup, and it refuses to open a database whose version is newer than its code.
- **One writer:** a lock file in the data folder (`flock`) stops a second server from opening the same folder.
- **Deletion:** `secure_delete` is on, and a purge command vacuums the file.
- **Search:** SQLite FTS5 indexes facts, the vault and markdown files, replacing the substring match and the embeddings index.

### Tables

| Table | Holds | Change from today |
| --- | --- | --- |
| `facts` | Text, category, pinned, source (you, model, tool), conversation, created, confirmed, expires, superseded by | Adds provenance, expiry and supersede; the `remember` tool marks tool-derived facts unconfirmed until you confirm |
| `conversations`, `messages`, `conversation_state`, `conversation_files` | History, titles, summaries, attached files, the model of the conversation, an `ephemeral` flag | `person_id`, `surface` and spaces removed; a client label stays |
| `projects`, `project_files`, `project_sources` | Uploaded files, zips, GitHub snapshots | `person_id` removed |
| `markdown_files` | Notes Clara writes | `person_id` removed |
| `reminders`, `tasks`, `task_reminders`, `schedules`, `schedule_runs` | Time features | Zone names only; `person_id` removed; runs get their own table |
| `events` | Reminders fired, notifications, server states, approval requests, with a per-client read cursor | Merges `reminder_events` and the notification stream; kept until read, not pruned after 7 days |
| `approvals` | Frozen action, state, expiry, who answered from which client | Ported from the broker |
| `rules` | Effect, tool, target, situation, level, origin (console or approval), hits | New |
| `audit_log` | Every gate decision and its outcome | Replaces `integration_log` |
| `connections`, `resources` | GitHub and Drive connections, and the repositories and folders you added | `integration_accounts`, `integration_resources` without people |
| `secrets`, `tokens` | Encrypted values; hashed client tokens | New |
| `models`, `settings` | Catalogue of models; key-value settings | `options` and `models` without weights or per-person choices |
| `usage_log` | One row per model call: provider, model, kind, tokens in, out and cached, duration, outcome | Kept; no credits |
| `ws_runs` | One row per container run: command digest, exit code, duration, network flag, commit | New |

### Dropped tables

`people`, `person_aliases`, `accounts`, `account_logins`, `users`, `sessions`, `spaces`, `usage` (the credit counters), `user_api_keys`, `user_music`, `model_choices`, `integration_jobs`, `integration_attachments`, and the embeddings tables `chunks` and `indexed`.

## Components and clients

### Server packages

| Package | Role | Built from |
| --- | --- | --- |
| `settings`, `app` | Typed settings, strict start-up checks, app factory, lifespan | Rewrite of `settings.py` (495 lines), `server.py` (517) |
| `auth` | Tokens, web cookie, failure limiter | New, from `auth.py`, `ratelimit.py` |
| `secrets` | Encrypted store, rotation | From `integrations/vault.py` (67) |
| `db` | Connection, migrations, repositories | Rewrite of `memory.py` (1,966) |
| `llm` | Providers, streaming, retries, model catalogue, fallbacks | Port of `providers.py`, `llm.py`, `retry.py`, `models.py`, `modelapi.py` |
| `agent` | Turn loop, prompt, compaction, titles, time budget, model slots | Rewrite of `agent.py` (1,545), `prompt.py`; port of `compaction.py` |
| `tools` | Tool registry and toolsets | Split of `tools.py` (1,361) |
| `gate` | Rules, decisions, approvals, audit | Evolved from `integrations/permissions.py`, `broker.py`, `approvals.py` |
| `workspace` | Files, git history, container runner | New |
| `timekeeping` | Due loops, reminders, tasks, schedules, announcements | Port of `dueloop.py`, `reminders.py`, `tasks.py`, `taskstore.py`, `taskai.py`, `schedule.py`, `announce.py` |
| `events` | Event store, SSE stream, channels | Port of `notifications.py` |
| `knowledge` | Projects, ingest, attachments, markdown files, slim vault, conversation files | Port of `projects.py`, `ingest.py`, `attachments.py`, `markdownfiles.py`, `conversationfiles.py`; slim rewrite of `vault/` |
| `connectors` | GitHub, Google Drive, web (SearXNG), forms | Port of `github_live.py`, `gdrive.py`, `github.py`, `qcm.py`; rewrite of `web.py` |
| `api` | One router per area, no `user_id` or `surface` fields | Trim of the `*api.py` files |
| `ops` | Lifecycle, updater, backups, Tailscale, self-test, traffic log, usage log | Port of `lifecycle.py`, `tailscale.py`, `selftest.py`, `traffic.py`, `usagelog.py`, `private.py`; rewrite of `restart.py` |
| `cli` | `clara` (operations), `clara-chat`, `clara-admin` | Port of `client.py`, `admin.py`, `console.py`, `commands.py` |

### API conventions

- **No identity fields:** requests no longer carry `user_id`, `user_name` or `surface`. A header `X-Clara-Client` (web, cli, console, discord, script) labels where a message came from, for the prompt and the logs.
- **Events kept:** `turn`, `token`, `tool`, `tool_requests` (client-side tools), `usage`, `compacted`, `warning`, `approval`, `done`.
- **Versioning:** everything under `/v1`; errors are `{"error": {"code", "message"}}`; the OpenAPI file is generated into the repository, not served in production.

### Clients

- **Web site:** a Svelte app built with Vite, English only. Pages: Chat (streaming, attachments, forms, inline approvals), Conversations, Memory, Tasks and Reminders, Schedules, Projects, Notes, Vault, Workspace (file tree, git log, undo, run logs), Connections, Rules and approvals, Models, Usage, Admin. The release process builds it; the Linux machine needs no Node. Files get hashed names and a strict Content-Security-Policy.
- **Terminal chat:** `clara-chat`, ported from `client.py` (730 lines) without the login: it reads a token from a file readable by you alone. It keeps `/remind`, `/task`, the notification listener and the client-tools protocol.
- **Admin console:** `clara-admin` is the same slash-command prompt over HTTP (status, provider and model, fallbacks, tokens, rules, approvals, backup, update, restart, stop, debug switch), plus the Admin pages of the web site.
- **HTTP API for scripts:** the same API with a script token and the SSE event stream; the generated OpenAPI file is the contract.
- **Discord DM bot:** `clara-discord`, a separate process and a separate client token. It connects with direct-message intents only (no guild messages, no member list, no message-content access in servers), ignores every user id except yours, answers in DMs, sends approval requests with buttons, and relays alerts from the event stream. Its Discord token is kept in its own credential, not in Clara's database.

## Build phases

The order puts the dangerous parts before the useful ones: the rule gate and the workspace sandbox are finished before any connector can change anything outside the machine. There are no dates. You set the pace, and a failed gate sends the phase back.

```text
Phase 1  Foundations and chat
         Schema, secrets store, models, agent loop, token auth, /v1 API, terminal chat
   <> Gate 1: a streamed chat works from the terminal; an old build refuses a newer database
   |
   v
Phase 2  Memory, knowledge and time
         Facts, compaction, projects, files, forms, reminders, tasks, schedules, notifications
   <> Gate 2: reminders fire on time across a clock change; a fact keeps its source and can be superseded
   |
   v
Phase 3  Rule gate and workspace
         Rules, approvals, audit, workspace tools, git history, rootless Podman runs
   <> Gate 3: a script sees no network, no .git, no host files; an unanswered ask never runs the action
   |
   v
Phase 4  Web site and admin
         Svelte site, Admin pages, rules page, pending requests, usage statistics
   <> Gate 4: you can answer an approval and edit a rule from the browser alone
   |
   v
Phase 5  Connectors and Discord
         GitHub, Drive, SearXNG, Obsidian vault, DM-only Discord bot and alert relay
   <> Gate 5: GitHub and Drive changes always ask first; the bot ignores every Discord id but yours
   |
   v
Phase 6  Operations and cut-over
         tailscale serve, backups, tagged updates, alerts, clara doctor, restore drill
   <> Gate 6: update and rollback rehearsed; a backup restores on a clean machine; Funnel blocks start
```

Each phase ports its modules together with their tests. Of the 1,198 current tests, 294 cover removed features and are not carried over.

| Phase | What gets built | Ported from | Exit criteria |
| --- | --- | --- | --- |
| 1. Foundations and chat | Project layout, settings, schema v1 with versioned migrations and a backup before each one, instance lock, encrypted secrets store, provider layer with fallbacks, agent loop, per-client tokens and failure limiter, `/v1` API with events, `clara-chat` | `llm`, `providers`, `retry`, `models`, trimmed `agent`, `client`, `session` | A streamed chat works from the terminal; the next provider answers when the first fails or returns 402; a revoked token is refused without a restart; a database from a newer schema is refused |
| 2. Memory, knowledge and time | Facts with provenance and FTS5 search, one global memory, the per-conversation "remember nothing" switch, compaction, titles and summaries, projects, markdown files, attachments, forms, reminders, tasks, schedules, notifications | `compaction`, `projects`, `markdownfiles`, `attachments`, `qcm`, `reminders`, `tasks`, `schedule`, `notifications`, `dueloop`; `memory` rewritten | Reminders stored as zone names fire correctly across a clock change (test dates around 25 October); facts taken from tools stay unconfirmed until you confirm them; a "remember nothing" conversation writes no facts |
| 3. Rule gate and workspace | Rule table, built-in floor, matching on tool, target and situation, approvals with a 24 h expiry, audit log, tainted-turn rule, the nine workspace tools, path safety, git commit per change, rootless Podman runs with the pinned image, `clara doctor` checks | `permissions`, `broker`, `approvals` evolved | Rule tests for specificity, ties and the floor; the model has no tool that approves or edits a rule; a script sees no network, no `.git` and no files outside its folder; deleting or overwriting workspace files asks; a pending ask never runs the action |
| 4. Web site and admin | Svelte site, Admin pages (rules, pending requests, tokens, usage), the `clara-admin` slash-command console, cookie login with `X-Clara-Web` | `commands`, `admin`, `console`; `web/` rewritten | You can answer an approval and edit a rule from the browser alone; cookie flags and header checked by tests; `/docs` is off and `/health` returns only the status |
| 5. Connectors and Discord | GitHub with a fine-grained token, Drive with `drive.file`, SearXNG search and page fetch, the slim vault with FTS5 and git sync, the DM-only `clara-discord` bot with approval buttons and alert relay | `github_live`, `gdrive`, connector `base`, `github`, `ingest`, `web`, vault git code | GitHub and Drive changes ask by default; the bot ignores every user id but yours and uses its own token; a failing vault push raises an alert |
| 6. Operations and cut-over | `tailscale serve` with a refusal to start while Funnel is on, nightly and weekly backups with the copy to another tailnet machine, the tagged-release updater with rollback, alerts, traffic log and usage statistics, release build for the Svelte assets and the sandbox image, systemd units | `tailscale`, `lifecycle`, `selftest`, `traffic`, `usagelog`; `restart` rewritten | An update to a test tag succeeds and a broken one rolls back by itself; `clara restore --verify` works on a clean machine; every alert in the list fires in a test; then Clara moves over and the old repository is archived |

## Operations

### Updates: tagged releases only

The current restart button pulls whatever the tracked branch holds and checks the new code only after the old server has stopped. The new updater inverts that.

1. The console lists new release tags with their notes (the changelog, migrations, new settings). Nothing installs until you pick one.
2. The tag is verified (`git verify-tag` when you configure a signing key) and checked out into `releases/<tag>`, with a virtualenv built from a hash-pinned lock file. The web files and the sandbox image arrive prebuilt with the release.
3. A preflight runs the new tree against a copy of the database: settings validation, migrations, the self-test.
4. A backup is taken. Then the old process drains (with a visible progress and a hard limit, not an open-ended wait) and stops.
5. The `current` link switches to the new release and systemd starts it.
6. The updater waits up to 60 seconds for `/health` and the new `restarted` id. If the check fails, it switches the link back, restores the database if a migration ran, restarts the old release and alerts you.

The systemd unit sets `TimeoutStopSec` above the drain limit, `Restart=on-failure`, and a watchdog that the scheduler loops keep fed.

### Backups

- **Local:** a SQLite backup through the `backup()` API every night, kept for 14 days plus 8 weekly copies, written in a folder only the service user can read.
- **Remote:** the newest backup and the workspace repository are copied to another machine on your tailnet with an encrypted tool such as restic. The secrets key is never in that copy; you keep it elsewhere.
- **Restore drill:** `clara restore --verify` opens a backup, runs `integrity_check` and counts rows. A monthly reminder asks you to run it.

### Logs, usage and alerts

- **Traffic log:** metadata only by default: time, route, status, duration, client, model, token counts, outcome. A debug switch turns on full prompts and answers for one hour, into a separate file readable by the service user alone, then switches itself off. Nothing is encrypted at rest because nothing sensitive is written by default.
- **Usage statistics:** one `usage_log` row per model call (chat, title, summary, reminder, scheduled run), with tokens in, out and cached. The Admin page aggregates by day, model and kind. There are no limits and no credits.
- **Alerts to you** (inbox and Discord DM): a provider key that stops working, a vault push that fails, disk above 85%, a reminder or schedule running more than 5 minutes late, a failed backup, the Discord bot disconnected, and Tailscale not serving.
- **Health:** `/health` answers `ok` and nothing more; detail is behind a token at `/v1/admin/status`.

### Time budget and background work

- **Per answer:** a total deadline (default 15 minutes) on top of the per-model timeouts, so a chain of silent fallbacks cannot run for an hour.
- **Pools:** model calls for you get their own slots. Titles, summaries, reminders and scheduled runs draw from a smaller background pool, so they cannot starve a chat.
- **Fallbacks:** the list may only contain providers you enabled; a local model never falls back to a hosted one; a payment-required error (402) switches provider and alerts you.

### Tests

The current suite defines 1,198 tests. About 294 of them cover removed features (Discord, music, users, limits, aliases, link checks) and go away. The rest are ported with the person and surface arguments removed. New suites cover the rule engine (precedence, ties, the built-in floor, the tainted-turn rule), the workspace (path escapes, symlinks, `.git` protection), the container runner (limits, timeouts, no network), the updater (failed preflight, failed health check, rollback), migrations (forward only, refusal of newer schemas), and daylight-saving behaviour for reminders. Continuous integration runs lint, the unit tests on Python 3.12 and 3.13, a container test, and a backup-restore test on every push.

## Risks and open points

The biggest risk is not technical: the current project grew into more products than one person can secure, operate and explain. Each phase below ends with a gate for that reason, and a feature that fails its gate waits.

### Risks

| Risk | Why it matters | Answer in this plan |
| --- | --- | --- |
| Prompt injection reaches script execution | A web page, GitHub issue or fetched file can steer Clara toward running code | No network by default, empty environment, tainted-turn rule, approvals, `.git` hidden from scripts |
| Rootless containers unavailable | They need subuid and subgid ranges and cgroup v2 on the host | `clara doctor` checks first; if it fails, `ws_run` stays off. There is no fallback to a plain subprocess |
| A stolen Discord account answers approvals | The bot accepts your user id, so whoever holds your account holds the buttons | You choose which approval classes Discord may answer; deletions and merges stay on the console by default |
| Ollama on your Windows PC has no authentication | Anyone who can reach port 11434 can use it | Bind it to the tailnet address and restrict it with a Tailscale ACL to the Linux machine only |
| The data-layer rewrite | "Person" appears about 2,100 times in 75 files | Rewrite `db` first; port each module against its tests, one at a time |
| Scope creep | The old project grew to 162 routes and 65 tools | Phase gates and a written non-goals list in the repository |
| Hosted providers see prompts | Free tiers of some providers may use submitted data; I have not checked current terms | Record each provider's terms and date in the repository; consider a "local only" conversation flag |
| Google refresh tokens | An app left in testing mode may have its refresh tokens expire after about 7 days (to verify with Google's current rules) | Check the consent-screen status before phase 5 |
| One machine | A dead disk loses the memory | Nightly backups, a copy on another tailnet machine, and a monthly restore drill |

### Decisions I made for you, to confirm

- [ ] One token per client (terminal, web, console, Discord bot, each script), instead of a single shared token.
- [ ] Scripts have no network unless a run asks for it and you approve.
- [ ] The tainted-turn rule is on by default.
- [ ] Discord may answer approvals, except deletions and merges.
- [ ] The client-side tools protocol stays in the API, although no current client uses it.
- [ ] Reminder announcements are still written by the model, with the stored text as the fallback.
- [ ] Python 3.12 is the baseline (the current code asks for 3.11 or later and has run on 3.14 on your machine).
- [ ] The new Clara lives in a new repository, versioned 1.0; the old one is archived.
- [ ] The old Clara stays reachable read-only for a few weeks after cut-over, since nothing is migrated.

## Appendix: module map

Line counts are from the current source (30,799 lines in `src/clara`). "Port" means the logic is kept and every `person`, `user` and `surface` argument is removed.

| Current module | Lines | Decision | New home |
| --- | --- | --- | --- |
| `agent.py` | 1,545 | Rewrite: drop roster, focus, chime-in, observe, credits and own-key paths | `agent` |
| `memory.py` | 1,966 | Rewrite for one memory | `db` |
| `tools.py` | 1,361 | Split. Drop `music_*`, `res_*`, `adjust_relation`, `about_person`; keep the rest | `tools` |
| `prompt.py`, `config/system_prompt.md` | 156 + 40 | Rewrite as a fixed persona file | `agent` |
| `llm.py`, `providers.py`, `retry.py` | 483 + 434 + 32 | Port | `llm` |
| `models.py`, `modelapi.py` | 443 + 203 | Port without weights or per-person choices | `llm` |
| `compaction.py` | 124 | Port | `agent` |
| `settings.py`, `server.py` | 495 + 517 | Rewrite | `settings`, `app` |
| `auth.py`, `ratelimit.py`, `session.py` | 202 + 81 + 122 | Rewrite as tokens, a failure limiter and a token file | `auth`, `cli` |
| `accountapi.py`, `linking.py`, `users.py` | 238 + 42 + 455 | Drop | none |
| `limits.py`, `erasure.py`, `userkeys.py`, `keyapi.py` | 193 + 47 + 146 + 88 | Drop | none |
| `clientapi.py` | 438 | Drop (spaces, rosters, registration) | none |
| `music.py`, `musicaccounts.py`, `musicapi.py`, `musicweb/` | 748 + 78 + 409 + site | Drop | none |
| `integrations/connectors/serverfs.py`, `computer.py` | 236 + 81 | Drop; the workspace replaces them | `workspace` |
| `reminders.py`, `announce.py`, `dueloop.py` | 258 + 76 + 46 | Port with zone names only | `timekeeping` |
| `tasks.py`, `taskstore.py`, `taskai.py`, `taskapi.py` | 680 + 294 + 162 + 168 | Port | `timekeeping` |
| `schedule.py`, `scheduleapi.py` | 477 + 181 | Port | `timekeeping` |
| `notifications.py`, `notificationapi.py` | 311 + 175 | Port into `events`, kept until read | `events` |
| `projects.py`, `projectapi.py`, `github.py`, `ingest.py` | 573 + 356 + 133 + 278 | Port | `knowledge` |
| `attachments.py`, `conversationfiles.py`, `conversationapi.py`, `chatapi.py` | 204 + 155 + 213 + 303 | Port | `knowledge`, `api` |
| `markdownfiles.py`, `markdownapi.py` | 223 + 67 | Port | `knowledge` |
| `vault/` and `vaulttools.py` | about 3,400 | Slim rewrite: keep git sync and notes, add FTS5 search, about 6 tools | `knowledge` |
| `vault/embeddings.py` | 253 | Drop | none |
| `integrations/permissions.py`, `broker.py`, `approvals.py`, `store.py`, `service.py` | 84 + 271 + 233 + 640 + 47 | Evolve into the rule gate | `gate`, `connectors` |
| `integrations/connectors/base.py`, `github_live.py`, `gdrive.py` | 109 + 353 + 411 | Port; Drive scope becomes `drive.file` | `connectors` |
| `integrations/vault.py` | 67 | Rewrite: AES-GCM, scrypt, rotation | `secrets` |
| `integrationapi.py` | 896 | Trim | `api` |
| `qcm.py`, `web.py` | 227 + 94 | Port forms; rewrite web for SearXNG | `connectors` |
| `lifecycle.py`, `tailscale.py`, `selftest.py`, `private.py`, `headless.py`, `httpclient.py`, `bodylimit.py`, `apicommon.py` | 127 + 208 + 215 + 57 + 51 + 28 + 83 + 126 | Port | `ops`, `api` |
| `restart.py` | 318 | Rewrite as the tagged-release updater | `ops` |
| `traffic.py`, `usagelog.py` | 388 + 231 | Port; metadata-only by default | `ops` |
| `webapi.py`, `commands.py` | 749 + 823 | Trim and rewrite | `api`, `cli` |
| `client.py`, `admin.py`, `console.py` | 730 + 98 + 90 | Port | `cli` |
| `web/` | 6,658 lines of JavaScript | Rewrite in Svelte | `web` |
| `discord_bot/` | 2,655 | Rewrite: DM-only, separate process | `clara_discord` |

In total about 3,450 lines are dropped outright and 2,655 are replaced by a smaller DM-only bot. 294 of the 1,198 tests go with them.
