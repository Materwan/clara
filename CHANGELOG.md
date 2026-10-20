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
  local-only conversations, attachments (PDF, Word, text, pictures, animations), client-side tools.
- **Memory:** facts with source, confirmation, supersede, expiry and forget tombstones; FTS5 search; history search.
- **Time:** reminders, tasks with sub-tasks, unattended schedules, notifications, an event store with a cursor per
  client; zone names only, so daylight-saving changes do not shift anything.
- **Knowledge:** projects (files, `.zip` archives, GitHub snapshots), conversation files, a slim Obsidian vault (seven
  tools behind `open_second_brain`), forms.
- **Rule gate:** allow, ask or refuse for every tool call; editable rules; the tainted-turn rule; secrets leaving the
  machine raise to ask; approvals from any client (Discord buttons included, destructive excepted); an audit log.
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
  - **Tasks and Schedules:** the list comes first; the form opens from **New task** / **New schedule** (and from **+ Sub**
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
  diff), **daily notes** (a range), and, for scheduled runs, **ask you** (`ask_person`, `finish_run`) and **Discord thread**.
  In the web chat the attach button became a **"+" menu**: add files, switch the packs, web search and the second brain on
  or off, choose the project; the choice is saved with the conversation, and `/tools` does the same on Discord and in the
  terminal. A pack decides which tools exist, never what the gate allows. Incognito conversations are never read back;
  local-only ones only from a local-only conversation.
- **Schedules grew up:** packs, a model of its own, one conversation per run, a run that **asks and waits** for your answer
  (`waiting`, reminded once, closed from what Clara has, `finish_run`), what to do with runs missed while the server was off
  (`skip`, `latest`, `all`) and with a run that comes due before the last is over (`skip`, `replace`, `queue`). The
  Schedules page has all of it. Fixed: "Run now" on the Schedules page failed (the route needed the server's event loop).
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
- **Sub agents of the console on the web site:** a console sub agent (`local_task`) works in an incognito conversation of its
  own. It no longer shows in the rail, in a search or in the console's list: the chat page of the conversation that started
  it shows it as a block that opens under the `local_task` call (a link to the sub agent's own conversation leads to its
  parent). It is kept as long as its parent: the incognito sweep skips it, and deleting or sweeping the parent deletes it.
  New: `parent` in the body of `POST /v1/chat/stream` (a new conversation only; one level deep), `parent` on a conversation
  and `subagents` on `GET /v1/conversations/{id}`.
- **Discord and console conversations can be hidden in the rail:** two switches in the account menu, kept in the browser,
  both on at first. A pinned conversation and a search still show them. New: `hide` on `GET /v1/conversations`.
- **Operations:** tagged releases with checksums, optional Ed25519 signatures, preflight on a database copy and
  automatic rollback; nightly SQLite backups and restic copies; alerts; metadata-only traffic log (a failed model call keeps
  its HTTP status and the provider's error code, never its text); `clara doctor`.
- **New settings:** all of them (see FEATURES.md, section 14).
- **Migrations:** schema version 6 (0002: a model you showed or hid yourself is told from one you never decided about; 0003: the daily recap's days; 0004: tool packs, the new fields of schedules and their runs, the files Clara made on Drive; the recap's table is kept for one start so its days become runs; 0005: which projects and schedules list their conversations in the web history, all off at first; 0006: `parent_id` of a conversation, for a console sub agent's, deleted with its parent).
