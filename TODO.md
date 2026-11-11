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
- **The migration number.** The migration is `0030_school.sql`: it was `0028_school.sql` on the branch, and was renamed when the branch
  was merged after `0028_shown_question.sql` (`fix/automation-fix-prompt`) and `0029_github_pack.sql` (`fix/github-tool-pack`).
  Nothing in it depends on the others (it rebuilds `connections` and `resources`, as 0008 and 0014 did). A database that ran the
  old `0028_school` is at version 28 with this schema: restore it from its `pre-migration-v<N>-…db` backup before starting this code.
  The dev database (`.dev/data`) was at schema 28 with this migration applied under its old number, and holds no
  `pre-migration-v27` backup (the latest was v24 when this was written): under the new chain it would run 0029 and 0030 and skip
  `0028_shown_question` (the `messages.shown` column), so start the dev server from a fresh copy of the data or add that column by hand.
- **Later: keep the owner's email and password** and sign in to Auriga again with them whenever the refresh token fails, as the student app
  Chrysalide does (`gitlab.com/epimac-asso/projects/chrysalide/ChrysalideApp`, read on 2026-11-02, nothing copied): it scripts Keycloak's login
  form (`kc-form-login`, with the `AUTH_SESSION_ID` and `KC_RESTART` cookies), takes `code` from the redirect and swaps it. Nothing to
  reconnect until the password changes or Keycloak adds a check a script cannot pass. The cost: the school password sits in Clara's database,
  a bigger secret than a token, and it works only if the login form accepts the owner's account (not Microsoft). It needs its own decision.
- **Not built:** Moodle's iCal export as a lighter source for the calendar (its link carries a key and does not lapse until it is reset);
  Moodle's `mod_assign_get_assignments` (assignment cut-off dates, texts); the text of Moodle `page`, `book` and `hvp` activities (it is not in
  `core_course_get_contents`); any write to either site.
