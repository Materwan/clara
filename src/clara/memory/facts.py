"""The facts Clara keeps, each with where it came from, in one global memory and one for each project, schedule and hook.

* **scope** `""` is the global memory (about you, true everywhere). `project:<id>`, `schedule:<id>` and `hook:<id>` are
  the memory common to every conversation and run of that project, schedule or hook: what Clara learns in one, the next
  knows. The same sentence may stand in several scopes. A scope's facts go when it is deleted (a trigger of the database).
  A **scoped fact saved in a tainted turn** is stored `tool`, unconfirmed and *hidden*: no conversation sees it, in the
  prompt or in `recall_facts`, until you confirm it (the global memory shows such a fact, tagged). A planted note would
  otherwise steer every later run, not just one.
* **source** `you` (you wrote it: Memory page, `/remember`, the API), `model` (Clara saved it with `remember` in a
  turn that read nothing untrusted) or `tool` (she saved it in a *tainted* turn, after reading a web page, a fetched
  file, an issue…). `tool` facts start **unconfirmed**: they are shown to Clara tagged `[unconfirmed]` until you
  confirm or delete them on the Memory page. A turn is tainted for the whole conversation once any of its turns read
  untrusted content, so the reply that repeated a planted note cannot save it as confirmed later.
* **confirm** is the only thing that confirms a fact: pinning, dating, categorising or editing one leaves it waiting.
* **supersede**: a newer fact can replace an older one (`remember` with `replaces`); the old one is kept as history
  and no longer shown. Only a confirmed fact can be replaced.
* **expiry**: a fact can say until when it holds ("in Lyon until Friday"); once past, it is no longer shown.
* **pinned** facts are always in the prompt first; the others fill the token budget, newest first.
* **forget** deletes the fact and remembers its folded text: Clara cannot save the same fact again by herself
  (you still can).
* Search is SQLite FTS5 (accents and case ignored, prefix matching), ranked by bm25.
* Facts that look like secrets (API keys, private keys, passwords) are refused.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from ..db import Database
from ..secretscan import looks_secret
from ..timeutil import now_iso

MAX_FACT_LENGTH = 300
MAX_CATEGORY = 32
SOURCES = ("you", "model", "tool")
GLOBAL = ""
PROJECT, SCHEDULE, HOOK = "project", "schedule", "hook"
SCOPE_TABLES = {PROJECT: "projects", SCHEDULE: "schedules", HOOK: "triggers"}
MAX_SCOPED_FACTS = 100  # live facts of one project, schedule or hook (the ones waiting for you included)
# ...of which at most this many may wait for your confirmation: a hostile mail cannot fill the memory with them
MAX_WAITING_FACTS = 20


class FactError(ValueError):
    pass


def scope_name(kind: str, number: int) -> str:
    return f"{kind}:{number}"


def split_scope(scope: str) -> tuple[str, int] | None:
    """('hook', 5) for 'hook:5'; None for the global memory or for text that is no scope."""
    kind, _, number = scope.partition(":")
    if kind in SCOPE_TABLES and number.isdecimal():
        return kind, int(number)
    return None


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    category: str
    pinned: bool
    source: str
    confirmed: bool
    conversation_id: str | None
    created_at: str
    confirmed_at: str | None
    expires_at: str | None
    superseded_by: int | None
    scope: str = GLOBAL

    def line(self) -> str:
        """As the prompt shows it."""
        tags = []
        if not self.confirmed:
            tags.append("unconfirmed")
        if self.expires_at:
            tags.append(f"until {self.expires_at[:16].replace('T', ' ')} UTC")
        tag = f" ({', '.join(tags)})" if tags else ""
        return f"- [{self.id}] {self.text}{tag}"

    def describe(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "pinned": self.pinned,
            "source": self.source,
            "confirmed": self.confirmed,
            "conversation": self.conversation_id,
            "created_at": self.created_at,
            "confirmed_at": self.confirmed_at,
            "expires_at": self.expires_at,
            "superseded_by": self.superseded_by,
            "scope": self.scope,
        }


def fold(text: str) -> str:
    """Text as compared for duplicates: no accents, no case, single spaces, no final full stop."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return " ".join(plain.split()).rstrip(" .!")


