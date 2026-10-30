# Code review: `src/` (Logging, Exceptions, Stdlib security)

- **Commit:** `bc439e6` on branch `v2`
- **Date:** 2026-10-21
- **Scope:** `src/` (145 Python files), in the three areas the `/python-quality` skill grades against: Logging, Exceptions and Stdlib security. The results of `/python-quality src/` are the baseline.
- **Not covered:** `web/` (TypeScript), `scripts/`, `deploy/`, and the robustness, concurrency and lifecycle findings already in [`docs/audits/audit-2026-10-21.md`](docs/audits/audit-2026-10-21.md). Section 6 cross-references that audit instead of repeating it.
- **Method:** pattern searches across all of `src/`, then reads of the flagged sites and of the security-sensitive modules (auth, secrets, workspace sandbox and git, ingest, ops/updater). Other files were searched, not read line by line.

Severity: **Critical** (exploitable, fix first) · **High** · **Medium** · **Low** · **Info** (no action needed).

---

## 1. Verdict

**FAIL**, under the skill's rules, because of one open critical security issue (S1). Without S1 the verdict would be WARN, for formatting, outdated dependencies and missing tools.

The code is otherwise in good shape for these three areas. There is no bare `except`, no `shell=True`, no `pickle`, and no `random` for secrets. Secrets, tokens and sessions use the right primitives. Two items need attention:

1. **S1 (Critical, still open):** a sandboxed script can make the host's git run a command.
2. **E1 (Medium):** legacy recap rows are dropped without a log line.

Everything else is Low or Info.

---

## 2. Baseline from `/python-quality src/`

| Check | Result |
|---|---|
| Ruff lint (project config) | 0 issues |
| Ruff format | 104 of 146 files would change (style only) |
| Tests | 421 passed, 1 skipped, 0 failed (coverage not measured) |
| Outdated dependencies | 8 (`pillow` 12.3.0 → 13.0.0 is a major bump) |
| Not run | mypy, pyright and ty (no checker installed); pip-audit (no CVE scan); pytest-cov; vulture |

Ruff's `S` (bandit-style) and `B` (bugbear) rules are active and pass, so the items below are the ones the linter cannot see.

---

## 3. Logging

Reference: https://docs.python.org/3/howto/logging.html

**What works**

- **Lazy formatting.** No logger call on a single line passes an f-string, `%` or `.format()` as its message (grep over `log`, `logger`, `self._logger`, `self.log`). Multi-line calls were not checked. The Ruff `G` rules that would enforce this are not enabled.
- **Tracebacks are kept.** There are 21 `log.exception` calls. The global exception handler (`api/common.py:62`) and `failure()` (`api/common.py:128`) log unexpected errors with the traceback, and the client gets only a generic message.
- **Secrets are masked before the in-app log view.** `ops/logstream.py`, `LogHub.add`, runs `mask()` on every line.
- **Startup output is ordered correctly.** `app.py:413` prints the settings error to stderr before `setup_logging` runs (`app.py:415`), so nothing is lost.

**Findings**

**L1 · Low · Logger names are mixed.** 28 files use `logging.getLogger(__name__)`. Four use hand-picked names: `app.py:90` (`"clara"`), `api/common.py:27` (`"clara.api"`), `ops/launcher.py:33` (`"clara.launcher"`), `clara_discord/bot.py:52` (`"clara_discord"`). They sit inside the `clara` hierarchy, so `basicConfig` still routes them, but `__name__` is the documented convention and shows where a line came from.
- Fix: use `logging.getLogger(__name__)` unless the fixed name is a deliberate namespace.
- Ref: https://docs.python.org/3/howto/logging.html

**L2 · Info · Root logger capture.** `ops/logstream.py:111` attaches the log view to the root logger by default, so httpx and discord.py lines are included as well. This looks intentional; it is recorded so it stays intentional.
- Ref: https://docs.python.org/3/howto/logging.html

Silent fallbacks that lose the reason are listed under E3.

