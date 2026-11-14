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

## New kinds of event: what is left

The owner asked for automations that start from more than mail and GitHub (event sources, the school watcher, feeds, a second port for
external events and GitHub's webhook). All of it is built on the server (`docs/DECISIONS.md`: « Event sources », « School events »,
« Feeds » and « The events door and the GitHub webhook »). What is left:

- **The live check on a real Funnel is the owner's** (nothing here ran against a real Funnel, a real GitHub or a TCP port: the tests serve the door with the real uvicorn on a UNIX socket): `funnel` in the
  `nodeAttrs` of the tailnet policy, `CLARA_EVENTS=on`, the webhook added in a repository, a `ping`, a push and a pull request,
  a `curl` from outside that gets a bare 404 for everything else, and a look at the header Funnel sends for the caller's address
  (`docs/OPERATIONS.md`, « The events door »). Until it is done the per-address behaviour of the limiter must not be relied on (the door
  uses none).
- **Not decided:** whether a bearer source should be public only when you say so (today every source is reachable on the door, as the route
  is one); a bigger body limit for GitHub's pushes alone (64 KB today: a big push is a 413 and is lost); a longer retention of a source's
  events than the 30 days every event has.

## Skills: what is left

Phases 1 and 2 of the skills are built (`docs/DECISIONS.md`, « Skills (owner's decision, 2026-11-13) », with the rows of 2026-11-14; `FEATURES.md`,
section 3 and the Skills page of the web site): a skill is a `SKILL.md` and the files of its folder, added by a token with the approval scope `all` from
a pasted text or a zip, after a preview; it is stored in the database (so it is in the backups), global or reserved for projects; `/name arguments` in a
chat is expanded by the server, `messages.shown` carries what was typed, and a summary names the skills that were run. The web site has the Skills page
(Settings), the `/` menu of the box you write in and, under the box, the argument hint and the packs a skill needs (a click switches one on). **Clara
runs a skill herself** with the tool `skill_run` (2026-11-14): in a chat only, with her list of skills in her context, the instructions kept in the
conversation as a message (`shown` « Clara ran /name arguments »), the gate classes `skill_run` (asks after untrusted content) and `skill_pack` (asks
to switch a pack on). A skill grants no right (`allowed-tools` is ignored). What the owner decided and is not built yet:

- **Phase 3, the scripts of a skill, in the sandbox.** They stay in the sandbox, with its limits and no network unless approved; running them needs a
  read-only mount of the skill's folder, which is a change to the sandbox (only the workspace is mounted today), and no server code: the files are
  already stored, as bytes. Their gate class (that of `ws_run`, or its own) and whether a skill may ask for the network are not decided. They need
  their own row in `docs/THREAT-MODEL.md`.
- **Which client after the web site:** both are built (2026-11-14). The terminal: `clara-chat` sends `/name arguments` as typed, `/skills` lists
  them, Tab completes them where `readline` exists. Discord: one native command `/skill` with `name` (autocomplete) and `arguments`, the server
  expanding (`docs/DECISIONS.md`, « Skills »).
- **Schedules and hooks running a skill:** later; the owner left them out of this item.
- **Answered by phase 2:** a mode can refuse or ask a skill Clara runs, by class (`skill_run`, `skill_pack`) or for one skill (a line with the target `skill:<name>`).

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
it. The data folder was shared between runs, so the notes the model wrote (R1b, now a rule of the prompt: `docs/DECISIONS.md`) were loaded into later runs: the first 28
hostile runs, which had none, are the clean ones.

What the real-site run settled (the assumptions this section used to list): the Moodle token and its seven read functions; the Auriga
offline token, its 60-second access token and the rotation of the refresh token; the room, the activity type and the unit caption of
a planning entry; the `…Z` timestamps (true UTC) and the inclusive local `startDate` and `endDate`; the layout of an éval line; the saved
queries 1144 / 1198 and 1036 / 945 with the year filter; `totalPages` as an integer; `GET /api/me`. When asking for school events, the
owner said they had tried the connectors and they work: that holds for signing in and for most reads, not for the synthèse grades (D1)
or the teachers (D3).

What the owner decided:

- **The web defects that are not specific to the pack** go under « small ones » of D12, marked as such.
- **This section is completed**, not a new item.

To fix, most serious first (all checked by the orchestrator by reading the code and by rerunning the stand-in; the facts about the real
Auriga and Moodle come from the structure-only logs of the sub-agent, counts and types, no personal content):

- **D11. Small ones in the pack (low).**
  - The 92-day limit (`school_tools.py`, `_window`): it is not clear what the defect is. The limit counts `end - start`, as the default of
    7 days does, so it was left as it is.

From the real-model run (R1, ideas, not decisions):

- **R1d. Timing (information).** 17.7 s a run on average and 168 s at most; one « planning for the whole year » run passed the 120 s
  silence limit of the model (`ModelTimeout`) and succeeded on the second try. The model recovers from the 92-day limit by asking in
  pieces.

Still open from before:

- **The migration number.** The migration is `0030_school.sql`: it was `0028_school.sql` on the branch, and was renamed when the branch
  was merged after `0028_shown_question.sql` (`fix/automation-fix-prompt`) and `0029_github_pack.sql` (`fix/github-tool-pack`). On
  `main` the three are in order. What remains is the dev database (`.dev/data`): it was at schema 28 with this migration applied under
  its old number and holds no `pre-migration-v27` backup, so under the new chain it would run 0029 and 0030 and skip
  `0028_shown_question` (the `messages.shown` column). Start the dev server from a fresh copy of the data or add that column by hand.
- **Not built:** Moodle's iCal export as a lighter source for the calendar (its link carries a key and does not lapse until it is
  reset); Moodle's `mod_assign_get_assignments` (assignment cut-off dates, texts); the text of Moodle `page`, `book` and `hvp`
  activities (it is not in `core_course_get_contents`; a `page` module lists a 0-byte `index.html` file, which may be how its text can
  be read, not tried); any write to either site.

