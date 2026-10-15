"""Client tokens: one per client (terminal, console, web, Discord bot, each script).

A token is `clara_<id>_<secret>`: the id finds the row, and the SHA-256 of the 256-bit secret is compared with the
stored one in constant time. Only the hash is kept. Tokens are not accounts: every token has the same power, except
which approval requests it may answer (`approvals`: all, safe = everything but destructive actions, none).

Revoking takes effect at the next request: tokens are read from the database each time, never cached at start.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from dataclasses import dataclass

from ..db import Database
from ..timeutil import now_iso, parse_iso, utcnow

CLIENTS = ("web", "cli", "console", "discord", "script")
APPROVALS = ("all", "safe", "none")
NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
TOKEN = re.compile(r"^clara_(\d{1,12})_([A-Za-z0-9_-]{43})$")
TOUCH_SECONDS = 60  # last_used_at is written at most this often per token


class TokenError(ValueError):
    pass


@dataclass(frozen=True)
class TokenInfo:
    id: int
    name: str
    client: str
    approvals: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None

    def may_answer(self, level: str) -> bool:
        """May this token answer an approval request of this level?"""
        if self.approvals == "all":
            return True
        if self.approvals == "safe":
            return level != "destructive"
        return False

    def describe(self) -> dict:
        return {
            "id": self.id, "name": self.name, "client": self.client, "approvals": self.approvals,
            "created_at": self.created_at, "last_used_at": self.last_used_at, "revoked_at": self.revoked_at,
        }


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _info(row) -> TokenInfo:
    return TokenInfo(row["id"], row["name"], row["client"], row["approvals"], row["created_at"], row["last_used_at"],
                     row["revoked_at"])


class Tokens:
    def __init__(self, db: Database):
        self.db = db

    def create(self, name: str, client: str, approvals: str | None = None) -> tuple[TokenInfo, str]:
        """A new token: its record, and the token itself (shown once, never stored)."""
        name = name.strip().lower()
        if not NAME.match(name):
            raise TokenError("A token name is 1-40 lower-case letters, digits, '.', '_' or '-'.")
        if client not in CLIENTS:
            raise TokenError(f"client must be one of {', '.join(CLIENTS)}.")
        approvals = approvals or ("safe" if client == "discord" else "none" if client == "script" else "all")
        if approvals not in APPROVALS:
            raise TokenError(f"approvals must be one of {', '.join(APPROVALS)}.")
        if self.db.one("SELECT 1 FROM tokens WHERE name = ?", (name,)):
            raise TokenError(f"A token named {name} exists already (revoked ones keep their name).")
        secret = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        token_id = self.db.insert(
            "INSERT INTO tokens (name, client, secret_hash, approvals, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, client, _hash(secret), approvals, now_iso()),
        )
        info = self.get(token_id)
        assert info is not None
        return info, f"clara_{token_id}_{secret}"

    def get(self, token_id: int) -> TokenInfo | None:
        row = self.db.one("SELECT * FROM tokens WHERE id = ?", (token_id,))
        return _info(row) if row else None

    def find(self, name: str) -> TokenInfo | None:
        row = self.db.one("SELECT * FROM tokens WHERE name = ?", (name.strip().lower(),))
        return _info(row) if row else None

    def list(self) -> list[TokenInfo]:
        return [_info(row) for row in self.db.all("SELECT * FROM tokens ORDER BY id")]

    def verify(self, token: str) -> TokenInfo | None:
        """The live token this text is, or None. The comparison of the secret takes the same time whatever it is."""
        match = TOKEN.match(token.strip())
        if match is None:
            return None
        row = self.db.one("SELECT * FROM tokens WHERE id = ?", (int(match.group(1)),))
        expected = row["secret_hash"] if row is not None else _hash("")
        if not hmac.compare_digest(expected, _hash(match.group(2))) or row is None or row["revoked_at"]:
            return None
        info = _info(row)
        self._touch(info)
        return info

    def _touch(self, info: TokenInfo) -> None:
        now = utcnow()
        if info.last_used_at and (now - parse_iso(info.last_used_at)).total_seconds() < TOUCH_SECONDS:
            return
        self.db.execute("UPDATE tokens SET last_used_at = ? WHERE id = ?", (now_iso(), info.id))

    def revoke(self, name: str) -> TokenInfo:
        info = self.find(name)
        if info is None:
            raise TokenError(f"No token named {name}.")
        if info.revoked_at:
            return info
        with self.db.transaction() as tx:
            tx.execute("UPDATE tokens SET revoked_at = ? WHERE id = ?", (now_iso(), info.id))
            tx.execute("DELETE FROM web_sessions WHERE token_id = ?", (info.id,))
        revoked = self.get(info.id)
        assert revoked is not None
        return revoked

    def set_approvals(self, name: str, approvals: str) -> TokenInfo:
        if approvals not in APPROVALS:
            raise TokenError(f"approvals must be one of {', '.join(APPROVALS)}.")
        info = self.find(name)
        if info is None:
            raise TokenError(f"No token named {name}.")
        self.db.execute("UPDATE tokens SET approvals = ? WHERE id = ?", (approvals, info.id))
        updated = self.get(info.id)
        assert updated is not None
        return updated
