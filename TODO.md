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

The three phases of the skills are built (`docs/DECISIONS.md`, « Skills (owner's decision, 2026-11-13) », with the rows of 2026-11-14; `FEATURES.md`,
section 3 and the Skills page of the web site): a skill is a `SKILL.md` and the files of its folder, added by a token with the approval scope `all` from
a pasted text or a zip, after a preview; it is stored in the database (so it is in the backups), global or reserved for projects; `/name arguments` in a
chat is expanded by the server, `messages.shown` carries what was typed, and a summary names the skills that were run. The web site has the Skills page
(Settings), the `/` menu of the box you write in and, under the box, the argument hint and the packs a skill needs (a click switches one on). **Clara
runs a skill herself** with the tool `skill_run` (2026-11-14): in a chat only, with her list of skills in her context, the instructions kept in the
conversation as a message (`shown` « Clara ran /name arguments »), the gate classes `skill_run` (asks after untrusted content) and `skill_pack` (asks
to switch a pack on). **Its scripts run in the sandbox** (phase 3, 2026-11-14): `ws_run` with the parameter `skill` mounts the skill's files read-only at
`/skill` for that run (the classes `run` and `run_network`, with the target `skill:<name>`; the network only through `ws_run`'s own card). A skill grants no
right (`allowed-tools` is ignored). The terminal and Discord clients run skills too (2026-11-14). Not built:

- **Schedules and hooks running a skill:** later; the owner left them out of this item.

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

## Voice dictation into Clara: send from the phone or the island, transcribed on the PC

The owner wants a voice command that needs as little phone use as possible: speak, and the words become a message in Clara. The
transcription runs on the PC that hosts Clara, from the audio sent by the client, for all clients (the Windows island, the Android
phone, the web site (a button only, see below), the terminal, Discord voice messages and scripts through the API). It is not a voice
answer: Clara still answers in text. The client-side parts (the trigger, which is the
Android default assistant if the phone allows it, else a persistent notification with a button; the island shortcut; the recording) live
in the separate `clara_coucou` repository; this item covers the Clara side.

What the owner decided:

- **Automatic sending** (`envoi automatique`): a setting per client (island, phone, web), off by default. When on, the transcribed
  text is sent as a message at once, even with the phone's screen locked. When off, the text waits in the box and the owner sends it
  (« relire et envoyer »). The owner takes the responsibility for the automatic case.
- **Transcription on the PC**, not the phone and not a third-party service. The audio is never kept: only the text is stored, as an
  ordinary message.
- **A dictation conversation per client.** Each client has one current dictation conversation. A « new dictation conversation » button
  creates a normal conversation (no project, no automation) and makes it the target of the dictations that follow, from that client. Any
  client can dictate into any conversation. The conversations are normal: they are synchronised across the clients.
- **One setting per client for the approval mode and the tool packs** of the dictation conversations. The new conversation takes them.
  The owner decides them in the application.
- **A dictation conversation is tainted**, as a conversation that read content from outside: its facts and its files are not trusted
  until the owner has checked them. Tainted means the owner's rule for a tainted turn applies to the whole conversation (see below).
- **No approval by voice.** A spoken phrase never approves a request of Clara.
- **The web site may use the microphone, on a button press only.** The site's microphone permission goes from none to Clara's own
  origin alone. The microphone never opens in the background: it opens when the owner presses the button, and stops with it.
- **Other clients can dictate too:** the terminal (`clara-chat`), Discord voice messages sent to the bot, and scripts that call the API.

What I corrected or added to the owner's plan:

- **« Approbation » is not the word for the client's confirmation.** The item uses « automatic sending » for the setting and « review and
  send » for the manual case. « Approval » stays the word for Clara's tool requests, as it is elsewhere.
- **A tainted conversation asks for every outside write.** The existing rule for a tainted turn raises the verdict of outside writes
  (mail, Drive, and others), so each such write asks the owner, on the phone if that is where the owner is. The automatic sending does
  not get round that rule: a dictation can put a message into the conversation without a question, but a mail sent from it still asks.
  This is the cost the owner accepted; it is recorded here so that it is seen when the item is built.
- **The existing marks are reused.** Facts written in a tainted turn are already kept and shown to the model with the tag
  `[unconfirmed]` until the owner confirms them. A file written after an untrusted read already carries the `tainted` mark. The new
  source is « conversation of a dictation », not a new mechanism.
- **Server transcription is a new audio route**, which is also a new surface: a decoder for audio sent by a client. It needs a size
  limit, a duration limit and a timeout, and the audio must be dropped once transcribed, whatever the outcome.
- **The current conversation of a client** follows a precedent: the Discord bot keeps its current conversation id in its state file
  (`docs/DATA-MAP.md`, line 25). The item decides whether it is a table or a field, not whether it exists.

- **The web microphone is a recorded guarantee that changes.** `src/clara/api/middleware.py` (line 264) sends
  `camera=(), microphone=(), geolocation=()`, and `FEATURES.md` (line 1641) states « no camera, microphone or location ». The
  microphone goes to Clara's own origin only; both places change when the item is built. This is a change of a decision, not an
  exception to it.
- **The threat model gains a row.** Once the origin may use the microphone, a script running on that origin can use it while the
  page is open. The strict content security policy limits this, and `docs/THREAT-MODEL.md` must name it.
- **A microphone needs a secure context.** Clara is served over HTTPS through Tailscale (`docs/OPERATIONS.md`, line 41), so the
  condition holds; the browser behaviour still has to be checked on the real site.

Not decided:

- The model used on the PC. The owner's machine ran `small` in int8 at about 0.47 times real time (measured on 2026-11-22, a 13.7 s
  French sample; the peak memory was 833 MB). A larger model is possible on the same machine, but the figures are not measured.
- The maximum length of one dictation and the audio format sent by the clients.
- What the « new dictation conversation » button does with the previous one: it is left as it is, with its title.
- Whether Discord voice messages go into the Discord conversation of their channel, or into the dictation conversation of a client.
- Whether the terminal's dictation follows the per-client automatic-sending setting, and which client's setting that is.
- The place and the label of the button on the web site: an interface choice, left for the implementation.

Left for the implementation (the owner asked to be shown a proposal and to approve it before it is built):

- The route that receives the audio, its format, its limits, its timeout and the drop of the audio after use.
- How the current dictation conversation of each client is stored (a table, or a field), and its migration (the last one is `0037`).
- The title of a dictation conversation, and how it is marked in the list of conversations.
- The Settings page for the per-client setting, the approval mode and the packs.
- The tests: the route with a fake transcriber, the rule that the audio is dropped, the locked-screen case, and the taint of the
  conversation.
- The change of the microphone header for Clara's origin, the two `FEATURES.md` lines it changes, and the threat-model row.
- The audio library of the terminal (not checked yet), and the code of the web button: recording, sending, stopping.

What the current design already sets:

- `src/clara/db/migrations/0001_initial.sql` (lines 93-107): a conversation has a `client`, an optional `project_id`, and the flags
  `incognito` and `local_only`.
- `src/clara/api/modes.py`: a conversation's mode is read and set with `GET` and `PUT /v1/conversations/{id}/mode`; packs are switched
  per conversation.
- `docs/DECISIONS.md` (line 47): a tainted turn raises the verdict of outside writes; a mail trash is the one grantable exception.
- `docs/DECISIONS.md` (line 72): facts from tainted turns are kept, tagged `[unconfirmed]` until confirmed.
- `docs/DECISIONS.md` (line 303): the workspace marks files `quarantine` and `tainted`.
- `docs/DATA-MAP.md` (line 25): the Discord bot keeps its current conversation id in its state file.
- `coucou_copy/CLAUDE.md` (line 32): the phone's token is `phone`, created with `--approvals all`; a voice phrase must never approve.
- `docs/THREAT-MODEL.md`: the content a conversation reads is the threat; a dictation is the owner's voice, but it can capture other
  people or the television in the room.
- `src/clara/api/middleware.py` (line 264): `permissions-policy: camera=(), microphone=(), geolocation=()` on every response.
- `FEATURES.md` (line 1641): the web promise « no camera, microphone or location ».
- `docs/OPERATIONS.md` (line 41): Clara is served over HTTPS by Tailscale.
- Measured on 2026-11-22 on this PC: `faster-whisper` `small`, int8, 0.47 times real time on a 13.7 s French sample, 833 MB peak.

## `/goal`: Clara keeps working on a conversation until a condition is met

The owner wants an equivalent of Claude Code's `/goal` for Clara. In a web conversation the owner sets a completion condition, and
Clara keeps taking turns without a new message until a judge finds the condition met, judged impossible, or a limit is reached. Today
one message gives one answer, bounded by `CLARA_MAX_TOOL_ROUNDS` and `CLARA_ANSWER_BUDGET` (`src/clara/settings.py:89`, `:94`), and
nothing continues between messages.

What the owner decided:

- **The loop runs on the server**, in the turn driver, through the `Runs` mechanism that already keeps a web turn going when the page
  is closed (`docs/DECISIONS.md:55`, `src/clara/agent/runs.py`). Web only: the terminal and Discord are later items.
- **Commands**: `/goal <condition>` starts a goal, `/goal` alone shows its state, `/goal clear` ends it.
- **One goal per conversation.**
- **The model that works the goal is the conversation's model** by default; each goal may name another.
- **The judge is the same model as the goal**, with no tools. After each turn it answers met, not yet met, or impossible. It sees the
  goal, the reply of the turn and its tool results. It can stop the loop; it cannot approve or run anything.
- **The judge checks only the rules written in the condition**, for instance « only use the following websites ». It does not judge
  tool permissions: the modes and the gate decide those, unchanged.
- **Default limits: 10 turns and 15 minutes of model time**, set in the settings and changeable for each goal. A turn is one turn of
  the conversation, which may itself use up to `CLARA_MAX_TOOL_ROUNDS` tool rounds. The time counts model time only, as the answer
  budget does (`docs/DECISIONS.md:124`), so a pause for an approval does not use it.
- **When a limit is reached**, the goal is paused and says where it stopped. `/goal` resumes it, or adds turns.
- **An approval requested during a goal** pauses it. It resumes after the owner answers, through the follow-up turn that already
  exists (`docs/DECISIONS.md:115`).
- **A restart of the server** pauses the goal. Nothing resumes on its own.
- **A conversation contaminated by outside content** (a mail, a page) is not limited further. The tainted rule keeps its existing
  effect on outside writes (`docs/DECISIONS.md:47`).

What I corrected or added to the owner's plan:

- **The judge can stop, not decide.** A judge that reads outside text can be misled into stopping early, but not into doing more.
- **« Turn » is ambiguous.** It is counted as a turn of the conversation, not a tool round: otherwise ten turns could mean two hundred
  tool rounds.
- **Cost.** Each turn costs two model calls, since the judge is the same model. Spending is not capped (`docs/NON-GOALS.md`, « Spending
  caps »), so the turn and time limits bound the work, not the money. The build must say so in `docs/DECISIONS.md`: a turn limit is
  not a spending cap.
- **The judge sees only what the reply and the tool results show.** A rule such as « only use these websites » is checked against the
  addresses that appear there, not against everything a tool did, unless the tool reports it.

Not decided:

- How a goal reaches the terminal and Discord, when those clients are added.
- Whether a goal survives a change of the conversation's model or provider.
- The exact syntax for changing the limits of one goal.
- Whether a goal may run in a conversation that belongs to a project or to an automation.

Left for the implementation (the owner asked to be shown a proposal and to approve it before it is built):

- The field or table that holds a goal, its state and its counters, and its migration (the last one is `0037`).
- The judge's prompt and its answer format.
- The status line on the web page, and how a paused goal is shown.
- The names and ranges of the settings for the default limits.
- The tests, with `FakeLLM` and a scripted judge: met, not yet met, impossible, a limit reached, a pause for an approval, a pause on
  restart, and a judge that stops on a broken rule.
- The lines of `FEATURES.md` and the entry of `docs/DECISIONS.md` on the limits.

What the current design already sets:

- `src/clara/agent/turn.py:651`: `Agent.turn()` is the one path for all conversation.
- `src/clara/settings.py:89`, `:94`, `:466`, `:471`: `CLARA_MAX_TOOL_ROUNDS` (20) and `CLARA_ANSWER_BUDGET` (900 s), per answer.
- `src/clara/agent/runs.py`: the web turn kept alive when the page closes (`docs/DECISIONS.md:55`).
- `docs/DECISIONS.md:115`: an approval waits up to `CLARA_APPROVAL_WAIT`, then a follow-up turn starts.
- `docs/DECISIONS.md:124`: the answer budget counts model time only.
- `docs/DECISIONS.md:47`: a tainted turn raises the verdict of outside writes.
- `docs/NON-GOALS.md`, « Spending caps »: spending is shown, not capped.
- `docs/THREAT-MODEL.md`, « A model that misbehaves »: tool rounds and model time are bounded.
- `src/clara/tools/base.py:7-9`, `:260-261`: a refusal is recorded in the audit log, and the model is told the action was not done.
