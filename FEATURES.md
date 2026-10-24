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
| `clara-discord` | Discord bot in your private server, answers your user id only: its conversations are threads in forums, `#general` says what is pending (separate process) | `discord` |
| Your scripts | The HTTP API and its event stream | `script` |

### Web site

Sign in once by pasting a `web` token; the browser keeps an HttpOnly, Secure, SameSite=Strict cookie (30 days idle,
90 days at most) and sends the `X-Clara-Web: 1` header with every call.

The design is the first Clara site's ("Cyanotype"): a Prussian-blue navigation rail in both themes with Clara's
portrait, the page in cool chalk (or deep blue in the dark theme), one violet signal for what is live or waits for you,
Hanken Grotesk and Epilogue served by Clara herself (SIL Open Font Licence, the licences at `/fonts/`), line icons.
The rail holds **New chat** (the logo leads to the same place: the chat has no link of its own), the pages you work in (Projects, Tasks & reminders, Automation, Library),
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
| Chat | Streamed answers with thinking, Clara's portrait beside each answer, tool calls in the order she made them (a run of calls is one line that opens on each call's arguments and result), Markdown with LaTeX (KaTeX) and code; the **"+" menu** of the box you write in (add files, switch special tools and web search on or off, the second brain, choose the project; the choices are saved with the conversation) and drag and drop for files (PDF, Word, text, pictures, animations); approval requests inline; the **side panel** on the right of a conversation (a button in its header, with a count; closed until something arrives, it opens by itself on the first card unless you closed it, and on a QCM when Clara makes one; your choice is kept in the browser; a drawer on a phone): one **card** for each workspace file Clara wrote in the conversation (name, folder, when it was first written, **Download**), for each vault note she created or edited in it (name, Created or Edited, folder, when, size) and for each QCM (title, questions, how far you are). A click on a file or a note leads to the **Library** and opens it there (`#/library?path=…` for a file, `#/library?view=vault&note=…` for a note): the Library is the whole workspace and the whole vault. A QCM card replaces the list with the quiz, one question at a time as in NotebookLM: a progress bar and arrows, lettered options, **Hint**, **Check** (right and wrong are coloured at once from Clara's key and her correction follows in the chat), **Skip** (Clara is not woken), the explanation, and at the end a score with each question's result and **Retry**; answers wait in a queue while Clara is still correcting the one before. The chat keeps a chip where the QCM was made. `ask_questions` replaces the box you write in with its questions, one after the other (keys 1 to 4, a last "Other" line where you write anything, **Back**, a review step before **Submit**, Escape dismisses); edit a message or ask again (versions, with arrows between them); a reply that failed or was cut off has **Try again** (its question is asked again, as a new version when it is stored); stop an answer (it goes on if you leave the conversation or close the page: open it again and you see what it did and the rest as it comes); pick the model per conversation; start an **incognito** or a **local-only** conversation; compact it; notes Clara writes in `notes/` appear as cards you open or download (files sent in a conversation are downloaded through the API) |
| Archive | Each conversation has an **Archive** button next to its Delete button (and in its row menu): an archived one is kept whole, can be read (Clara reads it with her tools as any other), and takes no new message, from the web, Discord or a scheduled or hook run (which then fails). The rail lists the archived ones under **Archived**; **Unarchive** brings one back. Archived in Discord, it is archived here, and the other way round |
| Conversations list | In the rail under the pages, on every page: one list shared by every client, searchable; pinned ones first, then the **projects**, the **schedules** and the **hooks** that are switched on, each a group that opens on its conversations (the latest 5, then "Show more"; open or closed is remembered in the browser), then the loose conversations, newest first; pin, rename, delete. A project, a schedule or a hook shows its conversations here only when its **Show in history** button is on (all start off); a search looks through all of them, and a pinned conversation stays in Pinned whatever its group. A switch in the account menu, **Discord conversations** (on at first, kept in the browser), leaves out of the list what Discord started; a search and the pinned ones still show them. A conversation made by a schedule's or a hook's run belongs to that schedule or hook even when it has a project |
| Memory (Settings) | Facts: live, unconfirmed, expired, superseded, all; search; add, edit, pin, confirm, forget; source and dates shown |
| Tasks & reminders | Tasks with sub-tasks, due dates and reminders (list and calendar views); reminders to come; add and cancel |
| Automation | One page for what Clara does by herself, unattended: one list of her **schedules** (at a time) and **hooks** (when something happens), and one **New automation** button. Its first step says what sets it off, and the steps after it show what fits that choice. The old address `#/schedules` still leads there. Another kind of automation is one more trigger in that first step. Clara can do the same from a chat when its **Automations** special tool is on (the "+" menu): each change she makes is an approval request you read first |
| Automation → Schedules | The items at a time: prompts that run unattended on a schedule, made with **New automation** (*At a time*): add, edit, pause, run now, see past runs and their summaries; tick the **special tools** (packs) its runs get, choose one conversation for all runs or one per run, what happens to runs missed while the server was off and to a run that comes due before the last one is over, and a model of its own, and how many tries a run gets when the model is busy (1 to 3, 3 by default); answer or close a run that waits for you; **Show in history** lists its conversations in the rail |
| Automation → Hooks | The items set off by an event: what each waits for, in words, with what each waits for in words, its tools, its pace, what is waiting and its last run; **New automation** (with a kind of event as its trigger) and **Edit**, with a condition builder (a field, a comparison, a value; commas mean « or »; a GitHub repository is picked from the ones added on Connections), run Clara or only notify, the tools (the safe default, or a list to tick), the project of the runs (none by default; its files and instructions are context for every run), and under *More options* the backlog policy, the hourly limit, how old a waiting run may get, a model, tries, own-account mail and repeating an interrupted run; **Test** (which event, the verdict, the tools, and the exact message Clara would get, nothing run); **Runs** (status, the event, what happened, a link to each conversation); **Pause**, **Show in history** (lists the hook's runs as a group of its own in the rail), **Delete**; *the events seen*. It warns, for a source a hook uses, when no mailbox is connected or no repository added, or when its polling is off, and reloads by itself while a run goes or waits |
| Projects | **New project** opens a stepped form like an automation's: *Basics* (name, description, instructions), *Content* (a card for each way to start: upload files, a folder, a GitHub repository, or start empty), *Tools* (a card for each special tool: the ones its new conversations start with) and *Review*; the project and its files are made when you confirm. On a project's page: upload files and archives, add a GitHub repository snapshot, sync or remove it, read files, set instructions, and the **Special tools** panel (a card per tool, **Save the tools**); **Show in history** lists its conversations in the rail. Its card in the list says which tools beyond web search it starts with |
| Library | One page for the files Clara keeps, with a switch at its top between its views: **Workspace** (the default) and **Vault** (`#/library?view=vault`). The old addresses `#/workspace` and `#/vault` still lead there. A link can name what to open: `#/library?path=notes/plan.md` opens that file in the Workspace view, `#/library?view=vault&note=Ideas/Kayak.md` that note in the Vault view (the chat's side panel uses them). Another place is one more view |
| Library → Workspace | File tree, read and edit files, upload, delete, git log with each commit's diff, undo, script runs and their logs (three small tabs: Files, History, Script runs) |
| Library → Vault | Status of the Obsidian vault, search, read notes, sync, reindex |
| Connections (Settings) | GitHub token and the repositories Clara may use, with since when hooks look at each; Google Drive consent (the Google address is shown before you confirm), the files and folders you opened to Clara with the Google Picker, disconnect (the token is revoked at Google) |
| Rules & approvals (Settings) | Requests waiting (answer them), recent ones, the rule list (add, edit, switch off, delete), the tainted-turn switch, the defaults table, the test box (which rule decides a given call), the audit log |
| Models (Settings) | Providers (enable, check), the catalogue (show in pickers, set default, side-jobs model, fallbacks in order), a selector **Work with Clara / All / Don't work** that keeps the models Clara cannot use out of the way, each with its reason |
| Usage (Settings) | Token counts by day, model, kind, provider or client over 24 h to all time; the last calls |
| Admin (Settings) | Status, alerts, restart and stop; tokens (create, revoke); secrets (one panel per group, each with a link to the provider's page where its value is made: Ollama's keys page for `ollama_api_key`, and so on; set, replace or remove, values never shown; the refresh tokens are made by connecting, with a link to Connections); runtime settings; **Discord** (the bot's state and the channels it sees, which channel carries each kind of message, a test message per channel); debug switch; backups (list, back up now); releases (notes, install); the console |

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
| `/triggers [list] \| show \| add \| edit \| on \| off \| del \| test \| runs \| events` | Hooks: what Clara does when something happens (a mail arrives) |
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
- Answers **only** `CLARA_DISCORD_USER_ID`, and only in its forums (the threads of the Discord mirror, below), in a thread of
  a scheduled run under `#general`, and in `/` commands; every other user, channel and server is ignored without a reply. At start it finds the four channels: if one is missing, is not a text channel, or they are not all
  in one server, it refuses to start and says which. Only one bot runs per state file.
- **Conversations on Discord (the mirror).** Each place of the history is a **category** in your server with one forum
  in it: a project, a schedule or a hook (its name) whose **Show in history** is on; **`Clara`** for the conversations of
  none of them (the main forum); **`Clara · incognito`** for the incognito ones. Each conversation of those places is a
  **thread** (a post) in its forum, named by its title. Local-only conversations are never on Discord.
  - *Web to Discord:* a question and its answer are posted in the thread, the question as its user's words (documents
    named, not repeated) and the answer with a line of the tools Clara used (`🔧 Searched the web ×24, read a page ×6 · 31
    actions`, the web site's words; the results of tools are never shown); a long answer is split at 2,000 characters.
    A new conversation makes its thread; a conversation that had turns before it had a thread (its project, schedule or
    hook was not shown in the history yet, or the bot was off) gets it when its next question is asked, made whole: the
    thread starts with its first question, the earlier turns follow in order, then the new question; a rename of the conversation renames the thread; a question asked again or
    edited replaces its messages (the old ones are removed, the new ones posted); a deleted conversation removes its thread.
  - *Discord to Clara:* what you write in a thread goes to its conversation as the web site would send it: the same
    tools (the conversation's special tools, `/tools`), the same rule gate, files attached (as on the web). A new post in
    the main forum, in a project's forum or in the incognito forum starts a conversation there. An automation's forum
    takes no new post (its conversations are made by its runs). Editing your last question in Discord asks Clara again,
    as the web site does; only the last one can be changed, and your messages are never deleted by Clara.
  - *The answer is written as it comes:* the bot posts a message when a turn starts and edits it while Clara writes
    (a line of tools, then the text, at most about once every 1.5 seconds), then gives it its final text. Only the answer
    and the questions are written, never the results of tools.
  - *Archive:* an archived conversation is kept whole in both places, and takes no new message (a message in Discord is
    refused, and the thread is archived again, unless Discord archived it on its own after a week, which reopens the
    conversation). A conversation whose project, schedule or hook is switched off in **Show in history** is archived, and
    unarchived again when it is switched back on.
  - *Deletions:* deleting a conversation, a project, a schedule or a hook on the web site deletes its thread or forum, as
    it is asked now. Deleting a thread or a forum in Discord asks for your approval on the web site (the bot's token cannot
    approve a deletion); once you approve, the conversation (or the project, schedule or hook and its conversations; a
    main or incognito forum's conversations) is deleted here. Refuse it and nothing is deleted; the conversation then has
    no thread.
  - *The first copy:* the first sync makes the categories, forums and threads of every conversation that is shown, and
    posts their messages; a later change is sent as a change. Discord's limits (500 channels and 50 categories per server)
    stop the mirror: nothing more is made, the Admin page and `#alerts` say so, and **Resume the mirror** on the Admin page
    starts it again.
  - The bot needs *Manage Channels*, *Create Public Threads*, *Send Messages in Threads*, *Manage Threads*, *Read Message
    History*, *Attach Files* and *View Channel* in your server.
- Files and pictures you attach in a thread go with the message (4 files, 25 MB each, 34 MB in all); Clara's answer, and
  the notes she writes, come back in the thread (split at 2,000 characters).
- Where each kind of message goes:

  | Channel | What |
  | --- | --- |
  | `#general` | Notices and where to answer: a line saying where to answer when she waits for an approval; `/new` opens a conversation in the Conversations forum |
  | `#approval` | Approval requests, with buttons: Allow once, Allow always, Refuse once, Refuse always. The token's default approval scope is `safe`: Discord may answer everything except destructive actions, which stay with the web site and the terminals |
  | `#notification` | Reminders, notifications, task reminders, schedule and hook summaries and update news (by default: see below) |
  | `#alerts` | Alerts |

- **Which channel carries what (Admin page, Discord tab; runtime setting `discord_routes`):** approval requests always go
  to `#approval` and the questions of scheduled runs that do not deliver to Discord to `#general` (the ones that do ask
  in their forum thread). Every other message belongs to one of thirteen
  categories, each sent by default where the table above says: reminders, tasks, scheduled tasks, automation started
  (`automation_started`), hook results, hook
  failures, hook notices, new mail, Clara's own notices (her `notify` tool), answer ready, other notifications
  (scripts and other clients), updates (all to `#notification`) and alerts (to `#alerts`). You send a category to
  `#notification`, `#general` or `#alerts`, or switch it off (Discord stays silent; the web site and the other clients
  still get it). `#approval` is not a choice, so whatever is there waits for your answer, and alerts cannot be switched
  off. The page shows what each channel receives now. The server works out the channel when it sends an event to the
  bot (the event's `channel`), so a change applies to the next message and the bot holds no table of its own beyond the
  fallback for a server that does not say. The page never sees or sets channel ids; they stay in the bot's settings.
- **What the page shows about the bot:** whether it is connected (its last heartbeat), its name, whether the server
  started it, a problem it ended with, and for each of the four channels the name Discord gives it and the rights the
  bot lacks there. The heartbeat carries it (every minute). **Test message:** a button per channel posts a short message
  there, whatever the routes say, and reports whether Discord accepted it (at most 6 a minute; refused when the bot is
  not connected; the bot drops a test older than two minutes, so none arrives late).

- **A run that delivers to Discord** (`deliver` = `discord` or `both`, schedule or hook) lives in **one forum thread**: its
  conversation, in its automation's forum, and nothing in `#general` (no message, no thread under one). Choosing
  `discord` or `both` switches the automation's **Show in history** on (the forum is made from it; you may switch it off
  again, and the run then falls back to the way below, still without a thread in `#general`); an automation that already
  delivered to Discord had it switched on by the upgrade. When the run starts, the bot posts a line in `#notification`
  (a routed category, **Automation started**: move it or switch it off on the Admin page) with the **link of the forum
  thread**; a notice the bot is too late for (15 minutes) is dropped, and one whose thread is not made in 10 seconds goes
  without the link. The run's questions, its reminder and its end are written in that thread, where you answer (it goes to
  the run's conversation as any message in a thread); the thread stays, as it is the conversation. The run's answer is
  written there by the mirror as it comes, so the `said` message is not posted again; the **Discord thread** pack is
  not offered to such a run. Automations with `notice` or `web` keep everything below.
- **What any other scheduled run says** (the `ask` event, section 5): a run's question is posted in `#general`, with a **thread** under it
  when the run has the Discord thread pack; what you write in the thread goes to the run's conversation, whatever the current
  conversation is. Without a thread (no pack, or the bot may not open one) the run's conversation becomes the current
  one, until the run ends. A reminder is posted in the thread. **When the run ends** (it finishes, or it is closed because
  you did not answer) the bot says so in `#general` (the run's summary, which for the daily note gives the note's path),
  after the answer that finished it was posted, and **deletes the thread**; the conversation stays in Clara, and a
  correction is made there (the web site). A run can also open, write in and remove a thread by itself
  (`discord_thread`). The bot needs *Create Public Threads*, *Send Messages in Threads* and *Manage Threads* in `#general`
  (it logs what it lacks at start); without *Manage Threads* it archives the thread instead.
- **A message to answer** (not a ping): what Clara says with `discord_say`, or an automation that delivers to Discord
  (section 5, `deliver`), is an `ask` event of stage `said`. The bot posts it in the conversation's thread when it is on
  Discord (split into messages of 2,000 characters, up to 6,000 characters in all); otherwise in `#general`, where the
  conversation becomes the current one until you write `/new` or another such message arrives. It is never sent in a
  local-only or an incognito conversation. `#notification` stays for pings.
- Slash commands (in your server, for you only): `/tools` (the special tools of the current conversation, the one of the thread you are in: list them, or switch one on or off), `/new` (a new conversation: a thread in the main forum), `/status`, `/tasks`, `/reminders`,
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
  `tool_start`, `tool`, `approval`, `tool_requests`, `usage`, `compacting`, `compacted`, `discarded`, `warning`, `done`, or `error`
  (status, code and message) when the turn fails. A `tool` event carries `grant` (the number of the Approve all next
  button that allowed the call, see Approval requests) when one did. `POST /v1/chat` returns the whole answer at once. Closing the
  stream stops the answer: what was said is kept, marked interrupted, and tool calls that had not run are recorded as
  not run. Unless the request has `keep_running` (the web sets it): then the answer is driven by the server and the
  stream only follows it, so closing the page, going to another conversation or losing the connection does not stop
  it, and it is stored when it ends. Open the conversation again and `GET /v1/conversations/{id}/run` gives the same
  events from the start: `resume` first (your question and the names of its files), then everything said so far
  (consecutive `token` and `thinking` events joined), then what comes, `done` at the end; an approval card shows how
  its request ended if it was answered meanwhile. `DELETE /v1/conversations/{id}/run` stops it (the web's Stop
  button): what was said is kept, marked `interrupted: you stopped it`. One kept answer at a time per conversation
  (a second question is refused with 409), and not with client-side `tools` (422: they need the client). A kept answer
  that needs an approval waits for it as any chat does (`CLARA_APPROVAL_WAIT`), whoever is reading. A graceful stop
  waits for kept answers like for any running turn, then saves them as interrupted.
- **Request fields:** `message`, `conversation`, `attachments` (base64, at most 4), `project`, `model`, `fork_of`
  (ask again or edit: a new version of a message), `timezone`, `tools` (client-side tools), `instructions`,
  `incognito`, `local_only`, `keep_running` (the answer goes on without the stream, above).
- **Versions:** editing a message or asking again keeps the earlier answer as another version; you move between them.
- **Special tools (packs):** a pack is a name for a group of tools that exist only where it is switched on: **Web search**
  (on by default), **Second brain** (the vault open), **Mail** (your Gmail: search, read, draft, send, labels, the read mark and the Trash;
  off until you switch it on, and offered only with a mailbox connected), **QCM** (`qcm`: the quiz tool; chats only, off until you
  switch it on, and only the web chat shows a quiz), **Day conversations**, **Day activity**, **Files created**, **Daily
  notes**, **Automations** (chats only, off until you switch it on: Clara reads, creates, changes, pauses and deletes your
  schedules and hooks, see the Automation page; every change asks you first and a scheduled or hook run is never offered it),
  and, for scheduled runs only, **Ask you** and **Discord thread**. The "+" menu of the web chat, `/tools` on
  Discord and in the terminal, and `PATCH /v1/conversations/{id}` switch them for a conversation; the choice is saved with
  it, so it follows the conversation to every client, and a new conversation starts with web search only. A **project** can
  start its conversations with other tools: its `packs` (chosen when the project is made or later, on its page) are copied to each
  new conversation of the project, from any client, unless the request names its own; a conversation already begun keeps
  its tools, and changing the project's list changes only the next ones. A pack that
  cannot be used says why (no vault, Discord bot not connected). A pack only decides which tools exist: the rule gate
  still decides each call. `GET /v1/packs` lists them.
- **Titles and summaries:** a title is written after the first answer (by the side-jobs model). When a conversation
  passes `CLARA_COMPACT_PERCENT` of the model's window, or `CLARA_COMPACT_TOKENS`, older turns are summarised; the last
  `CLARA_KEEP_RECENT_TURNS` stay whole. The answer is complete (`done`) before that: the summary is a step of its own
  (`compacting`, then `compacted` or `warning`), shown as "Compacting the conversation…" in the web chat. With `CLARA_PURGE_SUMMARISED=true` the summarised messages are deleted.
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
  first, or `CLARA_IDLE_TIMEOUT` (120 s) once started, is given up. `CLARA_MAX_TOOL_ROUNDS` (20) rounds at most: when they are used, the answer is given without tools, with a `warning`
  event and a note to the model to say what it found and what is missing, instead of ending on its last announcement.
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
    `CLARA_WAITING_REMIND_AT` (09:00) after at least three hours; `Close it` on the Automation page (or the next run, below)
    closes it from what Clara has: she is told no answer came, does what the task says for that case and finishes.
  - **Missed** (the server was off, more than an hour late): `skip` records them; `latest` runs the latest one at the next
    start; `all` runs every missed one in turn (31 at most). **Overlap** (a run comes due while the previous one still
    runs or waits): `skip` records the new one as skipped; `replace` closes a waiting run, then starts the new one (a
    running one is waited for); `queue` starts it when the old one ends.
  - **Retry** (`attempts`, 3 by default): a run the model could not answer (429, 5xx, a timeout, no connection) is tried again after 1 and 5 minutes. A run that already used a tool or wrote text is not run again, so its actions are not repeated; a refused key or a bad request is not retried.
  - **Where the result goes** (`deliver`, the same on a schedule and on a hook): `notice` (the default, what they always did: a
    short notification to every client, Discord's `#notification` included), `web` (the same, without Discord), `discord` (the
    run's **whole** answer on Discord, in the run's forum thread, which you answer; nothing else) or `both` (that, and the
    short notification for the clients that are not Discord). A run that delivers to Discord starts a thread and a start
    notice (see the Discord section), so choosing it switches **Show in history** on. Your reply in the thread goes to the
    run's conversation, so Clara still has the run in front of her (the mail she read, what she found): « add it to my
    reminders, an hour before » works. The run is told its answer is a message you will answer, so it may end with a
    proposal or a question. A reply is an ordinary chat turn in that conversation: all her tools (a hook's tool list limits the run, not
    your reply), and the turn is tainted while a mail the run read is still in the prompt, so an outside write still asks.
    A run that failed always tells you the way `notice` does, and **announce** off keeps a good one quiet. A local-only automation
    cannot deliver to Discord (it is outside the machine), nor can a hook that only notifies (no conversation to answer in).
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
  back (`GET /v1/events`, or the live stream `GET /v1/events/stream`, which tells the Discord bot each event's `channel`). Event kinds: `reminder`, `notification`,
  `approval`, `approval_resolved`, `alert`, `server`, `schedule`, `task`, `update`, `ask` (what a scheduled run says to you: its question, reminder and end for the web site and Discord, and the thread it opens, writes in or removes for Discord alone; and, for Discord alone, a message to answer in `#general`, stage `said`; for a run that delivers to Discord these lines carry `forum` and are written in its forum thread instead). A run that delivers to Discord also tells Discord, with a `notification` of source `automations`, when it starts.
- **On time:** the due loops sleep until the next due instant (30 s at most); something that fires more than
  `CLARA_LATE_ALERT_SECONDS` (300 s) late raises a `late:` alert.

### Hooks: when something happens

A schedule runs Clara at a time; a **hook** runs her when something happens outside. Today that is a new message in the
inbox of a connected Gmail mailbox (`mail.received`), a pull request opened, merged or closed in a GitHub repository you
added (`github.pull_request`) and a push to one of its branches (`github.push`); the design takes other sources (a file).

- **Events.** The mail poll (section 8) finds new messages and stores each as an *event*, once: `(source, key)` is unique,
  so the same message seen twice (a restart, a replay) is one event. An event carries the sender, the subject, a short
  snippet, the mailbox and whether it has attachments, never the body; everything in it was written by someone else. Events
  are kept 30 days (longer while a run still waits for one), runs `CLARA_RETENTION_DAYS`.
- **GitHub.** GitHub cannot call Clara (she is on your tailnet only), so she looks: every `CLARA_GITHUB_POLL_SECONDS` (120),
  at each repository added on the Connections page, and only while an enabled hook waits for that kind. Like the mail poll, the
  first look only notes where things stand (a new hook never replays history), the place is forgotten when no hook waits, and the
  place moves in the same transaction as the events and the runs they plan. For pull requests the first look notes things as of
  the moment the repository began to be waited for (the later of its being added and a hook's being made, at most 15 minutes back),
  so what happens before the poll gets round to it is not lost; a push cannot be told from before the first look. The Connections
  page says, for each repository, since when its pull requests and pushes are looked for. An answer carries an etag, so
  « nothing changed » costs no rate limit.
  - `github.pull_request` (fields `repo`, `action`, `title`, `author`, `branch`, `base`, `snippet`, `draft`, `from_fork`): a
    pull request **opened**, **merged** or **closed** (a merged one is not also closed), found by comparing the times GitHub
    gives with the time the poll had reached; the most recently updated 300 pull requests are looked at in a poll. The event
    carries the title, the author, both branches, the first 300 characters of the description, the head commit and the address,
    never more.
  - `github.push` (fields `repo`, `branch`, `author`, `message`, `created`, `forced`): the head of each of the first 100 branches
    against the head seen last. The event lists up to 10 of the new commits (the count is always given); `author` and `message`
    are the newest commit's. A new branch is a push only when its commit is not another branch's; a branch rewritten so that its
    old head is no ancestor is `forced`. GitHub does not say when a push was made, so a push is dated from the last look: one
    found after a gap of more than 15 minutes (the server was off) is a backlog for the hook's `catch_up`.
  - **No event is `self_caused`.** The token is yours, so her commits and yours look the same, and a hook sees both. What keeps a
    hook from answering itself is the gate: a hook's run is tainted, so every change it makes on GitHub (a commit, a branch, a pull
    request, an issue, a merge) asks you first, whatever the rules say, and the loop turns only as fast as you approve (and at most
    `max_per_hour`). Only turning the tainted-turn rule off (`/tainted off`) lifts this, so a rule that allows `github_write` is
    then enough to write without asking.
  - A run reads the rest with `github_read` (its `diff` action shows what a pull request or a commit changes); with no tools
    listed it has `github_read` and `notify` only.
- **Triggers.** You write them (`/triggers`, `POST /v1/triggers`), or Clara does with the **Automations** pack of a chat
  (`automation_create`, `automation_update`, `automation_delete`: the gate asks you first, refuses it unattended and asks again
  after untrusted content whatever a rule says, so a message cannot make a hook you did not read; a hook's own runs never
  have the pack). A trigger has: a name; an event kind; a **filter** (for mail the fields `account`, `mailbox`, `from`,
  `from_address`, `subject`, `snippet`, `has_attachment`, `unread`, for GitHub those above; operators `eq`, `contains`, `starts_with`, `ends_with`,
  `not_contains`; all fields must hold, any value of an operator; no regular expression, on purpose); an **action** (`run`: a turn of
  Clara's with your prompt, or `notify`: a text with `{from}`, `{subject}`, `{snippet}`, `{mailbox}`… (GitHub: `{repo}`, `{number}`, `{action}`, `{title}`,
  `{author}`, `{branch}`, `{message}`, `{sha}`, `{url}`) and no model, with a default text for each kind); its
  `tools`; an optional `project` (none by default); an optional `model`; `local_only`; `catch_up`; `max_per_hour`;
  `max_age_hours`; `include_self`; `rerun_interrupted`; `attempts`; `announce`; `in_history`; `deliver` (where the result goes, below). Mail from Clara's own account is left out unless `include_self` (which does nothing for GitHub); a sender's
  `From` can be forged, so a sender filter is a convenience, not a proof.
- **A run** is an unattended turn in a conversation of its own, which belongs to the hook: the web history lists the conversations
  of a hook as a group under its name, like a schedule's, only when you switch the hook's `in_history` on (it starts off). The hook may
  also name a **project**, whose files and instructions are then context for every run. It is **tainted from its first word**: every outside write (a send, a GitHub change, a
  Drive change) asks whatever the rules say, and approvals are pushed to you, never waited for. It has **only the tools the hook
  lists**; with none listed, the event kind's read tools and `notify` (mail: `mail_search`, `mail_read`, `mail_attachment_read`; GitHub: `github_read`),
  so a hook never has more than you gave it. A run whose tools name a mail tool gets the Mail pack for that run. The same limits hold for the turn that follows one of its approvals. The event
  reaches the model after your prompt, inside an `<event>` tag as JSON with `<` and `>` escaped, with a line saying it is
  data. To let a hook send, list `mail_send` in its tools **and** write the rule; the run is tainted, so each send still asks.
- **A stop loses nothing and repeats nothing.** The events, the runs they plan and the place the poll (mail or GitHub) had reached are
  written in one transaction. Mail that arrived while Clara was off is found (oldest first, section 8) and handled by the
  trigger's `catch_up`, for events found more than 15 minutes after they happened: `all` (a run each, the default, at the
  pace of `max_per_hour`), `latest` (a run for the newest; the others are recorded as skipped), `digest` (one run for the
  whole backlog, listing up to 25 events) or `skip` (all recorded, none run). `max_age_hours` makes a run that waited too
  long `expired`. A run the stop cut that had done nothing starts again; one that had begun is `interrupted` and not run
  again (Clara cannot know what it did) unless the hook has `rerun_interrupted`.
- **Pace.** One run of a hook at a time, at most `max_per_hour` started in any hour (20 by default), two hooks at once, 500 runs
  waiting per hook (more are recorded as skipped, with a `trigger:<id>` alert). Turning a hook off drops what waited.
- **The mail poll** looks only while a listener exists (notifications on, or a hook enabled); with none it forgets its place, so the
  first look after one appears starts from now and never replays old mail. A hook made today sees mail from now on.
- **Looking.** `POST /v1/triggers/{id}/test` and `/triggers test` show, for a stored event, a sample or the latest event, whether
  the filter matches, the exact message Clara would get and her tools, without running anything. Each run is recorded
  (`ok`, `failed`, `interrupted`, `skipped`, `expired`) with its summary and conversation, and tells you how it ended with a
  `schedule` event (`announce` off keeps a good run quiet; a failed one always tells).

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
  Each change Clara makes in a conversation is a commit that ends with the line `Conversation: <id>` (as the workspace's do), which is how the side panel lists the notes a conversation created or edited; notes written before that line existed are not listed.
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
- **Forms:** two tools, both for the web chat only; `qcm` is in the **QCM** pack (off until switched on, see Special tools), `ask_questions` is always there. `qcm` creates a quiz (single choice, multiple choice, free text; 20
  questions, 10 options each, LaTeX allowed, an optional key that marks each answer, a hint and an explanation per
  question) that opens in the side panel and is answered **one question at a time**: each answered question reaches
  Clara as a message of its own (`[QCM answer …]`, with what the key says) and she corrects it at once; a skipped
  question sends nothing; when the last one is answered or skipped, one line gives her the final score (once per
  attempt); **Retry** starts a new attempt. The progress is kept on the server (`quiz_attempts`, `quiz_answers`), so a
  reload or another device shows it. `ask_questions` asks you 1 to 4 questions while she works (a short header, 2 to 4
  options with a description, one or several choices; you can always write your own answer, "Other"): the questions
  replace the box you write in and the turn waits (`CLARA_APPROVAL_WAIT`) for your answers, which are the result of
  the call; dismissing them tells her you declined, and no answer in time withdraws them. Neither is offered to the
  terminal, Discord or an unattended run.

## 7. Workspace and scripts

The workspace (`CLARA_WORKSPACE`, default `/var/lib/clara/workspace`) is the only place Clara writes or runs anything
on the machine. It is a git repository: every change by a tool is one commit (tool, summary, turn id), changes a
script makes are one commit after the run, and any commit can be undone (`/undo`, the Workspace view of the Library page, `/v1/workspace/undo`).

- **Paths:** joined to the root and resolved; absolute paths, `..`, symlinks and anything under `.git` are refused;
  files are opened without following links, and only regular files are opened (a named pipe or socket a script left
  behind is refused, never waited for). 400 characters per path.
- **Limits:** `CLARA_WORKSPACE_MAX_SIZE` (5 GB) and `CLARA_WORKSPACE_MAX_FILE` (100 MB); a file bigger than that
  (a script can write one) is not read. `ws_edit` leaves bytes that are not UTF-8 as they were; the web editor does not
  open such a file (download only).
- **Git:** every git command on the workspace runs with `core.fsmonitor=false`, so a repository a script plants inside
  it cannot make the host's git run a command.
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

- **Web search:** through the Brave Search API when a key is set (the secret `brave_api_key`, set on the Admin page's
  Secrets tab, with a link to Brave's dashboard; nothing checks it when it is saved), else, or when Brave cannot answer, through a self-hosted
  SearXNG (`CLARA_SEARXNG_URL`), with the JSON format enabled (`deploy/searxng/`). A service that answers with no result
  is believed (no second try); one that cannot answer (a refused key, a spent monthly credit or a rate limit: HTTP 401,
  403 or 429; every search engine of SearXNG refused or timed out, which SearXNG reports and used to be shown as « No
  result. ») is skipped, and when none can, `web_search` fails with each one's reason and says that trying again at
  once will not help. A key Brave refuses raises the `brave` alert (over once the key is replaced or removed);
  searches go on through SearXNG. The web tools are offered while a Brave key or `CLARA_SEARXNG_URL` is set. The search
  words go to Brave (and, as before, to the engines SearXNG asks).
- **Web fetch:** `web_fetch` reads http and https pages (title, text, links; `CLARA_FETCH_MAX_SIZE`, 2 MB). The address
  is resolved once and the connection pinned to that address (with the right TLS name), so a DNS change cannot point it
  inside; private, loopback, link-local and tailnet addresses are refused, at every redirect (5 at most). An address
  you wrote or a search returned is read at once; any other asks (refused when unattended).
- **GitHub:** a fine-grained personal access token you paste on the Connections page, and the list of repositories
  Clara may use (others are refused before any request). Reading (tree, files, code search, branches, issues, pull
  requests, threads, and the changes of a pull request or a commit) is allowed; every change (commit, branch, issue, pull request, merge) asks by default.
- **Google Drive:** OAuth with the `drive.file` scope only (plus `openid email` to show which account you attach), with
  PKCE. Clara sees only files she created (in her own "Clara" folder) and files or folders you open to her with the
  Google Picker. The consent is bound to the browser session that started it; the Google address is shown and you
  confirm before anything is stored. Disconnecting revokes the token at Google. Reading Google Docs, Sheets and Slides
  gives text; every create, update or delete (to Drive's trash) asks by default.
- **Gmail:** OAuth with PKCE and three scopes only, `gmail.readonly`, `gmail.compose` and `gmail.modify` (plus `openid email`
  to show which mailbox you attach), using the Google client of Drive but a token of its own. Clara can search (free words,
  sender, recipient, subject, unread, with an attachment, between two dates, in the inbox, sent, drafts, trash, spam or
  everywhere), read a message, read an attachment's text, keep an attachment in the workspace, save a draft and send. She
  can list your labels, make one, add or remove your labels on a message, delete one of your labels (its messages stay),
  take the unread mark off a message, and move a message to the Trash. She cannot archive, empty the Trash or change any
  setting: the token has no scope for settings, and Clara never calls the permanent delete (`messages.delete`), which
  `gmail.modify` does not allow. A label is named from your own labels only, so INBOX is refused; the mark read touches
  UNREAD and nothing else. A connection made before this version asks you to connect Gmail once more, for `gmail.modify`. The Gmail API itself says
  whose mailbox it is, which also proves the API is on in your Google Cloud project, before anything is stored.
- **Mail:** one Gmail mailbox at most. What a message says is untrusted: hidden characters (zero-width,
  direction controls, Unicode tag characters) are removed and HTML is read without text that CSS hides, the result is
  handed over as data, and the turn is tainted. An email goes only to the addresses given in the call (no address is
  found for her), at most 20, text up to 6,000 characters (all of it is shown in the approval request), at most 5
  attachments from the workspace (Gmail 7 MB in all), at most 5 emails per answer.
  A reply keeps its thread (the headers and thread id).
- **New-mail notifications:** off until `mail_notify` is on (Connections page). Every `CLARA_MAIL_POLL_SECONDS` (120) the
  inbox of each connected mailbox is looked at; the first look only notes where it stands. A message announces itself as
  « sender: subject » (never the text) to every client, Discord's `#notification` included; more than four new messages
  at once make one notification with a count; `mail_notify_senders` keeps only senders containing one of its words.
  Mail that arrived while Clara was off is taken oldest first, 100 messages a round and 5 rounds a poll (a bigger
  backlog goes on at the next poll, from where this one stopped); the place only moves once a poll's messages were handled,
  so an error half way repeats the poll instead of skipping anything.
  This loop is not counted by the systemd watchdog.
- **Content leaving the machine:** a call that would send text looking like a secret to GitHub, Drive, the vault's
  remote, a draft, an email or a networked script is raised to `ask`, naming the kind of secret found.

## 9. The rule gate

Every tool call is an **action**: a tool, a class, a level (`read`, `write`, `destructive`), its targets
(`ws:<path>`, `github:<owner/repo>`, `drive:<id>`, `mail:gmail`, `mail:to:<address>`, `vault:<note>`, `domain:<host>`, or `net`, `search`, `memory`,
`reminders`, `tasks`, `notify`, `forms`, `history`, `discord`, `automations`) and a one-line summary. The gate answers allow, ask or refuse. The model can
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
| `mail_read`: mail read: search, read a message or an attachment (Gmail) | allow | allow |
| `mail_draft`: mail draft: save a draft in your mailbox (nothing is sent) | allow | allow |
| `mail_send`: mail send: an email leaves your mailbox | ask | refuse |
| `mail_label`: mail label: add or remove your labels on a message, or make one (nothing is archived) | allow | ask |
| `mail_trash`: mail trash: move a message to the Trash (never emptied) | ask | ask |
| `mail_mark`: mail mark: take the unread mark off a message (nothing else changes) | allow | ask |
| `mail_label_delete`: mail label delete: delete one of your labels (its messages stay) | ask | ask |
| `vault_write`: create or edit vault notes (git history) | allow | allow |
| `vault_destroy`: delete or move vault notes | ask | ask |
| `web_search`: web search | allow | allow |
| `fetch_trusted`: fetch a URL you wrote or a search returned | allow | allow |
| `fetch_other`: fetch any other URL | ask | refuse |
| `discord`: open, write in or remove a Discord thread, or say something in `#general` | allow | allow |
| `automation`: create, change or delete an automation, a schedule or a hook (the Automations pack) | ask | refuse |
| `discord_forget`: delete a conversation, a project, a schedule or a hook because its Discord thread or forum was deleted | ask | ask |

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
  on the Rules page (an "always" answer whose rule cannot be written, a target over 400 characters, is refused with
  HTTP 400 and the request stays pending: answer it once). In a local-only conversation the mail tools are not offered and the gate refuses them.
- **Labels, the read mark and the Trash:** a label (`mail_label_create`, `mail_label`) and a mark read (`mail_mark_read`) are allowed in a chat and wait for you unattended. A trash (`mail_trash`) asks in a chat at the `destructive` level, like Drive's trash, so the Discord token cannot approve it; a turn that has read mail does not raise it (the owner chose that, so that a sort of many mails does not ask for each one), and « Approve all next » covers it. Deleting a label (`mail_label_delete`) asks every time at the `destructive` level, is raised after untrusted content, and « Approve all next » does not cover it, because a deleted label cannot be taken back. Unattended, all of them but a label or mark read wait for you. Every one is refused in a local-only conversation. Nothing here archives or empties the Trash.
- **Tainted turns:** once a turn has read untrusted content (a web page or search result, a GitHub file or thread, a
  Drive file, an email or an attachment; and every run a hook starts, from its first word), outside-world writes (`github_write`, `drive_write`, `mail_send`, `mail_label_delete`, `vault_write`, `vault_destroy`), networked
  scripts (`run_network`) and changes to your automations (`automation`) are raised to at least `ask`, whatever the rules say. On by default; `/tainted off` or the
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
- **Approve all next** (a button on the approval cards of a chat in the web site). It allows the request on the card and
  then answers « allow once » for you to the next requests of the same tool and the same class in that conversation,
  without a card; every call is still printed in the chat and has its line in the audit log (`grant` when it starts,
  `ran` with « auto-approved by Approve all next #n » for each call, `grant_end` with the reason). It covers workspace
  and vault changes and moving a mail to the Trash (`ws_write`, `ws_destroy`, `vault_write`, `vault_destroy`, `mail_trash`: a
  workspace or vault change is a git commit, and a trashed mail stays in the Trash), turns of the web only, never an
  unattended run. It never covers what the gate raised on purpose (a tainted turn, except that `mail_trash` is not raised by one;
  content that looks like a secret), never an « ask » for another tool or class, never a rule that refuses, and it writes no
  rule. The page beats every 10 s and a banner at the foot of the page shows the tool, the calls used, the time left and
  **Stop**; it ends when the page stops beating for 90 s (closed, reloaded, left, asleep), when `approve_all_minutes` have
  passed or `approve_all_calls` calls were made (the Admin page, Settings tab), on **Stop**, or when the server restarts.
  Pressing it on another page, or in another conversation, never carries it over.

### Audit

Every gate decision is written to the audit log: time, conversation, turn, tool, target, level, situation, a digest of
the arguments (never the arguments themselves), the deciding rule, the effect and the outcome. Kept
`CLARA_RETENTION_DAYS` (400).

## 10. Tools

76 tools. "Untrusted" tools taint the turn (section 9). Tools that need something absent (no workspace, no sandbox, no
vault, no GitHub, Drive or Gmail connection, a local-only conversation) are not offered.

| Tool | What it does | Class / level |
| --- | --- | --- |
| `remember` | Save one durable fact (with `replaces`, `expires`, category) | memory / write |
| `recall_facts` | Search remembered facts (10 best) | read |
| `forget` | Delete a fact, by id (tombstone) | memory / write |
| `search_history` | Search earlier conversations (8 best, with dates and excerpts) | read |
| `remind` | Set a reminder (once, daily, weekly, monthly; for some clients) | memory / write |
| `list_reminders` | Reminders to come | read |
| `cancel_reminder` | Cancel a reminder | memory / write |
| `notify` | Notify you now on your clients (3 per turn): a ping that needs no answer, in `#notification` on Discord | memory / write |
| `discord_forget` | What a deleted Discord thread or forum asks for, once you approve it: the conversation, or the project, schedule or hook with its conversations, or the main or incognito conversations. The model is never offered it, and the Discord token cannot approve it |
| `discord_say` | Say something on Discord in `#general`, as a message you can answer: the conversation goes on there with your reply (3 per turn, 6,000 characters; not offered from Discord itself, in an incognito or a local-only conversation; refused when the bot is not connected) | discord / write |
| `add_task` | Add a task or sub-task with reminders | memory / write |
| `list_tasks` | Tasks, or one task in full | read |
| `update_task` | Change a task (title, description, due, reminders, status, parent) | memory / write |
| `delete_task` | Delete a task and its sub-tasks | memory / write |
| `qcm` | Create a QCM (a quiz) that opens in the side panel, answered one question at a time (pack QCM; web only) | memory / write |
| `ask_questions` | Ask you 1 to 4 questions with choices, in place of the box you write in; the turn waits for your answers (web) | memory / write |
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
| `web_search` | Search the web (Brave, else SearXNG) — untrusted | web_search / read |
| `web_fetch` | Read a web page — untrusted | fetch_trusted or fetch_other / read |
| `github_read` | Look at a connected repository: a folder, a file, code search, branches, issues, pull requests, a thread, the changes of a pull request or a commit (`diff`) — untrusted | github_read / read |
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
| `mail_labels` | Your own labels of the mailbox, not the system ones (pack Mail) | mail_read / read |
| `mail_label_create` | Make a label of yours; it moves nothing (pack Mail) | mail_label / write |
| `mail_label` | Add or remove your labels on one message; never archives or marks read (pack Mail) | mail_label / write |
| `mail_trash` | Move one message to the Trash; never emptied; asks, and Approve all next covers it (pack Mail) | mail_trash / destructive |
| `mail_mark_read` | Take the unread mark off one message; nothing else changes, never an archive (pack Mail) | mail_mark / write |
| `mail_label_delete` | Delete one of your labels; it comes off its messages, which stay; asks, not grantable (pack Mail) | mail_label_delete / destructive |
| `day_conversations` | What was said on a day, with the context of conversations begun earlier (pack Day conversations) | read |
| `read_conversation` | One conversation in full, a page at a time | read |
| `day_activity` | Tasks, reminders, scheduled runs and Clara's actions of a day (pack Day activity) | read |
| `files_created` | The files of a day, one short line each (pack Files created) | read |
| `read_created_file` | One of them, whole or as the day's diff | read; drive_read for a Drive file |
| `daily_notes` | A range of daily notes of the vault (pack Daily notes) | read |
| `ask_person` | Ask you one question from a scheduled run and wait for the answer (pack Ask you; scheduled runs only) | memory / write |
| `finish_run` | End the scheduled run, with a summary | memory / write |
| `automation_list` | Your schedules and hooks with their numbers, or one in full (pack Automations; chats only). The prompt and filter of a local-only automation are shown only to a local-only conversation | read |
| `automation_create` | Make a schedule (at a time, once or repeating) or a hook (when a mail arrives or a GitHub pull request or push happens): every setting of the Automation page. It asks you first, with the whole automation (and, for a schedule, when it first runs) in the request; refused unattended; asks again in a tainted turn whatever a rule says. Made in a local-only conversation, it is local only | automation / write |
| `automation_update` | Change one, or pause or resume it; the request lists what changes (`old → new`). A hook's kind of event cannot change | automation / write |
| `automation_delete` | Delete one with its history (to stop it for a while, pause it) | automation / destructive |
| `discord_thread` | Open, write in or remove a Discord thread (pack Discord thread; scheduled runs only, and not offered to a run that delivers to Discord: its thread is the forum's) | discord / write |
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
- **Usage:** one row per model call (kind `chat`, `title`, `summary`, `reminder`, `task`, `schedule`, `hook`, `followup`;
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
  | `brave` | Brave refused the API key |
  | `github` | GitHub refused the token |
  | `google` | Google refused the refresh token (reconnect Drive) |
  | `trigger:<id>` | A hook has more than 500 runs waiting: the newest events were recorded but not run |
  | `gmail` | Google refused the Gmail refresh token (reconnect Gmail) |
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
| `CLARA_MAIL_POLL_SECONDS` | `120` | How often the inbox is looked at for new-mail notifications; `0` never; 30 at least |
| `CLARA_GITHUB_POLL_SECONDS` | `120` | How often the added repositories are looked at for hooks on pull requests and pushes (only while a hook waits for them); `0` never; 30 at least |
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
| `approve_all_minutes` | Minutes an **Approve all next** button stays on after it is pressed (30; 1 to 1,440) |
| `approve_all_calls` | Calls an **Approve all next** button lets through without asking (50; 1 to 1,000) |
| `debug_until` | The debug content log's end (empty: off) |
| `sandbox_image` | The sandbox image pinned by the last release (empty: `CLARA_SANDBOX_IMAGE`) |
| `hidden_tools` | Tools not offered to some clients: `{client: [tool, …]}` |
| `discord_routes` | Where Discord posts each kind of message: `{category: notification\|general\|alerts\|off}`; a category not named keeps its default |
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
| `brave_api_key` | Brave Search API key (set on the Admin page, Secrets tab) |
| `github_token` | GitHub fine-grained personal access token (set from the Connections page) |
| `google_client_secret` | Google OAuth client secret (Drive and Gmail) |
| `google_refresh_token` | Google refresh token (set by connecting Drive, never by hand) |
| `gmail_refresh_token` | Gmail refresh token (set by connecting Gmail, never by hand) |
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
| `GET /v1/conversations` | List and search conversations (`kind` chat, schedule or any; `history=true` leaves out those of a project, schedule or hook not shown in the history; `hide=discord` (client names, separated by commas) leaves out what those clients started, the pinned ones apart; each names its `group`) |
| `POST /v1/conversations` | Create one (incognito, local only, model, project, `packs`; in a project, without `packs`, it starts with the project's) |
| `GET /v1/conversations/{conversation_id}` | One conversation, with its context, files and waiting approvals |
| `PATCH /v1/conversations/{conversation_id}` | Rename, pin, change model or project, local only |
| `DELETE /v1/conversations/{conversation_id}` | Delete it, with its files |
| `GET /v1/conversations/{conversation_id}/messages` | Its messages (the chosen versions) |
| `GET /v1/conversations/{conversation_id}/run` | Follow the answer being given in it (a kept one), from its first event; 404 when there is none |
| `DELETE /v1/conversations/{conversation_id}/run` | Stop that answer; what was said is kept, marked interrupted |
| `POST /v1/conversations/{conversation_id}/compact` | Summarise it now |
| `POST /v1/conversations/{conversation_id}/title` | Write its title again |
| `POST /v1/conversations/{conversation_id}/version` | Choose which version of a message is shown |
| `GET /v1/conversations/{conversation_id}/files/{file_id}` | Download a file sent in it |
| `GET /v1/conversations/{conversation_id}/panel` | What the web chat's side panel shows: the workspace files Clara wrote in it (`files`: path, first and last change, size; found from the commits' `Conversation:` line, the logs of scripts left out), the vault notes she created or edited in it (`notes`: path, `action` created or edited, first and last change, size; found the same way in the vault's commits, none when there is no vault) and its QCMs with their progress |
| `POST /v1/conversations/{conversation_id}/qcm/{ref}/answer` | Answer one question of a QCM (`question` from 1, `answer`: option numbers or text): returns the progress and the `message` to send Clara; an answered question cannot be answered again (409) |
| `POST /v1/conversations/{conversation_id}/qcm/{ref}/skip` | Skip a question (no message, unless it was the last one left) |
| `POST /v1/conversations/{conversation_id}/qcm/{ref}/retry` | Start the QCM again as a new attempt (nothing happens while the current attempt has no answer) |
| `POST /v1/conversations/{conversation_id}/questions/{ask_id}` | Answer (`answers`: one `{options, other}` per question) or dismiss (`declined`) the questions `ask_questions` is waiting on; web tokens only, 409 when nobody waits for them any more |
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
| `POST /v1/projects` | Create one (`packs`: the special tools its new conversations start with, web search by default) |
| `GET /v1/projects/{project_id}` | One, with its files and sources |
| `PATCH /v1/projects/{project_id}` | Change name, description, instructions, `packs`, pin; `in_history` lists its conversations in the web history |
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
| `POST /v1/approvals/{approval_id}/grant` | **Approve all next** on a waiting request: allow it, and the next requests of its tool and class in its conversation, until the page stops beating or a limit is reached (web tokens only; 422 for any other class than a workspace or vault change) |
| `PUT /v1/conversations/{conversation_id}/grants` | The page's beat (every 10 s): the grants on, with calls used and time left, and the ones that ended since the last beat |
| `DELETE /v1/conversations/{conversation_id}/grants` | Stop every grant of the conversation |
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
| `GET /v1/admin/secrets` | Secrets: set or not, and when, never values; each with its `group` (Model providers, Web search, GitHub, Google, Vault and backups, Other), a `link` to the page of its provider where its value is made (or to the Connections page, for the refresh tokens), and whether it is `settable` by hand |
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
| `GET /v1/admin/discord` | The Discord page: the bot's state, its channels, the category routes, the last test, and the mirror (conversations in Discord, a stop and its reason) |
| `POST /v1/admin/discord/resume` | Start the Discord mirror again after it stopped at a Discord limit |
| `GET /v1/discord/sync` | The bot's: the places, the conversations changed since a revision, the threads to remove (the Discord client only) |
| `POST /v1/discord/report` | The bot's: the places, threads and messages it made, and what stopped it (the Discord client only) |
| `GET /v1/discord/live` | The bot's: the answers being written in mirrored conversations, as they come (the Discord client only, server-sent events) |
| `POST /v1/discord/event` | The bot's: a thread archived or deleted, a forum deleted, a question edited, on Discord (the Discord client only) |
| `POST /v1/admin/discord/test` | Post a test message in one channel (`general`, `notification`, `approval`, `alerts`) |
| `POST /v1/clients/heartbeat` | A client says it is alive (the Discord bot also says its name, the channels it sees and how a test went) |
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
| `GET /v1/triggers` | The hooks and the kinds of event (their fields, their default tools) |
| `POST /v1/triggers` | Make a hook |
| `GET /v1/triggers/{trigger_id}` | One hook, with its latest runs |
| `PATCH /v1/triggers/{trigger_id}` | Change it, turn it on or off |
| `DELETE /v1/triggers/{trigger_id}` | Delete it (its runs go; the conversations they made stay, no longer grouped under it) |
| `POST /v1/triggers/{trigger_id}/test` | What would happen for an event (stored, sample or latest): match, message, tools; nothing is done |
| `GET /v1/triggers/{trigger_id}/runs` | Its runs |
| `GET /v1/trigger-events` | The events stored |
| `GET /v1/connections` | GitHub, Drive and Gmail: state, repositories (with since when hooks look at each), files; the mail notification settings |
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
| `POST /v1/connections/mail/{mailbox}/start` | Start the Gmail consent (web session only) |
| `POST /v1/connections/mail/{mailbox}/finish` | Exchange the code and prove the mailbox is reachable; returns its address |
| `POST /v1/connections/mail/{mailbox}/confirm` | Attach that mailbox |
| `DELETE /v1/connections/mail/{mailbox}` | Disconnect (revoked at Google) |
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
| Event text | 2,000 characters (6,000 for what an automation or `discord_say` says in `#general`; the bot splits it into messages of 2,000, Discord's limit); title 100 |
| Forms | QCM: 20 questions, 2 to 10 options, 4,000 characters for a typed answer. `ask_questions`: 4 questions, 2 to 4 options, a 12-character header, 5 calls per answer |
| Projects | 90 MB and 1,000 files per upload; `CLARA_PROJECT_MAX_CHARS`, `CLARA_PROJECT_MAX_FILES`; a file over 30 MB in an archive is skipped; 600 MB unpacked per archive; GitHub archives 300 MB |
| Workspace | `CLARA_WORKSPACE_MAX_SIZE`, `CLARA_WORKSPACE_MAX_FILE`; reads 1,000 lines at once; 80 search matches |
| Vault note | 400,000 characters; 20 search results |
| Special tools | `day_conversations` `CLARA_DAY_DIGEST_CHARS`; `files_created` 60 files a page; `daily_notes` 31 days; a question of `ask_person` 2,000 characters; 31 missed or queued runs per schedule |
| GitHub and Drive files | 30 MB read; 1,000,000 characters written |
| Hooks | 50 hooks; a prompt of 20,000 characters; 20 runs an hour each by default (1 to 600); 500 runs waiting each; 2 hooks running at once; a filter of 10 fields with 20 values of 200 characters; events kept 30 days; GitHub: 300 pull requests and the first 100 branches looked at in a poll, 10 commits listed in a push event |
| Mail | a search lists 25 messages at most; 20 recipients, a subject of 300 and a text of 6,000 characters, 5 attachments, 5 emails per answer; a label's name 100 characters; an attachment over 25 MB is not fetched and one over 10 MB is not parsed for text |
| Web page | `CLARA_FETCH_MAX_SIZE`; 5 redirects; 40 links listed |
| Request bodies | see section 12 |
