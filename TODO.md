# TODO

Planned work, not built yet. Each item says what the owner asked for; the notes under it are open questions and the
constraints the current design already sets, not decisions. When an item is built, name its pieces in
[FEATURES.md](FEATURES.md) (the docs test requires it), record the choice in [docs/DECISIONS.md](docs/DECISIONS.md) as a
dated entry, and remove it from here.

## The firewall: what is left

The firewall against tainted conversations is built (`docs/DECISIONS.md`, « A firewall against tainted conversations »).
What it leaves, not decided:

- GitHub, Drive, folder files and the events of GitHub hooks keep the taint flag only: they are not read by the quarantined reader.
- The reader is on by default. Its cost (one call for a page, a mail or an attachment, one for a list) is in the usage log under the
  kind `reader`; there is no per-day cap.
- A script's reading of a marked file is not followed (`docs/THREAT-MODEL.md`).
- The raw text of a page or a mail is not shown to you in the chat: only the report is. A card that opens the raw text, for you and not for
  the model, would need a client-side change.

## GitHub as a special tool

The owner asked for the GitHub tools to be a special tool of a conversation, as Mail is: a **GitHub** pack, switched per
conversation from the "+" menu or `/tools`, off in a new conversation. The pack holds all five tools: `github_read`,
`github_write`, `github_branch`, `github_pr` and `github_issue`.

Not decided:

- Today the five tools are offered in every chat once a token and a repository are set (`connectors/setup.py`). With a
  pack they are offered only where it is on. Whether existing conversations keep them after the change is open.
- Whether schedules, and the packs a project starts its conversations with, can take the GitHub pack, as they can take
  Mail. Hook runs get `github_read` from their event kind, not from a pack.
- Its check: unusable, with the reason, when no token or no repository is set, as Mail is when no mailbox is connected.

What the current design already sets:

- A pack only decides which tools exist. The rule gate still decides each call; a write asks first from level 4, as
  the gate sets it.
- A local-only conversation is never offered the GitHub tools, whatever packs are on.
- `github_read` still taints the turn.

## A way for Clara to add to the TODO

The owner asked for a way for Clara to add something to the TODO. The first reading is this file: she writes an item
here, and the owner reads it and commits it. Whether "the TODO" means this file or Clara's own task list is open.

Not decided:

- Which list: this file, or Clara's own tasks. The tasks already exist (`add_task`, the tasks API).
- How she adds it: a tool in a chat, a console command, or both.
- What she may write: a new item, a note under an existing item, or a new section.
- Whether an item made from outside content (a web page, a mail, a GitHub thread) is allowed. The tainted rule raises
  outside writes to `ask`, but a write to a local file is not on its list today, so such an item would not ask.

What the current design already sets:

- A write to this file works only where this checkout is (the development server). Production runs from a release in
  `/opt/clara`, and the owner commits any change, never Clara.
- A new tool needs a class, a level and a target in the gate, is named in FEATURES.md (the docs test checks it), and
  the tool count line is updated.

## Moodle and Auriga (EPITA)

The owner asked for Clara to be connected to two school sites: Moodle (https://moodle.epita.fr/my/) for the owner's
courses, and Auriga (https://auriga.epita.fr/) for the owner's calendar.

Not decided:

- How Clara signs in. Both sites sit behind the school login. Whether Moodle's web-services token, or a calendar export
  from Auriga if it has one, can replace the password has not been checked.
- What she may do. The request is to read the courses and the calendar. Writing (a calendar entry, a Moodle submission)
  is not asked for and would need its own decision.
- The shape: a connection on the Connections page, as GitHub and Drive have, or a pack of read tools, as Mail has.
- Whether a turn that reads course pages or calendar entries taints the conversation. They are outside content, as a
  GitHub thread is, unless the owner decides otherwise.

What the current design already sets:

- A credential goes into the encrypted secrets store (`clara secrets set`, or the Connections page), never into `.env`,
  which holds only non-secret settings.
- A connector's reads are untrusted and taint the turn (`github_read`, `drive_read`); a new one follows that.
- A new tool needs a gate class, a level and a target, and FEATURES.md names it.
