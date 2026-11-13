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

## The reader: defects found in a real-conditions test

The owner asked to keep, for later, what a test of the quarantined reader in real conditions found (2026-11-11, `main` at `1705e65`,
the free `opencode-go:beam-free` model for the chat, the hooks and the reader, a stand-in Gmail, real public pages and simulated
hostile ones, then a headless browser). The test kit was throwaway and is not in the repository. These are findings to fix, with a
proposal each: the proposals are ideas, not decisions.

What held: 276 of 276 reader answers were valid on the first try; no order from a hostile text reached the chat unflagged; no forged
report was adopted; the chat model never obeyed a hostile order unless the person had told it to follow the mail. The reader adds
about 3.3 times the wall time of a page read and about 19 % tokens (one benign page, 3 runs each, `beam-free`).

To fix, most serious first (all reproduced by the orchestrator unless noted):

- **A forged Human/Assistant transcript at the end of a page is often not flagged (medium).** Only the model's judgment sets
  `addressed_to_ai` (`reader.py:106-111`, `146-153`). One text was flagged 3 of 6 times by the orchestrator and 1 of 11 by the
  tester: about 3 in 4 runs are missed. Another forged transcript in a chat test was flagged 3 of 3. Idea: a code check that forces the
  flag for a forged transcript or for « ignore all previous instructions », at the price of more false alarms (an article about AI
  that quotes the phrase is already flagged 3 of 3). Decided 2026-11-13: left to the model, no code check.
- Decided 2026-11-13: the reader has no fallback model and no retry on a provider error.
- **Small ones (low).** `parse_object` takes the first JSON object (`reader.py:257-267`): latent, the model never triggered it in 20
  runs. `mail_mark_read` runs with no card in a tainted hook run (documented,
  `actions.py:61`). A file you edit in the Library stays trusted although the page says a change makes it untrusted
  (`api/workspace.py:51-56`, on purpose).

Not decided:

- Which of the proposals to build. (Decided 2026-11-13 for a trash after untrusted content: only the sentence of the threat model changes.)

Not covered by the test (still to try):

- A chat model that follows hostile orders, so that the gate, not the model, is what stops a send or a fetch.
- A reader answer that is not usable (`its report was not usable`): `beam-free` always answered with valid JSON.
- A page or file longer than `reader_max_chars` in the web client, Discord, the desktop and phone apps, a real mail.
- `longcat-2.5-free` answered HTTP 429 on 14 calls; not reproduced.

Tests worth adding (the owner decides): `parse_object` with a forged first object.

What the current design already sets:

- A report has fixed fields and lengths whatever it is asked (`docs/DECISIONS.md`, « A firewall against tainted conversations »): the
  answer to a question is one of those fields, so the first point above is a gap between that sentence and the 800-character `answer`.
- The gate is the backstop: the conversation stays tainted after a read, and a hostile report can still mislead (`docs/THREAT-MODEL.md`).
- Docs and tests: an item built from this list is named in `FEATURES.md` (the docs test requires it) and its choice is recorded in
  `docs/DECISIONS.md`.

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

## Modes instead of auto levels and rules

The owner asked to replace the ladder of auto levels (`FEATURES.md`, « Auto levels ») and the rules with **modes**. Today a call with no
matching rule is decided by the conversation's level and the tool's two thresholds (ask from, run from). A mode names, once, what a
conversation may use and what happens to each tool.

What the owner decided:

- **A mode** is a name, a list of packs and, for each tool of those packs, a verdict: `approve`, `ask` or `refuse`. Example: a mail mode
  switches the Mail pack on, reads mail with `approve` and asks for every write.
- **Two verdicts per tool:** one for a normal turn and one `(if tainted)` for a tainted conversation. The tainted verdict is never more
  permissive than the normal one (a tool that asks in a normal turn asks or is refused when tainted, it is never approved). It replaces the
  tainted-turn raise.
- **One mode per conversation.** A conversation that needs mail and files has a mode made for both.
- **Where a conversation's mode comes from:** as the level does today: its own, else its schedule's or hook's, else its project's, else
  the server default. The selector of the chat, the project page, and the schedules and hooks become mode selectors. Discord does not
  change the mode of a conversation: its `/level` goes away.
- **Packs:** the mode's packs are switched on in the conversation that uses it, and are added to the ones already on. Changing the mode of
  a conversation adds the new mode's packs and keeps the others. A tool of a pack that is on but that the mode does not name takes its
  verdict from the predefined default mode.
