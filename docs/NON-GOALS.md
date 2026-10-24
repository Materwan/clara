# Non-goals

What Clara 1.0 deliberately does not do, so the project stays something one person can secure, run and explain. A
change that adds one of these needs a decision written in [DECISIONS.md](DECISIONS.md) first.

## Gone on purpose

| Not done | Why | Instead |
| --- | --- | --- |
| Accounts, users, passwords, roles, sign-up, account linking, aliases | One person: every one of them was attack surface and complexity for nobody | One token per client |
| Credits, quotas, daily limits, per-user keys | Nothing to share, nothing to ration | Token statistics (Usage page) |
| Discord for other people: shared servers, chime-in, observe mode, mentions, members, direct messages | Reading other people is a privacy problem and a large product of its own | A bot in a private server of your own: it answers your id only, in one channel, and has no member list |
| Music (Music Assistant tools and site) | A separate product | — |
| The relationship score | A hidden mood that changed tone was surprising, not useful | A fixed persona file |
| Server-folder access (`res_*`) and the desktop "computer folders" | Folders on the server could hold Clara's own secrets | The workspace, and `clara-chat --local-tools` for your own machine's files |
| Embeddings and semantic search | Heavy, and FTS5 serves one person's notes | FTS5 for facts, messages and the vault |
| Public exposure (Funnel, a public URL) | The tailnet is the perimeter | `tailscale serve`; Funnel is refused |
| A shared GitHub token | Copying any private repository the token reached | Your fine-grained token and an explicit repository list |
| Most vault tools (20 before) | Fewer, sharper tools | Seven, behind `open_second_brain` |
| Migrating the old data | A clean start without people and credits in the schema | The old server stays readable for a few weeks |

## Not planned

- **Several people or a family mode.** Everything assumes one person; adding people would bring back the whole
  account layer.
- **Other operating systems for the server.** Linux with systemd and rootless Podman only. Clients work anywhere.
- **Scripts without a container.** If rootless Podman does not work, scripts stay off: there is no plain-subprocess
  fallback.
- **Automatic updates.** Releases are installed when you choose one.
- **Spending caps.** Spending is shown, not capped. Use the providers' own limits if you need one.
- **Arbitrary MCP servers or plug-ins.** Every tool is in this repository, with its gate class, and tested.
- **Hook sources other than mail and GitHub** in 1.0 (Drive changes, files, GitHub issues and comments, workflow runs). The design
  takes them (`triggers/events.py`, a source hands events to `Triggers.ingest`); the mail source and GitHub pull requests and pushes exist.
- **Receiving webhooks from the internet.** Clara is reachable on your tailnet only, so GitHub or Google cannot call her; sources are
  polled. A script on your own tailnet is the one thing that could push (not built).
- **Calendar and messaging connectors** in 1.0. Each would be a new outside-world class with its own defaults. (Email
  moved out of this list: Gmail is connected, with three classes of its own; see
  [DECISIONS.md](DECISIONS.md).)
- **More than Gmail for mail, and more than one mailbox.** Other providers, IMAP/SMTP with a password
  (a password cannot be limited to reading), several Gmail mailboxes at once.
- **Organising a mailbox:** archive, filters, forwarding rules, an HTML composer, and any permanent delete. Labels,
  deleting a label, the read mark and moving a message to the Trash are in (see [DECISIONS.md](DECISIONS.md)). The Gmail
  token has no scope for the settings, and no tool calls the permanent delete.
- **Translation or stemming in memory search.**
- **A mobile app.** The web site works on a phone over the tailnet.
- **Rewriting git history to erase deleted files** in the workspace or the vault. Deletion there is undoable on
  purpose; erase by hand if you must.
