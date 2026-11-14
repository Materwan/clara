-- Schema version 37: the GitHub webhook adapter (DECISIONS.md, « The events door (2026-11-13) »). An event source may now prove itself
-- with `github_hmac`: GitHub signs the raw body with the source's secret (X-Hub-Signature-256), and the source belongs to one
-- repository, `owner/name`, which is the new `repo` column. A bearer source has no repository, a GitHub source always has one,
-- and only a bearer source may be trusted (the rule of 0033 stays). `proof` could be any text before; it is now one of the two
-- proofs the code knows, so a row of an unknown proof cannot exist. One webhook per repository (case does not count): the poll of
-- that repository is off while it exists (connectors/github_watch.py).
-- SQLite cannot add a CHECK to a table, so the table is made again; its rows are copied as they are.
CREATE TABLE event_sources_new (
    id              TEXT PRIMARY KEY CHECK (length(id) = 33 AND id GLOB 'k*'),  -- k + 32 random hex characters (128 bits)
    name            TEXT NOT NULL UNIQUE CHECK (name <> ''),                    -- a slug: lower-case letters, digits, '-'
    fields          TEXT NOT NULL DEFAULT '{}',        -- JSON: field -> 'text' | 'bool' (a GitHub source has none: its kinds are github.*)
    proof           TEXT NOT NULL DEFAULT 'bearer' CHECK (proof IN ('bearer', 'github_hmac')),
    trusted         INTEGER NOT NULL DEFAULT 0 CHECK (trusted IN (0, 1)),   -- 1: a run its events start is not tainted
    failures        INTEGER NOT NULL DEFAULT 0,        -- calls that came with a wrong or no secret or signature, in all
    last_failure_at TEXT,
    last_event_at   TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',          -- the name of the token that made it
    repo            TEXT NOT NULL DEFAULT '',          -- github_hmac: the repository (owner/name); '' for a bearer source
    CHECK (trusted = 0 OR proof = 'bearer'),           -- only a source whose sender you control (a bearer secret) may be trusted
    CHECK ((proof = 'github_hmac') = (repo <> ''))     -- a GitHub source has a repository, and only a GitHub source has one
);
INSERT INTO event_sources_new (id, name, fields, proof, trusted, failures, last_failure_at, last_event_at, created_at, created_by)
    SELECT id, name, fields, proof, trusted, failures, last_failure_at, last_event_at, created_at, created_by FROM event_sources;
DROP TABLE event_sources;
ALTER TABLE event_sources_new RENAME TO event_sources;
CREATE UNIQUE INDEX event_sources_repo ON event_sources (lower(repo)) WHERE repo <> '';
