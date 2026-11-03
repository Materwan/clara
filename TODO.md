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

## Moodle and Auriga: what is left

The Moodle/Auriga pack is built (`docs/DECISIONS.md`, « Moodle and Auriga »; `FEATURES.md`, section 8). It was never tried against the
real sites: its tests run against stand-ins made from what was read on 2026-11-01 and 2026-11-02, and the sign-in needs the owner at a
browser. What the first real use must settle, and what was left out:

- **The live check, by the owner** (`clara moodle connect`, `clara auriga connect`, or the Connections page): that the pasted Moodle link
  gives a token Moodle accepts; that Auriga's consent screen for `offline_access` is accepted and the token it gives is an offline one (the
  sign-in says so; `lasting` in the answer); that the Auriga answers have the shapes the client reads. Those shapes were read from
  field names, types and counts only, so they are the likeliest to differ: the room, teacher and activity type of a planning entry
  (`interventionResources[].resource`, `interventionInstructors`, `activityType`), the places of the values in a grade line
  (synthèse `[id, average, code, title, "Moy. (finale)"]`, éval `[id, mark, coef, code, type]`), the menu entries and saved queries
  (1144 / 1198 and 1036 / 945, which may differ from one account to another) and `GET /api/me`. A line that does not fit is left out, so
  a wrong guess shows as missing lines, not as a crash.
- **The migration number.** The migration is `0028_school.sql` here so that the chain is contiguous. Two other branches hold a `0028` too
  (`fix/automation-fix-prompt`, `fix/github-tool-pack`): the branch that merges after them renames its file to the next free number.
  Nothing in it depends on the others (it rebuilds `connections` and `resources`, as 0008 and 0014 did).
- **Later: keep the owner's email and password** and sign in to Auriga again with them whenever the refresh token fails, as the student app
  Chrysalide does (`gitlab.com/epimac-asso/projects/chrysalide/ChrysalideApp`, read on 2026-11-02, nothing copied): it scripts Keycloak's login
  form (`kc-form-login`, with the `AUTH_SESSION_ID` and `KC_RESTART` cookies), takes `code` from the redirect and swaps it. Nothing to
  reconnect until the password changes or Keycloak adds a check a script cannot pass. The cost: the school password sits in Clara's database,
  a bigger secret than a token, and it works only if the login form accepts the owner's account (not Microsoft). It needs its own decision.
- **Not built:** Moodle's iCal export as a lighter source for the calendar (its link carries a key and does not lapse until it is reset);
  Moodle's `mod_assign_get_assignments` (assignment cut-off dates, texts); the text of Moodle `page`, `book` and `hvp` activities (it is not in
  `core_course_get_contents`); any write to either site.

## A short first message for an automation run

The owner asked (2026-11-02) for a shorter first message on Discord when an automation starts. Its first message is the
automation's instructions and the information the run works on (a GitHub PR, a mail, …), and it takes several Discord
messages.

Decided (owner, 2026-11-02):

- The change is visual only. The model's input is not changed: a run is still given the whole message, and the stored
  question stays whole.
- The first message of an automation run shows only its origin: a mail's object (subject) and sender, a push's commit,
  a PR's name, and so on. It does not show the automation's instructions.
- The cut is for the trigger message only: the first message sent to Clara when an automation is triggered. Clara's
  answers and the owner's next messages are shown as now.
- For now it is for automation conversations only (the kinds `schedule` and `hook`).

What the code does today (read on 2026-11-02):

- A hook's first message is its prompt, then each event as JSON, up to 25 events (`message_of`, `triggers/message.py`).
  A schedule's is its prompt, then `(Attached: …)` and the document names (`message_of`, `timekeeping/schedules.py`).
  The mirror shows the stored question's text (`question_text`, `mirror/text.py`).
- Each event already has a `title` that holds its origin: a mail is `sender: subject` (`connectors/mail_watch.py`), a PR
  is `repo #number action: title` and a push is `repo branch: message` (`connectors/github_watch.py`). The titles are
  cut in code.
- The instructions go as a system message (`instructions_of`). They are not stored and not posted.
- Discord takes 2,000 characters per message, and the mirror splits the question into as many messages as it needs
  (`split`, `clara_discord/mirror.py`).

Not decided:

- A schedule has no outside origin. Its first message would be its name and the run number, and perhaps the names of
  its attached documents. The owner to say whether the document names belong there.
- Which fields each origin shows. The event `title` is the proposal; whether to add the actor, the time or a link to the
  source is open.
- A hook run with several events: one origin line per event, up to a limit, then how many are left out.
- Where the origin is read from. Recommended: the events the run was started by (the `events` table, by id), not text
  parsed out of the message, so the first message does not depend on what the model reads.
- Whether the web chat also shows the origin-only first message. Until the owner says, the web chat keeps the whole
  message.

What the current design already sets:

- The mirror's digest is taken from the text it shows (`_turns`, `mirror/service.py`). A new first message has a new
  digest, so the first message of each automation thread already on Discord is rewritten once at the next sync.
- The thread is made from the first question (DECISIONS, « a thread is made whole »), so the origin line is the first
  thing the owner sees in it.
