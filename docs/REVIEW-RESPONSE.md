# Response to the review of Clara 0.3.0

The review (`clara-review.md`, 14 October 2026) checked 59 numbered points and 8 new findings against the old code.
Clara 1.0 is a rewrite, so each point is answered by what the new code does, with the file or test that shows it.
"Gone" means the feature the point is about no longer exists (see [NON-GOALS.md](NON-GOALS.md)).

## Before anything else: 25 October

The review's one deadline concerns the **old** server, which stays in use until the cut-over: repeating reminders
stored with a fixed offset (`+02:00`) fire an hour early from Sunday 25 October. On the old machine, list them with
`SELECT id, text, repeat, timezone FROM reminders WHERE repeat != '' AND (timezone LIKE '+%' OR timezone LIKE '-%');`
and recreate them with a zone name. Clara 1.0 cannot have this problem: wall-clock times are stored with zone names
only, the schema refuses offsets (`CHECK` constraints in `db/migrations/0001_initial.sql`), and the tests run
reminders across that very night (`tests/test_time.py`, `tests/test_reminders.py`).

## Findings worse than first written (review section 4)

| # | Finding | Clara 1.0 |
| --- | --- | --- |
| 1 | Drive asked with the full `drive` scope; callback not tied to the browser; confirm page named the Clara user | `drive.file` (plus `openid email`) only, with PKCE (`connectors/drive.py`); the consent is bound to the web session that started it, the Google address is shown before you confirm, disconnecting revokes the token (`api/connections.py`; `tests/test_connectors.py`) |
| 2 | Passphrase hashed with one unsalted SHA-256 | scrypt N=2^17 with a random salt, or a raw 32-byte key; AES-GCM; a check value; `clara secrets rotate` (`secrets/store.py`; `tests/test_foundations.py`) |
| 3 | Fallbacks could leave a local choice for a hosted provider | A local model never falls back to a hosted one; a local-only conversation is refused any hosted model and anything leaving the machine (`llm/router.py`, `gate/engine.py`; `tests/test_foundations.py`, `tests/test_gate.py`) |
| 4 | Users on their own key bypassed the limit | Gone: no users, no limits |

## Part 1: access

| # | Point | Clara 1.0 |
| --- | --- | --- |
| 1 | A client token speaks for every surface and account | No accounts. One hashed token per client, revocable at once, each with an approval scope (`all`, `safe`, `none`) (`auth/tokens.py`) |
| 2 | `/docs` public; limiter exemptions; console on the public listener; long sessions; no second factor | `/docs` only with `CLARA_DEBUG`; listener on 127.0.0.1 only, Tailscale only, Funnel refused; limiter (10 failures a minute, 5-minute block) with no exemption, forwarded addresses trusted only from 127.0.0.1; no passwords at all: tokens plus the tailnet; web sessions 30 days idle, 90 at most (`api/middleware.py`, `auth/limiter.py`, `auth/web.py`, `ops/tailscale.py`) |
| 3 | Discord `/register` bypasses the sign-up switch | Gone: no sign-up; the bot answers one Discord id |

## Part 2: secrets and what the server keeps

