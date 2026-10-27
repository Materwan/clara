# Changelog

Every release has a section here, under its tag; the release workflow publishes that section as the release notes
(what changed, migrations, new settings). Dates are in UTC.

## v1.0.0 — unreleased

First release of the rewrite: Clara for one person on one machine, built from zero following
[the rebuild plan](clara-rebuild-plan.md) and [the review](docs/REVIEW-RESPONSE.md). Nothing is migrated from the
earlier server: the new Clara starts empty.

- **Access:** Tailscale only (`tailscale serve`, Funnel refused at start and turned off if it appears), a hashed,
  revocable token per client, a failure limiter, web cookie plus `X-Clara-Web` header, `/docs` only in debug.
- **Secrets:** an AES-GCM store in the database; its key in a systemd credential, never in the data folder; scrypt
  (N=2^17, salted) for passphrases; `clara secrets rotate`.
- **Models:** local Ollama hosts, Ollama cloud, Gemini, DeepSeek, Mistral and OpenCode Go (its own models, the `-free` ones it lists included; OpenCode Zen's free models are not offered, as OpenCode keeps them for its own app).
  OpenCode's models of APIs Clara does not speak are told apart from the rest (a table made from models.dev), kept out of
  the pickers and behind a selector on the Models page, with the reason; so are the pictures it cannot give to a text-only
  model. A 403 that is not about the key (OpenCode's free tier) is no longer reported as a refused key.
- **Chat:** streaming, versions (edit, ask again), titles, compaction that never cuts silently, a time budget per
  answer, separate model slots for background work, fallbacks that never leave a local choice, incognito and
  local-only conversations, attachments (PDF, Word, text, pictures, animations), client-side tools. An answer goes on
  when the web page is closed or left (`keep_running`), can be followed again from its start and stopped on purpose;
  one that uses all its tool rounds says so. Web search goes to the Brave Search API when a key is set (Admin,
  Secrets), with SearXNG as the fallback, and says why when no search service can answer. A long conversation is summarised after its answer is complete, shown as
  "Compacting the conversation…" (the stop button then reads "Stop compacting").
- **Memory:** facts with source, confirmation, supersede, expiry and forget tombstones; FTS5 search; history search.
- **Time:** reminders, tasks with sub-tasks, unattended schedules, notifications, an event store with a cursor per
  client; zone names only, so daylight-saving changes do not shift anything.
- **Knowledge:** projects (files, `.zip` archives, GitHub snapshots), conversation files, a slim Obsidian vault (seven
  tools behind `open_second_brain`), forms.
- **Rule gate:** allow, ask or refuse for every tool call; editable rules; the tainted-turn rule; secrets leaving the
  machine raise to ask; approvals from any client (Discord buttons included, destructive excepted); an audit log.
- **Approve all next:** a button on a chat's approval card (web site) that allows the request and the next ones of the same
  tool in that conversation without a card, while the page stays open: workspace and vault changes only, never after untrusted
  content or for content that looks like a secret, ended by Stop, a silent page (90 s), 30 minutes or 50 calls (new runtime
  settings `approve_all_minutes` and `approve_all_calls`, on the Admin page). A banner at the foot of the chat shows what is on;
  every call stays printed and is in the audit log.
- **Workspace:** a git repository with a commit per change and undo; scripts in rootless Podman with no network, an
  empty environment and resource limits.
- **Connectors:** SearXNG search, SSRF-safe page fetching, GitHub with a fine-grained token and a repository list,
  Google Drive with `drive.file` and the Picker.
- **Clients:** a Svelte web site in the first site's design (the Prussian-blue rail, Clara's portrait, Hanken
  Grotesk and Epilogue, with darker muted text for reading; the conversation list in the rail; Settings pages grouped under one tab bar; drawn checkboxes), `clara-chat`, `clara-admin`, a Discord bot for your own private server (chat in `#general`, approvals with buttons in `#approval`, reminders and other news in `#notification`, alerts in `#alerts`; no direct messages; it can start with the server: `CLARA_DISCORD_AUTOSTART`), the HTTP API.
- **Web site design system:** every colour written once as `light-dark()` (no duplicated dark theme), one type scale,
  a 2px space grid, shape, motion and stacking tokens, and a "negative" surface shared by the rail and the Admin console
  ([docs/WEB-DESIGN.md](docs/WEB-DESIGN.md)). Questions (delete, revoke, a secret's value) are a dialog in the site's
  own style instead of the browser's `confirm()` and `prompt()`, so a secret is typed in a masked field. Every control
  has an accessible name; views are a segmented control with `aria-pressed`; a skip link and a `<main>` landmark;
  drawers move focus and make the page behind inert; toasts wait while pointed at and can be dismissed; touch targets
  of 36 px and up; a high-contrast fallback. Fixes: the rail overflowed by 7 px and pushed Sign out out of view, the
  Settings alert count was dark red on blue in the light theme, and the Admin console's input border was too faint.
- **Web site, second pass (a design review):**
  - **Layout:** a page's title, its Settings tabs and its body start at one left edge at any width (`--col-pad`, drawn
    from the width of `.content`), and every Settings page has the same width (Connections was narrower, so the page
    jumped between tabs). On a phone the open Settings tab scrolls into view (Models, Usage and Admin were off-screen).
  - **Tasks and Schedules:** the list comes first; the form opens from **New task** / **New automation** (and from **+ Sub**
    and **Edit**), with its fields labelled above them, focus in the first field and back on the button afterwards. The
    Tasks row no longer collapses on a phone (the title was 0 px wide, under the buttons), and its actions are quiet at
    rest with text at full strength (the faded buttons were 3:1, under the 4.5:1 the design asks for). Tools a server
    cannot give (no vault, no Discord bot) are disabled on Schedules, as in the chat's menu, not marked red. Next runs
    are dated like everything else, not ISO text.
  - **Phone:** a conversation's pin, rename and delete are one ⋯ button that opens them, named, under the row (the three
    icons took 40% of the title); tables are as wide as their words and scroll sideways instead of breaking them in
    the middle; the touch size of the page links is 44 px.
  - **Copy and state:** settings the server names (`CLARA_VAULT_PATH`…) are set in code style after a plain sentence;
    "off (off (CLARA_RUN=off))" is now "off: CLARA_RUN is set to off" (the server's message is `CLARA_RUN is set to
    off`). Admin Status is a list of facts with `ok`/`warn` tags; allow, ask and refuse are `ok`, `warn` and `danger`
    tags in the rules. A new conversation says what Incognito and Local only mean once ticked (a tooltip does not exist
    on touch); the rail no longer repeats "web ·" on every conversation.
  - **A message with documents:** reopened from the history, it drew the whole attached document inside the purple bubble
    (a 40,000-character document made a bubble many screens tall); the bubble now holds only what you wrote, with a chip
    per file (its size in the tooltip), as while you were sending it. Edit changes your words and sends the files again
    with them; Ask again is unchanged. The stored message is not touched: Clara still reads the documents in it.
  - **Keyboard:** the Upload and Add files buttons are reachable by Tab (their file input was `hidden`, which cannot take
    focus); empty table headers say "Actions" to a screen reader; a table that scrolls sideways has a focus stop.
- **Live log:** an Admin **Logs** tab streams the server's log and the traffic records as they happen (source and
  level filters applied on the server, pause, follow, clear). Memory only: the last 500 lines, secrets masked, no
  sign-in addresses, never the debug content. New route: `GET /v1/admin/logs/stream`.
- **Tool packs:** special tools a conversation or a schedule is granted, listed by `GET /v1/packs`: web search, second
  brain, **day conversations** (`day_conversations`, `read_conversation`), **day activity**, **files created**
  (`files_created` lists a day's files in short lines, `read_created_file` reads one on demand, whole or as the day's
  diff), **daily notes** (a range), **automations** (chats only: `automation_list`, `automation_create`,
  `automation_update`, `automation_delete` read, make, change, pause and delete schedules and hooks; the new action class
  `automation` asks in a chat, is refused unattended and asks again after untrusted content; the request shows the whole
  automation), and, for scheduled runs, **ask you** (`ask_person`, `finish_run`) and **Discord thread**.
  In the web chat the attach button became a **"+" menu**: add files, switch the packs, web search and the second brain on
  or off, choose the project; the choice is saved with the conversation, and `/tools` does the same on Discord and in the
  terminal. A pack decides which tools exist, never what the gate allows. Incognito conversations are never read back;
  local-only ones only from a local-only conversation.
- **Schedules grew up:** packs, a model of its own, one conversation per run, a run that **asks and waits** for your answer
  (`waiting`, reminded once, closed from what Clara has, `finish_run`), what to do with runs missed while the server was off
  (`skip`, `latest`, `all`) and with a run that comes due before the last is over (`skip`, `replace`, `queue`). The
  Automation page has all of it. Fixed: "Run now" on the Automation page failed (the route needed the server's event loop).
- **Try again:** a reply on the web site that failed or was cut off has a **Try again** button (its question is asked again; a new version when it is stored). Schedules have `attempts` (1 to 3, 3 by default): a run the model could not answer is tried again after 1 and 5 minutes, unless it had already used a tool or written text. The Automation page has the setting; the API takes `attempts` on create and change.
- **The daily note is a schedule:** "Bilan du jour" is made once at start when there is a vault (every day at 23:59). It
  reads the day with the packs' tools, asks you one question on the web site and Discord (a thread under it in `#general`),
  waits for your answer, writes the day into a block of that day's daily note (`vault_daily_block`, `[[links]]` kept only
  for notes that exist), marks the tasks you confirmed, and the thread is deleted when it ends. It replaces the built-in
  recap of the same day (`CLARA_RECAP*`, `/recap`, `write_daily_recap`, the `recap` alert and the `checkin` event are gone;
  an earlier recap's days become its runs). The vault's daily note now follows the Clara vault's own layout
  (`types.daily.folder` with `daily_format`, such as `1-daily/2026/2026-10-18.md`), is made from `_templates/daily.md`,
  and `vault_daily` adds its lines under `## Log`. New: `ask` event, `discord` action class, `CLARA_DAY_DIGEST_CHARS`,
  `CLARA_WAITING_REMIND_AT`. The Discord bot needs *Create Public Threads*, *Send Messages in Threads* and *Manage Threads*
  in `#general`.
- **Conversation history of the web site:** the rail groups the conversations of each project and each schedule under
  its name (the latest 5, then "Show more"), above the loose ones; the **Chat** link is gone (the logo and **New chat**
  lead to the same page). Each project and schedule has a **Show in history** button, off at first: a conversation is
  listed only when its schedule (else its project) is on, a search finds them all, a pinned one always stays. New:
  `in_history` on `PATCH /v1/projects/{id}` and `PATCH /v1/schedules/{id}`; `kind=any`, `history` and `group` on
  `GET /v1/conversations`.
- **Discord conversations can be hidden in the rail:** a switch in the account menu, kept in the browser, on at first.
  A pinned conversation and a search still show them. New: `hide` on `GET /v1/conversations`.
- **Operations:** tagged releases with checksums, optional Ed25519 signatures, preflight on a database copy and
  automatic rollback; nightly SQLite backups and restic copies; alerts; metadata-only traffic log (a failed model call keeps
  its HTTP status and the provider's error code, never its text); `clara doctor`.
- **Mail (Gmail):** one more connection on the Connections page. Gmail through its API with `gmail.readonly` and
  `gmail.compose` (and `gmail.modify`, see below; a token of its own, not Drive's). Six
  tools (`mail_search`, `mail_read`, `mail_attachment_read`, `mail_attachment_save`, `mail_draft`, `mail_send`) and three
  gate classes: reading and drafts are allowed, sending asks in a chat (the whole text and every address in the request,
  shown in full), is refused unattended, cannot be approved from Discord, and is raised to ask after untrusted content
  whatever the rules say. Rules can name a recipient (`mail:to:*@example.com`). What a message says is cleaned of hidden
  characters and CSS-hidden text and handed over as data; the sign-in proves the token reaches a mailbox before anything is
  kept; optional new-mail notifications (sender and subject only; a backlog from a stop is taken oldest first, none skipped). The tools, the classes and the limits are in FEATURES.md (sections 8, 9 and 10), the reasons in DECISIONS.md, the setup in OPERATIONS.md. Email
  connectors move out of NON-GOALS.md.
- **Mail labels, the read mark and the Trash:** Clara lists your Gmail labels, makes one, adds or removes your labels on a
  message, deletes one of your labels (its messages stay), marks a message read, and moves a message to the Trash
  (`mail_labels`, `mail_label_create`, `mail_label`, `mail_label_delete`, `mail_mark_read`, `mail_trash`). Never archived,
  never emptied, never a permanent delete. The Gmail token gains `gmail.modify`: **every Gmail connection
  made before this asks you to connect Gmail once more.** The mail tools are a **Mail pack** that the "+" menu switches on
  (off by default); migration 0016 adds it to schedules whose runs called a mail tool, and a hook gets it from its tools.
  A label is allowed in a chat; a trash asks, and "Approve all next" covers it even after mail was read (the owner's
  choice); unattended, both wait for you.
- **Hooks:** Clara acts when something happens outside. A *trigger* you write (`/triggers`, `POST /v1/triggers`; the model cannot
  make one) matches new mail by field (no regular expression), and either notifies you or runs Clara: unattended, **tainted from
  its first word**, with **only the tools you list** (by default: read mail and notify), in a conversation of its own, listed under the
  hook in the web history (a group like a schedule's, off until you switch it on). Events are stored once; the events, the runs they plan and the mail poll's place are one transaction, so a stop
  loses nothing and repeats nothing. Mail that arrived while Clara was off is handled by the trigger's `catch_up` (`all` by default,
  at `max_per_hour`; `latest`, `digest`, `skip`), a run the stop cut is not repeated unless asked. Also fixed: mail past the 50th
  that arrived during a stop was never announced; the poll now takes a backlog oldest first and forgets its place when nobody
  listens. Schema version 9 adds `trigger_events`, `triggers` and `trigger_runs`; version 10 gives a hook its `in_history` switch and removes the project that was made for each hook when it held nothing else. The web site lists hooks on the Automation page, with schedules (list, form with a condition builder, test, runs, events seen); GitHub pull requests and pushes came next (see « Hooks on GitHub » below).
- **Discord page:** the Admin page has a **Discord** tab: whether the bot is connected and what it sees in each of its four
  channels (their names, the rights it lacks), a **test message** per channel, and which channel carries each of twelve kinds of
  message (`#notification`, `#general`, `#alerts` or off on Discord; hook results and failures, new mail, Clara's own notices and
  the rest can now be told apart). Approval requests stay in `#approval`, the questions of scheduled runs in `#general`, and alerts
  cannot be switched off. The server tells the bot each event's channel, so the choice applies to the next message; with nothing
  chosen every message goes where it always went. The bot's heartbeat now also carries its name, its channels and the result of a test.
- **Questions and quizzes:** `qcm` is split in two. `ask_questions` (new) asks you up to four questions with choices while Clara works: they replace the box you write in, one after the other (a last "Other" line to write anything, a review step, Escape to dismiss), and the turn waits for your answers. `qcm` still makes a quiz, now opened in a **side panel** on the right of the conversation, one question at a time as in NotebookLM (progress bar and arrows, hint, check, skip, explanation, final score, retry): each answered question is corrected by Clara at once, a skipped one does not wake her, and the progress is kept on the server. The same panel lists, as cards you can download, the workspace files Clara wrote in the conversation. Migration 17 adds `quiz_attempts` and `quiz_answers`; new routes `GET /v1/conversations/{id}/panel`, `POST …/qcm/{ref}/answer|skip|retry` and `POST …/questions/{ask_id}`. A QCM made before this keeps its answers (one message) and shows in the panel.
- **Project tools and the QCM pack:** a project is made in steps like an automation (Basics, Content, Tools, Review), with a card for each way to start and for each special tool, and it says which special tools its new conversations start with (Mail in every one, say): `packs` on `POST` and `PATCH /v1/projects`, copied to each new conversation of the project, from any client; the project's page has a **Special tools** panel. `qcm` is now a special tool, the **QCM** pack, off until you switch it on (in the "+" menu or as a project's default); conversations made before do not get it. `ask_questions` stays always offered. Migration 18 adds `projects.packs`.
- **Side panel: vault notes and links to the Library:** the panel also lists the vault notes Clara created or edited in the conversation, and a click on a file or a note opens it in the Library (`#/library?path=…`, `#/library?view=vault&note=…`; a file keeps its download button). Each commit Clara makes in the vault in a conversation now ends with the line `Conversation: <id>`, as the workspace's do; notes written before it are not listed. `GET /v1/conversations/{id}/panel` returns `notes` beside `files`.
- **Automation forms:** the schedule and hook forms on the web site are told in steps (Trigger, Action, Tools, Runs, Review) with a bar at the foot that stays in view, a choice of radios in place of the short dropdowns (the hook's trigger as cards), a condition builder that reads as a rule, a last step that says the whole in words, and a form that does not let you go on while a step has something missing or invalid (a name, a prompt, a time, a number): Next says what, shows the error and puts the focus on the field. Nothing changes in the API or in what is stored.
- **Conversations in Discord:** the conversations of each project, schedule and hook shown in the history, and the others, are threads in a forum per place in your Discord server (a category each), with the answer written as it comes, Discord's messages read by Clara with the same tools, and archive and deletion followed both ways (a deletion on Discord asks for your approval first). Local-only conversations stay on the web site. The bot needs Manage Channels and Create Posts; `scripts/discord_live_check.py` checks them on your server. Discord's limits (500 channels, 50 categories) stop the mirror, which the Admin page can resume.
- **Automations that deliver to Discord live in a forum thread:** a schedule or a hook whose `deliver` is `discord` or `both` now
  has one thread in its automation's forum, and nothing in `#general` (no message, no thread under one). When a run starts the bot
  posts a line with the thread's link in `#notification` (a new routed category, **Automation started**, on the Admin page's
  Discord tab); the run's questions, reminder and end are written in the thread, where you answer, and the thread stays. Choosing
  `discord` or `both` switches **Show in history** on (the forum is made from it); migration 0015 does it for the automations that
  already deliver to Discord, and brings back the conversations that switch had archived. The **Discord thread** pack is not offered
  to such a run; the others keep their `#general` thread.
- **Archive:** a conversation can be archived next to its Delete button: it is kept whole and can be read, and takes no new message.
- **One automation list and form:** schedules and hooks share one list on the Automation page and one **New automation** button; its first step says what sets it off (at a time, or one of the events) and the steps after it show what fits that choice. A GitHub repository is picked from the ones added on Connections. The API is unchanged.
- **Hooks on GitHub:** a hook can wait for a pull request opened, merged or closed (`github.pull_request`) or a push to a branch
  (`github.push`) in the repositories you added. GitHub cannot call Clara, so they are found by a poll
  (`CLARA_GITHUB_POLL_SECONDS`), only while a hook waits; nothing is replayed when a hook is made, and a stop loses and repeats
  nothing. The Connections page says since when each repository's pull requests and pushes are looked for. Every change a hook's run makes on GitHub asks you first. `github_read` gained a `diff` action (a pull request's or a
  commit's changes).
- **Folders on your computer (web chat, Chromium browsers):** the **+** menu's *Folder on this computer* lets the browser open one folder
  for a conversation; Clara walks and reads it with `folder_list`, `folder_find`, `folder_grep` and `folder_read`. The server never reads your
  disk: it asks the page for each call, through the rule gate, and the page answers (read-only, with a budget of file content, `folder_budget` and
  `folder_budget_max`). Hidden files (a dot name in the path) and files a `.gitignore` ignores are listed but ask before they are read, and a card
  can *Allow for this conversation*; such a request is shown to the web only and never sent to Discord. A name pattern is matched without a regular
  expression, and a regular expression of `folder_grep` runs in a worker that is ended after 2 seconds. New: schema version 20 (`folder_grants`,
  `conversation_allowances`), the gate classes `folder_read` and `folder_sensitive`, the answer `allow_conversation`, `npm test` in `web/`.
- **Her questions on the desktop island and the phone:** `ask_questions` is also offered to a `script` token that may answer approvals (the Windows
  island and the Android app, in the separate `clara_coucou` project), which show the questions of every chat and send the answers back. New route:
  `GET /v1/questions`.
- **Before 1.0, a review:** a search that starts inside a hidden folder asked for nothing; the pending requests that name a file of your computer
  reached Discord's `/approvals`, the island and the phone; *Allow always* on such a request made a rule for every folder; a time service whose
  firing kept failing ran thousands of passes a second (it now backs off, 1 s up to 30 s); Gmail repeated a send after a 503 (only reads are
  repeated now; a 429 is still retried); `deploy/install.sh` put the secret key on a command line while it made the first tokens (it is in the
  environment now); an empty chat with no default model says so and links to Models. A rehearsal of the update (real releases, real launcher,
  a stand-in for GitHub) found that no update could ever succeed: the new release's virtualenv was built in a `.partial` folder and renamed,
  which left its scripts pointing at the old folder, so the preflight failed with "No such file or directory". It is built relocatable now.
- **New settings:** all of them (see FEATURES.md, section 14). New here: `CLARA_MAIL_POLL_SECONDS`, `CLARA_GITHUB_POLL_SECONDS`; the runtime settings `discord_routes`, `mail_notify` and `mail_notify_senders`; the secret
  `gmail_refresh_token`; the alert `gmail`.
- **Migrations:** schema version 20 (0020: the folder grants and allowances; 0019: a conversation no longer has a parent, the sub agents are gone; 0018: the special tools a project's new conversations start with; 0017: the progress of a QCM; 0016: the Mail pack; 0015: automations that deliver to Discord are shown in the history; 0014: the Brave Search connection; 0013: the Discord mirror and archived conversations; 0012: where an automation's result goes; 0011: where the GitHub poll had reached, `github_cursors`; 0010: a hook's own history group, the project made for each hook goes when it was empty; 0009: hooks, the tables `trigger_events`, `triggers` and `trigger_runs`; 0008: Gmail as a connection, which rebuilds `connections` and keeps every repository and Drive file you added, and where the new-mail notifications stopped; 0002: a model you showed or hid yourself is told from one you never decided about; 0003: the daily recap's days; 0004: tool packs, the new fields of schedules and their runs, the files Clara made on Drive; the recap's table is kept for one start so its days become runs; 0005: which projects and schedules list their conversations in the web history, all off at first; 0007: `attempts` of a schedule, 3 by default).