- The model's input does not change, so the quarantined reader's rule holds as before: a mail's subject reaches the
  owner's screen through the mirror, which only displays it, and never reaches the conversation through this path.
- The bot sends with no mentions (`allowed_mentions` none, `clara_discord/bot.py`), so a subject or a commit title
  cannot ping anyone.

## Clean the Discord channels: #notification, #alerts, #approval

The owner asked (2026-11-02) for three channels to stop keeping what no longer helps:

- `#notification`: the lines whose thread is gone. These are the "automation started" messages, which end with a link to
  the run's forum thread. The thread was deleted (by the retention limits, by a deleted conversation, or by a dropped
  forum), so the link opens nothing. The owner: "no use to have them on the channel".
- `#alerts`: old alerts about problems that have since been corrected. The owner: "no use to have old alert about
  corrected problems".
- `#approval`: whether the history of every request is needed, since the server stores them. The owner asked; the first
  decision below answers it.

Decided (owner, 2026-11-02):

- A thread line whose thread is gone is removed from `#notification`.
- An alert about a corrected problem is removed from `#alerts`.

Proposed (council, 2026-11-02; the owner to confirm):

- `#approval` holds only pending requests. Settled and expired requests are not kept in Discord. The server keeps finished
  requests for 30 days (`KEEP_DAYS`, `gate/approvals.py`) and the audit log keeps every decision for 400 days.

Plan (council, 2026-11-02):

**`#approval`**

- On `approval_resolved` (sent when a request is settled or expires), the bot deletes its message instead of editing it to
  "**status**" (`clara_discord/bot.py`, `deliver`, the edit near line 607).
- At start, the bot deletes its own messages in `#approval` whose `#id` is not in `GET /v1/approvals?status=pending`. This
  covers the backlog and anything settled while the bot was down.

**`#alerts`**

- `ops/alerts.py`: `clear()` publishes a new event kind, `alert_resolved`, with the key. Today it only drops the key from
  `active` and `_sent`, and publishes nothing, so Discord never learns the problem was corrected.
- Publish it when the key was active, and also on the first clear of each key in each server run. Without the second
  rule, an alert raised before a restart (when `active` is empty) stays in Discord.
- The bot writes `alert key → message id` into its state file when it posts an alert, as it already keeps the threads. On
  `alert_resolved` it deletes that message and removes the entry.
- Backlog: once, delete every bot message in `#alerts` (see the decisions below).

**`#notification`**

- A sweep in the bot, at start and then every hour. It reads the bot's messages in `#notification` (the whole history the
  first time, then the newest 200), takes the thread link from each line that has one, fetches the thread, and deletes
  the line only on NotFound. Any other error keeps it.
- Reminders, schedule summaries, hook results and updates stay. How long they stay is a separate choice (see below).

Blind spots:

- After a server restart `alerts.active` is empty, so a fixed alert from before the restart stays in Discord (the
  first-clear rule above covers it).
- A sweep that reads only the newest 200 messages never reaches older links. The first run must read the whole channel.
- Matching `#alerts` messages to the active alerts by their text is wrong. `raise_` changes the text in memory during the
  cool-down while Discord keeps the old one, so a live alert would be deleted. Match by key only.
- A sweep that deletes on any HTTP error removes lines during a Discord outage. Only NotFound may delete.
- Retention runs every 10 minutes (`docs/DECISIONS.md`, automation retention), so a stale line can stay up to an hour
  between two sweeps. Accepted.
- A new event kind must be named in FEATURES.md, or `tests/test_docs.py` fails.

Not decided:

1. The `#approval` change (delete on settle or expiry, and the start-up sweep). Recommended; the owner's call.
2. The `#alerts` backlog: delete every bot message in `#alerts` once (recommended; the web Admin page keeps the active
   list), or keep the messages of alerts still active (needs the text match, rejected above).
3. The restart gap: the first-clear rule (recommended), or keep the active alerts in the database so the server knows
   them after a restart. The second is a bigger change, and it would also fix the Admin page after a restart.
4. The `#notification` mechanism: the sweep (recommended: one path, survives restarts, covers retention, owner deletion
   and forum drops), or event-driven removal in the bot's `remove_channel` and `remove_thread` (fewer API calls, misses
   forum drops and retention unless the server publishes them).
5. The sweep interval: every hour is proposed.
6. Whether reminders, summaries and updates in `#notification` should also be removed after a time. Not asked; a cap by
   age is a new rule.

What the current design already sets:

- The server decides the channel of each event (`events/routing.py`), and the bot posts what it is told. Alerts cannot be
  switched off.
- The Discord copy is a view: the server says what should be there and the bot makes it (`docs/DECISIONS.md`, « Discord
  mirror »).
- The bot can delete its own messages without Manage Messages, and the mirror already does so in threads. Bulk delete needs
  Manage Messages and covers only messages under 14 days old: not used.
- Destructive approvals are answered on the web site or with `clara-admin`, and the Discord token's scope is `safe`.
- Alerts are in memory (`ops/alerts.py`: `active`, `_sent`, the cool-down). Any change to `clear()` must keep the cool-down
  behaviour.
- A new event kind goes in `events/store.py` and is named in FEATURES.md.