| # | Point | Clara 1.0 |
| --- | --- | --- |
| 4 | Master key beside the data; weak derivation; no rotation | Key in a systemd credential (or a file you name), never in the data folder or `.env`; scrypt; rotation |
| 5 | `.env` holds every credential, unchecked; restart installs code with secrets in reach | `.env` is non-secret only (a `*_API_KEY`, `*_PASSWORD`, `*_SECRET` variable stops the start); its permissions, the data folder's and the key file's are checked at start; updates install only checked releases into their own folder (`settings.py`, `ops/private.py`, `ops/updater.py`) |
| 6 | Shared GitHub token copies any private repository | Gone; GitHub uses your fine-grained token and only the repositories you list (`connectors/github.py`) |
| 7 | Tokens reach further than needed; Discord takes passwords | `drive.file`; GitHub fine-grained plus a repository list; Discord takes no passwords (it has nothing to sign in to) |
| 8 | Smaller leaks | Vault token as a header, never in the URL (kept from the old code); token files 0600; no headers or bodies in the log; provider error texts never in the traffic log (a failed model call keeps its HTTP status and the provider's error code, a plain token; the text is in the debug log while the switch is on) (`ops/traffic.py`) |
| 9 | Server folder roots can contain the server's secrets | Gone: Clara's files are the workspace only, a separate git repository; no tool reaches the data folder, the database, `.env` or the key (`workspace/paths.py`; `tests/test_workspace.py`) |
| 10 | Nothing screens for secrets | Secret-looking text (keys, tokens, private keys, passwords, connection strings, Clara tokens) is refused as a fact and raises any call sending it out of the machine to `ask` (`secretscan.py`, `memory/facts.py`, `gate/engine.py`). What you type is not screened: it is yours, and goes to the model you chose (use a local-only conversation for what must stay home) |
| 11 | Archives could write outside their folder | Refuted then and still true: archives are read in memory, `..` and links refused, nested archives skipped, 30 MB per file, 600 MB per archive (`knowledge/ingest.py`) |
| 12 | The traffic log is a plaintext copy of nearly everything | Metadata only (no text, no tool arguments, no addresses but failed sign-ins), 7 days; full content only while the debug switch is on, one hour, in a separate 0600 file deleted at the end (`ops/traffic.py`) |
| 13 | The owner reads more than the documents admit | One person: everything stored is yours, and [DATA-MAP.md](DATA-MAP.md) lists all of it |
| 14 | Erasure narrower than it reads | No per-person erasure (one person). Deleting a conversation removes its messages and files; `secure_delete` overwrites deleted text; `clara purge` vacuums the file; disconnecting Drive revokes the token at Google. What deletion does not reach is written down: backups (14 days, 8 weeks, restic's own policy) and the vault's and workspace's git history ([DATA-MAP.md](DATA-MAP.md)) |
| 15 | Group memory carries private facts into shared conversations | Gone: no groups, no other people |
| 16 | Discord stores members' messages | Gone: DMs only, one id answered, nothing stored from anyone else |
| 17 | Providers see the whole prompt; "local" not fully local | Unchanged by nature for hosted providers (their terms, dated, in [PROVIDERS.md](PROVIDERS.md)); local choices stay local; local-only conversations; web search through your own SearXNG |
| 18 | The fetch allowlist does not cover queries | An address is trusted only exactly as you wrote it or a search returned it; any other (a changed query string included) asks in a chat and is refused unattended; a URL holding something secret-looking asks (`connectors/tools.py`). Remaining: a search query itself goes to SearXNG's engines (see [THREAT-MODEL.md](THREAT-MODEL.md)) |

## Part 3: cost and memory

| # | Point | Clara 1.0 |
| --- | --- | --- |
| 19–23 | Credits measure size not money; checked once; background unmetered; reminders spend the allowance; defaults spend freely | Gone: no credits, no limits. Every model call (background ones too, by kind) is a `usage_log` row with tokens in, out and cached; the Usage page and `/usage` show them (`ops/usage.py`). Spending is watched, not capped ([NON-GOALS.md](NON-GOALS.md)) |
| 22 | (second half) A refused reminder has no fallback | Kept from the old code: the stored text is sent with the reason (`timekeeping/clara_jobs.py`; `tests/test_reminders.py`) |
| 24 | Recency decides what Clara remembers; recall needs the exact word | Pinned facts first, then newest, within a token budget, and the model is told how many it does not see; recall is FTS5 (case and accents folded, word starts). No stemming or translation: a known limit |
| 25 | Facts carry no source | Source `you`, `model` or `tool`; facts saved in a tainted turn are unconfirmed until you confirm (`memory/facts.py`; `tests/test_facts.py`) |
| 26 | Facts never update, expire or supersede | `replaces`, `expires`, editing (`tests/test_facts.py`) |
| 27 | Forget deletes the note, not its source; the same text comes back | A tombstone stops the model saving it again (you still can). The conversation it came from stays until you delete it |
| 28 | Clara cannot search earlier conversations | `search_history` (FTS5 over messages; incognito ones excluded) |
| 29 | Relationship score | Gone |
| 30 | No memory off switch | Incognito conversations: no facts read or written, deleted 24 hours after their last message |
| 31 | No retention limit, no disk ceiling | Traffic log 7 days; events until read (90 days at most); usage and audit 400 days; finished approvals 30 days; workspace 5 GB; disk alert at 85%. Messages and facts are kept until you delete them, by choice; the one exception is what automations make: the conversations of hook runs and per-run schedules are deleted past limits you set (off at first, `docs/DECISIONS.md`, « Automation retention ») |

## Part 4: running, updating, failing

| # | Point | Clara 1.0 |
| --- | --- | --- |
| 32 | The restart checks the update, not the new server; no rollback | Preflight of the new code against a copy of the database before stopping; the launcher waits for `/health` and the new boot id and rolls back by itself, restoring the database if a migration ran (`ops/updater.py`, `ops/launcher.py`; `tests/test_ops.py` rehearses a broken update) |
| 33 | New files before new code; the drain has no limit | Each release in its own folder, the site inside its wheel; the drain is bounded (`CLARA_DRAIN_SECONDS`, 120 s) with progress (`ops/lifecycle.py`; `tests/test_ops.py`) |
| 34 | Three update paths; unknown unit | One path (`/update` or the Admin page); the units are in `deploy/` |
| 35 | Dependencies move; no lock | `uv.lock`; each release carries a hash-pinned `requirements.lock`, installed with `--require-hashes` |
| 36 | The button installs whatever the branch holds | Tagged releases only, built by CI, with checksums and optional Ed25519 signatures that the updater then requires; CI runs Python 3.12 and 3.13 |
| 37 | Client tokens read once, unrevocable until restart | Tokens in the database, revoked at once (`tests/test_foundations.py`) |
| 38 | Start-only settings; stale environment | Settings are read at start and validated all at once; what changes at runtime (models, providers, fallbacks, the tainted rule…) are runtime settings in the database (`runtime.py`) |
| 39 | The update writes defaults into `.env` | The updater never touches `.env`; new settings are announced in the changelog |
| 40 | No server-side backup; key with the data | Nightly SQLite backups (14 daily, 8 weekly, 0600), a restic copy of the newest and of the workspace to another machine, backups before migrations and updates, a monthly restore-drill reminder; the key is never in a backup (`ops/backups.py`) |
| 41 | Migrations with no schema version | `PRAGMA user_version`, numbered migrations, a backup before each, a newer database refused (`db/migrate.py`; `tests/test_foundations.py`) |
| 42 | Crashes not handled | At start, the half-done is put straight: questions without answers put back, approved actions that were running marked failed (never run twice), interrupted scheduled runs due again, workspace changes committed (`app.recover`; `tests/test_ops.py`) |
| 43 | Two servers can share a data folder | `flock` on a lock file (`db/lock.py`; `tests/test_foundations.py`) |
| 44 | (refuted) One clock loop | Separate due loops, each catching its own errors; a systemd watchdog fed only while every loop turns (`ops/upkeep.py`) |
| 45 | Background work shares the model slots | Separate pools: `CLARA_CHAT_SLOTS` for you, `CLARA_BACKGROUND_SLOTS` for side jobs (`agent/model.py`) |
| 46 | A silent provider stalls 300 s; no total budget; 402 not a fallback trigger | A total model-time budget per answer (900 s); a model silent past the first-token timeout hands over to the next; 402 and refused keys move on and alert (`agent/model.py`, `llm/retry.py`; `tests/test_foundations.py`) |
| 47 | Events expire after a week; Discord stays down | Events are kept until every active client read them (90 days at most); the bot is its own service with `Restart=always`, and its silence raises an alert |
| 48 | Offsets and the clock change | Zone names only (see the top of this page) |
| 49 | Nothing tells the owner; `/health` documented both ways | Thirteen alerts, in every client and as Discord DMs ([FEATURES.md](../FEATURES.md#13-operations)); `/health` answers `{"status": "ok"}` and nothing else, detail behind a token |

## Part 5: documents and scope

| # | Point | Clara 1.0 |
| --- | --- | --- |
| 50 | The documents contradict each other and the code | One feature list, [FEATURES.md](../FEATURES.md); `tests/test_docs.py` fails when the code has a tool, route, setting, command, secret, runtime setting, event kind, action class or alert it does not name, or when its tool count is wrong |
| 51 | Discord tasks hidden; file limits that do not fit | Every client gets the same tools (`hidden_tools` is empty unless you set it); a message carries 4 files and 34 MB in all, which fits the 48 MB request once base64-encoded, and the web site and the bot check before sending (`tests/test_chat.py`) |
| 52 | Enforced guarantees and requests to the model share one voice | [THREAT-MODEL.md](THREAT-MODEL.md) separates what code enforces from what is only asked of the model |
| 53 | "User" means three things | No users: a *token* belongs to a *client* |
| 54 | Seven ways to join accounts | Gone |
| 55 | Who the server is for | One person, one machine: the first line of the README |
| 56 | A product line | Now: 47 tools, 121 API routes, 83 environment settings, 5 clients, 5 providers, one person (counted from the code; `tests/test_docs.py` keeps FEATURES.md listing all of them) |
| 57 | What to cut first | Cut: accounts, credits, the Discord platform, music, the relationship score, server folders, embeddings, public exposure ([NON-GOALS.md](NON-GOALS.md)) |
| 58 | No threat model, data map, changelog, non-goals, security contact, licence | [THREAT-MODEL.md](THREAT-MODEL.md), [DATA-MAP.md](DATA-MAP.md), [CHANGELOG.md](../CHANGELOG.md), [NON-GOALS.md](NON-GOALS.md), [SECURITY.md](../SECURITY.md), [LICENSE](../LICENSE) |
| 59 | External facts undated; default models are aliases | No hosted model is hard-coded as a default: you choose the default model; provider facts are dated and marked "to verify" in [PROVIDERS.md](PROVIDERS.md) |

## New findings (review section 6)

| # | Finding | Clara 1.0 |
| --- | --- | --- |
| N1 | Full Drive scope | `drive.file` |
| N2 | Unsalted SHA-256 passphrase | scrypt, salted |
| N3 | Owner `/link` merges filled accounts | Gone |
| N4 | A stale comment about erasure | Gone with erasure; documents are checked by tests |
| N5 | Own keys outside the limit | Gone |
| N6 | Python 3.14 bytecode; CI lists 3.11 and 3.12; no CI config | Python 3.12 baseline, CI on 3.12 and 3.13 in `.github/workflows/ci.yml`; bytecode is ignored by git |
| N7 | `data/` inside the shared folder | The data live in `/var/lib/clara`, outside the code; `data/` and `.env*` are in `.gitignore` |
| N8 | No way for a Discord member to stop being read | Gone: no members |

## The review's priorities

| Priority | Done in Clara 1.0 |
| --- | --- |
| 1. Fix offset reminders before 25 October | Old server: see the top of this page. New server: zone names only |
| 2. Narrow Drive | `drive.file`, session-bound consent, address shown before confirming |
| 3. Close the open defaults | No sign-up, no surfaces, Funnel refused, `/docs` off |
| 4. Replace the passphrase derivation, add rotation | Done |
| 5. Keep a local choice local | Done, plus local-only conversations |
| 6. Stop logging prompts and answers by default | Done |
| 7. Refuse folders holding `data/` or `.env`; screen secrets | Server folders gone; secret screening for facts and outgoing content |
| 8. Safer restarts: check before stopping, rollback, pinned dependencies, SQLite backups, a rehearsed restore | Done; `clara restore --verify` and a monthly reminder |
| 9. A lock on the data folder | Done |
| 10. Fix the documents; say who the server is for | Done; checked by tests |
