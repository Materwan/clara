"""The browser's sign-in.

You paste a token once on the sign-in page; the server answers with a cookie that stands for that token
(HttpOnly, Secure, SameSite=Strict). Every request made with the cookie must also carry `X-Clara-Web: 1`, which a
form or a page on another site cannot add, and CORS stays off: another site cannot act with your cookie.

A web session lives 30 days without use and 90 days at most, and dies when its token is revoked.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import timedelta

from ..db import Database
from ..timeutil import iso, now_iso, parse_iso, utcnow
from .tokens import TokenInfo, Tokens

COOKIE = "clara_session"
HEADER = "X-Clara-Web"
IDLE_DAYS = 30
MAX_DAYS = 90
TOUCH_SECONDS = 300


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class WebSessions:
    def __init__(self, db: Database, tokens: Tokens):
        self.db = db
        self.tokens = tokens

    def start(self, token: TokenInfo) -> str:
        """A new session for a verified token: the cookie value."""
        secret = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        now = utcnow()
        self.db.execute(
            "INSERT INTO web_sessions (token_id, secret_hash, created_at, last_used_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token.id, _hash(secret), iso(now), iso(now), iso(now + timedelta(days=MAX_DAYS))),
        )
        return secret

    def verify(self, cookie: str) -> tuple[int, TokenInfo] | None:
        """(session id, its token) for a live cookie, else None (expired sessions are deleted as they are found)."""
        if not cookie or len(cookie) > 100:
            return None
        row = self.db.one("SELECT * FROM web_sessions WHERE secret_hash = ?", (_hash(cookie),))
        if row is None:
            return None
        now = utcnow()
        idle = now - parse_iso(row["last_used_at"])
        if now >= parse_iso(row["expires_at"]) or idle > timedelta(days=IDLE_DAYS):
            self.db.execute("DELETE FROM web_sessions WHERE id = ?", (row["id"],))
            return None
        token = self.tokens.get(row["token_id"])
        if token is None or token.revoked_at:
            return None
        if idle.total_seconds() > TOUCH_SECONDS:
            self.db.execute("UPDATE web_sessions SET last_used_at = ? WHERE id = ?", (now_iso(), row["id"]))
        return row["id"], token

    def end(self, session_id: int) -> None:
        self.db.execute("DELETE FROM web_sessions WHERE id = ?", (session_id,))

    def prune(self) -> int:
        now = utcnow()
        return self.db.execute(
            "DELETE FROM web_sessions WHERE expires_at <= ? OR last_used_at <= ?",
            (iso(now), iso(now - timedelta(days=IDLE_DAYS))),
        ).rowcount
