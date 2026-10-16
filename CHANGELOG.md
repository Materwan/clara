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
- **Operations:** tagged releases with checksums, optional Ed25519 signatures, preflight on a database copy and
  automatic rollback; nightly SQLite backups and restic copies; alerts; metadata-only traffic log; `clara doctor`.
- **New settings:** all of them (see FEATURES.md, section 14).
- **Migrations:** schema version 1.
