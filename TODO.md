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
- **Later: keep the owner's email and password** and sign in to Auriga again with them whenever the refresh token fails, as the student app
  Chrysalide does (`gitlab.com/epimac-asso/projects/chrysalide/ChrysalideApp`, read on 2026-11-02, nothing copied): it scripts Keycloak's login
  form (`kc-form-login`, with the `AUTH_SESSION_ID` and `KC_RESTART` cookies), takes `code` from the redirect and swaps it. Nothing to
  reconnect until the password changes or Keycloak adds a check a script cannot pass. The cost: the school password sits in Clara's database,
  a bigger secret than a token, and it works only if the login form accepts the owner's account (not Microsoft). It needs its own decision.
- **Not built:** Moodle's iCal export as a lighter source for the calendar (its link carries a key and does not lapse until it is reset);
  Moodle's `mod_assign_get_assignments` (assignment cut-off dates, texts); the text of Moodle `page`, `book` and `hvp` activities (it is not in
  `core_course_get_contents`); any write to either site.
