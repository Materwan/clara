# Data map

Everything Clara stores, where, for how long, and what leaves the machine. Paths are those of an installation made by
`deploy/install.sh`; `<data>` is `CLARA_DATA_DIR` (`/var/lib/clara`). Every file and folder below belongs to the
`clara` user and is readable by it alone (folders 0700, files 0600), which Clara checks at start.

## On the Linux machine

| Where | What | Kept | In backups |
| --- | --- | --- | --- |
| `<data>/clara.db` (with `-wal`, `-shm`) | The database, below | See each table | Yes |
| `<data>/backups/daily/`, `weekly/`, `manual/`, `pre-update/` | Copies of the database | 14 daily, 8 weekly; manual and pre-update until you delete them | — |
| `<data>/backups/workspace.bundle` | A git bundle of the workspace, made for the remote copy | Replaced at each remote backup | Remote only |
| `<data>/logs/traffic-<date>.jsonl` | One line per request: time, method, route (ids replaced), status, duration, client, token name, model, token counts, outcome (for a failed model call also its HTTP status and the provider's error code, never its text); the address of failed sign-ins | `CLARA_TRAFFIC_LOG_DAYS` (7) | No |
| `<data>/logs/debug-content.jsonl` | Full prompts and answers, and the text of failed model calls, only while `/debug on` | Deleted when the hour ends or `/debug off` | No |
| *(memory only)* the live log | The last 500 lines of the server's log (INFO and up) and of the traffic records, shown in the Admin page's Logs tab; secret-looking text masked, no sign-in addresses, no debug content | Until the server restarts | No |
| `<data>/run/` | The instance lock, the boot record, a pending update's plan and result | While needed | No |
| `<data>/workspace/` | Clara's files and notes (`notes/`), script logs, and its git history | Until deleted; **the git history keeps deleted files** until you rewrite it | Remote copy (bundle) |
| `CLARA_VAULT_PATH` | Your Obsidian vault (a git clone) | Yours; deleted notes go to `.trash/`, and stay in git history | No (it has its own remote) |
| `/etc/clara/clara.env` | Non-secret settings | — | No |
| `/etc/clara/secret-key.cred` | The secrets store's key, encrypted by systemd for this machine | — | **Never**: keep a copy elsewhere |
| `/etc/clara/discord-token.cred`, `discord-clara-token.cred`, `discord.env` (or the token files named in `clara.env` when the server starts the bot) | The Discord bot's token, its Clara token, your Discord id and the ids of your four channels | — | No |
| `/opt/clara/releases/<tag>/`, `/opt/clara/current` | Installed releases and their virtualenvs | Until you remove old ones | No |
| `/var/lib/clara-home/` | The `clara` user's home: rootless Podman's images and storage, SearXNG's settings | — | No |
| `/var/lib/clara-discord/state.json` (`<data>/discord-state.json` when the server starts the bot) | The bot's current conversation id, and the open threads of scheduled runs, at most 30 (thread id → conversation; removed when the run ends) | — | No |
| `~/.config/clara/token` (on each device) | A client token (0600) | Until you delete it | No |

## In the database

| Table | Holds | Kept |
| --- | --- | --- |
| `conversations`, `messages`, `messages_fts` | Conversations (title, summary, model, project, flags, the packs switched on, client), every message and its versions, tool calls and results, the full-text index | Until you delete the conversation; incognito ones 24 h after their last message; with `CLARA_PURGE_SUMMARISED`, summarised messages go |
| `conversation_files` | Files sent in a conversation (their bytes) and their text | With the conversation |
| `facts`, `facts_fts`, `forgotten` | Facts with source, dates, pin, expiry, supersede; tombstones of forgotten facts (their folded text) | Until you delete them; superseded and expired facts stay visible on the Memory page |
| `projects`, `project_sources`, `project_files` | Project settings, GitHub sources, file contents | Until you delete the project |
| `created_files` | The Drive files Clara created (id, name, conversation, time), which have no history of ours to read back | Until deleted |
| `reminders`, `tasks`, `task_reminders`, `schedules`, `schedule_runs` | Time features; a schedule's packs, model and policies; each run's status (including `waiting`), summary and conversation | Until deleted; a once-only reminder goes when it fires |
| `events`, `event_cursors` | Reminders fired, notifications, approval news, alerts, server and update news; each token's read position | Until every active client read them (a day at least), 90 days at most |
| `approvals` | Requests with their frozen action and arguments, answer, result | 30 days after they finish |
| `rules` | Your rules | Until you delete them |
| `audit_log` | Every gate decision: tool, target, level, situation, a **digest** of the arguments, rule, effect, outcome | `CLARA_RETENTION_DAYS` (400) |
| `usage_log` | One row per model call: kind, client, conversation, provider, model, token counts, duration, outcome | `CLARA_RETENTION_DAYS` (400) |
| `ws_runs` | Script runs: command digest, exit code, duration, network flag, commit | Not pruned (a few hundred bytes a run) |
| `connections`, `resources` | GitHub and Google account names, connection state; the repositories, files and folders Clara may use | Until you remove them |
| `secrets`, `secrets_meta` | Encrypted secrets (AES-GCM) and the key check value | Until removed |
| `tokens`, `web_sessions` | Token hashes, names, scopes, last use; web session hashes | Revoked tokens stay listed; sessions until they expire |
| `models`, `settings` | The model catalogue and your choices; runtime settings | — |
| `vault_notes`, `vault_fts` | The vault's search index (rebuilt from the files) | Follows the vault |

Deleted rows are overwritten in the file (`PRAGMA secure_delete`); `clara purge` (server stopped) vacuums it. Backups
keep what they held until they rotate out.

## What leaves the machine

| To | What | When |
| --- | --- | --- |
| The model provider of the conversation | The prompt: persona, your facts (not in incognito), the conversation or its summary, project content, tool results, attached files and pictures | Every model call. Local Ollama hosts keep it on your machines; hosted providers are listed with their terms in [PROVIDERS.md](PROVIDERS.md). A local-only conversation never reaches a hosted provider |
| The model provider of a conversation that reads a day back | What the special tools return: your conversations of that day (normal, project and scheduled ones; never incognito ones, local-only ones only to a local-only conversation), with their earlier summary and last exchanges, tasks, reminders, a short list of the day's files (and a file's text or diff when Clara asks for it), daily notes; a long conversation first goes to the side-jobs model for a summary | When a conversation or schedule has those packs on |
| SearXNG, then the search engines it asks | The search query | `web_search` |
| The web site fetched | A request for that address | `web_fetch` |
| GitHub | Requests for the repositories you listed; the content of commits, issues, pull requests Clara writes | GitHub tools (writes ask first) |
| Google | Requests for Drive files Clara may use; content Clara writes; the token at revocation | Drive tools (writes ask first) |
| The vault's git remote | Your vault's changes | After vault changes, when `CLARA_VAULT_PUSH` |
| Discord | Clara's answers, notifications, alerts and approval requests, posted in the channels of your private server | When the bot runs |
| The restic repository | The newest database backup and the workspace bundle, encrypted by restic | Nightly, when `CLARA_RESTIC_REPOSITORY` is set |
| GitHub releases | Requests for the release list and assets | When you list or install releases |
| Tailscale | Nothing of Clara's content: it carries the encrypted traffic between your devices | Always |

Nothing is sent anywhere else: no telemetry, no analytics, no crash reports.
