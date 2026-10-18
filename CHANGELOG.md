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
- **Live log:** an Admin **Logs** tab streams the server's log and the traffic records as they happen (source and
  level filters applied on the server, pause, follow, clear). Memory only: the last 500 lines, secrets masked, no
  sign-in addresses, never the debug content. New route: `GET /v1/admin/logs/stream`.
- **Daily recap:** at the end of each day (`CLARA_RECAP`, 23:59, or at the next start) Clara opens a French check-in
  conversation with a digest of the day made in code (conversations with their earlier context, tasks, reminders,
  scheduled runs, vault changes from git, her own actions), announces it on the web site and in a Discord thread under
  the question in `#general`, **waits for your answer**, then writes the recap into one replaceable block of the
  vault's daily note, with `[[links]]` to notes that exist, and marks the tasks you confirmed. Unanswered, it is
  reminded the next morning and closed from the digest alone when the next check-in comes. New: the `write_daily_recap`
  tool, the `checkin` event, `/recap`, the `recap` alert, `CLARA_RECAP`, `CLARA_RECAP_AT`, `CLARA_RECAP_REMIND_AT`,
  `CLARA_RECAP_DIGEST_CHARS`. The vault's daily note now follows the Clara vault's own layout
  (`types.daily.folder` with `daily_format`, such as `1-daily/2026/2026-10-18.md`), is made from `_templates/daily.md`,
  and `vault_daily` adds its lines under `## Log`. The Discord bot needs *Create Public Threads*, *Send Messages in
  Threads* and *Manage Threads* in `#general`: it deletes the check-in's thread once the recap is written (a line in
  `#general` gives the note's path).
- **Operations:** tagged releases with checksums, optional Ed25519 signatures, preflight on a database copy and
  automatic rollback; nightly SQLite backups and restic copies; alerts; metadata-only traffic log (a failed model call keeps
  its HTTP status and the provider's error code, never its text); `clara doctor`.
- **New settings:** all of them (see FEATURES.md, section 14).
- **Migrations:** schema version 3 (0002: a model you showed or hid yourself is told from one you never decided about; 0003: the daily recap's days).
