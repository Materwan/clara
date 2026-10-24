-- Schema version 14: Brave. A third kind of connection besides GitHub, Google and the mailboxes: the Brave Search API
-- key, whose connection says whether Brave still accepts it.
--
-- `connections.kind` has a CHECK that names the kinds, and SQLite cannot change a CHECK in place: the table is made
-- again, as in version 8. `resources` points at it with ON DELETE CASCADE (and foreign keys are on), so dropping
-- `connections` would delete every repository and Drive file you added. They are copied aside, the two tables are
-- rebuilt in the order that never has a child without its parent, and the copy is put back.
CREATE TABLE resources_keep AS SELECT * FROM resources;
DROP TABLE resources;

CREATE TABLE connections_new (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL UNIQUE CHECK (kind IN ('github', 'google', 'gmail', 'outlook', 'brave')),
    account    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'needs_reconnect')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO connections_new (id, kind, account, status, created_at, updated_at)
    SELECT id, kind, account, status, created_at, updated_at FROM connections;
DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;

CREATE TABLE resources (
    id            INTEGER PRIMARY KEY,
    connection_id INTEGER NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('github_repo', 'drive_file', 'drive_folder')),
    locator       TEXT NOT NULL,  -- owner/repo, or a Drive file id
    label         TEXT NOT NULL,
    branch        TEXT NOT NULL DEFAULT '',
    mime          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (kind, locator)
);
INSERT INTO resources (id, connection_id, kind, locator, label, branch, mime, created_at)
    SELECT id, connection_id, kind, locator, label, branch, mime, created_at FROM resources_keep;
DROP TABLE resources_keep;