---

## 4. Exceptions

Reference: https://docs.python.org/3/tutorial/errors.html

**What works**

- **No bare `except:`** anywhere in `src/`. Most handlers name the exceptions they expect.
- **`raise … from` discipline.** Ruff rule B904 is active and passes. Each re-raise inside an `except` either chains, or drops the chain on purpose with `from None`. The `from None` sites in `api/` are the ones that need logging, and they log through `failure()`.
- **`BaseException` handlers never swallow.** `agent/turn.py:668-671` records the reason and re-raises. `db/connection.py:91-94` rolls back and re-raises. `agent/model.py:189` hands the error to its consumer so that the consumer cannot wait forever.
- **Tool crashes are isolated.** `tools/base.py:208-210` logs the traceback and gives the model a generic message, so internals stay out of the prompt.

**Findings**

**E1 · Medium · Legacy recaps are dropped silently and permanently.** `packs/daily_note.py:51-52` skips any row whose date does not parse (`except ValueError: continue`). `daily_note.py:61` then runs `DROP TABLE recaps_legacy`. A row skipped this way is gone, and nothing is logged. The audit rated this Low. It is rated Medium here because the loss is permanent and invisible.
- Fix: log each skipped row (its id and the bad value), or log a count before the drop. Do not drop the table while any row was skipped.
- Ref: https://docs.python.org/3/tutorial/errors.html

**E2 · Low · Matching on exception text.** `memory/facts.py:199-200` checks `"UNIQUE" in str(error)` to detect a duplicate fact. That depends on SQLite's message wording, which can change across versions or locales.
- Fix: catch the integrity error type that the database layer raises on a unique violation (`sqlite3.IntegrityError` if it is not wrapped), and keep the `raise` for everything else.
- Ref: https://docs.python.org/3/tutorial/errors.html

**E3 · Low · Broad catches hide the reason.** Each of these returns a fallback without a log line:
- `packs/tools.py:58`: `_summariser` returns `None` on any exception.
- `llm/catalog.py:97` and `:101`: model sizes and capabilities silently become empty. A provider that fails to report them looks like one that has none.
- `packs/files.py:267`: any error from `vault.find` becomes `note = path`, which also hides programming errors.
- `agent/turn.py:973-977`: the summariser falls back to another model after a failure, and the first failure is not logged.
- Fix: add `log.info("…: %s", type(error).__name__)`, as the neighbouring sites already do (for example `agent/turn.py:1020-1021`), and narrow the type where a specific one exists.
- Ref: https://docs.python.org/3/tutorial/errors.html

**E4 · Low · Stream lines are dropped silently.** `llm/openai.py:241-242` and `llm/ollama.py:97-98` skip malformed stream lines. Together with the audit's finding that cut replies are stored as complete (`openai.py:241`), a truncated answer looks the same as a full one.
- Fix: count the skipped lines, log a warning when there were any, and read the provider's finish reason.
- Ref: https://docs.python.org/3/tutorial/errors.html

**E5 · Low · Exception text reaches the client.** `api/admin.py:241-242` returns `f"Could not list the releases: {error}"` as a 502 body. The rest of the API sends the type name and logs the detail. The impact is small (a single user, reached over Tailscale), but the convention is worth keeping.
- Fix: `log.exception` and a fixed message, as `failure()` does.
- Ref: https://docs.python.org/3/tutorial/errors.html

**E6 · Info · `assert` used as a runtime check.** There are 23 `assert` statements in `src/`. Ruff rule `S101` is ignored, so the linter does not object. The ones I read (`auth/tokens.py:89`, `:132`, `:143` and `secrets/store.py:116`) narrow types right after a write or load in the same function, and none guards an authorization decision. Asserts are removed under `python -O`, so they must stay type narrowing only.
- Ref: https://docs.python.org/3/library/security_warnings.html

---

## 5. Stdlib security

Reference: https://docs.python.org/3/library/security_warnings.html