Not covered by the test (still to try):

- R1 with the gate in play: no run made the model call a risky tool, so the gate was not exercised with a real model. A more obedient
  model, level 5, and a reader-style hostile text that asks for a `remember` first and an action later were not tried. R1 used only
  the stand-ins' shapes (before the fixes of D1, D3 and D4). The kit is `test-r1/` in the scratchpad of the session and is not in
  the repository.
- Discord, the desktop and phone apps.
- The prompt rule against storing text quoted from the school sites with `remember` (R1b): not tried with a real model. Before it, 6 of 78 hostile runs stored such a note.
- Moodle's error code for an expired token (only `invalidtoken` is known), `assign` rows, an `isExam` entry that is true, cancelled
  interventions, entries near midnight.
- Nothing else of the real sites: the real-site run read the structure (counts and types), not the content.

Tests worth adding (the owner decides): a web test of the alert badge after a resolved alert. (`tests/schoolworld.py` is on the real shapes
and the wrong-shape answers are tested: `fix/school-reads`.)

What the current design already sets:

- Only the seven read functions of `READ_FUNCTIONS` are called, nothing is written to either site, and the gate classes are
  `school_read` (2|2) and `school_save` (2|3) with the targets `school:moodle` and `school:auriga`.
- Reading these sites does not taint the turn (the owner's choice, `docs/DECISIONS.md`, « Moodle and Auriga »): the course text is
  untrusted but not quarantined, which is why a document's bytes and cost are bounded (`docs/DECISIONS.md`).
- `FEATURES.md` is tested against the code: every tool, route, secret, command and setting added is named there (the docs test), and the
  decision is recorded in `docs/DECISIONS.md`. A secret is set by a hidden prompt (`clara secrets set` or the connect command), never in
  `.env` or the chat.
- A migration number is taken when the branch is merged.
