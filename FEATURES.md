# Clara 1.0: features

This is the precise list of what Clara 1.0 does: every client, tool, rule default, route, setting, alert and limit.
`tests/test_docs.py` checks it against the code: a tool, a route, a setting, a console command, a secret, a runtime
setting, an event kind or an alert the code has and this file does not name makes the test suite fail.

What Clara deliberately does **not** do is in [docs/NON-GOALS.md](docs/NON-GOALS.md).

Contents: [1. Who it is for](#1-who-it-is-for) · [2. Clients](#2-clients) · [3. Conversations](#3-conversations) ·
[4. Memory](#4-memory) · [5. Time](#5-time) · [6. Knowledge](#6-knowledge) · [7. Workspace and scripts](#7-workspace-and-scripts) ·
[8. The outside world](#8-the-outside-world) · [9. The rule gate](#9-the-rule-gate) · [10. Tools](#10-tools) ·
[11. Models](#11-models) · [12. Security](#12-security) · [13. Operations](#13-operations) ·
[14. Settings](#14-settings) · [15. HTTP API](#15-http-api) · [16. Limits](#16-limits)

---

## 1. Who it is for

One person, on one Linux machine, reachable only from that person's own devices over Tailscale. There are no
accounts, users, passwords, roles, credits or quotas: every client token has the same power, and every conversation,
fact, task and file belongs to the same person. Clara is an agent: she remembers, keeps time, reads and writes files
in her own workspace, runs scripts in isolated containers, and reaches GitHub, Google Drive and the web, each action
going through rules you set.

## 2. Clients

All clients speak the same HTTP API (section 15) with their own token, and share one list of conversations.

| Client | What it is | Token client |
| --- | --- | --- |
| Web site | Svelte single-page site served by Clara itself | `web` |
| `clara-chat` | Terminal chat | `cli` |
| `clara-admin` | Terminal admin console (the console commands below) | `console` |
| `clara-discord` | Discord bot in your private server, answers your user id only, in #general (separate process) | `discord` |
| Your scripts | The HTTP API and its event stream | `script` |

### Web site

Sign in once by pasting a `web` token; the browser keeps an HttpOnly, Secure, SameSite=Strict cookie (30 days idle,
90 days at most) and sends the `X-Clara-Web: 1` header with every call.

The design is the first Clara site's ("Cyanotype"): a Prussian-blue navigation rail in both themes with Clara's
portrait, the page in cool chalk (or deep blue in the dark theme), one violet signal for what is live or waits for you,
Hanken Grotesk and Epilogue served by Clara herself (SIL Open Font Licence, the licences at `/fonts/`), line icons.
The rail holds **New chat** (the logo leads to the same place: the chat has no link of its own), the pages you work in (Projects, Tasks & reminders, Schedules, Workspace, Vault),
the list of conversations (on every page) and **Settings**, whose six pages share a tab bar (Memory, Connections, Rules
& approvals, Models, Usage, Admin). The logo and **New chat** stay at the top, the account tab at the foot; the pages and
conversations scroll between them. The connection is a lamp on Clara's portrait: green when connected, amber while
it reconnects, red when Clara is not reachable. Pinned at the foot: **Waiting for you** with a count when approval
requests wait (it opens Rules & approvals), and the account tab, which shows the name of the token you are signed in
with and a red count while alerts are active (also on the Admin tab). It opens onto **Settings**, an **Auto /
Light / Dark** theme switch (remembered in this browser, applied before the page is drawn) and Sign out. On a phone
the rail, with the list of conversations, slides in over the page (focus moves into it, and the page behind is inert until
it closes; a conversation's pin, rename and delete are one ⋯ button there, as there is no hover). Checkboxes are drawn in the site's style (a box that fills violet, with a white tick). A question that needs an answer (delete, revoke, a secret's value, when a token or an account is attached)
opens a dialog in the site's own style: Escape cancels, and a secret is typed in a masked field. Notifications wait while
you point at them and can be dismissed. The first Tab stop is **Skip to content**. Pages:

| Page | What you do there |
| --- | --- |
| Chat | Streamed answers with thinking, Clara's portrait beside each answer, tool calls in the order she made them (a run of calls is one line that opens on each call's arguments and result), Markdown with LaTeX (KaTeX) and code; the **"+" menu** of the box you write in (add files, switch special tools and web search on or off, the second brain, choose the project; the choices are saved with the conversation) and drag and drop for files (PDF, Word, text, pictures, animations); answer forms (`qcm`) and approval requests inline; edit a message or ask again (versions, with arrows between them); a reply that failed or was cut off has **Try again** (its question is asked again, as a new version when it is stored); stop an answer; pick the model per conversation; start an **incognito** or a **local-only** conversation; compact it; a console sub agent's conversation opens as a block under the `local_task` call that started it; notes Clara writes in `notes/` appear as cards you open or download (files sent in a conversation are downloaded through the API) |
| Conversations list | In the rail under the pages, on every page: one list shared by every client, searchable; pinned ones first, then the **projects** and the **schedules** that are switched on, each a group that opens on its conversations (the latest 5, then "Show more"; open or closed is remembered in the browser), then the loose conversations, newest first; pin, rename, delete. A project or a schedule shows its conversations here only when its **Show in history** button is on (both start off); a search looks through all of them, and a pinned conversation stays in Pinned whatever its group. Two switches in the account menu, **Discord conversations** and **Console conversations** (on at first, kept in the browser), leave out of the list what those clients started; a search and the pinned ones still show them. A conversation made by a schedule's run belongs to the schedule even when the schedule has a project |
| Memory (Settings) | Facts: live, unconfirmed, expired, superseded, all; search; add, edit, pin, confirm, forget; source and dates shown |
| Tasks & reminders | Tasks with sub-tasks, due dates and reminders (list and calendar views); reminders to come; add and cancel |
| Schedules | Prompts that run unattended on a schedule: add, edit, pause, run now, see past runs and their summaries; tick the **special tools** (packs) its runs get, choose one conversation for all runs or one per run, what happens to runs missed while the server was off and to a run that comes due before the last one is over, and a model of its own, and how many tries a run gets when the model is busy (1 to 3, 3 by default); answer or close a run that waits for you; **Show in history** lists its conversations in the rail |
| Projects | Upload files and archives, add a GitHub repository snapshot, sync or remove it, read files, set instructions; **Show in history** lists its conversations in the rail |
| Workspace | File tree, read and edit files, upload, delete, git log with each commit's diff, undo, script runs and their logs |
| Vault | Status of the Obsidian vault, search, read notes, sync, reindex |
| Connections (Settings) | GitHub token and the repositories Clara may use; Google Drive consent (the Google address is shown before you confirm), the files and folders you opened to Clara with the Google Picker, disconnect (the token is revoked at Google) |
| Rules & approvals (Settings) | Requests waiting (answer them), recent ones, the rule list (add, edit, switch off, delete), the tainted-turn switch, the defaults table, the test box (which rule decides a given call), the audit log |
| Models (Settings) | Providers (enable, check), the catalogue (show in pickers, set default, side-jobs model, fallbacks in order), a selector **Work with Clara / All / Don't work** that keeps the models Clara cannot use out of the way, each with its reason |
| Usage (Settings) | Token counts by day, model, kind, provider or client over 24 h to all time; the last calls |
| Admin (Settings) | Status, alerts, restart and stop; tokens (create, revoke); secrets (set or remove, values never shown); runtime settings; debug switch; backups (list, back up now); releases (notes, install); the console |

The site is built at release time (`scripts/build-web.sh`): the machine needs no Node. Every page is served with a
strict Content-Security-Policy (scripts, styles, fonts and connections from the site only; nothing is inlined as a
`data:` URI, so KaTeX's fonts load too); the Drive picker window is the one page allowed Google's picker script.

### `clara-chat`

`clara-chat [--url URL] [--token-file F] [--conversation ID | --resume] [--incognito] [--local-only] [--local-tools [--yes]] [message…]`

- With a message: one question, the answer, then exit. Without: an interactive chat with history and line editing.
- `--save-token` stores your token (typed hidden) in `~/.config/clara/token`, mode 0600.
- Reminders and notifications pop up while it runs; approval requests are answered at the prompt (o: allow once,
  a: allow always, r: refuse once, n: refuse always).
- `--local-tools` gives Clara two client-side tools, `local_list` and `local_read`, limited to the folder you started
  in; each call is shown and needs your yes unless `--yes`. They run on your machine, not on the server (the
  client-side tools protocol: `tool_requests` events, then `POST /v1/turns/{turn_id}/tool-results`).

| Command | Does |
| --- | --- |
| `/new [incognito] [local]` | Start a new conversation |
| `/resume [id]` | List the terminal's conversations, or go on with one |
| `/conversations [words]` | Search every conversation |
| `/facts`, `/remember <text>`, `/forget <id>` | Memory |
| `/remind [daily\|weekly\|monthly] [@clients] <when> <text>` | A reminder (`+30m`, `+2h`, `+3d`, `09:30`, `tomorrow 09:30`, `2026-10-05 09:30`) |
| `/reminders`, `/unremind <id>` | Reminders to come; cancel one |
| `/tasks [all\|done]`, `/task <id>` | Tasks; one in full |
| `/task add [due <when>] [remind <when>]… <title> [\| <description>]`, `/task sub <id> …`, `/task done\|reopen\|delete <id>`, `/task move <id> <parent>\|none`, `/task set <id> title\|description\|due\|remind <value>` | Manage tasks |
| `/notify [@clients] <text>` | Send yourself a notification |
| `/model [provider:model \| default]` | This conversation's model |
| `/tools [<pack> [on\|off]]` | The special tools of this conversation: list them, or switch one |
| `/approvals`, `/allow`, `/always`, `/refuse`, `/never <id>` | Approval requests |
| `/undo` | Revert Clara's last workspace change |
| `/quit` | Leave |

### Console commands (`clara-admin`, the Admin page's console, `POST /v1/admin/command`)

| Command | Does |
| --- | --- |
| `/status` | Version, state, models, storage, Tailscale, backups, alerts |
| `/provider [list] \| enable\|disable\|check <id>` | Model providers |
| `/model [provider:model]` | Show or set the default model |
| `/models [refresh] \| show\|hide <ref>...` | The catalogue; `*` marks models shown in pickers; a model that does not work says why |
| `/fallbacks [<ref>... \| clear]` | Models asked in turn when one fails |
| `/upkeep [<ref> \| none]` | The model of titles and summaries |
| `/tokens [list] \| create \| revoke \| approvals` | Client tokens (revoking works at once) |
| `/secrets [list] \| set <name> <value> \| unset <name>` | The encrypted secrets store |
| `/rules [list] \| add \| del \| on \| off \| test` | The rule gate |
| `/tainted [on\|off]` | The tainted-turn rule |
| `/approvals [list] \| allow\|always\|refuse\|never <id>` | Pending approval requests |
| `/debug [on\|off]` | Full prompts and answers to the debug log for one hour |
| `/usage [24h\|7d\|30d\|90d\|365d\|all] [day\|model\|kind\|provider\|client]` | Token statistics |
| `/alerts [clear <key>]` | Active and recent alerts |
| `/audit [n]` | The last gate decisions |
| `/undo [commit]` | Revert the last workspace change (or a given commit) |
| `/vault [status\|sync\|reindex]` | The Obsidian vault |
| `/backup [now]` | Local backups (and the remote copy) |
| `/update [list \| <tag>]` | Tagged releases: list, or install one (preflight, then restart) |
| `/restart [now]` | Stop and start again (the same release) |
| `/stop [now]` | Graceful stop (`now`: cut running answers) |

`clara-admin [--url URL] [--token-file F] [--save-token] [command…]`: interactive, or one command then exit.

### `clara-discord`

A separate process with its own Clara token (client `discord`) and its own Discord bot token, never in Clara's
database. It lives in a **private Discord server of your own** (you and the bot, nobody else) and uses four text
channels, each named by its id. It has **no direct-message function**: a DM to the bot gets no answer.

- **Start:** with `CLARA_DISCORD_AUTOSTART=true` the server starts the bot (`python -m clara_discord.bot`) when it
  starts, starts it again after a crash (2, 5, 15, 30, then 60 s apart), and stops it when it stops; it also expects
  its heartbeat (the `discord` alert). Otherwise run it as `clara-discord.service`. Use one of the two, never both.
- Connects with the guild, guild-message and message-content intents (the last is privileged: switch it on in the
  developer portal): no member list, no presences, no direct messages.
- Answers **only** `CLARA_DISCORD_USER_ID`, and only in `#general` (and in a thread of a scheduled run under it); every other user, channel and server is ignored
  without a reply. At start it finds the four channels: if one is missing, is not a text channel, or they are not all
  in one server, it refuses to start and says which. Only one bot runs per state file.
- What you write in `#general` is a chat message in the bot's current conversation; files and pictures you attach go
  with it (4 files, 25 MB each, 34 MB in all); Clara's answer, and the notes she writes, come back there (split at
  2,000 characters).
- Where each kind of message goes:

  | Channel | What |
  | --- | --- |
  | `#general` | Your conversation with Clara; a line saying where to answer when she waits for an approval |
  | `#approval` | Approval requests, with buttons: Allow once, Allow always, Refuse once, Refuse always. The token's default approval scope is `safe`: Discord may answer everything except destructive actions, which stay with the web site and the terminals |
  | `#notification` | Reminders, notifications, task reminders, schedule summaries and update news |
  | `#alerts` | Alerts |

- **What a scheduled run says** (the `ask` event, section 5): a run's question is posted in `#general`, with a **thread** under it
  when the run has the Discord thread pack; what you write in the thread goes to the run's conversation, whatever the current
  conversation is. Without a thread (no pack, or the bot may not open one) the run's conversation becomes the current
  one, until the run ends. A reminder is posted in the thread. **When the run ends** (it finishes, or it is closed because
  you did not answer) the bot says so in `#general` (the run's summary, which for the daily note gives the note's path),
  after the answer that finished it was posted, and **deletes the thread**; the conversation stays in Clara, and a
  correction is made there (the web site). A run can also open, write in and remove a thread by itself
  (`discord_thread`). The bot needs *Create Public Threads*, *Send Messages in Threads* and *Manage Threads* in `#general`
  (it logs what it lacks at start); without *Manage Threads* it archives the thread instead.
- Slash commands (in your server, for you only): `/tools` (the special tools of the current conversation: list them, or switch one on or off), `/new` (a new conversation), `/status`, `/tasks`, `/reminders`,
  `/approvals`.
- Sends a heartbeat every minute; with `CLARA_DISCORD_EXPECTED=true` (or `CLARA_DISCORD_AUTOSTART=true`) the server
  raises the `discord` alert after 5 minutes of silence. A bot that stopped by itself (exit code 0) is not started
  again until the server restarts; one that refused its settings (code 2) raises the alert at once.

Bot settings: `CLARA_DISCORD_USER_ID`, `CLARA_DISCORD_CHANNEL_GENERAL`, `CLARA_DISCORD_CHANNEL_NOTIFICATION`,
`CLARA_DISCORD_CHANNEL_APPROVAL`, `CLARA_DISCORD_CHANNEL_ALERTS`, `CLARA_URL`, `CLARA_TIMEZONE`, `CLARA_DISCORD_STATE`,
and the credentials `discord-token` and `clara-token` (or the files named by `DISCORD_BOT_TOKEN_FILE` and
`CLARA_TOKEN_FILE`). When the server starts the bot it hands it the `CLARA_DISCORD_*` settings, `DISCORD_BOT_TOKEN_FILE`
and `CLARA_TOKEN_FILE` found in its environment or `.env`, its own address and time zone, and a state file in the data
folder (`discord-state.json`) unless `CLARA_DISCORD_STATE` says otherwise; no other `CLARA_*` setting reaches it.

## 3. Conversations

- **One shared list:** a conversation started on Discord can be continued on the web or in the terminal. Each message
  records the client it came from (`X-Clara-Client`), which the prompt mentions.
- **Streaming:** `POST /v1/chat/stream` answers with server-sent events: `turn`, `thinking`, `token`, `retrying`,
  `tool_start`, `tool`, `approval`, `tool_requests`, `usage`, `compacted`, `discarded`, `warning`, `done`, or `error`
  (status, code and message) when the turn fails. `POST /v1/chat` returns the whole answer at once. Closing the
  stream (the web's Stop button) stops the answer: what was said is kept, marked interrupted, and tool calls that had
  not run are recorded as not run.
- **Request fields:** `message`, `conversation`, `attachments` (base64, at most 4), `project`, `model`, `fork_of`
  (ask again or edit: a new version of a message), `timezone`, `tools` (client-side tools), `instructions`,
  `incognito`, `local_only`, `parent` (with a new conversation: the one it is shown in. A console sub agent's conversation
  names the conversation that started it; it is left out of every list, listed in its parent's `subagents`, kept as long
  as the parent and deleted with it. One level only: a sub agent's conversation cannot be a parent).
- **Versions:** editing a message or asking again keeps the earlier answer as another version; you move between them.
- **Special tools (packs):** a pack is a name for a group of tools that exist only where it is switched on: **Web search**
  (on by default), **Second brain** (the vault open), **Day conversations**, **Day activity**, **Files created**, **Daily
  notes**, and, for scheduled runs only, **Ask you** and **Discord thread**. The "+" menu of the web chat, `/tools` on
  Discord and in the terminal, and `PATCH /v1/conversations/{id}` switch them for a conversation; the choice is saved with
  it, so it follows the conversation to every client, and a new conversation starts with web search only. A pack that
  cannot be used says why (no vault, Discord bot not connected). A pack only decides which tools exist: the rule gate
  still decides each call. `GET /v1/packs` lists them.
- **Titles and summaries:** a title is written after the first answer (by the side-jobs model). When a conversation
  passes `CLARA_COMPACT_PERCENT` of the model's window, or `CLARA_COMPACT_TOKENS`, older turns are summarised; the last
  `CLARA_KEEP_RECENT_TURNS` stay whole. With `CLARA_PURGE_SUMMARISED=true` the summarised messages are deleted.
- **Prompt fitting:** nothing is cut silently. Old tool results are shortened first (marked as omitted), then turns
  are left out (and you are told in a `warning` event); a message that alone exceeds half the window is refused.
- **Incognito:** no fact is read or written, the conversation is never searched by `search_history`, and it is deleted
  `CLARA_INCOGNITO_HOURS` (24) after its last message.
- **Local only:** the conversation is answered only by local Ollama hosts (no fallback ever reaches a hosted
  provider, and titles and summaries are written locally too); the web, GitHub and Drive tools are not offered, and
  the gate refuses, whatever the rules, anything that would send something off the machine: web search and fetch,
  GitHub and Drive, scripts with network, and vault changes when the vault pushes to its remote.
- **Attachments:** PDF (also those encrypted with an empty password), Word (`.docx`), text and code files, still
  pictures (8 MB) and animated GIF or WebP (30 MB; 6 frames are shown to the model); 4 files and 34 MB in all per
  message (so that, base64-encoded, they fit the 48 MB request; the web site checks before sending). A picture goes to models that see
  pictures; a document's text goes into the prompt (150,000 characters for all the documents of a message) and stays
  readable later with `read_conversation_file`.
- **Time budget:** an answer may take `CLARA_ANSWER_BUDGET` (900 s) of model time in all, across rounds and fallbacks
  (waiting for your approval does not count). A model that says nothing for `CLARA_FIRST_TOKEN_TIMEOUT` (300 s) at
  first, or `CLARA_IDLE_TIMEOUT` (120 s) once started, is given up. `CLARA_MAX_TOOL_ROUNDS` (20) rounds at most.
- **Model slots:** your chats use `CLARA_CHAT_SLOTS` (2); titles, summaries, reminders, tasks and schedules use a
  separate pool of `CLARA_BACKGROUND_SLOTS` (1), so they cannot starve a chat.
- **Long turns:** an answer still running after `CLARA_NOTIFY_LONG_TURN` (120 s) sends you a notification when done,
  in case you left.

## 4. Memory

One memory, shared by every client and conversation (except incognito ones).

- **Facts** are short sentences (300 characters) with a category, a pin, a **source** (`you`, `model` or `tool`), the
  conversation they came from, and dates (created, confirmed, expires).
- A fact you add has source `you`; one Clara saves has source `model`, or `tool` when she saves it during a turn that
  read untrusted content (a web page, a fetched file, an issue). A `tool` fact is **unconfirmed**: it is shown to the
  model tagged `[unconfirmed]` until you confirm it.
- **Supersede:** a new fact can replace an old one (`replaces`); the old one is kept, marked superseded.
- **Expiry:** a fact true for a while (`expires`) stops being shown after that instant.
- **Forget** deletes the fact and leaves a tombstone, so the same sentence is not saved again by the model; you can
  still add it yourself.
- **Never secrets:** text that looks like a key, token, password or private key is refused as a fact.
- **Recall:** full-text search (SQLite FTS5: any case, accents ignored, word starts). The prompt carries pinned facts
  first, then the newest, within `CLARA_FACTS_TOKEN_BUDGET` (2,000 tokens); the model is told how many were left out
  and finds them with `recall_facts`.
- **History search:** `search_history` searches every earlier conversation (FTS5 over messages), with dates and excerpts.

## 5. Time

Every time you mean on the wall clock is stored with a zone name (`CLARA_TIMEZONE`, or the client's), never a bare
offset, so daylight-saving changes cannot move a reminder. A wall-clock time that does not exist (the spring gap)
moves forward by the gap (02:30 becomes 03:30); the hour that repeats in autumn means its first occurrence.

- **Reminders:** once, daily, weekly or monthly; 200 at most, 500 characters each; addressed to some clients or all.
  At the due time the side-jobs model may write the reminder in context (`CLARA_REMINDER_AI_TIMEOUT`, 60 s); if it
  fails or is slow, the stored text is sent with the reason.
- **Tasks:** a to-do list with sub-tasks (50 per task), due dates and reminders. When you give no reminder times,
  Clara picks some (and says when); 10 reminders at most per task (`CLARA_TASK_MAX_REMINDERS`). 200 open tasks.
- **Schedules:** a prompt that runs unattended on a schedule (once, daily, weekly on chosen weekdays, monthly), with
  optional documents, a project, a model of its own, the local-only flag and its **packs** (the special tools its runs get;
  none by default but web search). Each run is a turn in a conversation: one shared by all the runs, or (**per run**) a
  new one each time, which is a normal chat in your list. Each run is recorded with a short summary and notifies you.
  - **Asking and waiting:** a schedule with the *Ask you* pack may ask you one question (`ask_person`: an `ask` event on
    the web site and Discord) and end its turn; the run is then `waiting`, your answer arrives in the run's conversation
    like in any chat (with the same packs), and `finish_run` ends it. A waiting run is reminded once, at
    `CLARA_WAITING_REMIND_AT` (09:00) after at least three hours; `Close it` on the Schedules page (or the next run, below)
    closes it from what Clara has: she is told no answer came, does what the task says for that case and finishes.
  - **Missed** (the server was off, more than an hour late): `skip` records them; `latest` runs the latest one at the next
    start; `all` runs every missed one in turn (31 at most). **Overlap** (a run comes due while the previous one still
    runs or waits): `skip` records the new one as skipped; `replace` closes a waiting run, then starts the new one (a
    running one is waited for); `queue` starts it when the old one ends.
  - **Retry** (`attempts`, 3 by default): a run the model could not answer (429, 5xx, a timeout, no connection) is tried again after 1 and 5 minutes. A run that already used a tool or wrote text is not run again, so its actions are not repeated; a refused key or a bad request is not retried.
  - A run more than an hour late is skipped and recorded as missed unless `missed` says otherwise. 100 schedules.
  - **The daily note** is a schedule made once at start when there is a vault ("Bilan du jour", every day at 23:59, one
    conversation per run, missed `latest`, overlap `replace`, packs second brain, day conversations, day activity, files
    created, ask you, Discord thread). Its prompt (French, yours to edit) has Clara read the day, ask you one question,
    wait for your answer, write the day into a block of that day's daily note (`vault_daily_block`), mark the tasks you
    confirmed done and finish; unanswered, she writes it from what she read, the uncertain points under « À confirmer ».
    Delete or pause it as any schedule; it is not made again.
- **Notifications:** Clara may notify you (3 per turn, only when asked or when long work is done); you can notify
  yourself from any client or script (10 a minute).
- **Events:** reminders, notifications, approval requests and their outcomes, alerts, server states, schedule and task
  news, and updates go to one event store with a read cursor per token. Each client gets what it missed when it comes
  back (`GET /v1/events`, or the live stream `GET /v1/events/stream`). Event kinds: `reminder`, `notification`,
  `approval`, `approval_resolved`, `alert`, `server`, `schedule`, `task`, `update`, `ask` (what a scheduled run says to you: its question, reminder and end for the web site and Discord, and the thread it opens, writes in or removes for Discord alone).
- **On time:** the due loops sleep until the next due instant (30 s at most); something that fires more than
  `CLARA_LATE_ALERT_SECONDS` (300 s) late raises a `late:` alert.

## 6. Knowledge

- **Projects:** a set of files a conversation works with: uploads (files, and `.zip` archives whose text files are
  kept; other archive types are skipped; 90 MB per upload, 1,000 files), GitHub repository snapshots (synced on demand), instructions and a description. A small
  project goes whole into the prompt (`CLARA_PROJECT_INLINE_PERCENT`, `CLARA_PROJECT_INLINE_TOKENS`); a bigger one is
  read with tools. Limits: `CLARA_PROJECT_MAX_CHARS` (20 million characters), `CLARA_PROJECT_MAX_FILES` (5,000).
  Archives are read in memory, never unpacked to disk: `..` and links are refused, nested archives skipped, a file
  over 30 MB is not read, and one archive may unpack to 600 MB at most.
- **Files sent in a conversation:** kept with the conversation (20 per conversation) and readable later by number.
- **Notes:** Clara's notes are Markdown files in the workspace folder `notes/` (with its git history and undo).
- **Second brain (Obsidian vault):** a git clone of your vault (`CLARA_VAULT_PATH`). It is closed by default: the model
  sees a one-line mention and calls `open_second_brain` when your notes matter, which opens eight tools for that
  conversation. Search is FTS5 over titles and text. Moving a note updates every `[[link]]` to it (bare links stay
  bare). Deleted notes go to `.trash/`. The daily note follows the vault's own settings (`_clara/schema.json`: `daily.folder` and `daily.format`, or the Clara vault's `types.daily.folder` with `daily_format` such as `YYYY/YYYY-MM-DD`); a new one is made from `_templates/daily.md`, and a line added to today's note goes under its `## Log`.
  Changes are committed and pushed (`CLARA_VAULT_PUSH`) a few seconds after the last one (never force-pushed; the
  token travels as an HTTP header, never in the URL); the vault is pulled every `CLARA_VAULT_PULL_SECONDS` (30). A
  failed push raises the `vault_push` alert.
- **Daily notes:** a block of a day's daily note sits between `<!-- clara:<name> -->` and `<!-- /clara:<name> -->` under its own
  `## ` heading; `vault_daily_block` writes it (the note is made from `_templates/daily.md` when it does not exist), and
  writing it again replaces that block and nothing else, so what you wrote in the note is never touched. `[[Links]]`
  are kept only for notes that exist (rewritten to the note's name). `daily_notes` reads a week or a month back.
- **A day read back (the packs' tools):** `day_conversations` gives the conversations with a message that day (normal,
  project and scheduled ones, with the stored summary and last exchanges for those begun earlier, the clients used and
  the vault notes used; over `CLARA_DAY_DIGEST_CHARS` (60,000, and at most a fifth of the model's window) the longest are
  summarised by the side-jobs model, or cut shorter if it fails); `read_conversation` gives one in full, a page at a time;
  `day_activity` the tasks done and the open ones that may be done, reminders that fired, scheduled runs and what Clara
  did through the gate. **Who may read what:** an incognito conversation is never read; a local-only one only from a
  local-only conversation; what a conversation nobody may read made (a task, a note, a file) stays out too. With no date,
  a tool means the day of the scheduled run it is in (the local date of the moment the run was due), else today.
- **Files of a day without their content:** `files_created` lists them, one short line each (reference, what happened,
  when, who, lines added and removed, size, a few words of what it is), at most 60 at a time: the workspace and the vault
  (from their git history; the vault's include yours, made through Obsidian), the Drive files Clara created, and the
  files you sent. `read_created_file` reads one by its reference when Clara needs it, whole or (`mode=diff`) as that
  day's diff only, a page at a time.
- **Forms:** the `qcm` tool shows a form (single choice, multiple choice, free text; 20 questions, 10 options each,
  LaTeX allowed) that you answer in the web chat; an optional key grades it.

## 7. Workspace and scripts

The workspace (`CLARA_WORKSPACE`, default `/var/lib/clara/workspace`) is the only place Clara writes or runs anything
on the machine. It is a git repository: every change by a tool is one commit (tool, summary, turn id), changes a
script makes are one commit after the run, and any commit can be undone (`/undo`, the Workspace page, `/v1/workspace/undo`).

- **Paths:** joined to the root and resolved; absolute paths, `..`, symlinks and anything under `.git` are refused;
  files are opened without following links. 400 characters per path.
- **Limits:** `CLARA_WORKSPACE_MAX_SIZE` (5 GB) and `CLARA_WORKSPACE_MAX_FILE` (100 MB).
- **Scripts (`ws_run`):** one rootless Podman container per run, removed afterwards: no network (`--network=none`;
  with `network=true`, Podman's user-mode network, and the gate asks), read-only root, all capabilities dropped,
  `no-new-privileges`, your user mapped in (`keep-id`), `--pids-limit`, memory and CPU limits, a wall-clock timeout,
  an empty tmpfs over `.git`, no host proxy variables, an **empty environment**. Only the workspace is mounted.
- **Script limits:** `CLARA_RUN_TIMEOUT` (120 s; `CLARA_RUN_MAX_TIMEOUT` 900 s), `CLARA_RUN_MEMORY` (1g),
  `CLARA_RUN_CPUS` (2), `CLARA_RUN_PIDS` (256), `CLARA_RUN_SLOTS` (2 at once, apart from the model slots). The model
  gets the exit code and the first `CLARA_RUN_OUTPUT` (64 KB) of output; the full log (10 MB at most) is kept.
- **Image:** `CLARA_SANDBOX_IMAGE`, or the image a release pins by digest (Python 3.12 with numpy, pandas, matplotlib,
  requests, and common command-line tools).
- **No fallback:** if rootless Podman is not usable (`clara doctor` says why: rootless, cgroup v2 controllers,
  subuid/subgid ranges, the image, isolation), `ws_run` is not offered. `CLARA_RUN=off` switches scripts off.

## 8. The outside world

- **Web search:** through a self-hosted SearXNG (`CLARA_SEARXNG_URL`), with the JSON format enabled
  (`deploy/searxng/`).
- **Web fetch:** `web_fetch` reads http and https pages (title, text, links; `CLARA_FETCH_MAX_SIZE`, 2 MB). The address
  is resolved once and the connection pinned to that address (with the right TLS name), so a DNS change cannot point it
  inside; private, loopback, link-local and tailnet addresses are refused, at every redirect (5 at most). An address
  you wrote or a search returned is read at once; any other asks (refused when unattended).
- **GitHub:** a fine-grained personal access token you paste on the Connections page, and the list of repositories
  Clara may use (others are refused before any request). Reading (tree, files, code search, branches, issues, pull
  requests, threads) is allowed; every change (commit, branch, issue, pull request, merge) asks by default.
- **Google Drive:** OAuth with the `drive.file` scope only (plus `openid email` to show which account you attach), with
  PKCE. Clara sees only files she created (in her own "Clara" folder) and files or folders you open to her with the
  Google Picker. The consent is bound to the browser session that started it; the Google address is shown and you
  confirm before anything is stored. Disconnecting revokes the token at Google. Reading Google Docs, Sheets and Slides
  gives text; every create, update or delete (to Drive's trash) asks by default.
- **Gmail:** OAuth with PKCE and two scopes only, `gmail.readonly` and `gmail.compose` (plus `openid email` to show which
  mailbox you attach), using the Google client of Drive but a token of its own. Clara can search (free words, sender,
  recipient, subject, unread, with an attachment, between two dates, in the inbox, sent, drafts, trash, spam or
  everywhere), read a message, read an attachment's text, keep an attachment in the workspace, save a draft and send. She
  cannot label, archive, mark read, trash or change any setting: the token has no scope for it. The Gmail API itself says
  whose mailbox it is, which also proves the API is on in your Google Cloud project, before anything is stored.
- **Outlook:** Microsoft Graph for personal accounts (outlook.com, hotmail, live) and work or school accounts
  (`CLARA_MICROSOFT_TENANT`, `common` by default), OAuth with PKCE for a *web* app registration (a client secret in the
  store). Permissions `Mail.ReadWrite`, `Mail.Send`, `User.Read` and `offline_access`: Graph has no draft-only
  permission, so the token could also change or delete mail, which Clara's tools never do. The sign-in proves the token
  reaches an inbox before anything is stored. Microsoft rotates the refresh token at each use; the new one is stored at
  once. Microsoft has no call to revoke it: disconnecting forgets it here, and the app is removed in your Microsoft account.
- **Mail, both:** one Gmail and one Outlook mailbox at most. What a message says is untrusted: hidden characters (zero-width,
  direction controls, Unicode tag characters) are removed and HTML is read without text that CSS hides, the result is
  handed over as data, and the turn is tainted. An email goes only to the addresses given in the call (no address is
  found for her), at most 20, text up to 6,000 characters (all of it is shown in the approval request), at most 5
  attachments from the workspace (Gmail 7 MB in all; Outlook 2.2 MB each and 10 MB in all), at most 5 emails per answer.
  A reply keeps its thread (Gmail: the headers and thread id; Outlook: Graph's reply draft with the quoted message).
- **New-mail notifications:** off until `mail_notify` is on (Connections page). Every `CLARA_MAIL_POLL_SECONDS` (120) the
  inbox of each connected mailbox is looked at; the first look only notes where it stands. A message announces itself as
  « sender: subject » (never the text) to every client, Discord's `#notification` included; more than four new messages
  at once make one notification with a count; `mail_notify_senders` keeps only senders containing one of its words.
  This loop is not counted by the systemd watchdog.
- **Content leaving the machine:** a call that would send text looking like a secret to GitHub, Drive, the vault's
  remote, a draft, an email or a networked script is raised to `ask`, naming the kind of secret found.

## 9. The rule gate

Every tool call is an **action**: a tool, a class, a level (`read`, `write`, `destructive`), its targets
(`ws:<path>`, `github:<owner/repo>`, `drive:<id>`, `mail:<gmail|outlook>`, `mail:to:<address>`, `vault:<note>`, `domain:<host>`, or `net`, `search`, `memory`,
`reminders`, `tasks`, `notify`, `forms`, `history`, `discord`) and a one-line summary. The gate answers allow, ask or refuse. The model can
neither read nor change rules, and no tool answers a request.

### Defaults (when no rule matches)

| Action class | In a chat | Unattended |
| --- | --- | --- |
| `read`: read the workspace, projects, the vault, memory, files sent | allow | allow |
| `memory`: Clara's own records (facts, reminders, tasks, notifications, forms) | allow | allow |
| `ws_write`: create or edit workspace files (git history) | allow | allow |
| `ws_destroy`: delete or overwrite workspace files | ask | ask |
| `run`: run a script, no network | allow | ask |
| `run_network`: run a script with network | ask | ask |
| `github_read`: GitHub read | allow | allow |
| `github_write`: GitHub issue, branch, pull request, commit | ask | ask |
| `drive_read`: Drive read (files Clara created or you opened to her) | allow | allow |
| `drive_write`: Drive create, update, delete | ask | ask |
| `mail_read`: mail read: search, read a message or an attachment (Gmail, Outlook) | allow | allow |
| `mail_draft`: mail draft: save a draft in your mailbox (nothing is sent) | allow | allow |
| `mail_send`: mail send: an email leaves your mailbox | ask | refuse |
| `vault_write`: create or edit vault notes (git history) | allow | allow |
| `vault_destroy`: delete or move vault notes | ask | ask |
| `web_search`: web search | allow | allow |
| `fetch_trusted`: fetch a URL you wrote or a search returned | allow | allow |
| `fetch_other`: fetch any other URL | ask | refuse |
| `discord`: open, write in or remove a Discord thread | allow | allow |

### Rules

A rule has an effect (`allow`, `ask`, `refuse`) and optional fields: tool (a name or a glob such as `github_*`), target
(a glob such as `ws:scratch/**`, `github:me/clara`, `domain:*.wikipedia.org`), situation (`interactive`, `unattended`,
`tainted`) and level. Its origin is `console` (you made it) or `approval` (an "always" answer made it); hits are
counted.

- **Which rule wins:** the most specific matching rule: more fields set, then more literal characters in its target,
  then more literal characters in its tool (a name beats a glob); on a tie, the stricter effect (refuse, then ask,
  then allow).
- **The floor** (no rule changes it): any target inside the workspace's `.git`, any workspace path that leaves the
  workspace, and, in a local-only conversation, anything that sends something off the machine (section 3) are
  refused. Beyond the gate, by construction: no tool reaches the database, the secrets, the data folder or the
  settings (tools work in the workspace, the vault, projects and memory only), and scripts never get the server's
  environment.
- **Mail rules:** sending is the one action that always asks in a chat and is refused unattended, at the `destructive`
  level (so the Discord token, scope `safe`, cannot approve it: answer from the web site or the console, or give that
  token scope `all`). Its targets are the recipients, one `mail:to:<address>` each, so `allow mail_send mail:to:*@example.com`
  opens one domain and a message with any other address stays closed (an allow rule must match every target). An
  "always" answer to a message with several recipients writes a rule that matches nothing: write one rule per address
  on the Rules page. In a local-only conversation the mail tools are not offered and the gate refuses them.
- **Tainted turns:** once a turn has read untrusted content (a web page or search result, a GitHub file or thread, a
  Drive file, an email or an attachment), outside-world writes (`github_write`, `drive_write`, `mail_send`, `vault_write`, `vault_destroy`) and networked
  scripts (`run_network`) are raised to at least `ask`, whatever the rules say. On by default; `/tainted off` or the
  Rules page switches it off.
- **Secrets going out:** see section 8.
- **The test box** (Rules page, `POST /v1/rules/test`, `/rules test`): give a tool, its arguments and the situation
  (chat or unattended, tainted, local only), see the action, the deciding rule or default, what raised it, and every
  matching rule.

### Approval requests

- An `ask` creates a request with the frozen action. You answer **allow once**, **allow always**, **refuse once** or
  **refuse always**; the two "always" answers write a rule. A request expires after `CLARA_APPROVAL_EXPIRE` (24 h)
  and Clara is told it was not done. Finished requests are kept 30 days (the audit log keeps the decisions longer).
  A request to send an email shows the sender, every address, the subject, the attachments and the whole text
  (up to 8,000 characters; other requests show 500); Clara is told only the first 500 characters of it afterwards.
- **In a streamed chat** the turn waits up to `CLARA_APPROVAL_WAIT` (600 s) for your answer, then goes on without the
  action; a later answer runs it and starts a short follow-up turn with the result. A request not answered within 60 s
  is pushed to your other clients too.
- **Non-streaming calls and unattended runs** never wait: the request is stored and pushed, and the run goes on.
- **Who may answer:** each token has an approval scope: `all`, `safe` (everything except destructive actions) or
  `none`. Defaults: web, terminal and console `all`; Discord `safe`; scripts `none`.
- One conversation may have 10 requests waiting at once.

### Audit

Every gate decision is written to the audit log: time, conversation, turn, tool, target, level, situation, a digest of
the arguments (never the arguments themselves), the deciding rule, the effect and the outcome. Kept
`CLARA_RETENTION_DAYS` (400).

## 10. Tools

63 tools. "Untrusted" tools taint the turn (section 9). Tools that need something absent (no workspace, no sandbox, no
vault, no GitHub, Drive, Gmail or Outlook connection, a local-only conversation) are not offered.

| Tool | What it does | Class / level |
| --- | --- | --- |
| `remember` | Save one durable fact (with `replaces`, `expires`, category) | memory / write |
| `recall_facts` | Search remembered facts (10 best) | read |
| `forget` | Delete a fact, by id (tombstone) | memory / write |
| `search_history` | Search earlier conversations (8 best, with dates and excerpts) | read |
| `remind` | Set a reminder (once, daily, weekly, monthly; for some clients) | memory / write |
| `list_reminders` | Reminders to come | read |
| `cancel_reminder` | Cancel a reminder | memory / write |
| `notify` | Notify you now on your clients (3 per turn) | memory / write |
| `add_task` | Add a task or sub-task with reminders | memory / write |
| `list_tasks` | Tasks, or one task in full | read |
| `update_task` | Change a task (title, description, due, reminders, status, parent) | memory / write |
| `delete_task` | Delete a task and its sub-tasks | memory / write |
| `qcm` | Show a form you answer (web) | memory / write |
| `read_conversation_file` | Read a file sent in this conversation (pictures are shown to the model) | read |
| `list_project_files` | List the project's files | read |
| `read_project_file` | Read a project file, with line numbers | read |
| `search_project` | Search the project's files | read |
| `ws_list` | The workspace tree with sizes | read |
| `ws_read` | Read a workspace file, with line numbers (in parts) | read |
| `ws_search` | Search the workspace | read |
| `ws_log` | The git history of the workspace or of a path | read |
| `ws_write` | Create a file, or replace one (`overwrite`) | ws_write / write; ws_destroy / destructive when it overwrites |
| `ws_edit` | Replace an exact passage of a file | ws_write / write |
| `ws_move` | Rename or move inside the workspace | ws_write / write; ws_destroy / destructive when the target exists |
| `ws_delete` | Delete a file or folder (undoable) | ws_destroy / destructive |
| `ws_run` | Run a command in the sandbox (`network` optional) | run / write; run_network / write with network |
| `web_search` | Search the web (SearXNG) — untrusted | web_search / read |
| `web_fetch` | Read a web page — untrusted | fetch_trusted or fetch_other / read |
| `github_read` | Look at a connected repository — untrusted | github_read / read |
| `github_write` | Commit a file change (create, overwrite, append, delete) | github_write / write or destructive |
| `github_branch` | Create or delete a branch | github_write / write or destructive |
| `github_pr` | Open, comment, merge or close a pull request | github_write / write or destructive |
| `github_issue` | Open, comment or close an issue | github_write / write; destructive to close |
| `drive_list` | List what Clara may use on Drive, or a folder — untrusted | drive_read / read |
| `drive_read` | Read a Drive file (Docs, Sheets, Slides as text) — untrusted | drive_read / read |
| `drive_search` | Search the Drive files Clara may see — untrusted | drive_read / read |
| `drive_create` | Create a file (or a Google Doc) | drive_write / write |
| `drive_update` | Replace a file's content | drive_write / destructive |
| `drive_delete` | Move a file to Drive's trash | drive_write / destructive |
| `mail_search` | Search a mailbox, newest first — untrusted | mail_read / read |
| `mail_read` | Read one email: headers, text in parts, attachments — untrusted | mail_read / read |
| `mail_attachment_read` | The text of an attachment (text, code, PDF, Word) — untrusted | mail_read / read |
| `mail_attachment_save` | Keep an attachment as a workspace file, never over an existing one — untrusted | mail_read / write |
| `mail_draft` | Save a draft in the mailbox; nothing is sent | mail_draft / write |
| `mail_send` | Send an email (asks; refused unattended) | mail_send / destructive |
| `day_conversations` | What was said on a day, with the context of conversations begun earlier (pack Day conversations) | read |
| `read_conversation` | One conversation in full, a page at a time | read |
| `day_activity` | Tasks, reminders, scheduled runs and Clara's actions of a day (pack Day activity) | read |
| `files_created` | The files of a day, one short line each (pack Files created) | read |
| `read_created_file` | One of them, whole or as the day's diff | read; drive_read for a Drive file |
| `daily_notes` | A range of daily notes of the vault (pack Daily notes) | read |
| `ask_person` | Ask you one question from a scheduled run and wait for the answer (pack Ask you; scheduled runs only) | memory / write |
| `finish_run` | End the scheduled run, with a summary | memory / write |
| `discord_thread` | Open, write in or remove a Discord thread (pack Discord thread; scheduled runs only) | discord / write |
| `open_second_brain` | Open the vault and its tools for this conversation | read |
| `vault_search` | Search notes | read |
| `vault_read` | Read a note, with line numbers | read |
| `vault_list` | The notes, or a folder's | read |
| `vault_write` | Create a note, replace one, or delete one (to `.trash/`) | vault_write / write; vault_destroy / destructive to replace or delete |
| `vault_edit` | Replace a passage, or append (at the end or under a heading) | vault_write / write |
| `vault_daily` | Read today's daily note, or append a timestamped line | read; vault_write / write to append |
| `vault_daily_block` | Write a block of a day's daily note (replaced at each call, the rest of the note untouched) | vault_write / write |
| `vault_move` | Rename or move a note, updating every link | vault_destroy / destructive |

Client-side tools (`clara-chat --local-tools`: `local_list`, `local_read`) are declared by the client per request
and run on the client.

## 11. Models

- **Providers:** local Ollama hosts (`CLARA_OLLAMA_HOSTS`: this machine, your PC over the tailnet, any number), Ollama
  cloud (`cloud`), Google Gemini (`gemini`), DeepSeek (`deepseek`), Mistral (`mistral`), OpenCode Go (`opencode-go`:
  every model of Go's own list, its `-free` ones included; not OpenCode Zen). Local hosts are enabled at
  first; a hosted provider is enabled from the console or the Models page once its key is stored. Their terms are in
  [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **Catalogue:** models listed by each enabled provider, with context window and capabilities (tools, pictures,
  thinking), refreshed every minute at most; you choose which appear in the pickers (`/models show|hide`). The free models of
  OpenCode (`opencode-go`, names ending in `-free`) appear there until you hide them, after the other models.
- **Models that do not work:** OpenCode lists models of APIs Clara does not speak (Claude, GPT, Grok, Gemini, Muse
  Spark, MiniMax and Qwen models; it answers them with an error). A table made from models.dev
  (`scripts/refresh_opencode_models.py`) says which: those models are never in the pickers, `/models show` and the Models
  page refuse them with the reason, and the Models page leaves them out unless you pick **All** or **Don't work**. The
  same table says which models read pictures: a picture sent to one that reads text only is refused before it is sent.
  A model the table does not have is offered and marked *unverified*; the service answers for it.
- **Default model:** set by you; at first start with none set, Clara picks a local model and says so.
- **Side-jobs model** (`upkeep_model`): titles, summaries, reminder wording; the default model when unset.
- **Fallbacks:** an ordered list, of enabled providers only. When a model fails **before it has said anything**, the
  next is asked: the service is busy or unreachable (408, 409, 425, 429, 500, 502, 503, 504, 529, connection
  errors), the model is gone (404), it stayed silent past `CLARA_FIRST_TOKEN_TIMEOUT`, its key was refused (401, 403)
  or payment is required (402); a key really refused (a 403 that names another reason, such as OpenCode's `FreeTierError`
  for its free models, is not) and payment also raise an alert (`provider_key:<id>`, `provider_payment:<id>`). A
  model of the fallback list that fails for any other reason is skipped too. Once a model has started answering, or
  when the answer's time budget is spent, nothing is asked again. A local model never falls back to a hosted one.
- **Per conversation:** each conversation may have its own model.
- **Usage:** one row per model call (kind `chat`, `title`, `summary`, `reminder`, `task`, `schedule`, `followup`;
  provider, model, client, tokens in, out and cached, duration, outcome), kept `CLARA_RETENTION_DAYS`. No text. No
  limits, no credits.

## 12. Security

The threat model is in [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md); what is stored where in
[docs/DATA-MAP.md](docs/DATA-MAP.md).

- **Network:** the server listens on 127.0.0.1 only (`CLARA_PORT`, 8765). `tailscale serve` publishes it over HTTPS to
  your tailnet (`CLARA_TAILSCALE_PORT`: 443, 8443 or 10000). Clara refuses to start if Funnel is on for that port, and
  if Funnel appears while she runs, she turns it off and raises the `funnel` alert. If serving stops for 2 minutes:
  the `tailscale` alert.
- **Tokens:** one per client, `clara_<id>_<secret>` (256 random bits), stored as a hash, compared in constant time,
  revocable at once without a restart, with a last-used time. Every route needs one except `GET /health` (which
  answers `{"status": "ok"}` and nothing else), the web sign-in, and Google's redirect (which only forwards to the
  signed-in site).
- **Failure limiter:** 10 wrong tokens in a minute block the address for 5 minutes; forwarded addresses are trusted
  only from 127.0.0.1 (tailscale serve).
- **Web:** cookie plus `X-Clara-Web` header (another site cannot post to Clara); no CORS; strict CSP; `nosniff`,
  `no-referrer`, `DENY` framing, same-origin opener, no camera, microphone or location.
- **Request sizes:** 16 MB by default; chat 48 MB, document extraction 41 MB, client tool results 64 MB, project
  uploads 90 MB, workspace uploads 110 MB.
- **API docs:** `/docs` and `/openapi.json` are served only with `CLARA_DEBUG=true`.
- **Secrets store:** provider keys and every other secret (section 14) are encrypted in the database with AES-GCM. The
  key comes from a systemd credential (`clara-secret-key`), `CLARA_SECRET_KEY_FILE` or `CLARA_SECRET_KEY`, never from
  the data folder or the `.env` file: a raw 32-byte key, or a passphrase (16 characters at least) through scrypt
  (N=2^17, r=8, p=1) with a random salt. A check value refuses a wrong key at start. `clara secrets rotate`
  re-encrypts everything under a new key. The API and the clients say only "set" or "not set".
- **Settings:** `.env` holds non-secret settings only; a `*_API_KEY`, `*_PASSWORD` or `*_SECRET` variable is refused.
- **Permissions:** at start Clara checks that the data folder, the database, the settings file and the key file
  belong to her user and are not readable by anyone else (`CLARA_CHECK_PERMISSIONS`), and refuses to start otherwise.
- **One server per data folder:** a lock file (`flock`) refuses a second server.
- **Database:** `PRAGMA secure_delete` is on (deleted text is overwritten); `clara purge` vacuums the file.

## 13. Operations

How to run all this is in [docs/OPERATIONS.md](docs/OPERATIONS.md).

- **systemd:** `clara.service` runs `clara-launcher` as the `clara` system user, with `Restart=on-failure`, a
  watchdog fed only while every loop turns, and a stop timeout above the drain limit. `clara-discord.service` runs the
  bot (or the server starts it: `CLARA_DISCORD_AUTOSTART`). `deploy/install.sh` sets everything up.
- **Graceful stop:** new turns are refused, running answers get `CLARA_DRAIN_SECONDS` (120 s) to finish (progress is
  logged), then they are cut and saved as interrupted. `/stop now` cuts at once.
- **Crash recovery** (after `kill -9` or a power cut): at start, a question whose answer never came is put back,
  approved actions that were running are marked failed with a note to check them (they are never run twice),
  scheduled runs left running are marked interrupted and due again, and workspace changes left uncommitted are
  committed.
- **Updates (tagged releases only):** you pick a release (`/update`, Admin page); nothing installs by itself. Clara
  downloads `clara-<tag>.tar.gz`, `SHA256SUMS`, `manifest.json` and, when `CLARA_RELEASE_PUBKEY` is set,
  `SHA256SUMS.sig` (Ed25519, then required); checks every sum; unpacks safely (no absolute path, `..`, link or device);
  builds a virtualenv with `uv` from the release's hash-pinned `requirements.lock`; runs a preflight of the new code
  against a **copy** of the database (settings, migrations, the secrets key); pulls the sandbox image the release pins
  by digest; takes a backup; then restarts into it. The launcher waits up to 60 s for `/health` and the new boot id;
  if the new release does not come up, it switches back, restores the database if a migration ran, starts the old
  release and you get the `update` alert. Updates need the launcher (`CLARA_LAUNCHED`); a server started by hand says
  so.
- **Restart:** `/restart` (exit code 76) restarts the same release through the launcher.
- **Backups:** every night at `CLARA_BACKUP_TIME` (03:30, your zone), a consistent copy through SQLite's backup API in
  `backups/daily/` (mode 0600, `CLARA_BACKUP_KEEP_DAILY` 14 kept), Sundays also in `backups/weekly/`
  (`CLARA_BACKUP_KEEP_WEEKLY` 8). A copy is also taken before every migration and every update. With
  `CLARA_RESTIC_REPOSITORY`, the newest backup and a `git bundle` of the workspace go to restic (encrypted with the
  `restic_password` secret) on another machine. `clara restore --verify <file>` runs `integrity_check` and counts rows;
  a monthly notification reminds you to try it.
- **Schema versions:** numbered migrations, the version in `PRAGMA user_version`, a backup before each migration, and
  a database newer than the code is refused.
- **Alerts** (in every client's inbox and in Discord's `#alerts`; each repeats at most every 6 hours while it lasts; most clear
  by themselves when the cause is gone, and `/alerts clear <key>` clears one):

  | Key | When |
  | --- | --- |
  | `provider_key:<id>` | A provider refused its key |
  | `provider_payment:<id>` | A provider wants payment |
  | `late:<what>` | A reminder, task reminder or schedule fired more than 5 minutes late |
  | `disk` | The disk holding the data is above `CLARA_DISK_ALERT_PERCENT` (85%) |
  | `discord` | The Discord bot has not checked in for 5 minutes (with `CLARA_DISCORD_EXPECTED` or `CLARA_DISCORD_AUTOSTART`), or the server cannot keep it running (a wrong setting, a refused token or intent, no discord.py) |
  | `vault` | `CLARA_VAULT_PATH` is not a folder |
  | `vault_push` | The vault could not be pulled or pushed |
  | `backup` | A backup, its check or its remote copy failed |
  | `github` | GitHub refused the token |
  | `google` | Google refused the refresh token (reconnect Drive) |
  | `gmail` | Google refused the Gmail refresh token (reconnect Gmail) |
  | `outlook` | Microsoft refused the Outlook refresh token (reconnect Outlook) |
  | `update` | An update was not installed, or was rolled back |
  | `funnel` | Funnel was found on and turned off |
  | `tailscale` | Tailscale has not been serving Clara for 2 minutes |

- **Traffic log:** one line per request with metadata only (time, method, route with ids replaced, status, duration,
  client, token name, model, token counts, outcome), daily files kept `CLARA_TRAFFIC_LOG_DAYS` (7), mode 0600; failed
  sign-ins are recorded with the address. A failed model call adds the kind of failure, the HTTP status and the
  provider's own short code for it (`400 invalid_value`, `403 FreeTierError`): a plain token, never the provider's
  message, which can quote what you wrote. No message text, no headers, no bodies.
- **Live log:** the Admin page's **Logs** tab (`GET /v1/admin/logs/stream`) shows the server's own log (INFO and up)
  and the traffic records (requests, model calls, refused sign-ins) as they happen, filtered by source and level on
  the server. It keeps the last 500 lines in memory only (a restart empties them; nothing is written for it, and it
  works whatever `CLARA_TRAFFIC_LOG_DAYS` is). Anything that looks like a secret is masked, the address of a failed
  sign-in is left out, and the debug content never reaches it.
- **Debug switch:** `/debug on` writes full prompts and answers, and the text of failed model calls, to
  `logs/debug-content.jsonl` (0600) for one hour, then switches itself off and deletes the file.
- **`clara doctor`:** settings, permissions, the database, the secrets key, Podman (rootless, cgroups, subuid/subgid,
  the image, isolation; `--pull` pulls the image first), Funnel, Tailscale, each Ollama host, SearXNG, restic, the disk.
- **Status:** `/status`, the Admin page and `GET /v1/admin/status`: version and release, state, uptime, turns, models,
  providers, storage, scripts, vault, Tailscale, backups, event listeners, clients seen, alerts, debug, today's usage,
  pending approvals.
- **Retention:** events are deleted once every active listener has read them (and they are a day old), at the latest
  after `CLARA_EVENTS_MAX_DAYS` (90); a token silent for 30 days stops holding them back. Usage rows and audit lines:
  `CLARA_RETENTION_DAYS` (400). Finished approval requests: 30 days. Incognito conversations: 24 h idle. Other
  conversations and facts are kept until you delete them.

`clara` (run as the service user) has: `version`, `token create|list|revoke`, `secrets new-key|list|set|unset|rotate`,
`doctor [--pull]`, `backup [now|init-remote]`, `restore <file> [--verify]`, `migrate`, `purge`, `preflight --database <file>`.

## 14. Settings

### Environment (`/etc/clara/clara.env`, non-secret only)

| Setting | Default | Meaning |
| --- | --- | --- |
| `CLARA_DATA_DIR` | `/var/lib/clara` (or systemd's `STATE_DIRECTORY`) | Database, backups, logs, run files |
| `CLARA_PORT` | 8765 | The local port (127.0.0.1 only) |
| `CLARA_TIMEZONE` | the machine's | Your zone (IANA name) |
| `CLARA_PERSONA_FILE` | the shipped persona | Clara's fixed system prompt |
| `CLARA_DEBUG` | false | Serve `/docs` and the OpenAPI file |
| `CLARA_SECRET_KEY_FILE` | the `clara-secret-key` credential | File holding the secrets key |
| `CLARA_SECRET_KEY` | — | The secrets key itself (environment only, never in `.env`) |
| `CLARA_CHECK_PERMISSIONS` | true | Refuse to start with files others can read |
| `CLARA_OLLAMA_HOSTS` | `local=http://127.0.0.1:11434` | Local Ollama hosts, `id=url,…` |
| `CLARA_OLLAMA_CONTEXT` | 32768 | Context window asked of local models |
| `CLARA_CLOUD_HOST` | `https://ollama.com` | Ollama cloud |
| `CLARA_GEMINI_HOST`, `CLARA_DEEPSEEK_HOST`, `CLARA_MISTRAL_HOST` | the providers' API addresses | Hosted providers |
| `CLARA_OPENCODE_GO_HOST` | `https://opencode.ai/inference/go` | OpenCode Go (Clara adds `/openai/v1` for chat and `/v1/models` for the list) |
| `CLARA_CLOUD_CONTEXT`, `CLARA_GEMINI_CONTEXT`, `CLARA_DEEPSEEK_CONTEXT`, `CLARA_MISTRAL_CONTEXT`, `CLARA_OPENCODE_GO_CONTEXT` | per provider | Context window assumed when the provider does not say |
| `CLARA_DEEPSEEK_THINKING` | true | Use DeepSeek's thinking mode |
| `CLARA_HISTORY_TURNS` | 20 | Turns of history sent at most |
| `CLARA_MAX_TOOL_ROUNDS` | 20 | Tool rounds per answer |
| `CLARA_CHAT_SLOTS` | 2 | Model calls at once for chats |
| `CLARA_BACKGROUND_SLOTS` | 1 | Model calls at once for side jobs |
| `CLARA_FIRST_TOKEN_TIMEOUT` | 300 | Seconds to the first token |
| `CLARA_IDLE_TIMEOUT` | 120 | Seconds of silence once started |
| `CLARA_ANSWER_BUDGET` | 900 | Seconds of model time per answer |
| `CLARA_CLIENT_TOOL_TIMEOUT` | 900 | Seconds a client has to return client-side tool results |
| `CLARA_COMPACT_PERCENT` | 80 | Compact past this share of the window (0: never by share) |
| `CLARA_COMPACT_TOKENS` | 40000 | …or past this many tokens (0: never by count) |
| `CLARA_KEEP_RECENT_TURNS` | 2 | Turns kept whole when compacting |
| `CLARA_PURGE_SUMMARISED` | false | Delete messages once summarised |
| `CLARA_FACTS_TOKEN_BUDGET` | 2000 | Tokens of facts in a prompt |
| `CLARA_APPROVAL_WAIT` | 600 | Seconds a streamed chat waits for an approval |
| `CLARA_APPROVAL_EXPIRE` | 86400 | Seconds before a request expires |
| `CLARA_NOTIFY_LONG_TURN` | 120 | Notify when an answer took longer (0: never) |
| `CLARA_REMINDER_AI_TIMEOUT` | 60 | Seconds the model has to word a reminder (0: stored text) |
| `CLARA_TASK_MAX_REMINDERS` | 10 | Reminders per task |
| `CLARA_INCOGNITO_HOURS` | 24 | Idle hours before an incognito conversation is deleted |
| `CLARA_WORKSPACE` | `<data>/workspace` | The workspace |
| `CLARA_WORKSPACE_MAX_SIZE` | 5g | Workspace size limit |
| `CLARA_WORKSPACE_MAX_FILE` | 100m | One file's limit |
| `CLARA_RUN` | auto | `auto`: scripts when the sandbox is ready; `off` |
| `CLARA_PODMAN` | podman | The Podman binary |
| `CLARA_SANDBOX_IMAGE` | `localhost/clara-sandbox:latest` | Script image (a release pins its own) |
| `CLARA_RUN_TIMEOUT` | 120 | Default seconds per run |
| `CLARA_RUN_MAX_TIMEOUT` | 900 | Longest run allowed |
| `CLARA_RUN_MEMORY` | 1g | Memory per run |
| `CLARA_RUN_CPUS` | 2 | CPUs per run |
| `CLARA_RUN_PIDS` | 256 | Processes per run |
| `CLARA_RUN_SLOTS` | 2 | Runs at once |
| `CLARA_RUN_OUTPUT` | 64k | Output given to the model |
| `CLARA_PROJECT_MAX_CHARS` | 20000000 | Text of one project |
| `CLARA_PROJECT_MAX_FILES` | 5000 | Files of one project |
| `CLARA_PROJECT_INLINE_PERCENT` | 40 | A project under this share of the window goes whole in the prompt |
| `CLARA_PROJECT_INLINE_TOKENS` | 20000 | …and under this many tokens |
| `CLARA_VAULT_PATH` | — | Your Obsidian vault (a git clone) |
| `CLARA_VAULT_PUSH` | true | Push the vault after changes |
| `CLARA_VAULT_PULL_SECONDS` | 30 | Pull interval (0: never) |
| `CLARA_DAY_DIGEST_CHARS` | 60000 | Most characters `day_conversations` gives (and at most a fifth of the model's window) |
| `CLARA_WAITING_REMIND_AT` | 09:00 | When a scheduled run still waiting for your answer is reminded (HH:MM, after three hours at least) |
| `CLARA_SEARXNG_URL` | `http://127.0.0.1:8888` | SearXNG |
| `CLARA_FETCH_MAX_SIZE` | 2m | Largest page fetched |
| `CLARA_PUBLIC_URL` | the Tailscale address | Your `https://` address, for Google's redirect |
| `CLARA_GOOGLE_CLIENT_ID` | — | Google OAuth client (web application) |
| `CLARA_GOOGLE_PROJECT_NUMBER` | — | Google Cloud project number (the Picker) |
| `CLARA_MICROSOFT_CLIENT_ID` | — | Microsoft Entra (Azure) app registration, platform Web (Outlook) |
| `CLARA_MICROSOFT_TENANT` | `common` | `common`, `organizations`, `consumers`, a tenant id or a tenant domain |
| `CLARA_MAIL_POLL_SECONDS` | `120` | How often the inbox is looked at for new-mail notifications; `0` never; 30 at least |
| `CLARA_TAILSCALE` | serve | `serve` or `off` |
| `CLARA_TAILSCALE_PORT` | 443 | 443, 8443 or 10000 |
| `CLARA_TAILSCALE_BIN` | tailscale | The tailscale binary |
| `CLARA_TRAFFIC_LOG_DAYS` | 7 | Days of traffic log (0: none) |
| `CLARA_BACKUP_DIR` | `<data>/backups` | Local backups |
| `CLARA_BACKUP_TIME` | 03:30 | Nightly backup time (your zone) |
| `CLARA_BACKUP_KEEP_DAILY` | 14 | Daily backups kept |
| `CLARA_BACKUP_KEEP_WEEKLY` | 8 | Weekly backups kept |
| `CLARA_RESTIC_REPOSITORY` | — | Remote copy (restic `sftp:` or `rest:`) |
| `CLARA_RESTIC_BIN` | restic | The restic binary |
| `CLARA_RELEASE_REPO` | — | `owner/name` of the GitHub releases |
| `CLARA_RELEASES_DIR` | `/opt/clara/releases` | Installed releases (`current` is next to it) |
| `CLARA_RELEASE_PUBKEY` | — | Ed25519 public key (base64): releases must then be signed |
| `CLARA_DRAIN_SECONDS` | 120 | Longest drain at stop |
| `CLARA_DISK_ALERT_PERCENT` | 85 | Disk alert threshold |
| `CLARA_LATE_ALERT_SECONDS` | 300 | Lateness that raises an alert |
| `CLARA_DISCORD_EXPECTED` | false | Alert when the Discord bot stops checking in |
| `CLARA_DISCORD_AUTOSTART` | false | Start the Discord bot with the server (and expect it) |
| `CLARA_RETENTION_DAYS` | 400 | Usage rows and audit lines |
| `CLARA_EVENTS_MAX_DAYS` | 90 | Longest an event is kept |
| `CLARA_ENV_FILE` | `.env` | The settings file read at start |

Sizes accept `k`, `m` and `g`. A wrong value stops the start with the list of every problem found.

### Runtime settings (changed while Clara runs: console, Admin page)

| Setting | Meaning |
| --- | --- |
| `default_model` | The model that answers when a conversation has none of its own |
| `upkeep_model` | The model for titles and summaries (empty: the default model) |
| `fallbacks` | Models asked in turn when the one answering fails |
| `providers_enabled` | Providers that may be used (local Ollama hosts are enabled at first) |
| `tainted_rule` | The tainted-turn rule (on) |
| `debug_until` | The debug content log's end (empty: off) |
| `sandbox_image` | The sandbox image pinned by the last release (empty: `CLARA_SANDBOX_IMAGE`) |
| `hidden_tools` | Tools not offered to some clients: `{client: [tool, …]}` |
| `mail_notify` | Notify you of new mail in the inbox of a connected mailbox (off) |
| `mail_notify_senders` | Only for senders whose name or address contains one of these words (empty: all) |

### Secrets (encrypted store: `clara secrets set <name>`, the Admin page)

| Name | Purpose |
| --- | --- |
| `ollama_api_key` | Ollama cloud API key (provider `cloud`) |
| `gemini_api_key` | Google Gemini API key |
| `deepseek_api_key` | DeepSeek API key |
| `mistral_api_key` | Mistral API key |
| `opencode_go_api_key` | OpenCode Go API key |
| `github_token` | GitHub fine-grained personal access token (set from the Connections page) |
| `google_client_secret` | Google OAuth client secret (Drive and Gmail) |
| `google_refresh_token` | Google refresh token (set by connecting Drive, never by hand) |
| `gmail_refresh_token` | Gmail refresh token (set by connecting Gmail, never by hand) |
| `microsoft_client_secret` | Microsoft (Azure) app client secret (Outlook) |
| `outlook_refresh_token` | Outlook refresh token (set by connecting Outlook, never by hand; Microsoft renews it at each use) |
| `google_picker_api_key` | Google API key for the Drive Picker |
| `vault_git_token` | Token to push the vault over HTTPS |
| `restic_password` | Password of the restic repository |
| `release_token` | Optional GitHub token for release downloads |

## 15. HTTP API

Everything is under `/v1`, needs `Authorization: Bearer <token>` (or the web cookie with `X-Clara-Web: 1`), may carry
`X-Clara-Client`, and answers errors as `{"error": {"code": …, "message": …}}`. Streams are server-sent events with a
keep-alive every 15 s.

| Route | Does |
| --- | --- |
| `GET /health` | `{"status": "ok"}`, no token |
| `POST /v1/auth/web-login` | Exchange a token for the web cookie |
| `POST /v1/auth/logout` | End the web session |
| `GET /v1/auth/me` | The calling token: name, client, approval scope |
| `POST /v1/chat/stream` | Chat, streamed (section 3) |
| `POST /v1/chat` | Chat, the whole answer at once |
| `POST /v1/turns/{turn_id}/tool-results` | Results of client-side tools |
| `POST /v1/documents/extract` | The text of a document (as the chat would see it) |
| `GET /v1/conversations` | List and search conversations (`kind` chat, schedule or any; `history=true` leaves out those of a project or schedule not shown in the history; `hide=discord,console` leaves out what those clients started, the pinned ones apart; each names its `group`; a sub agent's conversation is never listed) |
| `POST /v1/conversations` | Create one (incognito, local only, model, project) |
| `GET /v1/conversations/{conversation_id}` | One conversation, with its context, files, waiting approvals and `subagents` (the console sub agents' conversations it started, each with the `prompt` it was given) |
| `PATCH /v1/conversations/{conversation_id}` | Rename, pin, change model or project, local only |
| `DELETE /v1/conversations/{conversation_id}` | Delete it, with its files |
| `GET /v1/conversations/{conversation_id}/messages` | Its messages (the chosen versions) |
| `POST /v1/conversations/{conversation_id}/compact` | Summarise it now |
| `POST /v1/conversations/{conversation_id}/title` | Write its title again |
| `POST /v1/conversations/{conversation_id}/version` | Choose which version of a message is shown |
| `GET /v1/conversations/{conversation_id}/files/{file_id}` | Download a file sent in it |
| `GET /v1/memory/facts` | Facts (by state, search) |
| `POST /v1/memory/facts` | Add a fact (source `you`) |
| `PATCH /v1/memory/facts/{fact_id}` | Change, pin, set expiry |
| `POST /v1/memory/facts/{fact_id}/confirm` | Confirm an unconfirmed fact |
| `DELETE /v1/memory/facts/{fact_id}` | Forget it |
| `GET /v1/reminders` | Reminders to come |
| `POST /v1/reminders` | Add one |
| `DELETE /v1/reminders/{reminder_id}` | Cancel one |
| `GET /v1/tasks` | Tasks |
| `POST /v1/tasks` | Add one |
| `GET /v1/tasks/{task_id}` | One task with its sub-tasks |
| `PATCH /v1/tasks/{task_id}` | Change one |
| `DELETE /v1/tasks/{task_id}` | Delete one |
| `GET /v1/packs` | The special tools (packs) for a chat or a schedule, with whether each can be used now |
| `GET /v1/schedules` | Schedules |
| `POST /v1/schedules` | Add one |
| `GET /v1/schedules/{schedule_id}` | One schedule with its runs |
| `PATCH /v1/schedules/{schedule_id}` | Change, pause or resume one; `in_history` lists its conversations in the web history |
| `POST /v1/schedules/{schedule_id}/run` | Run it now |
| `POST /v1/schedules/{schedule_id}/close` | Close the run that waits for your answer, from what Clara has |
| `DELETE /v1/schedules/{schedule_id}` | Delete one |
| `GET /v1/events/stream` | Live events (after your cursor) |
| `GET /v1/events` | Events you missed |
| `POST /v1/events/ack` | Move your cursor |
| `POST /v1/notifications` | Notify yourself |
| `GET /v1/projects` | Projects |
| `POST /v1/projects` | Create one |
| `GET /v1/projects/{project_id}` | One, with its files and sources |
| `PATCH /v1/projects/{project_id}` | Change name, description, instructions, pin; `in_history` lists its conversations in the web history |
| `DELETE /v1/projects/{project_id}` | Delete one |
| `POST /v1/projects/{project_id}/files` | Upload files or archives (multipart) |
| `DELETE /v1/projects/{project_id}/files` | Remove files |
| `GET /v1/projects/{project_id}/file` | Read one file |
| `POST /v1/projects/{project_id}/github` | Add a GitHub repository snapshot |
| `POST /v1/projects/{project_id}/sources/{source_id}/sync` | Sync it again |
| `DELETE /v1/projects/{project_id}/sources/{source_id}` | Remove it |
| `GET /v1/rules` | Rules, defaults, the tainted switch, tool names |
| `POST /v1/rules` | Add a rule |
| `PATCH /v1/rules/{rule_id}` | Change a rule (or switch it off) |
| `DELETE /v1/rules/{rule_id}` | Delete a rule |
| `PUT /v1/rules/tainted` | The tainted-turn rule on or off |
| `POST /v1/rules/test` | Which rule decides this call |
| `GET /v1/approvals` | Approval requests (by status) |
| `POST /v1/approvals/{approval_id}/decide` | Answer one |
| `GET /v1/audit` | The audit log |
| `GET /v1/models` | What a model picker shows |
| `GET /v1/admin/providers` | Providers |
| `PUT /v1/admin/providers/{provider_id}` | Enable or disable one |
| `POST /v1/admin/providers/{provider_id}/check` | Check one |
| `GET /v1/admin/models` | The catalogue (`refresh=true` to list again); each model has a `fit` (`works`: true, false, or null when unknown; `why`) |
| `PATCH /v1/admin/models` | Show or hide models in pickers |
| `PUT /v1/admin/models/default` | The default model |
| `PUT /v1/admin/models/upkeep` | The side-jobs model |
| `PUT /v1/admin/models/fallbacks` | The fallback list |
| `GET /v1/admin/status` | Status (section 13) |
| `GET /v1/admin/tokens` | Tokens |
| `POST /v1/admin/tokens` | Create one (the secret is shown once) |
| `PATCH /v1/admin/tokens/{name}` | Change its approval scope |
| `DELETE /v1/admin/tokens/{name}` | Revoke it (not the one you are using) |
| `GET /v1/admin/secrets` | Secrets: set or not, never values |
| `PUT /v1/admin/secrets/{name}` | Set one |
| `DELETE /v1/admin/secrets/{name}` | Remove one |
| `GET /v1/admin/settings` | Runtime settings |
| `PUT /v1/admin/settings/{key}` | Change one |
| `POST /v1/admin/debug` | The debug content log on (one hour) or off |
| `GET /v1/admin/commands` | The console commands |
| `POST /v1/admin/command` | Run a console command |
| `GET /v1/admin/usage` | Usage statistics |
| `GET /v1/admin/usage/history` | The last model calls |
| `GET /v1/admin/logs/stream` | The live log (server-sent events): the last lines, then each new one; `source` (`all`, `app`, `traffic`) and `level` (`INFO`, `WARNING`, `ERROR`, a minimum) |
| `POST /v1/admin/stop` | Stop (graceful, or `now`) |
| `POST /v1/admin/restart` | Restart through the launcher |
| `GET /v1/admin/backups` | Backups |
| `POST /v1/admin/backups` | Back up now |
| `GET /v1/admin/releases` | Releases and the updater's state |
| `POST /v1/admin/update` | Install a release |
| `GET /v1/admin/alerts` | Active and recent alerts |
| `POST /v1/clients/heartbeat` | A client says it is alive (the Discord bot) |
| `GET /v1/workspace` | Workspace status |
| `GET /v1/workspace/tree` | The tree |
| `GET /v1/workspace/file` | Read a file |
| `PUT /v1/workspace/file` | Write a file (committed) |
| `DELETE /v1/workspace/file` | Delete a file or folder (committed) |
| `POST /v1/workspace/upload` | Upload files (multipart) |
| `GET /v1/workspace/log` | The git log |
| `GET /v1/workspace/commits/{commit}` | One commit's diff |
| `POST /v1/workspace/undo` | Revert the last change, or a commit |
| `GET /v1/workspace/runs` | Script runs |
| `GET /v1/connections` | GitHub, Drive, Gmail and Outlook: state, repositories, files; the mail notification settings |
| `PUT /v1/connections/github` | Set the GitHub token |
| `DELETE /v1/connections/github` | Forget it |
| `POST /v1/connections/github/repos` | Add a repository |
| `DELETE /v1/connections/resources/{resource_id}` | Remove a repository, file or folder |
| `POST /v1/connections/google/start` | Start the Google consent (web session only) |
| `GET /v1/connections/google/callback` | Google's redirect (forwards to the site; no token, does nothing else) |
| `POST /v1/connections/google/finish` | Exchange the code; returns the Google address |
| `POST /v1/connections/google/confirm` | Attach that account |
| `DELETE /v1/connections/google` | Disconnect (revoke at Google) |
| `GET /v1/connections/google/picker` | What the picker window needs (web session only) |
| `POST /v1/connections/google/picked` | Files and folders you opened to Clara |
| `POST /v1/connections/mail/{mailbox}/start` | Start the Gmail or Outlook consent (web session only) |
| `GET /v1/connections/outlook/callback` | Microsoft's redirect (forwards to the site; no token, does nothing else) |
| `POST /v1/connections/mail/{mailbox}/finish` | Exchange the code and prove the mailbox is reachable; returns its address |
| `POST /v1/connections/mail/{mailbox}/confirm` | Attach that mailbox |
| `DELETE /v1/connections/mail/{mailbox}` | Disconnect (Gmail: revoked at Google; Outlook: forgotten here) |
| `GET /v1/vault` | Vault status |
| `GET /v1/vault/notes` | The notes |
| `GET /v1/vault/search` | Search |
| `GET /v1/vault/note` | Read a note |
| `POST /v1/vault/sync` | Pull and push now |
| `POST /v1/vault/reindex` | Rebuild the search index |

The web site itself is served at `/` (with `/assets/…` and `/picker.html`).

## 16. Limits

| What | Limit |
| --- | --- |
| Message | 2,000,000 characters |
| Attachments per message | 4 files and 34 MB in all; pictures 8 MB, animations 30 MB (6 frames shown), documents 30 MB; 150,000 characters of document text per message; 12 pictures shown per message |
| Files kept per conversation | 20 |
| Fact | 300 characters; category 32 |
| Reminders | 200; 500 characters each |
| Tasks | 1,000 in all, 200 open, 50 sub-tasks each, 10 reminders each |
| Schedules | 100; prompt 20,000 characters; documents 150,000 characters |
| Approval requests waiting per conversation | 10 |
| Notifications | 3 per turn from Clara; 10 a minute from clients |
| Event text | 2,000 characters; title 100 |
| Forms | 20 questions, 2 to 10 options |
| Projects | 90 MB and 1,000 files per upload; `CLARA_PROJECT_MAX_CHARS`, `CLARA_PROJECT_MAX_FILES`; a file over 30 MB in an archive is skipped; 600 MB unpacked per archive; GitHub archives 300 MB |
| Workspace | `CLARA_WORKSPACE_MAX_SIZE`, `CLARA_WORKSPACE_MAX_FILE`; reads 1,000 lines at once; 80 search matches |
| Vault note | 400,000 characters; 20 search results |
| Special tools | `day_conversations` `CLARA_DAY_DIGEST_CHARS`; `files_created` 60 files a page; `daily_notes` 31 days; a question of `ask_person` 2,000 characters; 31 missed or queued runs per schedule |
| GitHub and Drive files | 30 MB read; 1,000,000 characters written |
| Mail | a search lists 25 messages at most; 20 recipients, a subject of 300 and a text of 6,000 characters, 5 attachments, 5 emails per answer; an attachment over 25 MB is not fetched and one over 10 MB is not parsed for text |
| Web page | `CLARA_FETCH_MAX_SIZE`; 5 redirects; 40 links listed |
| Request bodies | see section 12 |
