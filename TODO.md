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

## A tool to present a document at the end of an answer

The owner asked (2026-11-02) for a new tool for Clara to present a document at the end of her answer: a document made in
the conversation, or a file of the workspace when she can reach it. Once presented, the document can be downloaded or
seen in the workspace.

Not decided (the first reading is below; the owner to correct it):

- "A document created on the conversation". The first reading: a document whose content Clara gives in the call itself
  (text, Markdown, code), kept with the conversation as the files you send are (`conversation_files`), for when there is
  no workspace or she does not want to write one. Whether she may also present a file of the workspace by its path (the
  other half of the request) is taken as yes.
- Which formats a document may have, and its size limit. Binary files (a PDF, a picture) can only come from the
  workspace, since a call carries text.
- "At the end of the answer". The first reading: a card under her last message, with the file's name, its kind, and the
  two actions, not a card in the side panel. Whether it may show several documents in one answer, and what happens to the
  card when the answer is edited, tried again or switched to another version.
- "Seen in the workspace". For a workspace file it opens the Library on that path (`#/library?path=…`). A document that
  lives only in the conversation has no place in the workspace: whether "see" then means a preview in the page, or only
  a download, or a button that keeps it in the workspace (a write, which the gate decides).
- Whether a presented file also gets a card in the side panel (it lists the workspace files she wrote in the
  conversation), and whether a file she did not write (a mail attachment she saved, a file of a project) can be
  presented.
- Which path the tool takes for a workspace file that is hidden or ignored (`folder_sensitive`), or marked as coming from
  outside: refuse, or ask each time.
- What the other clients show: the Discord mirror (a name and a link, or the file itself), the desktop and phone apps
  (`../coucou_copy`), and an unattended run (a reminder, a schedule, a hook) that presents a document nobody opens at
  once.

What the current design already sets:

- The side panel already has a card for each workspace file Clara wrote in the conversation, found from the git history,
  with a name, a folder, a time and **Download**. A click leads to the Library (`docs/DECISIONS.md`, « The side panel
  lists the workspace files Clara wrote in the conversation, found from the git history »). The new tool is her own choice of what to hand over, at the end of an answer;
  the panel lists everything she wrote.
- Files sent in a conversation are stored with it and downloaded through
  `GET /v1/conversations/{conversation_id}/files/{file_id}`; the file name is cleaned and sent as an attachment.
- The workspace is untrusted: `workspace/paths.py` is the only door to a file (no links, no `.git`, regular files only,
  reads capped at `CLARA_WORKSPACE_MAX_FILE`). The tool must read a path through it, and the download must not serve
  anything the Library would not.
- The workspace is `None` when it is off: the tool must still work, and say so, for a document made in the conversation.
- A new tool needs a gate class, a level and a target, is named in FEATURES.md (the docs test checks it), and the tool
  count line is updated. A new route or event kind is named there too.
- The card is shown to the owner; it must not carry the document into the model's context, so presenting does not
  read the file back to Clara.