**Confirmed absent by search:** `pickle`, `yaml.load`, `eval`/`exec`, `os.system`/`os.popen`, `shell=True`, `tempfile.mktemp`, `random` for tokens, `verify=False` or `CERT_NONE`, MD5, and dynamic code loading from input.

**What works**

- **Secrets store** (`secrets/store.py:150-151`, `:173-174`): AES-GCM, a fresh `os.urandom` nonce for each write, scrypt (N = 2^17) for passphrases, and the secret's name bound as associated data.
- **Tokens and web sessions** (`auth/tokens.py:83`, `auth/web.py:39`): 32 random bytes from `os.urandom`, stored as SHA-256. A plain hash is the right choice for 256-bit random values; a KDF is for low-entropy passwords. Token checks use `hmac.compare_digest` (`tokens.py:110`). Web sessions are looked up by hash (`web.py:51`), so no secret is compared directly.
- **Files on disk** are created `0600` (`ops/private.py`, `db/lock.py:25`, `cli/common.py:53`, `ops/backups.py:169`). Workspace reads use `O_NOFOLLOW` (`workspace/paths.py:61`).
- **Archives:** `ops/updater.py:87` extracts with `filter="data"`.
- **Subprocesses:** every call passes an argv list, with no shell. The sandbox environment is rebuilt from a short allowlist (`workspace/sandbox.py:231`), and the bot's environment drops every `CLARA_*` variable (`ops/discord_bot.py:50`).

**Ignored Ruff rules, checked one by one**

- **`S311` (random):** nothing in `src/` imports `random`.
- **`S603` / `S607`:** every call is an argv list. The git arguments are the exception, and they are covered by S1.
- **`S608` (SQL f-strings):** there are nine f-string SQL sites. Seven interpolate column or table names: `triggers/service.py:263` (keys come from `DEFAULTS`, checked at 247-249), `ops/backups.py:45` (names from `sqlite_master`), `knowledge/projects.py:253` (three literal keys), `memory/facts.py:198`, `memory/conversations.py:250` (allowed set checked at 226-229), `timekeeping/schedules.py:344`, and `timekeeping/tasks.py:476`. The other two (`facts.py:230`, `triggers/service.py:578`) interpolate placeholders only. Values are always bound with `?`. The ignore is justified, but it rests on convention: a future caller that passes request keys straight into one of these would be unsafe. Optional hardening: route column names through one helper that checks them against an allowlist.
- **`S101`:** see E6.

**Findings**

**S1 · Critical · The host's git runs commands planted by sandboxed scripts (still open).** `workspace/store.py:91` (`git_sync`), `:98` (`git`) and `:134` (`git add -A`) run git over a tree that sandboxed scripts can write. A script can create a nested `.git` whose config sets `core.fsmonitor`, and the host's git then runs that command as the `clara` user, outside podman. The audit reproduced this. I confirmed that nothing in `src/` sets `core.fsmonitor=false`: a search for `fsmonitor` returns no matches.
- Fix: pass `-c core.fsmonitor=false` before the subcommand on every git call that touches the workspace:
  ```python
  [self.git_bin, "-C", str(self.root), "-c", "core.fsmonitor=false", *args]
  ```
  Apply the same to the async `git()` helper. Check `knowledge/vault/git.py` for the same exposure; I did not check it.
- Add a regression test: plant a nested repository whose `core.fsmonitor` touches a marker file, call `Workspace.commit()`, and assert that the marker was not created.
- Ref: https://docs.python.org/3/library/security_warnings.html

**S2 · Medium · Archive size checks trust the zip header.** `knowledge/ingest.py:161` (docx) and `:246-248` (zip) compare `file_size` from the archive's central directory against the cap before reading. Whoever makes the archive writes that value. The audit reports a 290 KB file with a declared size of 10 that causes a ~290 MB spike. I did not re-run that case.
- Fix: read through `archive.open()` in chunks and count the bytes against the cap, instead of trusting the declared size.
- Ref: https://docs.python.org/3/library/security_warnings.html