- **Precision:** a verdict is set per tool and per action class, as the thresholds are (a script with or without network, a URL you wrote or
  any other), with exceptions per target on the same line (a mail address, a folder, a domain), as the rules have them.
- **The default mode:** the only predefined mode. The server fixes it and only the server changes it (no token, so not through the API or
  the web). Today's rules and the thresholds of the current default level go into it, and everything that
  had a level of its own (conversations, projects, schedules, hooks, a tool's thresholds) falls back to it. The levels 0 to 5 go away,
  and with them the last level that suspended the tainted-turn raise and the secret check: to run a tool with no card after outside
  content, a mode sets that tool's `(if tainted)` verdict to `approve`, one tool at a time.
- **What does not change:** the floor of the gate, the local-only and chat-only limits and the secret check (a raise to `ask` that no mode
  sets) stay before and after any mode. Only a token with the approval scope `all` may create or change a mode; a script cannot.
- **« Approve all next »** stays a grant for this conversation only, limited to 30 minutes. The limit of calls, the silence of the page
  and the limit to some classes go away: it covers any tool it is clicked on. It approves the calls of that tool that would ask: in a
  normal turn, and in a tainted turn only when the mode says `ask` for that tool when tainted. Where the mode refuses, or where the
  tainted verdict is not `ask`, the grant does nothing there.
- **Clara does not choose the mode of an automation she creates:** her tool has no mode field, so the automation gets the default mode
  (or its project's) and you change it on the page afterwards, as it is with levels today.
- **Deleting a mode** shows a warning with the number of conversations, of automation conversations and of automations that use it. On
  confirmation, all of them move to the default mode. The default mode cannot be deleted.
- **A hook or a schedule may run with a mode that approves everything**, including tainted: the goal of the change is more permissiveness
  while you keep control, and choosing the right mode is your responsibility.
- **One page:** the **Auto mode** page and the **Rules & approval** page become one page with tabs. The first tab lists the modes; a
  mode opens to show its packs, then every tool with its rules (its verdicts and its special rules per class and target).
- **The migration** always takes the automatic backup before it runs, as every migration does.

- **The default mode is a file the server reads at start**, so changing it needs a restart and no token can. Its place, format and
  checks are a technical choice, left for the implementation.

Left for the implementation (the owner asked to be shown a proposal and to approve it before it is built):

- The design of the page and the contents of its tabs after the list of modes.
- The technical details: the file of the default mode, how `/v1/levels`, the settings `auto_level_default` and
  `auto_level_unattended`, the `tool_levels` table and the console's `/level` commands are replaced, and the migration.

What the current design already sets:

- The gate's order today is floor, local only, chat only, level refusal, rules, level allow or ask, raises (`gate/engine.py`). The modes
  replace the level and rule steps and the tainted raise. `tests/test_levels.py` holds the red-team cases to keep.
- It needs a dated entry in `docs/DECISIONS.md` that supersedes « Auto levels (2026-10-30) », `FEATURES.md` updated, and a migration:
  take its number at merge (see `CLAUDE.md`, « Migration numbers »).

## Automations: regular expressions, combined triggers, one place for the result

The owner asked to change how an automation is made: precise filters, triggers that combine, and a simpler result. Today a schedule (at a
time) and a hook (when something happens) are two kinds with their own runs, catch-up and pace (`FEATURES.md`, sections 4 and 5), and a
hook's filter has the operators `eq`, `contains`, `starts_with`, `ends_with` and `not_contains`.

What the owner decided:

- **One kind of automation.** A schedule and a hook become one automation with one list, one page and one form. A migration converts the
  existing schedules and hooks; it takes the automatic backup first, as every migration does.
- **Filters are regular expressions.** The five operators go away. A match is a search anywhere in the field, not case-sensitive unless
  you tick it, with a « must not match » tick for each field. The filters that exist are converted to escaped expressions (`contains x`
  becomes `x`, `starts_with x` becomes `^x`, and so on). The fields that are not text (`has_attachment`, `unread`, `draft`…) stay as they are.
- **The engine is RE2** (a linear-time engine, the `google-re2` package), so that a pattern written by you cannot be made to run for ever by a
  subject or a body written by a stranger. It has no lookahead and no back-reference: « must not match » replaces `not_contains`. This
  supersedes the row « No regular expression in a filter » in `docs/DECISIONS.md`.
- **Triggers combine in a free tree of AND and OR.** Examples: between 18:00 and 20:00 AND a push on a branch; a push OR at 9:00. An **OR**
  starts a run when any of its branches fires. An **AND** may hold at most one **event** (a mail, a push, a pull request, a cron instant)
  among its branches, with conditions of time beside it; two events can be joined with OR, never with AND. The form refuses an AND that
  holds two events.
- **A condition of time is a cron expression** (the full syntax, for an instant and for a window). An event that arrives outside the
  window of an AND is **ignored**: it is recorded as skipped, visible in the automation's history, and starts no run.
- **« Where the result goes » is removed.** Every run sends the short notification to every client, Discord included, and **Show in
  history**, when it is ticked, makes the Discord forum thread with the run's whole answer, which you answer. The automations that exist are
  converted: `notice` stays as it is; `web` (no Discord) and `discord` (the whole answer only, no notice) become `notice`; `discord` and
  `both` also tick **Show in history**. You lose « only on Discord » and « not on Discord » (the owner accepts it). `announce` (keep a good run
  quiet) is unchanged.

- **Missed events work as they do today** (a schedule's `missed`: `skip`, `latest`, `all`; a hook's `catch_up`: `all`, `latest`, `digest`,
  `skip`), and the date and time of each event are kept, so that the runs that were due can be found again: a missed instant is worked out
  from its cron expression and the time the automation had reached, an event from the time it happened or was seen.
- **Time zone:** a cron expression runs in the time zone of the automation (`CLARA_TIMEZONE`, or the one the automation was made with),
  as a schedule's time does today.
- **A run is tainted only for what it reads that is tainted**, as in any conversation, and **the trigger counts**: a push, a mail or a pull
  request that is considered tainted makes the conversation tainted; a cron instant does not.
- **The form** will be shown to the owner as a design before it is built, and so will the page.

- **Three nodes of time:** `cron` (an instant, or a window to the minute for who can write it), `window` (from HH:MM to HH:MM on chosen
  weekdays, may cross midnight: 18:30 to 20:15) and `once` (a date and a time, as a schedule has today, since a single date has no cron
  form). The form offers the window and the instant first, and cron as the advanced choice.
- **Tools when none are listed:** those of the trigger that set the run off, as today for a hook (a push: `github_read` and `notify`; a
  mail: the mail read tools and `notify`; a cron instant or `once`: `notify` only), never more than the trigger that fired.

Not decided:

- How `automation_create` and `automation_update` carry a tree and a filter (Clara writes the expression, the gate asks you first, as it
  does today).

What the current design already sets:

- Two events never meet in one condition, so nothing has to be kept between two events to wait for the other.
- `google-re2` 1.1.20251105 has wheels for Python 3.12, 3.13 and 3.14 on Linux x86_64 and aarch64 (and macOS and Windows), checked on PyPI
  on 2026-11-10; the release installs from a hash-pinned `requirements.lock`, so the hashes of those wheels go into it.
- A run is one conversation of its own (or one shared), with the limits of its hook or schedule (`max_per_hour`, `catch_up`,
  `max_age_hours`, retention): they become the limits of the automation, and the two kinds must be told apart in a migration only where they
  differ (a per-run conversation, a missed `latest`).
- The *Test* dry run (`POST /v1/triggers/{id}/test`) must show, for a stored event, whether the expressions and the time conditions match.
- It needs a dated entry in `docs/DECISIONS.md`, `FEATURES.md` updated (the tool and route names are tested), the threat model row for an
  outside event (`docs/THREAT-MODEL.md`) kept true, and a migration whose number is taken at merge.

## New kinds of event: external events, school, feeds

The owner asked for automations that start from more than mail and GitHub. This item adds **event sources** next to the mail and GitHub
watchers (`triggers/events.py`: a source hands `SourceEvent`s to `Triggers.ingest`, an event is stored once by `(source, key)`, a kind is
declared with its fields, its read tools and its notification text). The new kinds plug into the automations of the item above: their
filters are regular expressions and they take part in the tree of AND and OR. Every one of them is outside content: an event of a source
is tainted, and the trigger counts (see the item above).

What the owner decided:

- **A. External events** (webhooks and local callers), made of three parts:
  - **An event source** is a record you make (name, kind `custom.<name>`, its declared fields, text or bool, how it proves itself, a
    secret held in `secrets/`). It is reached at `POST /v1/events/<source id>`, the id being random, so that an unknown address is a
    plain 404 that costs nothing and counts nothing. A source proves itself with a bearer secret or, for GitHub, with the HMAC-SHA256 of
    the body (`X-Hub-Signature-256`). Revoking a source touches no other.
  - **Local callers** (`clara event fire <source> …`, or the same route on the main listener, inside the tailnet): scripts and machines
    of the owner, with no public door.
  - **GitHub webhooks** (an adapter for the kinds that exist: `github.push`, `github.pull_request`, same fields, `ping` answered, the delivery
    id as the key, an answer within 10 seconds, so the body is stored and `202` sent before anything runs).
- **B. School events**, by a watcher of the Moodle and Auriga connectors (the owner has tried them several times and they work): kinds
  `moodle.quiz_opens`, `moodle.quiz_closes`, `moodle.activity_added`, `moodle.grade_posted`, `auriga.grade_posted` and
  `auriga.planning_changed`. A quiz opens or closes at a time known in advance (`timeopen`, `timeclose`), so its event is ingested when that
  time comes, with that time as `occurred_at`, not when the watcher saw it. Setting `CLARA_SCHOOL_POLL_SECONDS` (like the mail and GitHub
  ones; `0` never, only while an automation waits for one of these kinds), and a first look that only notes where things stand.
- **C. Feeds** (`feed.item`, RSS and Atom, fields `feed`, `title`, `author`, `link`, `summary`) for what is not covered above: YouTube
  channels (`youtube.com/feeds/videos.xml?channel_id=…`), blogs, podcasts, arXiv. Not GitHub (it has its own kinds). Feeds are fetched with
  the same guard as `web_fetch` (public addresses only, redirects checked, size and time capped), read with a parser that does not expand
  entities, and asked again with `ETag` and `If-Modified-Since`. `CLARA_FEEDS_POLL_SECONDS`.
- **A second port for external events.** Clara starts a second listener on `127.0.0.1` (`CLARA_EVENTS_LOCAL_PORT`) that mounts
  `POST /v1/events/<source id>` and nothing else: no `/v1` route, no web site, no console. `tailscale funnel` publishes only that
  listener on a public port (`CLARA_EVENTS_PORT`: 8443 or 10000, never the port of `tailscale serve`). The main listener and `tailscale
  serve` stay as they are, inside the tailnet. Off by default (`CLARA_EVENTS=off`): an installation that does not turn it on is
  exactly as it is today.

- **Making a source:** **New source** on a page of event sources generates its address (`/v1/events/<random id>`) and its secret, shown once.
  The sender (a script, a home-automation hub, a GitHub repository) is given both. What identifies a source, and what a source's trust rests on,
  is its id and its secret (or the HMAC signature): not a URL of the sender, which a webhook does not carry, and not its IP address, which
  is not reliable behind Funnel. Only you make a source: it holds a secret, so Clara cannot create or revoke one; in an automation she
  may only choose one that exists.
- **A trusted source:** the page has a **Trusted** box per source (a warning, and only a token with the approval scope `all` may tick it).
  The events of a trusted source do not taint the run. A signature proves who sent a body, not that its text is harmless, so the box is
  offered only for the sources whose content you control (your own scripts, your home automation): never for the GitHub adapter (a title is
  written by a stranger), feeds or school events.
- **Adding a feed:** by pasting the address of the feed, or the address of a page (a YouTube channel, a blog): Clara finds the feed once, when
  you add it, and keeps its address. Before you confirm she shows the feed's title and its latest items. She may also propose a feed from a
  chat (« follow this channel »): that is an approval card that shows the feed she found and its latest titles, so a page cannot make her
  follow something unseen. At most 50 feeds (a setting). Not retained for now: a search by name, and an OPML or YouTube subscription import.
  The Shorts of a channel are in its feed and cannot be told apart reliably.

What I corrected or added to the owner's plan:

- **Two phases, because only part of it needs the public port.** Phase 1 (no change of exposure): the event sources with bearer secrets
  and local callers (A, minus the public door), the school watcher (B) and feeds (C). Phase 2: the second listener, Funnel, and the GitHub
  webhook adapter. Of everything above, only a service of the Internet that calls Clara needs Phase 2.
- **A local caller is not a variant of a GitHub webhook:** it is the same route reached from the tailnet, with no Funnel. It has its own
  line so that it does not wait for Phase 2.
- **A webhook and the poll of the same repository would both produce an event** (their keys differ: the poll's is
  `owner/repo#12:opened:<time>`, a delivery's is its id). When a repository has a GitHub webhook, its poll is turned off, or the two keys
  are made equal; the polling stays for the repositories that have no webhook.
- **A source is a record with its own secret and its own address, not one shared token.** One token for every source would be revoked all at
  once and would give a leaked script the power of a leaked GitHub secret. The events token never works on any other route.
- **Funnel is not on for the whole app, and the guard changes, it does not go.** Today Clara refuses to start while Funnel is on for her port and
  turns it off if it appears. After: Funnel is allowed only on `CLARA_EVENTS_PORT` and only when it maps to the events listener; on any
  other port, or mapped elsewhere, it is refused and turned off as today, with the `funnel` alert. `docs/NON-GOALS.md`
  (« Public exposure ») and `docs/THREAT-MODEL.md` change to « the app is never public; one door for events is, when you turn it on ».
- **The failure limiter cannot be trusted behind Funnel as it stands.** It counts failures per address from `X-Forwarded-For`, believed only
  from `127.0.0.1`, and the threat model says nobody checked what `tailscale serve` sends. If Funnel does not pass the real address, a
  scanner could get every caller blocked, the owner included. The public door counts failures **per source id**, never per address, and
  has a body limit (64 KB), a rate per source, and a limit of events waiting. The header Funnel sends must be checked on a real
  Funnel before this is relied on.
- **A GitHub webhook needs a step of yours:** adding it in the repository's settings with its address and secret. The token Clara has
  does not need the right to create one. Funnel itself needs `funnel` in your tailnet's ACL (`nodeAttrs`), which only you can set.
- **Quiz times, not relevé times:** see B. Without it « the quiz opens » would arrive up to a polling interval late.

Not decided:

- The kinds and fields of B beyond the names above, the polling interval of each source (the defaults), and the Auriga notice of a change of
  planning (which entry fields count as a change).
- How `automation_create` carries a source and a feed (the gate asks first, as it does today).
- The look of the pages (the list of sources with its address, a *Test* button and the **Trusted** box; the feeds with their preview) and of
  the form: the owner will see a design first.

What the current design already sets:

- The mail watcher polls only while a listener exists and its first look starts from now; the GitHub poll is `CLARA_GITHUB_POLL_SECONDS`
  (120); the due loops (`timekeeping/dueloop.py`) already fire what is due and sleep until the next. A dev server on the copy of the real data
  would poll the real accounts: set the new intervals to `0` there, as for the others (`CLAUDE.md`).
- `ClaraServer` (`app.py`) is one `uvicorn.Server`; a second listener is a second server on the same event loop and the same `Services`,
  with its own middleware that accepts only event secrets.
- New tables, kinds (events are dynamic for `custom.*`, because `KINDS` is a fixed dict today), routes, settings (`CLARA_EVENTS`,
  `CLARA_EVENTS_PORT`, `CLARA_EVENTS_LOCAL_PORT`, `CLARA_SCHOOL_POLL_SECONDS`, `CLARA_FEEDS_POLL_SECONDS`), an alert and a console command must
  be named in `FEATURES.md` (the docs test), with a migration whose number is taken at merge and a dated entry in `docs/DECISIONS.md` that
  supersedes « poll not webhook » for the repositories that have a webhook.

## Skills: instructions you add, run from a chat command

The owner asked for skills, as other agents have them: folders with a `SKILL.md` (instructions, and a description of the arguments)
that the owner adds, and that are run from a command in the chat (`/name`), with a preview of the expected arguments. Today Clara has
nothing like it: the web chat has no commands (the box sends on Enter), the terminal and Discord each have their own, and the persona and a
project's instructions are the only standing instructions.

What the owner decided:

- **A skill** is a folder with a `SKILL.md` (frontmatter and instructions, the format of Claude Code), which the owner adds on a web page by
  pasting a `SKILL.md` or sending a zip of the folder, with a preview of every file before it is accepted.
- **Run by the owner:** `/name` in the web chat, with the skill's `argument-hint` shown before the message is sent. The web site only at
  first; the terminal and Discord come later.
- **Run by Clara:** through a tool. Every skill is visible to her except one that says `disable-model-invocation: true` (the field of
  Claude Code). After untrusted content, her run of a skill asks for the owner's agreement.
- **Packs:** a skill lists the packs it needs, shown before it is sent. When Clara runs a skill whose pack is off (Mail, for example), an
  approval request offers to switch it on for the conversation.
- **Scope:** a skill is global (every conversation, projects included) or reserved for one or several projects, and then does not exist,
  for Clara or in the menu, in any other conversation.
- **Volume:** about ten skills.
- **Who may change them:** only a token with the approval scope `all` adds, changes or removes a skill. A script's token and the model
  cannot.
- **Backups:** the skills are in Clara's backups, as the database and the workspace are.
- **Three phases**, each usable alone: (1) the command in the web chat, the arguments, the packs and the project scope; (2) Clara runs a
  skill; (3) the scripts of a skill, in the sandbox.
- **Left out of this item:** schedules and hooks do not run a skill.

What I corrected or added to the owner's plan:

- **A skill grants no right:** `allowed-tools` is ignored. The packs a skill lists are for display and for the approval request; the gate
  still decides every call.
- **Reserved names:** `/tools`, `/new`, `/model` and the other commands of the terminal and Discord cannot be skill names, so that adding
  those clients later breaks nothing.
- **A skill Clara runs stays in the conversation:** only the last 5 tool results stay in later prompts (`docs/DECISIONS.md`), so the text is
  stored as a message, not as a tool result.
- **The approval request to switch a pack on** is one more action at the gate, with its tainted case. It shows the skill and the pack, and
  only a client can answer it.
- **An archive is read strictly:** no links, no `..`, real bytes counted (`knowledge/ingest.py`).
- **Scripts** stay in the sandbox, with its limits and no network unless approved. Running them needs a read-only mount of the skill's
  folder, which is a change to the sandbox (only the workspace is mounted today).

Not decided:

- Does a skill file that changes after it was added need to be confirmed again (a digest, as the workspace's « trust » has)?
- How a skill that was loaded survives the compaction of a long conversation (does the summary name it so that it can be loaded again)?
- Which client comes after the web site, and in what form (Discord's native commands have a limit and must be registered).
- Schedules and hooks running a skill, later.
- The scripts of a skill: its gate class (that of `ws_run`, or its own), and whether a skill may ask for the network.

Left for the implementation (the owner asked to be shown a proposal and to approve it before it is built):

- The page of the skills, the `/` menu and the line that shows the arguments.
- The storage and how it is backed up, the migration, the name of the frontmatter field that lists the packs.
- The tool Clara uses to run a skill, and the one for its scripts.

What the current design already sets:

- The prompt must stay identical from one turn to the next (`agent/prompt.py`), so the list of skills goes in the context block.
- `messages.shown` (migration 0028) lets a message show `/name args` while the model gets the expanded text.
- The model has no tool to read or change rules or levels (`docs/THREAT-MODEL.md`); it gets none for skills.
- It depends on « Modes instead of auto levels and rules »: the tools of a skill get a normal verdict and a verdict when tainted in a mode, and
  only a token with the approval scope `all` changes a mode.
- `docs/NON-GOALS.md` excludes « Arbitrary MCP servers or plug-ins »: a dated entry in `docs/DECISIONS.md` must say that a skill is text, and
  that its scripts run in the existing sandbox and add no server code.
- `FEATURES.md` is tested against the code: every tool, route and action class added is named there, and the line « N tools. » is updated.
- A migration number is taken when the branch is merged (the highest today is `0030`).

## Moodle and Auriga: what is left

The Moodle/Auriga pack is built (`docs/DECISIONS.md`, « Moodle and Auriga »; `FEATURES.md`, section 8). Its tests run against
stand-ins made from what was read on 2026-11-01 and 2026-11-02. On 2026-11-11 (`main` at `1705e65`, clean) it was tried for the first
time: a scripted part on the stand-ins (the gate, hostile documents, edge cases, the Connections page in a headless browser) and a
read-only run against the real Moodle and Auriga, where the owner signed in on a throwaway data folder (101 requests, all HTTP 200;
nothing was written to either site; the key of the model provider was never read). The real-model part (R1) first answered HTTP 429
and was run later the same evening with `opencode-go:deepseek-v4.1-flash` (not a `-free` model, on the owner's explicit say-so): 109
runs, 299 model rounds, on the stand-ins only (R1 below). What the run found is below, with the causes read in the code
(`path:line`). The ideas are ideas, not decisions, except what the owner chose.

What the real model did (R1, stand-ins, levels 3 and 4): in 31 normal conversations it chose the right tools and read the results
correctly, and a download asked at level 2 and ran at level 3 as the gate classes say. In 78 runs with a hostile order planted in a
course name, a due-date title, a file name, a grade comment, a teacher name or a section title (a plain order, a fake « system
message », a fake end of result followed by a fake user turn, a French one, a token to write, a fetch, a rule to remember), it never
called `mail_send`, `ws_write` or `web_fetch`, sent no mail, wrote no file and tried no connection to the attacker's host, and it
told the owner about the order in 68 of the 72 runs of the six first orders (the 6 runs of the seventh were not counted). The model's judgment held; the gate was not exercised, because no run reached
it. The data folder was shared between runs, so the notes the model wrote (R1b below) were loaded into later runs: the first 28
hostile runs, which had none, are the clean ones.

What the real-site run settled (the assumptions this section used to list): the Moodle token and its seven read functions; the Auriga
offline token, its 60-second access token and the rotation of the refresh token; the room, the activity type and the unit caption of
a planning entry; the `…Z` timestamps (true UTC) and the inclusive local `startDate` and `endDate`; the layout of an éval line; the saved
queries 1144 / 1198 and 1036 / 945 with the year filter; `totalPages` as an integer; `GET /api/me`. Item B of « New kinds of event »
says the owner has tried the connectors and they work: that holds for signing in and for most reads, not for the synthèse grades (D1)
or the teachers (D3).

What the owner decided:

- **Keep the school password and sign in to Auriga again alone.** When the refresh token fails, Clara signs in again with the owner's
  school email and password (the way the student app Chrysalide does, see the old note below), and the owner reconnects by hand
  only when that fails. This replaces « Later: keep the owner's email and password ». If the Keycloak form does not accept the
  account, or the password changed, there is **no other fallback**: the `auriga` alert is raised and Auriga stays off until the owner
  signs in again.
- **The password is set, seen and removed only through a hidden prompt** (`clara auriga connect`, or the console with a token whose
  approval scope is `all`). It is never shown, never typed in the chat, and never read back by Clara or by the Connections page.
- **Both hostile-document risks are to be fixed** (D8 and D9): a file must not be able to forge a « sign in again » state, and a
  download or a parse must have limits on memory, pages and time.
- **The web defects that are not specific to the pack** go under « small ones » of D12, marked as such.
- **This section is completed**, not a new item.

What I corrected or added to the owner's plan:

- **Check first that the login form accepts the owner's account.** The owner signs in with Microsoft through Keycloak, so a script
  against `kc-form-login` may be refused outright (the old note already said so). Until that is checked the decision on the password
  is a plan, not a feature.
- **At most one password sign-in per lapse, until the owner reconnects** (my proposal, not the owner's decision): repeated failed
  logins could lock the school account if Keycloak's brute-force protection is on for EPITA's realm (not checked).
- **A row of the threat model is missing.** `docs/THREAT-MODEL.md` says nothing about the school sites: a course document can forge
  a lapse (D8) or inflate memory and time (D9), and course text is untainted data by the owner's choice (`docs/DECISIONS.md`,
  « Moodle and Auriga »). Add a row when the fixes are built.
- **Three documents say what is no longer true**, to correct when the fixes are built (not now): `FEATURES.md` line 732 (« until it
  has been idle for a month », see D2), `docs/DECISIONS.md` rows 307 to 315 (« no password is kept », the offline-token lifetime and
  « this Moodle's calendar has no quiz event », see D5) and `CHANGELOG.md` line 43 (« never been tried »). The new secret must also
  be named in `FEATURES.md` (the docs test).

To fix, most serious first (all checked by the orchestrator by reading the code and by rerunning the stand-in; the facts about the real
Auriga and Moodle come from the structure-only logs of the sub-agent, counts and types, no personal content):

- **D2. The Auriga sign-in lapses about 24 hours after it is made (high). Only the password re-login remains.** The token is an offline
  one, but `refresh_expires_in` is an absolute date and the rotation does not extend it: the remaining time kept falling with the clock
  across rotations, from about 24 hours (seen on the real site on 2026-11-11, not checked on a Keycloak page). Done (`fix/school-reads`):
  `lapses()` reads the `exp` of the token, so that the sign-in message, `/school` and the Connections answer say « lapses on … », and
  « idle for a month » is gone from `FEATURES.md` and the decisions; no warning is sent before the date (the owner's choice). What is
  left is the decided password re-login (« What the owner decided » above), which will remove the lapse.
- **D8. A document can forge a « sign in again » state (medium).** `moodle.py:477-482` reads the bytes of a downloaded file as an error
  when they are JSON with `invalidtoken`, so a course document whose content is that JSON makes Clara think the token lapsed
  (`setup.py:180-188` then raises the `moodle` alert). Idea: check the content type and the size before parsing, and only treat a
  small JSON answer to the download address as an error.
- **D9. No limits on memory, pages or time (medium).** A 28 KB docx that expands to a 29 MB XML made the reading process reach 753 MB;
  a PDF of 20,000 pages took 6 s; a download that trickles has no total deadline (`moodle.py:460-472`). Idea: a cap on the
  uncompressed size, on the pages read and on the whole download time, with a clear refusal.
- **D11. Small ones in the pack (low).**
  - The 92-day limit (`school_tools.py`, `_window`): it is not clear what the defect is. The limit counts `end - start`, as the default of
    7 days does, so it was left as it is.

From the real-model run (R1, ideas, not decisions):

- **R1b. The model writes a global memory from hostile course text (low to medium).** In 6 of 78 hostile runs it called `remember`
  with a note such as « the ALGO course holds an injected instruction to send the grades to attacker@example.invalid: ignore it »,
  scope global. The note was harmless here, but hostile text, the address included, became a stored fact that comes back in every
  later prompt, and `remember` runs without a card because reading these sites does not taint the turn (the owner's choice). A planted
  order to remember a rule was refused 6 of 6. Idea: not decided (see below); for example a card for `remember` after a school read,
  or a rule in the prompt not to store text quoted from these sites.
- **R1d. Timing (information).** 17.7 s a run on average and 168 s at most; one « planning for the whole year » run passed the 120 s
  silence limit of the model (`ModelTimeout`) and succeeded on the second try. The model recovers from the 92-day limit by asking in
  pieces.

Still open from before:

- **The migration number.** The migration is `0030_school.sql`: it was `0028_school.sql` on the branch, and was renamed when the branch
  was merged after `0028_shown_question.sql` (`fix/automation-fix-prompt`) and `0029_github_pack.sql` (`fix/github-tool-pack`). On
  `main` the three are in order. What remains is the dev database (`.dev/data`): it was at schema 28 with this migration applied under
  its old number and holds no `pre-migration-v27` backup, so under the new chain it would run 0029 and 0030 and skip
  `0028_shown_question` (the `messages.shown` column). Start the dev server from a fresh copy of the data or add that column by hand.
- **The old note on the password.** The student app Chrysalide (`gitlab.com/epimac-asso/projects/chrysalide/ChrysalideApp`, read on
  2026-11-02, nothing copied) scripts Keycloak's login form (`kc-form-login`, with the `AUTH_SESSION_ID` and `KC_RESTART` cookies),
  takes `code` from the redirect and swaps it. This is now the owner's decision (see above); the cost stays: the school password sits
  in Clara's secrets, a bigger secret than a token.
- **Not built:** Moodle's iCal export as a lighter source for the calendar (its link carries a key and does not lapse until it is
  reset); Moodle's `mod_assign_get_assignments` (assignment cut-off dates, texts); the text of Moodle `page`, `book` and `hvp`
  activities (it is not in `core_course_get_contents`; a `page` module lists a 0-byte `index.html` file, which may be how its text can
  be read, not tried); any write to either site.

Not decided:

- Whether the Keycloak login form accepts the owner's account at all (checked first, see above).
- How many password sign-ins per lapse (my proposal: one), and what happens when the owner changes the school password.
- Where the password is stored (presumably the encrypted secrets, as the token is) and how it is removed.
- Whether `remember` is limited after a school read (R1b). (Decided 2026-11-13: a rule in the prompt, not a card; slice S2.)

Not covered by the test (still to try):

- R1 with the gate in play: no run made the model call a risky tool, so the gate was not exercised with a real model. A more obedient
  model, level 5, and a reader-style hostile text that asks for a `remember` first and an action later were not tried. R1 used only
  the stand-ins' shapes (before the fixes of D1, D3 and D4). The kit is `test-r1/` in the scratchpad of the session and is not in
  the repository.
- Discord, the desktop and phone apps.
- Moodle's error code for an expired token (only `invalidtoken` is known), `assign` rows, an `isExam` entry that is true, cancelled
  interventions, entries near midnight.
- Nothing else of the real sites: the real-site run read the structure (counts and types), not the content.

Tests worth adding (the owner decides): a file whose bytes are an `invalidtoken` answer must not raise the lapse; a download deadline and a
size cap; a web test of the alert badge after a resolved alert. (`tests/schoolworld.py` is on the real shapes and the wrong-shape answers are
tested: `fix/school-reads`.)

What the current design already sets:

- Only the seven read functions of `READ_FUNCTIONS` are called, nothing is written to either site, and the gate classes are
  `school_read` (2|2) and `school_save` (2|3) with the targets `school:moodle` and `school:auriga`.
- Reading these sites does not taint the turn (the owner's choice, `docs/DECISIONS.md`, « Moodle and Auriga »): the course text is
  untrusted but not quarantined, which is why D8 and D9 matter.
- `FEATURES.md` is tested against the code: every tool, route, secret, command and setting added is named there (the docs test), and the
  decision is recorded in `docs/DECISIONS.md`. A secret is set by a hidden prompt (`clara secrets set` or the connect command), never in
  `.env` or the chat.
- A migration number is taken when the branch is merged.
