"""Token statistics: one `usage_log` row per model call (chat round, title, summary, reminder, task, schedule run).

Token counts only, never text. There are no limits and no credits: these numbers are for you to see what is spent,
per day, model and kind. Rows older than CLARA_RETENTION_DAYS are deleted.
"""

from __future__ import annotations

from datetime import timedelta

from ..db import Database
from ..timeutil import iso, now_iso, utcnow

KINDS = ("chat", "title", "summary", "reminder", "task", "schedule", "hook", "followup")
PERIODS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365, "all": None}
GROUPS = ("day", "model", "kind", "provider", "client")


class UsageLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, *, kind: str, client: str, conversation_id: str | None, provider: str, model: str,
               prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0, duration_ms: int = 0,
               outcome: str = "ok", estimated: bool = False) -> None:
        self.db.execute(
            "INSERT INTO usage_log (at, kind, client, conversation_id, provider, model, prompt_tokens, completion_tokens,"
            " cached_tokens, duration_ms, outcome, estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), kind, client, conversation_id, provider, model, prompt_tokens, completion_tokens,
             min(cached_tokens, prompt_tokens), duration_ms, outcome, int(estimated)),
        )

    def stats(self, period: str = "30d", group: str = "day") -> dict:
        if period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(PERIODS)}")
        if group not in GROUPS:
            raise ValueError(f"group must be one of {', '.join(GROUPS)}")
        days = PERIODS[period]
        since = iso(utcnow() - timedelta(days=days)) if days else ""
        key = {"day": "substr(at, 1, 10)", "model": "provider || ':' || model", "kind": "kind", "provider": "provider",
               "client": "client"}[group]
        rows = self.db.all(
            f"SELECT {key} AS key, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt, SUM(completion_tokens) AS completion,"
            " SUM(cached_tokens) AS cached, SUM(duration_ms) AS duration_ms, SUM(estimated) AS estimated,"
            " SUM(outcome != 'ok') AS failed FROM usage_log WHERE at >= ? GROUP BY key ORDER BY key",
            (since,),
        )
        totals = self.db.one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt, COALESCE(SUM(completion_tokens), 0)"
            " AS completion, COALESCE(SUM(cached_tokens), 0) AS cached FROM usage_log WHERE at >= ?",
            (since,),
        )
        return {"period": period, "group": group, "rows": [dict(row) for row in rows], "totals": dict(totals or {})}

    def history(self, limit: int = 100, before_id: int | None = None) -> list[dict]:
        rows = self.db.all(
            "SELECT * FROM usage_log WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id or 2**62, limit)
        )
        return [dict(row) for row in rows]

    def prune(self, days: int) -> int:
        return self.db.execute("DELETE FROM usage_log WHERE at < ?", (iso(utcnow() - timedelta(days=days)),)).rowcount

    def today(self) -> dict:
        start = now_iso()[:10]
        row = self.db.one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt,"
            " COALESCE(SUM(completion_tokens), 0) AS completion FROM usage_log WHERE at >= ?",
            (start,),
        )
        return dict(row or {})
