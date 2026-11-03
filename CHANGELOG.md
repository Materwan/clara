# Changelog

Every release has a section here, under its tag; the release workflow publishes that section as the release notes
(what changed, migrations, new settings). Dates are in UTC.

## v1.5.0 — 03/11/2026

**Clara reaches your school, hands files over, and Discord stays tidy.** Moodle and Auriga (EPITA) can be read by Clara, read
only. A new tool, `present`, hands a file over as a card under her answer. The Discord channels no longer keep what stopped being
true: settled requests, ended alerts and lines whose thread is gone are deleted. GitHub becomes a pack, as Mail is. A run of an
automation shows Discord only where it came from. **The Moodle/Auriga pack has never been tried against the real sites** (see
below).

**Moodle and Auriga, read only.**

- **A pack of five tools, « Moodle/Auriga »:** `school_calendar` (Auriga's classes, tutorials and exams with room and teacher,
  Moodle's assignments, activities and quizzes; 92 days at most, 7 by default), `school_grades` (Auriga's grades of a school year,
  or Moodle's grades of one course), `moodle_courses`, `moodle_files` and `moodle_file_save`. Switch it on per conversation in the
  « + » menu, as Mail; it is offered in a chat and in the packs of a schedule, only while a site is connected, never in a
  local-only conversation.
- **Signing in, once, in a browser:** on the Connections page (two new cards), or `clara moodle connect` and `clara auriga connect`.
  Moodle has no password form, so you open the page of its mobile app signed in and paste the `moodlemobile://token=…` link back;
  Auriga is signed in through its Keycloak and you paste back the address of the blank page you land on. No password is kept. Clara
  keeps the Moodle token only if Moodle accepts it, and renews Auriga's sign-in herself. `/school` shows who is connected and
  whether a sign-in lapsed, and forgets a token.
- **Read only, on purpose:** Moodle's token has all your rights there and cannot be made read-only, so Clara calls seven read
  functions and nothing else, and sends the token only to `moodle.epita.fr` (a redirect is not followed). Nothing is written to
  either site. A Moodle document is saved to the workspace, never over an existing file, with a text copy beside a PDF or a Word
  file.
- **A lapsed sign-in is said, not hidden:** the connection is marked « sign in again », the alert `moodle` or `auriga` is
  raised, and the tools say how to sign in again.
- **Reading these sites does not taint the turn** (your choice, unlike Mail, GitHub and Drive): their text reaches the
  conversation as written, with no quarantined reader.
- **Not tried live:** the tests run against stand-ins built from what was read of the two sites on 1 and 2 November. The shapes
  Auriga's planning and grades answers have are the likeliest to differ; a line that does not fit is left out, so a wrong guess
  shows as missing lines, not as a crash. The first real use is the check; [TODO.md](TODO.md) lists what to look at.

**Handing a file over: `present`.**

- Clara calls `present` with a workspace path, `vault:<note>` (the second brain, once open in the conversation) or `drive:<id>`,
  and you get a **card** at the end of her answer: name, folder and size, **See in the workspace** (or the vault) and
  **Download**; a Drive file is an **Open in Drive** link and nothing is downloaded through the server. At most 5 cards an answer.
- She may present the files she wrote in the conversation; any other workspace file only when the **Files created** pack is on.
- The file is not read: the model gets one line, and presenting does not taint the conversation. A file that came from outside
  (a mail attachment, what a script fetched) carries a « from outside » tag until you trust it.
- The card is found again when you open the conversation, and follows an answer that is edited, tried again or switched to
  another version. Discord shows a line `📎 <reference>` under the answer and uploads nothing.
- **A file she writes no longer shows a card by itself:** the web site used to draw an Open / Copy / Download card when Clara wrote a
  note under `notes/`, gone after a reload. The side panel still lists what she wrote, and her persona now tells her to call
  `present` to hand a file over.

**Clean Discord channels.**

- **`#approval`** holds only the requests that wait: a request that is answered or expires is deleted from the channel. A button
  answer deletes its card at once.
- **`#alerts`**: when an alert's problem is over, the server publishes `alert_resolved` and the bot deletes that alert's message.
  The Admin page lists the alerts that last.
- **`#notification`**: the line posted when a run starts, with the link of its thread, is deleted once the thread is gone
  (retention, a deleted conversation). Reminders, summaries, mail and update news stay.
- The bot deletes only its own messages, in its own process. It needs *Read Message History* in the three channels (the Admin
  page's Discord tab shows it when it is missing).

**Smaller changes:**

- **An automation run's first message in Discord:** the instructions and the data of a run (a mail, a pull request) fill several
  messages. The thread now shows only where the run came from: a hook's events, one line each (five at most, then `+N more`),
  or a schedule's name and the moment it was due. Clara is still given the whole message; the web site shows it whole.
- **GitHub is a pack:** the five `github_*` tools are offered only when the **GitHub** pack is switched on in the « + » menu (off in
  a new conversation), and only with a token and a repository set. A hook's run gets it from the tools its list names. What already
  used GitHub keeps it (see *What changes when you update*).
- **Run lines:** the Moodle and Auriga tools and `present` have their own words in the lines that say what Clara did, on the web
  site and on Discord.

**What changes when you update:**

- **Discord, once:** the first time the updated bot starts, it deletes **all its older messages in `#alerts`** (they carry no key,
  so nothing says which problem they were about; an alert that still lasts is on the Admin page and is posted again at its next
  repeat), and it reads the whole of `#notification` and `#approval` to delete the stale lines and cards. Give the bot *Read Message
  History* in those channels first, or it cleans nothing.
- The GitHub tools disappear from a conversation or a schedule that never used them, until you switch the pack on. A conversation
  and a schedule that called a GitHub tool before are given the pack once.
- The Moodle/Auriga pack is off everywhere and does nothing until a site is connected.
- A note Clara writes no longer shows a card in the chat; she hands files over with `present`.

**Interface changes:**

- **New routes:** `POST /v1/connections/moodle/start`, `PUT /v1/connections/moodle`, `DELETE /v1/connections/moodle`,
  `POST /v1/connections/auriga/start`, `POST /v1/connections/auriga/finish` and `DELETE /v1/connections/auriga`.
  `GET /v1/connections` also lists Moodle and Auriga. The messages of a conversation carry `presented`.
- **Tools:** 89 (83 before). New: `present`, `school_calendar`, `school_grades`, `moodle_courses`, `moodle_files`,
  `moodle_file_save`. The five `github_*` tools are in the pack GitHub.
- **Events:** the stream event `present` (a card for a presented file); the event `alert_resolved`, for the Discord bot only.
- **Commands:** `/school [status] | disconnect <moodle|auriga>`; `clara moodle connect` and `clara auriga connect`.
- **Secrets:** `moodle_token` and `auriga_refresh_token`, set by the sign-in, never by hand. **Alerts:** `moodle` and `auriga`.
- **Auto levels:** the classes `school_read` (asks from level 2, runs from 2) and `school_save` (asks from 2, runs from 3). Targets
  `school:moodle` and `school:auriga`.
- No new `CLARA_*` setting and no new runtime setting.

**Migrations:** 0028 adds the shorter text Discord shows for a question (`messages.shown`), 0029 gives the GitHub pack to what
already used GitHub, and 0030 adds the Moodle and Auriga connections (it rebuilds `connections` and `resources`, and keeps every
repository and Drive file you added). The server takes its backup before running them.

**Docs:** [DECISIONS.md](docs/DECISIONS.md) (« Clean Discord channels », « GitHub is a pack », « Moodle and Auriga »,
« Presenting a file », and the first message of an automation run), [FEATURES.md](FEATURES.md) and
[OPERATIONS.md](docs/OPERATIONS.md) (« Moodle and Auriga (EPITA) »).

## v1.4.0 — 01/11/2026

**A firewall against text written to steer Clara, and tool calls booked for later.** A web page, a mail or an attachment can
carry sentences meant for an AI. Until now Clara read them as they were and the gate was the only backstop. Now a separate
model reads that text first, and a conversation that read something from outside asks before it lets private text leave.
Clara can also book a tool call to run later (« trash this mail in two days »). **The reader is on after the update** (see
*What changes when you update*).

**The quarantined reader.**

- **What it reads:** the text of a web page, of a mail, of an attachment, of a workspace file that came from outside, and the
  results of a web search or a mail search. It is a separate model call with **no tools and no memory**, and it must answer with
  one JSON object of a fixed shape, which is checked field by field. Only that checked report reaches the conversation: a summary
  and an answer to the model's `focus` (what to look for), up to 10 facts and, for a mail, the kind of message, what the sender
  asks for and the dates it names. Text addressed to an AI sets `addressed_to_ai` and the report carries a warning.
- **What the conversation no longer sees as written:** the sender's name, a mail's subject and preview, a page's title and the
  words of its links, and an attachment's name. The headers it gets are addresses, dates and ids. In a list (a search, a mail
  search) a result is its address and a one-line gist of 160 characters.
- **It fails closed:** if the reader cannot answer (no model, an error, two answers that are not usable), the page, mail,
  attachment or file is **not** given to the conversation and the tool says so; a list is shown without its gists.
- **What it costs:** one model call for a page, a mail or an attachment, one for a list, in the usage log under the kind `reader`.
  The model is `reader_model`, else the one of titles and summaries; it is local when the conversation is local only. The
  conversation stays tainted afterwards.
- **Hooks:** a run that a mail started is told the sender's address, the ids, the flags and a gist, not what the sender wrote.
  GitHub events are shown as they were. Notifications (for you, not a model) and the *Test* dry run are not changed.

**Asking more after outside content.**

- **Fetching an address nobody gave** (`fetch_other`) asks in a tainted turn, whatever the level and the rules say. This
  **reverses** the choice of 30/10 that left it out: an untrusted host can carry private text in its path. An address you wrote
  or a search returned is still read without asking.
- **A search query that repeats private text** asks: in a tainted turn, a query that repeats four words in a row, or an
  identifier (an email address, a reference with a digit), from a mail, a file, a note or a fact, that you did not write. A query
  that looks like a secret asks too. It is a screen, not a guarantee (`THREAT-MODEL.md` says what passes).
- **Where a workspace file comes from:** a file that arrived from outside (an attachment kept by `mail_attachment_save`, what a
  script changed when it ran with the network) is marked `quarantine`, and one Clara wrote after the conversation read outside
  content is marked `tainted`. Reading a marked file taints the conversation, even a new one. The marks follow a move and
  survive a delete and its undo. You trust a file on the Library page (**Trust this file**), with `/trust <path>` or through the
  API; a file whose content changes is untrusted again. Only a token that may answer every request can trust, and the model
  has no tool for it.
- **Not covered yet:** GitHub, Drive and folder files keep the taint flag only, and a script that prints a marked file gives its
  text to the conversation (see [TODO.md](TODO.md) and [THREAT-MODEL.md](docs/THREAT-MODEL.md)).

**Booked calls.**

- **How it works:** with the **Booked calls** pack of a chat (off until you switch it on, never offered to a run), Clara calls
  `deferred_book` with a tool, its arguments and a date (between a minute and 366 days ahead, 50 waiting at most). The request
  shows the whole call and its date and you allow that exact call once, as a request to make an automation does. At the due time
  the server runs it by itself with the saved arguments: no model turn, nobody in the conversation.
- **What still stops it:** the floor, the local-only rule and a rule that **refuses**; `deferred_book` refuses at once a call that
  a rule would refuse then. A rule that **asks** turns the call into an ordinary approval request that runs only if you allow it
  within 24 hours. A call whose summary changed since you read it (a mail already trashed, an automation edited) is not run.
- **When it ran:** you get a notification (category `booked_calls`) saying it was done, failed or not run; Clara is not told. A
  call due while the server was off runs once when it comes back (the `late:` alert says so); a call that was running when the
  server stopped is marked `failed` and never run twice. A local-only call's notice stays off Discord and scripts.
- **Where to see them:** the **Booked calls** tab of the Tasks & reminders page, with **Cancel** for one that waits; Clara can
  list and cancel them too. Finished calls are kept 30 days.

**Smaller changes:**

- **Request cards name the item:** a request about a mail or a Drive file shows the subject and sender of the mail (the name of
  the file) in quotes, with the id last, on the web site, the island and the phone. Clara's own prompt gets the plain wording.
- **A local-only conversation's request never reaches Discord:** not its `#approval` message and, for the Discord token, not
  `GET /v1/approvals`. The other clients still get it.
- **The web site copes better with a slow link:** a conversation and its messages are fetched together; a GET is tried again twice
  after a network error, a timeout or a 502 to 504 (a write never is); the time limit counts only the wait for an answer to start;
  a conversation that failed to load takes no action (no Send, no menu). A long conversation is drawn newest first, a few
  exchanges at a time, and only the messages near the screen are in the page, so a conversation of thousands of messages no
  longer freezes the tab. The browser's find and a text selection across windows do not see the messages that are out of the window.
- **`GET /v1/admin/status`** (polled every few seconds by the phone and the island) no longer reads the disk and git on the
  event loop: one look at a time, and a poll during a slow one gets the last answer.

**What changes when you update:**

- The reader is on. Pages, mails and attachments cost a model call and Clara no longer sees a mail's subject or a sender's name
  as written. `reader` off (Admin page) brings the old behaviour back for the text; the new asks stay.
- A conversation that read outside content now asks before fetching an address nobody gave.
- The Booked calls pack is off in every conversation until you switch it on.
- The Discord category `booked_calls` goes to `#notification` unless you route it elsewhere.

**Interface changes:**

- **New routes:** `GET /v1/deferred` and `DELETE /v1/deferred/{deferred_id}`; `GET /v1/workspace/provenance`,
  `POST /v1/workspace/trust` and `DELETE /v1/workspace/trust`.
- **Changed:** `GET /v1/approvals` leaves out a local-only request for the Discord token.
- **Tools:** 83 (80 before). New: `deferred_book`, `deferred_list`, `deferred_cancel`. `web_fetch`, `mail_read`,
  `mail_attachment_read` and `ws_read` take `focus`; what `web_search`, `mail_search` and `ws_search` show is changed as above.
- **Command:** `/trust [list] | <path> | untrust <path>`.
- **Settings:** the runtime settings `reader` (on), `reader_model` (empty) and `reader_max_chars` (40,000). No new `CLARA_*`
  setting. The auto-level class `deferred` asks from level 2 and runs at the last.

**Migrations:** 0025 adds the booked calls (and the time zone of a request), 0026 remembers whether the turn that booked a call
had read outside content, 0027 adds the marks of the workspace files and the conversations kept tainted. The server takes its
backup before running them.

**Docs:** [DECISIONS.md](docs/DECISIONS.md) (« Deferred tool calls », « A firewall against tainted conversations »),
[FEATURES.md](FEATURES.md), [THREAT-MODEL.md](docs/THREAT-MODEL.md) and [DATA-MAP.md](docs/DATA-MAP.md).

## v1.3.0 — 31/10/2026

**Cleaning up what automations make.** Every hook run and every run of a per-run schedule leaves a conversation behind, and
they pile up. You can now set limits and Clara deletes the conversations of her automations that pass them. **Nothing changes
until you set one:** all the limits are off (0) after the update, and no conversation is deleted.

- **What is cleaned:** the conversation of each hook run and each conversation a per-run schedule makes. Never touched: a
  schedule's shared conversation, the chats reminders and tasks post into, project chats and every other conversation. Messages
  you talk in are still kept until you delete them.
- **Two limits, whichever comes first:** an **age** (days and hours since the last message) and a **count** (the latest N
  conversations of each schedule or hook). A kept conversation takes its place in the count.
- **Where you set them:** three new runtime settings on the Admin page (`automation_keep_days` with `automation_keep_hours`,
  and `automation_keep_last`), and a **Cleaning up** section on each schedule (per-run) and hook of the Automation page, with
  its own limits. Empty follows the Admin setting; `0` there means no limit for that automation.
- **Kept whatever the limits:** a pinned conversation; one **you** archived (an archive made by Discord's idle week or by
  switching a history group off does not count, and neither does closing a run's thread by hand on Discord: archive it from
  Clara to keep it); a run that is queued, running or waiting for an answer; a conversation with an approval request pending.
- **Said before it is kept:** deletion is for good, so the Admin page asks to confirm a limit that would delete something, the
  Automation form writes how many conversations the next pass would delete, and Clara's request says it too. The counts only
  include what the change adds to what the limits already saved delete.
- **Clara's Automations tool** may change the limits, as an ordinary change that asks (`-1` follows the setting). If more would
  be deleted by the time you allow it than the request said, nothing is changed and Clara asks again. The global settings are
  not in the tool.
- **How it deletes:** a pass every 10 minutes, in a loop of its own. It is the usual deletion: messages and files go, the
  Discord thread is removed, the run keeps its row without the link, and the facts the automation learned stay. Backups keep
  the text for as long as for any deleted conversation.

**Interface changes:**

- **New route:** `GET /v1/retention/preview`: how many conversations the next pass would delete for limits not saved yet
  (the runtime settings as given, or with `kind` and `id` one automation's own). Nothing is deleted.
- **Changed:** `POST /v1/schedules`, `PATCH /v1/schedules/{schedule_id}`, `POST /v1/triggers` and
  `PATCH /v1/triggers/{trigger_id}` take `keep_days`, `keep_hours` and `keep_last` (null follows the settings).
- **Tools:** `automation_create` and `automation_update` take `keep_days`, `keep_hours` and `keep_last`.
- **Settings:** the runtime settings `automation_keep_days` (0), `automation_keep_hours` (0) and `automation_keep_last` (0).

**Migrations:** 0024 adds `keep_days`, `keep_hours` and `keep_last` to schedules and hooks, empty on the ones that exist
(they follow the settings). The server takes its backup before running it.

**Docs:** [DECISIONS.md](docs/DECISIONS.md) (« Automation retention », with the decisions of this release),
[FEATURES.md](FEATURES.md) and [DATA-MAP.md](docs/DATA-MAP.md).

## v1.2.0 — 30/10/2026

**A memory for each project, schedule and hook.** Next to the global memory, every project, schedule and hook keeps a memory
of its own, shared by all its conversations and runs: what Clara learns in one, the next one knows. A standing rule such as
« remove the newsletters of Acme » reaches every run that needs it. Clara chooses where each fact goes (`remember` takes a
`scope`) whenever the conversation has a project or an automation; nothing is chosen for her otherwise. The Memory page keeps
the global memory; the others are on their own pages: the **Memory** panel of a project, and the **Memory** button of a
schedule or a hook.

- **Which memories a conversation reads:** the global one, its project's and its automation's. `recall_facts` searches them,
  and `forget` reaches only them.
- **Waiting facts:** after outside content (a web page, a mail, a Drive file, an issue), what Clara saves for a project or an
  automation is stored unconfirmed and hidden from every conversation until you confirm it on that page. A run nobody watches
  that leaves one sends one notification, without the fact's words.
- **Limits:** 100 live facts per project, schedule or hook, of which 20 may wait for your confirmation. The prompt shows each
  memory within `CLARA_SCOPE_FACTS_TOKEN_BUDGET` (1,000 tokens), pinned facts first.
- **Deletion:** deleting a project, a schedule or a hook deletes its memory with it, every fact included (expired and replaced
  ones too). The delete dialogs and the Discord request say how many.
- **Private conversations:** an incognito conversation reads and writes none of it. A local-only one reads its project's and
  its automation's memories but never writes to them.

**Changes you will notice:**

- **A conversation that has read untrusted content stays tainted for the rest of it.** In 1.1.0 the taint lasted while the
  content was still in the prompt. Now every later turn is tainted too, so an outside write (sending mail, a GitHub or Drive
  change, a networked script, a change to an automation) asks in those turns as well, even when the page was read long before.
  The last level of a chat still suspends this rule, as before.
- **Only Confirm confirms a fact.** In 1.1.0 editing a fact confirmed it, pinning included. Now pinning, dating, categorising or
  editing a fact that waits leaves it waiting until you press Confirm.
- **A fact that waits cannot be replaced.** Confirm it, or delete it, first.

**Interface changes:**

- **New route:** `GET /v1/memory/scopes`: for each project, schedule and hook that has a memory, its live facts (`facts`), how
  many wait for you (`waiting`) and every fact it keeps (`total`).
- **Changed:** `GET` and `POST /v1/memory/facts` take `scope`: empty for the global memory, else `project:<id>`,
  `schedule:<id>` or `hook:<id>`.
- **Tools:** `remember` takes `scope` (`global`, `project` or `automation`); `recall_facts` and `forget` reach the conversation's
  memories as well as the global one.
- **Setting:** `CLARA_SCOPE_FACTS_TOKEN_BUDGET` (1000).

**Migrations:** 0023 adds a scope to every fact (the facts that exist stay in the global memory), gives the tombstones a scope,
and deletes a memory together with its project, schedule or hook. The server takes its backup before running it.

**Docs:** [DECISIONS.md](docs/DECISIONS.md) (« A memory for each project, schedule and hook », with the decisions of this
release) and [FEATURES.md](FEATURES.md).

## v1.1.0 — 30/10/2026

**Auto levels.** What happens to a call when no rule matches is no longer a fixed table of chat and unattended defaults:
each conversation runs at a **level**, and the level decides, tool by tool, what is refused, asked or run. **Read the
"Changes you will notice" list below before updating**: several things that asked in a chat, or ran in a schedule, now sit at
a level of their own.

- **The ladder:** level 0 refuses every tool, the last level runs every tool, and four stand between at first (1 Trusted
  read, 2 Any read, 3 Files and vault, 4 Mail; the last is Everything). You can add, rename and remove the levels between the
  first and the last (at most 10 in all). A new level acts like the one below it until you edit it, and removing one never
  makes anything more permissive: what was set to it moves down (a conversation, a default) or up (a tool's threshold).
- **Tool thresholds:** each tool has the level it **asks** from and the level it **runs** from; below the first it is refused.
  A tool follows the default of its action class (the table in FEATURES.md, « Auto levels ») until you change it. They are
  kept class by class: a script with network (`run_network`) and one without (`run`), an address you wrote (`fetch_trusted`)
  and any other (`fetch_other`), a normal and a hidden file of your computer's folder each have their own default, and a
  change to one leaves the others as they were. A setting without a class covers all the classes of a tool.
- **Where a conversation's level comes from:** its own level, else the schedule or hook it belongs to, else its project, else the
  server default (new runtime settings `auto_level_default` for a chat and `auto_level_unattended` for a schedule, reminder or
  hook run, both 3 at first, on the Admin page). A conversation with none of its own *follows*, so changing a project's level
  changes all its conversations that follow it. The level is read again at every tool call: lowering it stops the next tool
  of a turn that is running.
- **Where you set it:** a **level selector** in the chat (a new conversation can be started at a level; choosing the last level
  asks first), the **Auto level** of a project, of a schedule and of a hook, the **Auto mode** page in Settings (the ladder, the two
  defaults, and every tool's thresholds with a bar showing refuse, ask and run at each level, changed one tool or one action
  class at a time), `/level` in the console (`/level tool <name> [<class>] [<ask> <run> | reset]` and the rest), `/level` in
  `clara-chat` and in Discord (inside a conversation), and the API.
- **A refusal of the level is final:** no rule can open a tool the level refuses, so level 0 really means no tool. Where the
  level asks or runs, your rules still decide first (an `ask` rule asks, a `refuse` rule refuses). A request you already
  answered runs as it was shown, whatever the level became meanwhile.
- **The last level of a chat suspends the tainted-turn rule and the secret check** (your choice; the selector and the Auto mode
  page say so). Levels 1 to 4 keep both, and a run nobody watches never has them suspended. The folder of your computer
  (`folder_read`, `folder_sensitive`) is refused to a run nobody watches at every level.
- **Who may change a level:** a level lets calls run without a card, so it is an answer given in advance. A token that may
  answer every request (web, terminal, console) may change anything; the Discord bot may lower a conversation's level or keep it,
  never raise it, and cannot change the ladder, the defaults, the thresholds or the level of a project, a schedule or a hook;
  a script token may change nothing. The model has no tool for it, and the Automations tool has no level field.
- **The turn after an approval** keeps the level of its conversation: in a chat, the default of a chat, not the default of
  runs nobody watches. It still waits for no answer, and the raises still hold.
- **Audit log:** each call a level lets through is a `ran` line whose detail names the level (« level 3 (Files and vault) »), and
  each change of a level, a default or a tool's thresholds is a line with the outcome `level`.
- **Approve all next** is unchanged: it still answers an `ask` for a while, and a level that asks is an `ask` it can answer.

**Changes you will notice** (the new defaults follow the ladder, not the old table; change a tool or a whole class on the
Auto mode page to get the old behaviour back):

- Any URL is fetched without asking from level 2 (it asked in a chat and was refused unattended). A turn that read untrusted
  content can therefore put text in an address it fetches; only content that looks like a secret is raised.
- Overwriting and deleting workspace files and vault notes run without asking at level 3 (they asked).
- Mail drafts, labels, marking read and the Trash run from level 4 and ask at level 3; the first three ran in a chat before. A hook
  that drafts mail is held until it has level 4.
- Sending mail, GitHub and Drive changes, scripts with network, automations and the Discord tools are refused below level 4,
  ask at level 4 and run at level 5. With both defaults at 3, **a chat that sent mail or opened a pull request after a card now
  needs level 4 first.**
- Every schedule, reminder and hook runs at `auto_level_unattended` (3) until you give it, or its project, a level.

**Interface changes:**

- **New routes:** `GET` and `POST /v1/levels`, `PATCH` and `DELETE /v1/levels/{position}`, `PUT /v1/levels/defaults`,
  `PUT` and `DELETE /v1/levels/tools/{tool}` (with `class`), `GET` and `PUT /v1/conversations/{id}/level`, and `PUT`
  `/v1/projects/{id}/level`, `/v1/schedules/{id}/level`, `/v1/triggers/{id}/level`. `POST /v1/chat` and
  `POST /v1/conversations` take `auto_level`.
- **Changed:** the `defaults` of `GET /v1/rules` list each class with `ask_from` and `approve_from` (they had `interactive`
  and `unattended`) and the response carries the ladder; the rule tester (`POST /v1/rules/test`) takes a `level`.
- **Migrations:** 0021 (the ladder in `auto_levels`, the thresholds in `tool_levels`, an `auto_level` column on conversations,
  projects, schedules and hooks) and 0022 (thresholds kept per action class). The server takes its backup before running them.

**Docs:** [TODO.md](TODO.md) (planned work), a new [code review](CODE_REVIEW.md) and an [audit](docs/audits/audit-2026-10-21.md)
of `src/`, the decisions of the levels in [DECISIONS.md](docs/DECISIONS.md), and the threat model and the data map brought up to date.

## v1.0.0 — 28/10/2026

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
  which left its scripts pointing at the old folder, so the preflight failed with "No such file or directory". It is built relocatable now. The
  same kind of rehearsal on a real Ubuntu machine, under systemd, found more: the watchdog was not fed after two minutes (the 10-minute and
  hourly loops counted as stalled), so systemd killed the server about 4.5 minutes after every start; a server that exited for an update told
  systemd the unit was stopping, which left it "deactivating" and killed after 180 s (a server run by the launcher no longer says it); the unit's
  `ProtectHome`, `ProtectKernelLogs` and `PrivateTmp` broke rootless Podman (no image pull, then no sandbox after a boot or a restart), so they are
  out and `InaccessiblePaths=/home /root` closes the same doors; and
  `scripts/build-release.sh`, `scripts/build-web.sh` and `deploy/install.sh` were committed without the executable bit, so CI and the release
  workflow failed with "Permission denied". `deploy/install.sh` also stopped on a fresh Ubuntu 24.04 (no `/etc/sysusers.d`), failed when the
  release was unpacked in a home directory (the `clara` user cannot enter it), and ran its final `clara doctor` without the secret key. The documented sandbox image step did not give scripts the image name Clara looks for
  (it tags it now); a rolled-back update left the failed release's image set (the old one is put back); the success of an update was only reported
  at the next restart (now within a minute); GitHub's rate limit is named as such (it used to read "HTTP 403"); and `-rc` tags are published as
  prereleases. The sandbox failed at the first real script, on any machine (a CI run and a local reproduction): Podman copied the workspace's
  `.git` into the 64 KiB tmpfs that is meant to hide it, a fresh repository is more than that ("No space left on device"), and the readiness
  check, which mounts no tmpfs, said "ready". The tmpfs is created empty now (`notmpcopyup`), which is what the documentation always said. A second rehearsal, on a
  one-core machine, found that the first script after a new image was installed ran for minutes and was killed by its own timeout, again
  and again: with `--userns=keep-id` this Podman copies the whole image the first time it starts (196 s for 684 MB, then 2 s). The readiness check
  now starts the image with the options of a real run, over a throw-away workspace with a `.git` bigger than the tmpfs, and is given the time
  (scripts are off until it is done); an update does the same with the new image before it restarts, and removes the sandbox images older
  than the running one and the one a rollback returns to (three updates had left 3 GB). Podman is started from `/`, so a `clara doctor` run
  from a home directory `clara` cannot enter no longer fails with "cannot chdir"; the documented image step and the installer's closing text
  say `cd /`; `clara doctor --pull` says what to do for a `localhost/…` image name instead of trying to pull it from a registry that is not there;
  a hand-run `clara doctor` points to the `clara` alias when it lacks the secret key; and it checks the image the server runs (the one the
  last release pinned) and not only `CLARA_SANDBOX_IMAGE`, so it no longer says "PASS" in 4 s on an image whose first start takes 3 minutes.
- **New settings:** all of them (see FEATURES.md, section 14). New here: `CLARA_MAIL_POLL_SECONDS`, `CLARA_GITHUB_POLL_SECONDS`; the runtime settings `discord_routes`, `mail_notify` and `mail_notify_senders`; the secret
  `gmail_refresh_token`; the alert `gmail`.
- **Migrations:** schema version 20 (0020: the folder grants and allowances; 0019: a conversation no longer has a parent, the sub agents are gone; 0018: the special tools a project's new conversations start with; 0017: the progress of a QCM; 0016: the Mail pack; 0015: automations that deliver to Discord are shown in the history; 0014: the Brave Search connection; 0013: the Discord mirror and archived conversations; 0012: where an automation's result goes; 0011: where the GitHub poll had reached, `github_cursors`; 0010: a hook's own history group, the project made for each hook goes when it was empty; 0009: hooks, the tables `trigger_events`, `triggers` and `trigger_runs`; 0008: Gmail as a connection, which rebuilds `connections` and keeps every repository and Drive file you added, and where the new-mail notifications stopped; 0002: a model you showed or hid yourself is told from one you never decided about; 0003: the daily recap's days; 0004: tool packs, the new fields of schedules and their runs, the files Clara made on Drive; the recap's table is kept for one start so its days become runs; 0005: which projects and schedules list their conversations in the web history, all off at first; 0007: `attempts` of a schedule, 3 by default).