def fts_query(text: str) -> str:
    """Words for FTS5: each one quoted (no operator injection) and prefix-matched; any of them may match."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " OR ".join(f'"{word}"*' for word in words[:12])


def _fact(row) -> Fact:
    return Fact(
        row["id"],
        row["text"],
        row["category"],
        bool(row["pinned"]),
        row["source"],
        bool(row["confirmed"]),
        row["conversation_id"],
        row["created_at"],
        row["confirmed_at"],
        row["expires_at"],
        row["superseded_by"],
        row["scope"],
    )


LIVE = "superseded_by IS NULL AND (expires_at IS NULL OR expires_at > ?)"


class Facts:
    def __init__(self, db: Database):
        self.db = db

    def add(
        self,
        text: str,
        source: str,
        *,
        category: str = "",
        pinned: bool = False,
        conversation_id: str | None = None,
        expires_at: str | None = None,
        replaces: int | None = None,
        scope: str = GLOBAL,
    ) -> tuple[Fact, bool]:
        """Store a fact in a scope. (the fact, True) when new; (the live fact with the same text, False) when known already."""
        scope = self.check_scope(scope)
        text = " ".join(str(text).split())
        if not text:
            raise FactError("The fact is empty.")
        if len(text) > MAX_FACT_LENGTH:
            raise FactError(f"A fact is at most {MAX_FACT_LENGTH} characters: write one short sentence.")
        if source not in SOURCES:
            raise FactError(f"source must be one of {', '.join(SOURCES)}")
        if looks_secret(text):
            raise FactError("This looks like a secret (a key, a token, a password): secrets are never stored as facts.")
        category = " ".join(str(category or "").split())[:MAX_CATEGORY]
        key = fold(text)
        if source != "you" and self.db.one("SELECT 1 FROM forgotten WHERE scope = ? AND text_key = ?", (scope, key)):
            raise FactError("You asked me to forget this before: I do not save it again unless you add it yourself.")
        existing = self.db.one(
            "SELECT * FROM facts WHERE scope = ? AND text_key = ? AND superseded_by IS NULL", (scope, key)
        )
        if existing is not None:
            return _fact(existing), False
        confirmed = source != "tool"
        if scope and replaces is not None and not confirmed:
            raise FactError("A fact that waits for your confirmation cannot replace another one.")
        now = now_iso()
        with self.db.transaction() as tx:
            if scope:
                self._check_room(tx, scope, waiting=not confirmed, replacing=replaces is not None)
            if replaces is not None:
                old = tx.execute(
                    "SELECT confirmed FROM facts WHERE id = ? AND scope = ? AND superseded_by IS NULL",
                    (replaces, scope),
                ).fetchone()
                if old is None:
                    raise FactError(f"No live fact {replaces} to replace.")
                if not old[0]:
                    raise FactError(
                        f"Fact {replaces} waits for the person's confirmation: it cannot be replaced until then."
                    )
            if source == "you":
                tx.execute("DELETE FROM forgotten WHERE scope = ? AND text_key = ?", (scope, key))
            fact_id = tx.execute(
                "INSERT INTO facts (text, text_key, category, pinned, source, confirmed, conversation_id, created_at,"
                " confirmed_at, expires_at, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    text,
                    key,
                    category,
                    int(pinned),
                    source,
                    int(confirmed),
                    conversation_id,
                    now,
                    now if confirmed else None,
                    expires_at,
                    scope,
                ),
            ).lastrowid
            if replaces is not None:
                tx.execute(
                    "UPDATE facts SET superseded_by = ?, superseded_at = ? WHERE id = ?", (fact_id, now, replaces)
                )
        fact = self.get(int(fact_id or 0))
        assert fact is not None
        return fact, True

    def check_scope(self, scope: str) -> str:
        """The scope is the global memory or a project, schedule or hook that exists. Returns it as facts store it: `project:03`
        and `project:3` are the same memory, and only the canonical spelling is stored, so no fact is left under another name."""
        if scope == GLOBAL:
            return GLOBAL
        found = split_scope(scope)
        if found is None:
            raise FactError("A scope is empty (everything), project:<id>, schedule:<id> or hook:<id>.")
        kind, number = found
        if self.db.one(f"SELECT 1 FROM {SCOPE_TABLES[kind]} WHERE id = ?", (number,)) is None:
            raise FactError(f"No {kind} {number}.")
        return scope_name(kind, number)

    @staticmethod
    def _check_room(tx, scope: str, *, waiting: bool, replacing: bool) -> None:
        now = now_iso()
        total, waits = tx.execute(
            f"SELECT COUNT(*), COALESCE(SUM(confirmed = 0), 0) FROM facts WHERE scope = ? AND {LIVE}", (scope, now)
        ).fetchone()
        taken = total - (1 if replacing else 0)  # a replacement takes the place of the fact it supersedes
        if taken >= MAX_SCOPED_FACTS:
            raise FactError(f"This memory holds {MAX_SCOPED_FACTS} facts already: delete some before adding more.")
        if waiting and waits >= MAX_WAITING_FACTS:
            raise FactError(f"{MAX_WAITING_FACTS} facts already wait for the person's confirmation: no more for now.")

    def get(self, fact_id: int) -> Fact | None:
        row = self.db.one("SELECT * FROM facts WHERE id = ?", (fact_id,))
        return _fact(row) if row else None

    def live(self, limit: int = 500, scope: str = GLOBAL) -> list[Fact]:
        """The facts of a scope that hold now: pinned first, then newest first."""
        rows = self.db.all(
            f"SELECT * FROM facts WHERE scope = ? AND {LIVE} ORDER BY pinned DESC, id DESC LIMIT ?",
            (scope, now_iso(), limit),
        )
        return [_fact(row) for row in rows]

    def shown(self, scope: str, limit: int = 500) -> list[Fact]:
        """What a conversation is shown of a scope: like `live`, but a scoped fact that waits for your confirmation is hidden."""
        return [fact for fact in self.live(limit, scope) if fact.confirmed or scope == GLOBAL]

    def count_live(self, scope: str = GLOBAL) -> int:
        return int(self.db.value(f"SELECT COUNT(*) FROM facts WHERE scope = ? AND {LIVE}", (scope, now_iso()), 0))

    def count_shown(self, scope: str) -> int:
        confirmed = "" if scope == GLOBAL else " AND confirmed = 1"
        return int(
            self.db.value(f"SELECT COUNT(*) FROM facts WHERE scope = ? AND {LIVE}{confirmed}", (scope, now_iso()), 0)
        )

    def scopes(self) -> dict[str, dict[str, int]]:
        """For each project, schedule or hook that keeps a memory: its live facts, how many of them wait for you, and every
        fact it keeps (`total`: expired and replaced ones too, since a delete removes them all)."""
        live = self.db.all(
            "SELECT scope, COUNT(*) AS facts, COALESCE(SUM(confirmed = 0), 0) AS waiting FROM facts"
            f" WHERE scope != '' AND {LIVE} GROUP BY scope",
            (now_iso(),),
        )
        kept = self.db.all("SELECT scope, COUNT(*) AS total FROM facts WHERE scope != '' GROUP BY scope")
        found = {row["scope"]: {"facts": 0, "waiting": 0, "total": row["total"]} for row in kept}
        for row in live:
            found[row["scope"]].update(facts=row["facts"], waiting=row["waiting"])
        return found

    def listing(self, state: str = "live", scope: str = GLOBAL) -> list[Fact]:
        """For the Memory page and the pages of a project or an automation: live, unconfirmed, expired, superseded or all."""
        now = now_iso()
        where = {
            "live": (LIVE, (now,)),
            "unconfirmed": (f"confirmed = 0 AND {LIVE}", (now,)),
            "expired": ("superseded_by IS NULL AND expires_at IS NOT NULL AND expires_at <= ?", (now,)),
            "superseded": ("superseded_by IS NOT NULL", ()),
            "all": ("1", ()),
        }.get(state)
        if where is None:
            raise FactError("state must be live, unconfirmed, expired, superseded or all")
        rows = self.db.all(
            f"SELECT * FROM facts WHERE scope = ? AND {where[0]} ORDER BY pinned DESC, id DESC", (scope, *where[1])
        )
        return [_fact(row) for row in rows]

    def search(
        self,
        query: str,
        limit: int = 10,
        include_old: bool = False,
        scopes: Sequence[str] = (GLOBAL,),
        *,
        for_model: bool = False,
    ) -> list[Fact]:
        """Full-text search in some scopes. `for_model` leaves out the scoped facts that wait for your confirmation."""
        match = fts_query(query)
        if not match or not scopes:
            return []
        where = f"f.scope IN ({','.join('?' * len(scopes))})"
        params: list = [match, *scopes]
        if not include_old:
            where += " AND f.superseded_by IS NULL AND (f.expires_at IS NULL OR f.expires_at > ?)"
            params.append(now_iso())
        if for_model:
            where += " AND (f.scope = '' OR f.confirmed = 1)"
        rows = self.db.all(
            f"SELECT f.* FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? AND {where}"
            " ORDER BY bm25(facts_fts) LIMIT ?",
            (*params, limit),
        )
        return [_fact(row) for row in rows]

    def update(
        self,
        fact_id: int,
        *,
        text: str | None = None,
        category: str | None = None,
        pinned: bool | None = None,
        expires_at: str | None | bool = False,
    ) -> Fact:
        """Your edit of a fact (the Memory page): a new text becomes yours (source `you`). It does not confirm the fact: only
        `confirm` does, so pinning or dating a fact that waits leaves it waiting. `expires_at` False: unchanged."""
        fact = self.get(fact_id)
        if fact is None:
            raise FactError(f"No fact {fact_id}.")
        fields: dict = {}
        if text is not None:
            text = " ".join(text.split())
            if not text or len(text) > MAX_FACT_LENGTH:
                raise FactError(f"A fact is 1 to {MAX_FACT_LENGTH} characters.")
            if looks_secret(text):
                raise FactError("This looks like a secret: secrets are never stored as facts.")
            fields.update(text=text, text_key=fold(text), source="you")
        if category is not None:
            fields["category"] = " ".join(category.split())[:MAX_CATEGORY]
        if pinned is not None:
            fields["pinned"] = int(pinned)
        if expires_at is not False:
            fields["expires_at"] = expires_at
        if not fields:
            return fact
        assignments = ", ".join(f"{name} = ?" for name in fields)
        try:
            self.db.execute(f"UPDATE facts SET {assignments} WHERE id = ?", (*fields.values(), fact_id))
        except Exception as error:
            if "UNIQUE" in str(error):
                raise FactError("Another fact already says exactly this.") from None
            raise
        updated = self.get(fact_id)
        assert updated is not None
        return updated

    def confirm(self, fact_id: int) -> Fact:
        if (
            self.db.execute(
                "UPDATE facts SET confirmed = 1, confirmed_at = ? WHERE id = ? AND confirmed = 0", (now_iso(), fact_id)
            ).rowcount
            == 0
            and self.get(fact_id) is None
        ):
            raise FactError(f"No fact {fact_id}.")
        fact = self.get(fact_id)
        assert fact is not None
        return fact

    def forget(self, fact_id: int) -> bool:
        """Delete a fact and the older ones it replaced; remember its text (in its scope) so Clara does not save it again."""
        fact = self.get(fact_id)
        if fact is None:
            return False
        with self.db.transaction() as tx:
            chain = [fact_id]
            while True:
                older = [
                    row[0]
                    for row in tx.execute(
                        f"SELECT id FROM facts WHERE superseded_by IN ({','.join('?' * len(chain))})", chain
                    ).fetchall()
                    if row[0] not in chain
                ]
                if not older:
                    break
                chain.extend(older)
            tx.execute(
                "INSERT OR REPLACE INTO forgotten (scope, text_key, forgotten_at) VALUES (?, ?, ?)",
                (fact.scope, fold(fact.text), now_iso()),
            )
            tx.execute(f"DELETE FROM facts WHERE id IN ({','.join('?' * len(chain))})", chain)
        return True
