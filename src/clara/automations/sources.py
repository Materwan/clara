"""Event sources: programs of yours that tell Clara something happened (a script, a home-automation hub, a machine).

A **source** is a record you make: a name, which is also the slug of its kind (`custom.<name>`), the fields an event of it may
carry (text or yes/no, at most 12), how it proves itself (`bearer`: a secret in the `Authorization` header; `github_hmac`: see
below), and a secret held in the encrypted store under `event_source_<id>`, never in the table. It is reached at
`POST /v1/events/<id>`; the id is 128 random bits, so an address nobody was given is a plain 404 that looks nothing up and
counts nothing. An event goes to `Automations.ingest` like a mail or a push: stored once by `(source, key)`, matched against
every automation, planned as runs.

What the body carries was written by whoever holds the secret. Unless the source is **trusted** (only a token that may answer
every request sets it, and only for a source whose proof is a bearer secret), a run its events start is tainted from its first
word, as a GitHub event's is. A source has no read tools: a run it starts has `notify` alone unless the automation lists more.

A **GitHub source** (`github_hmac`) belongs to one repository, `owner/name`, and is the address of that repository's webhook: GitHub signs
the raw body with the source's secret (`X-Hub-Signature-256`), which is checked in constant time before anything is read from the body.
Its events are not a custom kind: `X-GitHub-Event` `push` and `pull_request` become `github.push` and `github.pull_request`, the same
two events with the same fields as the poll makes (connectors/github_events.py), with the delivery id for their key; `ping` is
answered and starts nothing, any other event is ignored, and an event of another repository is refused. It can never be trusted.
While it exists the poll of that repository is off (connectors/github_watch.py).

The limits live here, per source (never per address): ten wrong secrets in a minute and the source answers 429 for a minute,
60 events a minute, 1,000 events waiting for a run.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets as token_source
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..auth import Blocked, FailureLimiter
from ..connectors.github_events import Ignored, Refused, from_webhook, repository_of, short
from ..db import Database
from ..gate import Action, Audit, Decision, Situation
from ..secrets import SecretsStore
from ..secrets.store import EVENT_SOURCE_PREFIX
from ..timeutil import iso, now_iso, parse_iso, utcnow
from .events import EventKind, SourceEvent
from .service import Automations

MAX_SOURCES = 50
MAX_NAME = 30
MAX_FIELDS = 12
MAX_TEXT = 2000  # a text field is cut to this
MAX_KEY = 200
MAX_TITLE = 300
MAX_ACTOR = 200
MAX_WAITING = 1000  # events of a source stored and not yet handled (a queued run wants them): more are refused
MAX_KEPT = 5000  # events of a source kept: past this the oldest that no queued or running run holds are deleted
RATE_PER_MINUTE = 60
BODY_LIMIT = 64 * 1024
MAX_AHEAD = timedelta(days=1)  # an event may not claim to be from further in the future than this
PROOFS = ("bearer", "github_hmac")
TYPES = ("text", "bool")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SIGNATURE = re.compile(r"sha256=([0-9a-fA-F]{64})")  # X-Hub-Signature-256: the hex of an HMAC-SHA256, either case
ID_PATTERN = r"k[0-9a-f]{32}"  # the address: 128 random bits; the middleware lets exactly this shape through
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ENVELOPE = frozenset(
    {"key", "title", "actor", "data", "occurred_at"}
)  # the names of the body itself: a field may not take one
KIND_PREFIX = "custom."
# firing from here proves nothing but your token: an event of a trusted source starts an untainted run, so the token must be able to answer everything
TRUSTED_FIRE = "This token may not fire an event of a trusted source: its runs are not tainted, so it takes a token that may answer every request."
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SourceError(ValueError):
    """A refusal, with the HTTP status it answers (422 unless told) and, for a 429, when to try again."""

    def __init__(self, message: str, status: int = 422, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    fields: dict[str, str]
    proof: str
    trusted: bool
    created_at: str
    created_by: str
    repo: str = ""  # a GitHub source: the repository (owner/name) its webhook is for

    @property
    def github(self) -> bool:
        return self.proof == "github_hmac"

    @property
    def kind(self) -> str:
        """`custom.<name>`; a GitHub source has no kind of its own, its events are `github.*`."""
        return "github" if self.github else KIND_PREFIX + self.name

    @property
    def origin(self) -> str:
        """The `source` its events are stored under: its kind, or for a GitHub source one that says webhook and which repository
        (the poll's events are `github`, and a repository has one webhook source at most)."""
        return f"github-webhook:{self.repo.lower()}" if self.github else self.kind

    @property
    def address(self) -> str:
        return f"/v1/events/{self.id}"


def secret_name(source_id: str) -> str:
    return EVENT_SOURCE_PREFIX + source_id


def slug_of(name: str) -> str:
    """`Front door!` is `front-door`: the part of the kind after `custom.`."""
    plain = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", plain))[:MAX_NAME].strip("-")


def check_fields(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SourceError('fields is an object: {"state": "text", "open": "bool"}.')
    if len(raw) > MAX_FIELDS:
        raise SourceError(f"At most {MAX_FIELDS} fields.")
    for name, type_ in raw.items():
        if not isinstance(name, str) or not FIELD_NAME.match(name):
            raise SourceError("A field name is a lower-case letter, then letters, digits or _ (32 characters at most).")
        if name in ENVELOPE:
            raise SourceError(
                f"{name} is a name of the event itself ({', '.join(sorted(ENVELOPE))}): pick another field name."
            )
        if type_ not in TYPES:
            raise SourceError(f"The type of {name} is text or bool.")
    return dict(raw)


def convert(fields: dict[str, str], pairs: list[str]) -> dict:
    """`state=open open=yes` as the values of the fields of a source (a yes/no field takes yes, no, true, false, 1, 0, on, off)."""
    data: dict = {}
    for pair in pairs:
        name, found, value = pair.partition("=")
        if not found or name not in fields:
            raise SourceError(
                f"{pair!r}: write field=value, one of: {', '.join(fields) or '(this source has no field)'}."
            )
        if fields[name] == "bool":
            words = {
                "yes": True,
                "true": True,
                "1": True,
                "on": True,
                "no": False,
                "false": False,
                "0": False,
                "off": False,
            }
            if value.lower() not in words:
                raise SourceError(f"{name} is yes or no.")
            data[name] = words[value.lower()]
        else:
            data[name] = value
    return data


def _text(value: object, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise SourceError(f"{what} is text.")
    return _CONTROL.sub("", value)[:limit]


class EventSources:
    def __init__(
        self,
        db: Database,
        secrets: SecretsStore,
        automations: Automations,
        audit: Audit | None = None,
        clock: Callable[[], datetime] = utcnow,
        ticks: Callable[[], float] = time.monotonic,
    ):
        self.db = db
        self.secrets = secrets
        self.automations = automations
        self.audit = audit
        self._clock = clock
        self._ticks = ticks
        self.by_id: dict[str, Source] = {}
        # state exists only for a source that exists: an unknown address never reaches either
        self.limiter = FailureLimiter(max_failures=10, window=60.0, block=60.0, clock=ticks)
        self._sent: dict[str, deque[float]] = {}
        self.reload()

    # ------------------------------------------------------------------
    # What exists
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self.by_id = {}
        self.automations.custom.clear()
        for row in self.db.all("SELECT * FROM event_sources ORDER BY created_at, id"):
            self._register(
                Source(
                    row["id"],
                    row["name"],
                    json.loads(row["fields"]),
                    row["proof"],
                    bool(row["trusted"]),
                    row["created_at"],
                    row["created_by"],
                    row["repo"],
                )
            )

    def _register(self, source: Source) -> None:
        """The source exists, and so does its kind (its fields drive the filters; no read tools; tainted unless trusted). A GitHub
        source adds none: `github.push` and `github.pull_request` are there already, and tainted."""
        self.by_id[source.id] = source
        if source.github:
            return
        self.automations.custom[source.kind] = EventKind(
            source.kind,
            f"An event your source “{source.name}” receives",
            dict(source.fields),
            (),
            "{title}",
            noun=f"an event of “{source.name}”",
            tainted=not source.trusted,
        )

    def find(self, ref: str) -> Source | None:
        """A source by its id, its name or its kind."""
        ref = ref.strip()
        if ref in self.by_id:
            return self.by_id[ref]
        name = ref.removeprefix(KIND_PREFIX)
        return next(
            (
                source
                for source in self.by_id.values()
                if source.name == name or (source.github and source.repo.lower() == ref.lower())
            ),
            None,
        )

    def webhook_repos(self) -> set[str]:
        """The repositories (lower case) that have a webhook: GitHub tells Clara of them, so the poll leaves them alone."""
        return {source.repo.lower() for source in self.by_id.values() if source.github}

    def describe(self, source: Source) -> dict:
        row = self.db.one(
            "SELECT failures, last_failure_at, last_event_at FROM event_sources WHERE id = ?", (source.id,)
        )
        return {
            "id": source.id,
            "name": source.name,
            "kind": source.kind,
            "repo": source.repo,
            "fields": dict(source.fields),
            "proof": source.proof,
            "trusted": source.trusted,
            "can_trust": source.proof == "bearer",
            "address": source.address,
            "events": int(self.db.value("SELECT COUNT(*) FROM trigger_events WHERE source = ?", (source.origin,), 0)),
            "waiting": self.waiting(source),
            "failures": row["failures"] if row else 0,
            "last_failure_at": row["last_failure_at"] if row else None,
            "last_event_at": row["last_event_at"] if row else None,
            "created_at": source.created_at,
            "created_by": source.created_by,
        }

    def listing(self) -> list[dict]:
        return [self.describe(source) for source in self.by_id.values()]

    def waiting(self, source: Source) -> int:
        """Events of this source that a queued run still wants: stored, and not yet handled."""
        return int(
            self.db.value(
                "SELECT COUNT(DISTINCT e.value) FROM automation_runs r, json_each(r.event_ids) e"
                " JOIN trigger_events t ON t.id = e.value WHERE r.status = 'queued' AND t.source = ?",
                (source.origin,),
                0,
            )
        )

    def _audit(self, detail: str) -> None:
        """Who made, changed or revoked a source, in the audit log. Never an address or a secret."""
        if self.audit is not None:
            self.audit.record(
                Action("event_source", "write", "memory", ("event_source",), detail),
                Situation("interactive"),
                "",
                Decision("allow", "you changed it"),
                "event_source",
                detail=detail,
            )

    # ------------------------------------------------------------------
    # Making, changing, revoking (the routes allow only a token that may answer every request)
    # ------------------------------------------------------------------
    def create(
        self, name: str, fields: object, *, trusted: bool = False, by: str = "", repo: str = ""
    ) -> tuple[Source, str]:
        """A new source and its secret, which is returned once and kept only in the encrypted store. With a `repo` (owner/name) it is a
        GitHub source: its name defaults to `github-<owner>-<name>`, it has no fields, and it can never be trusted."""
        if repo:
            repo = repo.strip()
            if not REPO.match(repo):
                raise SourceError("A GitHub source takes a repository as owner/name.")
            if trusted:
                raise SourceError(
                    "This source cannot be trusted: only a source whose sender you control (a bearer secret) can."
                )
            if fields:
                raise SourceError("A GitHub source has no fields: its events are github.push and github.pull_request.")
            if repo.lower() in self.webhook_repos():
                raise SourceError(f"{repo} has a webhook source already.", 409)
            slug = slug_of(name or "github " + repo.replace("/", " "))
            found: dict[str, str] = {}
        else:
            slug = slug_of(name)
            found = check_fields(fields)
        if not slug:
            raise SourceError("A source needs a name with letters or digits in it.")
        if len(self.by_id) >= MAX_SOURCES:
            raise SourceError(f"At most {MAX_SOURCES} sources: revoke one first.", 409)
        if any(source.name == slug for source in self.by_id.values()):
            raise SourceError(f"A source named {slug} exists already.", 409)
        proof = "github_hmac" if repo else "bearer"
        source = Source("k" + token_source.token_hex(16), slug, found, proof, trusted, now_iso(), by[:40], repo)
        secret = "evt_" + token_source.token_urlsafe(32)
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO event_sources (id, name, fields, proof, trusted, created_at, created_by, repo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source.id,
                    source.name,
                    json.dumps(found),
                    source.proof,
                    int(trusted),
                    source.created_at,
                    source.created_by,
                    source.repo,
                ),
            )
            self.secrets.set(secret_name(source.id), secret)
        self._register(source)
        detail = f" (the GitHub webhook of {repo})" if repo else " (trusted)" if trusted else ""
        self._audit(f"{by or 'someone'} made the event source {slug}{detail}")
        return source, secret

    def rotate(self, source: Source, by: str = "") -> str:
        """A new secret: the old one stops working at once."""
        secret = "evt_" + token_source.token_urlsafe(32)
        self.secrets.set(secret_name(source.id), secret)
        self.limiter.forget(source.id)
        self._audit(f"{by or 'someone'} changed the secret of the event source {source.name}")
        return secret

    def set_trusted(self, source: Source, trusted: bool, by: str = "") -> Source:
        if trusted and source.proof != "bearer":
            raise SourceError(
                "This source cannot be trusted: only a source whose sender you control (a bearer secret) can."
            )
        self.db.execute("UPDATE event_sources SET trusted = ? WHERE id = ?", (int(trusted), source.id))
        changed = replace(source, trusted=trusted)
        self._register(changed)
        self._audit(f"{by or 'someone'} {'trusted' if trusted else 'stopped trusting'} the event source {source.name}")
        return changed

    def revoke(self, source: Source, by: str = "") -> None:
        """Delete the source, its secret and the events no run still waits for. No other source and no token is touched; an
        automation that waits for its kind stays, and never fires. A GitHub source also hands its repository back to the poll, which
        starts from this moment: what the webhook delivered is not found again."""
        with self.db.transaction():
            self.db.execute("DELETE FROM event_sources WHERE id = ?", (source.id,))
            self.secrets.unset(secret_name(source.id))
            self.db.execute(
                "DELETE FROM trigger_events WHERE source = ? AND id NOT IN ("
                "SELECT e.value FROM automation_runs r, json_each(r.event_ids) e WHERE r.status IN ('queued', 'running'))",
                (source.origin,),
            )
            if source.github:
                self._resume_poll(source.repo)
        self.by_id.pop(source.id, None)
        if not source.github:
            self.automations.custom.pop(source.kind, None)
        self.limiter.forget(source.id)
        self._sent.pop(source.id, None)
        self._audit(f"{by or 'someone'} revoked the event source {source.name}")

    def _resume_poll(self, repo: str) -> None:
        """The repository is polled again from now: the place of its pull request stream is this moment (the first look of a stream
        would reach back STALE_AFTER, and find what the webhook delivered). A repository that is not one you added has no poll."""
        row = self.db.one(
            "SELECT locator FROM resources WHERE kind = 'github_repo' AND lower(locator) = lower(?)", (repo,)
        )
        if row is not None:
            now = now_iso()
            self.db.execute(
                "INSERT INTO github_cursors (repo, stream, state, etag, updated_at) VALUES (?, 'pulls', ?, '', ?)"
                " ON CONFLICT (repo, stream) DO UPDATE SET state = excluded.state, etag = '', updated_at = excluded.updated_at",
                (row["locator"], json.dumps({"since": now, "from": now}), now),
            )

    # ------------------------------------------------------------------
    # A call arrives
    # ------------------------------------------------------------------
    def check_open(self, source: Source) -> None:
        """SourceError 429 while this source is blocked for wrong secrets or signatures (nothing of the call has been read)."""
        try:
            self.limiter.check(source.id)
        except Blocked as error:
            raise SourceError(
                "Too many wrong secrets for this source: try again later.", 429, error.retry_after
            ) from None

    def _refuse(self, source: Source) -> None:
        """A call that did not prove itself: counted against this source and no other, answered 401 with no detail."""
        self.limiter.fail(source.id)
        self.db.execute(
            "UPDATE event_sources SET failures = failures + 1, last_failure_at = ? WHERE id = ?",
            (now_iso(), source.id),
        )
        raise SourceError("Not authorized.", 401)

    def admit(self, source: Source, authorization: str | None) -> None:
        """The call proves itself, or SourceError: 429 while this source is blocked, 401 (no detail) for a wrong or missing
        secret, which counts against this source and no other. The comparison takes the same time whatever is sent."""
        self.check_open(source)
        header = authorization or ""
        given = header[7:].strip() if header[:7].lower() == "bearer " else ""
        expected = self.secrets.get(secret_name(source.id)) or ""
        if not expected or not hmac.compare_digest(expected.encode(), given.encode()):
            self._refuse(source)

    def admit_signed(self, source: Source, signature: str | None, body: bytes) -> None:
        """A GitHub call proves itself: `X-Hub-Signature-256` is `sha256=` and the HMAC-SHA256 of the raw body with the source's secret
        (hex, either case). Checked before anything is read from the body, compared in constant time; a wrong, missing or malformed
        signature is a 401 with no detail, counted against this source like a wrong bearer secret."""
        self.check_open(source)
        expected = self.secrets.get(secret_name(source.id)) or ""
        found = SIGNATURE.fullmatch(signature or "")
        if not expected or found is None:
            self._refuse(source)
            return
        digest = hmac.new(expected.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest.encode(), found.group(1).lower().encode()):
            self._refuse(source)

    def accept(self, source: Source, raw: bytes) -> tuple[int | None, bool]:
        """Check, store and match one event. (its id, False) for a new one, (None, True) for one already stored."""
        if source.github:
            raise SourceError(
                "The events of a GitHub source come from GitHub (its webhook): there is nothing to send here."
            )
        self._pace(source)
        return self._keep(source, self._read(source, raw))

    def _keep(self, source: Source, event: SourceEvent) -> tuple[int | None, bool]:
        if self.waiting(source) > MAX_WAITING:
            raise SourceError(
                f"More than {MAX_WAITING} events of this source wait for a run: try again later.", 429, 60
            )
        if self.db.one("SELECT 1 FROM trigger_events WHERE source = ? AND key = ?", (event.source, event.key)):
            return None, True
        with self.db.transaction():  # the event, its runs and the cap stand or fall together
            self.automations.ingest([event])
            self._trim(source)
        found = self.db.one("SELECT id FROM trigger_events WHERE source = ? AND key = ?", (event.source, event.key))
        self.db.execute("UPDATE event_sources SET last_event_at = ? WHERE id = ?", (now_iso(), source.id))
        return int(found["id"]), False

    def accept_github(self, source: Source, event: str | None, delivery: str | None, raw: bytes) -> tuple[int, dict]:
        """One delivery of a GitHub webhook whose signature was checked: (status, answer). `ping` is a 200 `pong`, an event that is not a
        push or a pull request is ignored (202 `ignored`), a delivery of another repository is a 422, a delivery stored before is a 200
        `duplicate` (its id is the key), and a new one is stored and matched, 202 with its id. Nothing slow happens here: the runs are
        planned, and the loops start them."""
        self._pace(source)
        name = (event or "").strip()
        if not name:
            raise SourceError("X-GitHub-Event is missing.")
        if name not in ("ping", "push", "pull_request"):
            return 202, {"ignored": short(name, 40)}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            raise SourceError("The body is not JSON.", 400) from None
        if not isinstance(payload, dict):
            raise SourceError("The body is a JSON object.")
        named = repository_of(payload)
        if named.lower() != source.repo.lower() and (named or name != "ping"):
            raise SourceError(
                f"This source takes the events of {source.repo}, not of {short(named, 100) or 'no repository'}."
            )
        if name == "ping":
            return 200, {"pong": True}
        key = delivery or ""
        if not 1 <= len(key) <= MAX_KEY or _CONTROL.search(key):
            raise SourceError(f"X-GitHub-Delivery is missing or is not a text of 1 to {MAX_KEY} characters.")
        now = self._clock()
        try:
            found = from_webhook(name, payload, source.repo, source.origin, key, iso(now))
            if parse_iso(found.occurred_at) > now + MAX_AHEAD:
                raise Refused("The event is dated more than a day ahead.")
        except Ignored as why:
            return 202, {"ignored": str(why)}
        except Refused as error:
            raise SourceError(str(error)) from None
        stored, duplicate = self._keep(source, found)
        return (200, {"duplicate": True}) if duplicate else (202, {"id": stored})

    def _trim(self, source: Source) -> None:
        """A source keeps at most MAX_KEPT events: past that the oldest are deleted, except those a queued or running run holds.
        Done in the transaction of the ingest. The key of a deleted event can be accepted again as a new event."""
        if int(self.db.value("SELECT COUNT(*) FROM trigger_events WHERE source = ?", (source.origin,), 0)) <= MAX_KEPT:
            return
        self.db.execute(
            "DELETE FROM trigger_events WHERE source = ?"
            " AND id NOT IN (SELECT id FROM trigger_events WHERE source = ? ORDER BY id DESC LIMIT ?)"
            " AND id NOT IN (SELECT e.value FROM automation_runs r, json_each(r.event_ids) e"
            " WHERE r.status IN ('queued', 'running'))",
            (source.origin, source.origin, MAX_KEPT),
        )

    def _pace(self, source: Source) -> None:
        now = self._ticks()
        sent = self._sent.setdefault(source.id, deque())
        while sent and sent[0] <= now - 60:
            sent.popleft()
        if len(sent) >= RATE_PER_MINUTE:
            raise SourceError(
                f"Too many events: at most {RATE_PER_MINUTE} a minute for one source.", 429, int(sent[0] + 60 - now) + 1
            )
        sent.append(now)

    def _read(self, source: Source, raw: bytes) -> SourceEvent:
        """The body as an event: `{"key"?, "title"?, "occurred_at"?, "actor"?, "data": {declared fields}}`. Anything else is a 422."""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            raise SourceError("The body is not JSON.", 400) from None
        if not isinstance(body, dict):
            raise SourceError("The body is a JSON object.")
        unknown = sorted(set(body) - ENVELOPE)
        if unknown:
            raise SourceError(
                f"Not part of an event: {_shown(unknown)} (an event is key, title, occurred_at, actor and data)."
            )
        data_in = {} if body.get("data") is None else body["data"]
        if not isinstance(data_in, dict):
            raise SourceError("data is an object of the declared fields.")
        undeclared = sorted(set(data_in) - set(source.fields))
        if undeclared:
            raise SourceError(
                f"Not a field of this source: {_shown(undeclared)} (its fields: {', '.join(source.fields) or 'none'})."
            )
        data: dict = {}
        for name, value in data_in.items():
            if source.fields[name] == "bool":
                if not isinstance(value, bool):
                    raise SourceError(f"{name} is true or false.")
                data[name] = value
            else:
                data[name] = _text(value, MAX_TEXT, name)
        now = self._clock()
        if body.get("occurred_at") is None:
            occurred = now
        else:
            try:
                occurred = parse_iso(_text(body["occurred_at"], 40, "occurred_at"))
            except (ValueError, OverflowError):
                raise SourceError("occurred_at is a date and time (2026-11-13T19:30:00+01:00).") from None
            if occurred > now + MAX_AHEAD:
                raise SourceError("occurred_at is more than a day ahead.")
        key = body.get("key")
        if key is None:
            key = hashlib.sha256(raw).hexdigest()
        elif not isinstance(key, str) or not 1 <= len(key) <= MAX_KEY or _CONTROL.search(key):
            raise SourceError(f"key is a text of 1 to {MAX_KEY} characters.")
        title = " ".join(_text(body.get("title") or "", MAX_TITLE, "title").split())
        if not title:  # the notification then says which source, and what its first text field said
            first = next(
                (
                    " ".join(data[name].split())
                    for name, kind in source.fields.items()
                    if kind == "text" and data.get(name)
                ),
                "",
            )
            title = f"{source.name}: {first}"[:MAX_TITLE] if first else source.name
        return SourceEvent(
            source=source.kind,
            kind=source.kind,
            key=key,
            occurred_at=iso(occurred),
            actor=" ".join(_text(body.get("actor") or "", MAX_ACTOR, "actor").split()),
            title=title,
            data=data,
        )


def _shown(names: list[str]) -> str:
    """Names the sender chose, in a refusal to the same sender: a few, and short."""
    return ", ".join(name[:40] for name in names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
