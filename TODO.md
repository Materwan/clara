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

## Moodle and Auriga (EPITA)

The owner asked for Clara to be connected to two school sites: Moodle (https://moodle.epita.fr/my/) for the owner's
courses, and Auriga (https://auriga.epita.fr/) for the owner's calendar and grades. Both have an API. It was checked on
2026-11-01 from a browser the owner had signed in to; nothing is built yet.

Decided (owner, 2026-11-01):

- The shape: a new pack of tools, switched per conversation as Mail is. Not a connection on the Connections page.
- The pack is called "Moodle/Auriga". It can be used anywhere, as Mail can: in a chat and in the packs of a schedule.
- The tools wanted (names not fixed): a calendar (Auriga for the classes and exams, Moodle for the quizzes and assignment
  dates), grades (Auriga's, and Moodle's per course), and Moodle documents (list the files of a course, read one).
- The first version has read tools only. No write tool: no calendar entry, no Moodle submission, no Moodle message.
- The first version does not save the owner's password. Clara keeps tokens only, and the owner signs in again when they
  lapse. Keeping the password to sign in again by script is an upgrade for a later version (see "Later" below).
- Reading Moodle or Auriga does not taint the turn. This differs from Mail, GitHub and Drive, whose reads do. Course
  pages, forum posts and assignment texts are written by other people, and with no taint the tainted-turn rule and the
  reader do not apply to them.

What was found:

Moodle (version 4.5.15, "Plateforme d'enseignement EPITA"):

- Web services and the mobile web service are on (`enablewebservices` and `enablemobilewebservice` are 1 in the public
  mobile config, `POST /lib/ajax/service-nologin.php?info=tool_mobile_get_public_config`). The REST endpoint is
  `POST /webservice/rest/server.php` with `wstoken`, `wsfunction` and `moodlewsrestformat=json`.
- The only way to sign in is "Forge ID" (EPITA's OAuth2 identity provider). There is no password form, so the usual
  `/login/token.php` (username and password for a token) cannot work.
- A token comes from the mobile-app browser flow: signed in, open
  `/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=<random>&urlscheme=moodlemobile&confirmed=1`. The page
  answers with a link `moodlemobile://token=<base64 of "siteid:::token">`, not a redirect header. Tested: that token worked
  on `core_webservice_get_site_info`. The launch link carries no private token, but the site-info answer has a
  `userprivateaccesskey` field, which must never be logged or shown to the model. Clara cannot fetch it alone: the owner signs
  in once and stores the token with `clara secrets set`. Its lifetime is a site setting and was not checked.
- The token can call 451 functions, with the owner's full rights; Moodle cannot make a token read-only. The ones the pack
  would use are there: `core_enrol_get_users_courses`, `core_course_get_enrolled_courses_by_timeline_classification`,
  `core_course_get_contents`, `core_calendar_get_calendar_upcoming_view`, `core_calendar_get_action_events_by_timesort`,
  `mod_assign_get_assignments`, `gradereport_user_get_grade_items`. Writes are there too (`mod_assign_save_submission`,
  `core_message_send_instant_messages`), so the pack must call an allowlist of read functions and nothing else.
- The calendar export (iCal, `/calendar/export.php`) is on. It is a lighter way to get events and deadlines only. Its link
  carries a key; none was generated.
- The launch was run again on 2026-11-02 (three more times, for the survey below), so the token may be the same one or
  new ones. The test token of 2026-11-01 is still valid. `/user/managetoken.php` lists only the RSS key, so the owner cannot revoke
  it there; it expires with the site's token lifetime, and an admin can delete it. The token itself was never shown or
  stored.

Auriga:

- It is an Angular app (front 80.4.1) over a REST API at `https://auriga.epita.fr/api/`. Calls carry
  `Authorization: Bearer <token>` and `x-scope: frontend`; without them `/api/v1` answers 401. There is no public OpenAPI
  file and no iCal feed (`/ical` and `/calendar.ics` are 404).
- Sign-in is Keycloak, realm `npionisepita`, at `https://ionisepita-auth.np-auriga.nfrance.net/auth/realms/npionisepita`:
  public client `np-front`, authorization code with PKCE (S256), redirect `https://auriga.epita.fr/`. The login page offers
  email and password, and "Se connecter avec Microsoft" (a broker), which the owner uses. So a password grant is out.
- The realm's public OpenID configuration lists the grants `authorization_code`, `refresh_token`, `password`,
  `client_credentials` and `device_code`, and the scopes include `offline_access`. Probed on 2026-11-01 without signing in:
  the device grant is disabled for `np-front` (`unauthorized_client`); a sign-in request with the scope `offline_access`
  is accepted (the login page loads), but whether a token is then issued with it was not tried; `redirect_uri` accepts
  `https://auriga.epita.fr/` and other paths on that host (`https://auriga.epita.fr/clara-callback` is accepted, and is a
  plain 404 page, so a code left in its address bar stays visible) and refuses `http://localhost…` (400). Both
  `response_mode=fragment` and `query` are accepted. The page itself removes the code from its address bar as it loads.
  Checked again on 2026-11-02 from a signed-in session: the page's own sign-in (scope `openid profile email`, no
  `offline_access`) gives an access token of 60 seconds and a refresh token of 3600 seconds (type `Refresh`). A silent
  request (`prompt=none`) for scope `openid` with the redirect `…/clara-callback` returns a code; the same request with
  `openid offline_access` returns `error=interaction_required`, not `invalid_scope`. So the client accepts the scope and
  wants one consent screen first. No code was exchanged and no token was made.
- The planning: `GET /api/plannings/me?days=1&…&days=7&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` answers
  `{"interventions": [...], "unavailabilities": [...]}`. An intervention has `startDateTime` and `endDateTime` (UTC),
  `activityType` (`TD`, `TP`, `CM`, `EX`…), `interventionPedagogicalUnits[].pedagogicalUnit.caption` (`fr` and `en`),
  `interventionResources[].resource` (the room), `interventionInstructors`, `interventionPopulations` (groups),
  `participations[].attendanceStatus` and `isExam`. "Mon planning" is menu entry 227.
- The grades: `POST /api/menuEntries/<id>/searchResult?size=100&page=1&sort=id&disableWarnings=true` for "Mes notes
  (éval)" (1036) and "Mes notes (synthèse)" (1144). The body is a saved-query definition (`searchResultDefinition` with a
  `queryId` and a tree of columns, plus `searchResultGlobal` with filters), which the page loads from
  `GET /api/menuEntries/<id>/query/<queryId>` and `/form/<id>`. The synthèse query filters on a pedagogical component code
  starting with a school year (`2526` when read), so a tool must take the year as a parameter. The response was not read.
- Also seen: `/api/me`, `/api/me/authorizations`, `/api/menus`, `/api/menus/myPlanning`, `/api/notifications/me`.
- Chrysalide is a student app (a fork of Papillon, backed by the EpiMac association) that shows planning and grades from
  Auriga. Its source is public: `gitlab.com/epimac-asso/projects/chrysalide/ChrysalideApp` (Expo / React Native; the
  files are `utils/aurigaAuth.ts`, `utils/aurigaAuthWorker.ts` and `components/AurigaRefreshProvider.tsx`). It was read
  on 2026-11-02; nothing was copied. How it never asks the owner to reconnect:
  - The user types an email and a password once. The app keeps `{username, password}` and the Keycloak refresh token in the
    phone's secure store (`expo-secure-store`).
  - It signs in without a browser by scripting the Keycloak web form: it requests the login page for `np-front` (redirect
    `https://auriga.epita.fr/`, `response_mode=query`, scope `openid`, PKCE S256, `prompt=login`), reads the form
    `kc-form-login`, posts the email and password with the `AUTH_SESSION_ID` and `KC_RESTART` cookies, takes `code` from
    the redirect URL and swaps it at the token endpoint.
  - It renews with `grant_type=refresh_token` and client `np-front` (no secret), and saves each new refresh token. When
    that fails, and whenever the app comes back online, it signs in again with the stored password. It asks the user
    again only when Keycloak refuses the password. It does not ask for `offline_access`, so its refresh token is the
    session kind; the code logs `refresh_expires_in` for debugging, and the value is not known here.
  - It does not go through Microsoft. Whether the owner's Auriga account takes the same email and password in that form
    is for the owner to say (the owner types something into Chrysalide).

The calls (read on 2026-11-02 from signed-in sessions; only field names, types, counts and enum values were recorded,
no grade, course text or name):

Auriga, all `https://auriga.epita.fr/api/…`, JSON, with `Authorization: Bearer <access token>` and `x-scope: frontend`:

- Token: `POST https://ionisepita-auth.np-auriga.nfrance.net/auth/realms/npionisepita/protocol/openid-connect/token`, form
  `client_id=np-front` and `grant_type=refresh_token` (+ `refresh_token`) or `authorization_code` (+ `code`,
  `code_verifier`, `redirect_uri`). The answer has `access_token`, `expires_in`, `refresh_expires_in`, `refresh_token`,
  `token_type`, `id_token`, `session_state`, `scope`. Keep each new `refresh_token` (Chrysalide does).
- Planning: `GET /api/plannings/me?days=1&days=2&…&days=7&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` (fields above).
- Grades, two saved queries, both `POST /api/menuEntries/<entry>/searchResult?size=100&page=<n>&sort=id&disableWarnings=true`.
  The body is `{joinIdenticalLines, searchResultDefinition, searchResultGlobal, queryId}`, which is the answer of
  `GET /api/menuEntries/<entry>/query/<queryId>` (field `queryDefinition`, plus the `queryId`). So Clara can fetch the
  query and post it back, with no copy of it in the code. The answer is `{currentPage, pageSize, numberOfElements,
  totalPages, totalElements, content: {columns, lines}, dataviz}`; a line is an array.
  - "Mes notes (synthèse)": entry 1144, query 1198. The year is the one global filter: `searchResultGlobal.filters.
    conditions[0].value = ["<year>"]` (field `code`, operator `like`, path `Person-Registration-person-obligation`); the
    saved default was `2526`, which gave 234 lines over 3 pages, and `2627` gave 99 lines. A line is
    `[id, null, component code "YYYY_…", caption {fr, en?}, "Moy. (finale)"]`, the average a numeric string, null, or a
    few non-numeric strings. Every average was null for `2627` at the time. The positions are read from types and counts,
    not proven.
  - "Mes notes (éval)": entry 1036, query 945, no year filter: 167 lines over 2 pages, all years, sorted by component code.
    A line is `[id, note, coef, component code "YYYY_…", a 3-letter code]`; the note is a numeric string, empty or two
    letters, the coef an integer, and the last item is probably the component type. For a year, page through and keep the
    codes that start with it.
  - The component codes have the same form as the planning's `pedagogicalUnit.code` (`2627_I_INF_…`).

Moodle, `POST https://moodle.epita.fr/webservice/rest/server.php`, form `wstoken`, `wsfunction`,
`moodlewsrestformat=json`; a list parameter is sent as `name[0]`, `name[1]`:

- `core_webservice_get_site_info` (no parameter): `userid` is needed by the others. Keep only that; the answer also holds
  `userprivateaccesskey`.
- `core_enrol_get_users_courses` (`userid`): an array of courses (47 here, all years): `id`, `shortname`, `fullname`,
  `displayname`, `startdate`, `enddate`, `visible`, `progress`, `category`, `courseimage`, …
- `core_course_get_enrolled_courses_by_timeline_classification` (`classification=inprogress`, also `past` and `future`):
  `{courses, nextoffset}` with `id`, `fullname`, `shortname`, `startdate`, `enddate`, `progress`, `viewurl`,
  `coursecategory`. 20 courses in progress.
- `core_calendar_get_action_events_by_timesort` (`timesortfrom`, `timesortto`, `limitnum` up to 50): `{events, firstid,
  lastid}`. An event has `name`, `description`, `timestart`, `timesort`, `modulename`, `eventtype`, `course` (`id`,
  `fullname`, `shortname`), `action` (`name`, `url`, `actionable`), `url`, `overdue`, `formattedtime`. 32 events within a
  year either side of today, all of `assign` (`due`), `forum` and `groupselect` (`expectcompletionon`): **there is no quiz
  event in the calendar.** `core_calendar_get_calendar_upcoming_view` answered an empty list; `core_calendar_get_calendar_events`
  with the options tried answered nothing.
- `mod_quiz_get_quizzes_by_courses` (`courseids[]`): `{quizzes, warnings}`. 270 quizzes over the 47 courses, with `id`,
  `coursemodule`, `course`, `name`, `intro`, `timeopen`, `timeclose` (0 when unset), `timelimit`, `attempts`, `grade`,
  `sumgrades`… Only 21 have `timeopen` and `timeclose`, 15 a `timelimit`, and none opens or closes after today. So the
  quiz dates of the calendar tool come from here, not from the calendar.
- `mod_assign_get_assignments` (`courseids[]`): `{courses: [{id, fullname, shortname, assignments}], warnings}`. 31
  assignments: `id`, `cmid`, `course`, `name`, `duedate`, `allowsubmissionsfromdate`, `cutoffdate`, `gradingduedate`,
  `grade`, `intro`… 29 have a due date; none is after today.
- `gradereport_user_get_grade_items` (`courseid`, `userid`): `{usergrades: [{courseid, userid, gradeitems}], warnings}`.
  An item has `itemname`, `itemtype` (`course`, `category`, `manual`, `mod`), `itemmodule` (`quiz`, `hvp`), `cmid`,
  `graderaw`, `gradeformatted`, `grademin`, `grademax`, `rangeformatted`, `percentageformatted`, `feedback`,
  `gradedategraded`. Course and category items have null names. 135 items over 15 of the 20 courses in progress, up to
  39 in one; 42 of them are quizzes.
- `core_course_get_contents` (`courseid`): sections `{id, name, summary, modules}`; a module has `id`, `name`, `modname`,
  `url`, `contextid`, `visible`, `uservisible`, `description` and `contents`. Over the 47 courses: `resource` 411, `quiz`
  273, `hvp` 194, `label` 191, `page` 110, `folder` 77, `forum` 51, `assign` 44, `url` 38, `chat` 10, `book` 8, `scorm`
  7, `questionnaire` 6, `groupselect` 6, `scheduler` 5, `attendance` 1, `workshop` 1. 39 of the 47 have files to download.
  An item of `contents` has `type` (`file` or `url`), `filename`, `filepath`, `filesize`, `fileurl`, `timecreated`,
  `timemodified`, `mimetype`, `isexternalfile`, `author`, `license`.
- A document: `GET <fileurl>` with `&token=<token>` added; the `fileurl` has the form
  `https://moodle.epita.fr/webservice/pluginfile.php/<n>/mod_resource/content/<n>/<name>?forcedownload=1`. Tested on a
  PDF: 200, `application/pdf`, `Content-Length` present, the body starts with `%PDF-`, no redirect. The text of `page`,
  `book` and `hvp` activities is not in `contents` and was not looked at.
- The functions the pack would allow, from the above: `core_webservice_get_site_info`, `core_enrol_get_users_courses`,
  `core_course_get_enrolled_courses_by_timeline_classification`, `core_course_get_contents`,
  `core_calendar_get_action_events_by_timesort`, `mod_quiz_get_quizzes_by_courses`, `mod_assign_get_assignments` and
  `gradereport_user_get_grade_items`, plus the file download. Nothing else.

Not decided:

- How Clara signs in, and again when it lapses. Both sites need the owner in a browser once. The options:
  - Auriga, the likely one: a paste-back sign-in. A console command (or a card on the Connections page) gives the owner a
    URL (client `np-front`, scope `openid offline_access`, PKCE, redirect `https://auriga.epita.fr/clara-callback`). The
    owner signs in with Microsoft, lands on the 404 page and pastes its address into a hidden prompt. Clara swaps the code
    for tokens, keeps the refresh token in the secrets store and refreshes it herself. An offline token lives until it has
    been idle for a month (Keycloak's default, not checked here); without it, the token lives as long as the Keycloak
    session ("Remember me" on the login page may make that longer). Without `offline_access` the refresh token is the
    one-hour kind seen on 2026-11-02: Clara would have to keep refreshing it, and it would still end with the Keycloak
    session, so a daily paste would be likely. That makes `offline_access` the point of the whole option. It needs a
    consent screen at the sign-in, which fits an interactive paste. Not tried end to end: it takes one real sign-in and
    makes an offline session on the owner's account.
  - Auriga, the others: reuse the Keycloak session cookies with `prompt=none`, as the page does when it loads (the
    cookies are HttpOnly and the session short: fragile); a headless browser inside Clara (Microsoft's checks, heavy, a
    sandbox question); a service client from EPITA's IT (not in the owner's hands). The password and device grants are out.
  - Moodle, the likely one: the same paste-back. A command gives the `launch.php` URL; the owner opens it signed in,
    copies the address of the link "Cliquez ici si l'app ne s'ouvre pas automatiquement" (`moodlemobile://token=…`) and
    pastes it into a hidden prompt; Clara decodes and keeps the token. Moodle has no refresh: when the token lapses the
    calls fail with `invalidtoken` and the owner repeats the paste.
  - Moodle, a fallback for the calendar only: the iCal link, which does not lapse until it is reset.
  - Either site: how Clara asks the owner to reconnect. She does for Gmail and Drive (`connectors/setup.py`) what she
    would do here: when a refresh or a call fails for sign-in reasons, the connection is marked `needs_reconnect`, an
    alert is raised (one key per site, in every client's inbox and Discord's `#alerts`, repeated at most every 6 hours,
    cleared when the connection works again), the tools answer the model with the reason and how to reconnect, and the
    pack's check says it is unusable. The new alert keys are named in FEATURES.md.
- The tool names. The Moodle function list above is a proposal, not a decision.
- How a Moodle document is read: straight into the turn, or saved first. The file is outside content shaped by someone
  else; `knowledge/ingest.py` already reads such files with limits.
- The pack's check (unusable, with the reason, when no token is set, as Mail is with no mailbox).

Later, not in the first version:

- Auriga: keep the owner's email and password in the secrets store and sign in again with them whenever the refresh
  token fails, as Chrysalide does (above). Nothing to reconnect until the password changes or Keycloak adds a check a
  script cannot pass. The cost: the school password sits in Clara's database, a bigger secret than a token, and it works
  only if the login form accepts the owner's account (not Microsoft). It needs its own decision when the time comes.

What the current design already sets:

- A credential goes into the encrypted secrets store (`clara secrets set`, or the Connections page), never into `.env`,
  which holds only non-secret settings.
- Reads of GitHub, Drive and Mail taint the turn. This pack is the exception the owner chose (above), so its read tools
  must not mark the turn tainted.
- A new tool needs a gate class, a level and a target, and FEATURES.md names it.

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
