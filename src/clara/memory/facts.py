"""One global memory of facts about you, each with where it came from.

* **source** `you` (you wrote it: Memory page, `/remember`, the API), `model` (Clara saved it with `remember` in a
  turn that read nothing untrusted) or `tool` (she saved it in a *tainted* turn, after reading a web page, a fetched
  file, an issue…). `tool` facts start **unconfirmed**: they are shown to Clara tagged `[unconfirmed]` until you
  confirm or delete them on the Memory page.
* **supersede**: a newer fact can replace an older one (`remember` with `replaces`); the old one is kept as history
  and no longer shown.
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
from dataclasses import dataclass

from ..db import Database
from ..secretscan import looks_secret
from ..timeutil import now_iso

MAX_FACT_LENGTH = 300
MAX_CATEGORY = 32
SOURCES = ("you", "model", "tool")


class FactError(ValueError):
    pass


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
    ) -> tuple[Fact, bool]:
        """Store a fact. (the fact, True) when new; (the live fact with the same text, False) when known already."""
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
        if source != "you" and self.db.one("SELECT 1 FROM forgotten WHERE text_key = ?", (key,)):
            raise FactError("You asked me to forget this before: I do not save it again unless you add it yourself.")
        existing = self.db.one("SELECT * FROM facts WHERE text_key = ? AND superseded_by IS NULL", (key,))
        if existing is not None:
            return _fact(existing), False
        confirmed = source != "tool"
        now = now_iso()
        with self.db.transaction() as tx:
            if replaces is not None:
                old = tx.execute("SELECT id FROM facts WHERE id = ? AND superseded_by IS NULL", (replaces,)).fetchone()
                if old is None:
                    raise FactError(f"No live fact {replaces} to replace.")
            if source == "you":
                tx.execute("DELETE FROM forgotten WHERE text_key = ?", (key,))
            fact_id = tx.execute(
                "INSERT INTO facts (text, text_key, category, pinned, source, confirmed, conversation_id, created_at,"
                " confirmed_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            ).lastrowid
            if replaces is not None:
                tx.execute(
                    "UPDATE facts SET superseded_by = ?, superseded_at = ? WHERE id = ?", (fact_id, now, replaces)
                )
        fact = self.get(int(fact_id or 0))
        assert fact is not None
        return fact, True

    def get(self, fact_id: int) -> Fact | None:
        row = self.db.one("SELECT * FROM facts WHERE id = ?", (fact_id,))
        return _fact(row) if row else None

    def live(self, limit: int = 500) -> list[Fact]:
        """The facts that hold now: pinned first, then newest first."""
        rows = self.db.all(
            f"SELECT * FROM facts WHERE {LIVE} ORDER BY pinned DESC, id DESC LIMIT ?", (now_iso(), limit)
        )
        return [_fact(row) for row in rows]

    def count_live(self) -> int:
        return int(self.db.value(f"SELECT COUNT(*) FROM facts WHERE {LIVE}", (now_iso(),), 0))

    def listing(self, state: str = "live") -> list[Fact]:
        """For the Memory page: live, unconfirmed, expired, superseded or all."""
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
        rows = self.db.all(f"SELECT * FROM facts WHERE {where[0]} ORDER BY pinned DESC, id DESC", where[1])
        return [_fact(row) for row in rows]

    def search(self, query: str, limit: int = 10, include_old: bool = False) -> list[Fact]:
        match = fts_query(query)
        if not match:
            return []
        where = "1" if include_old else "f.superseded_by IS NULL AND (f.expires_at IS NULL OR f.expires_at > ?)"
        params: tuple = (match,) if include_old else (match, now_iso())
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
        """Your edit of a fact (the Memory page): it becomes yours, and confirmed. `expires_at` False: unchanged."""
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
        fields.update(confirmed=1, confirmed_at=fact.confirmed_at or now_iso())
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
        """Delete a fact and the older ones it replaced; remember its text so Clara does not save it again."""
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
                "INSERT OR REPLACE INTO forgotten (text_key, forgotten_at) VALUES (?, ?)", (fold(fact.text), now_iso())
            )
            tx.execute(f"DELETE FROM facts WHERE id IN ({','.join('?' * len(chain))})", chain)
        return True