**S3 · Low · `secrets` is imported under the name `random`.** `connectors/setup.py:6` (`import secrets as random`) and `:114` (`random.token_urlsafe(24)`). The call is correct, but a reader will take `random` for the stdlib module that must not be used for tokens.
- Fix: `import secrets` and `secrets.token_urlsafe(24)`.
- Ref: https://docs.python.org/3/library/security_warnings.html

**S4 · Info · XML parsing of an untrusted file.** `knowledge/ingest.py:169` calls `ElementTree.fromstring` on a `.docx`'s `document.xml` under `# noqa: S314`. The comment's reasoning holds: the input is size-capped first, Python's expat includes entity-amplification limits, and ElementTree does not fetch external entities. Acceptable as it is. If the stance tightens, `defusedxml` is the drop-in alternative.
- Ref: https://docs.python.org/3/library/xml.html#xml-vulnerabilities

**S5 · Low · Stale suppressions.** These rules are not selected, so their `noqa` comments do nothing: `agent/model.py:189` (`BLE001`), `app.py:376` (`ARG002`), `ops/launcher.py:107` (`ARG001`), `api/connections.py:242` (`N815`). `ops/launcher.py:126` (`S603`) is also redundant, because `S603` is in the project's ignore list. They are harmless but misleading. Enable `RUF100` to catch them automatically, or delete them.

**Outside `src/` (cross-reference only, not verified here):** the audit notes that `deploy/install.sh:76` passes `CLARA_SECRET_KEY` as a command argument, which makes it visible in `/proc/*/cmdline` while each command runs.

---

## 6. Cross-reference with `docs/audits/audit-2026-10-21.md`

| Audit item | This review |
|---|---|
| C1: `store.py:134`, git runs a planted fsmonitor (critical) | Still open. Confirmed by search. See S1. |
| `packs/daily_note.py:61`, legacy rows dropped (low) | Confirmed. Rated Medium here. See E1. |
| `llm/openai.py:241`, cut replies stored as complete | Silent skip confirmed. See E4. |
| `knowledge/ingest.py:246`, zip size spike | Logic confirmed (it trusts the header). Spike not re-run. See S2. |

The audit's other findings are about concurrency, lifecycle and data integrity. They are outside these three areas.

---

## 7. Other observations from the quality run

- **Format.** 104 of 146 files differ from `ruff format` at the project's 120-column limit. The sample I checked is `clara_discord/bot.py:96`, a long call that the formatter would wrap. The change is style-only. Apply it in its own commit (`uv run ruff format src/`) so it does not mix with logic changes.
- **Dependencies.** `pillow` 12.3.0 → 13.0.0 is a major version and needs a changelog read. `fastapi` 0.143.1 → 0.158.2 and `starlette` 1.8.0 → 1.9.0 need the test suite run afterwards.
- **Tooling gaps.** No type checker and no vulnerability scan ran, so this review has no type-level or CVE evidence. Install them with `uv add --dev mypy pip-audit pytest-cov vulture`.

---

## 8. Suggested order

1. **S1** (Critical). Add `-c core.fsmonitor=false` to every workspace git call, with the regression test.
2. **E1** (Medium). Log and guard the legacy drop before anything else touches that table.
3. **S2** (Medium). Bounded archive reads.
4. **E2, E3, E4, E5** (Low). Narrow the exception types, add the missing log lines, and count skipped stream lines.
5. **L1, S3, S5** (Low). Logger names, the `secrets` import, and stale `noqa` comments.
6. **Tooling.** Run `ruff format` in its own commit. Consider enabling `G` (logging format; zero hits today, so it costs nothing) and `RUF100` (stale suppressions). Do not enable `BLE` yet: it would flag the 54 broad `except Exception` sites, many of which look deliberate.

---

*Generated by a read-only review on 2026-10-21. No source files were changed.*
