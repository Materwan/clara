"""The connections (one GitHub, one Google) and the resources you added to them: GitHub repositories, Drive files and
folders. Clara reaches nothing else: a repository you did not add is refused, even by name."""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ..timeutil import now_iso


@dataclass(frozen=True)
class Resource:
    id: int
    kind: str  # github_repo, drive_file, drive_folder
    locator: str  # owner/repo, or a Drive file id
    label: str
    branch: str
    mime: str
    created_at: str

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "locator": self.locator, "label": self.label, "branch": self.branch,
                "mime": self.mime, "created_at": self.created_at}


def _resource(row) -> Resource:
    return Resource(row["id"], row["kind"], row["locator"], row["label"], row["branch"], row["mime"], row["created_at"])


class Connections:
    def __init__(self, db: Database):
        self.db = db

    def get(self, kind: str) -> dict | None:
        row = self.db.one("SELECT * FROM connections WHERE kind = ?", (kind,))
        return dict(row) if row else None

    def set(self, kind: str, account: str, status: str = "ok") -> dict:
        now = now_iso()
        self.db.execute(
            "INSERT INTO connections (kind, account, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (kind) DO UPDATE SET account = excluded.account, status = excluded.status,"
            " updated_at = excluded.updated_at",
            (kind, account, status, now, now),
        )
        found = self.get(kind)
        assert found is not None
        return found

    def mark(self, kind: str, status: str) -> None:
        self.db.execute("UPDATE connections SET status = ?, updated_at = ? WHERE kind = ?", (status, now_iso(), kind))

    def remove(self, kind: str) -> bool:
        return self.db.execute("DELETE FROM connections WHERE kind = ?", (kind,)).rowcount > 0

    def resources(self, kind: str | None = None) -> list[Resource]:
        if kind:
            rows = self.db.all("SELECT * FROM resources WHERE kind = ? ORDER BY label", (kind,))
        else:
            rows = self.db.all("SELECT * FROM resources ORDER BY kind, label")
        return [_resource(row) for row in rows]

    def add(self, connection: str, kind: str, locator: str, label: str, branch: str = "", mime: str = "") -> Resource:
        found = self.get(connection)
        if found is None:
            raise ValueError(f"Connect {connection} first.")
        self.db.execute(
            "INSERT INTO resources (connection_id, kind, locator, label, branch, mime, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (kind, locator) DO UPDATE SET label = excluded.label, branch = excluded.branch,"
            " mime = excluded.mime",
            (found["id"], kind, locator, label[:200], branch, mime, now_iso()),
        )
        row = self.db.one("SELECT * FROM resources WHERE kind = ? AND locator = ?", (kind, locator))
        return _resource(row)

    def delete(self, resource_id: int) -> bool:
        return self.db.execute("DELETE FROM resources WHERE id = ?", (resource_id,)).rowcount > 0

    def repo(self, name: str) -> Resource | None:
        row = self.db.one("SELECT * FROM resources WHERE kind = 'github_repo' AND lower(locator) = lower(?)", (name,))
        return _resource(row) if row else None
