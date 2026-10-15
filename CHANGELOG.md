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
- **Clients:** a Svelte web site, `clara-chat`, `clara-admin`, a DM-only Discord bot, the HTTP API.
- **Operations:** tagged releases with checksums, optional Ed25519 signatures, preflight on a database copy and
  automatic rollback; nightly SQLite backups and restic copies; alerts; metadata-only traffic log; `clara doctor`.
- **New settings:** all of them (see FEATURES.md, section 14).
- **Migrations:** schema version 1.
