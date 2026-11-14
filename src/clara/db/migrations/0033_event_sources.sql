-- Schema version 33: event sources (DECISIONS.md, « Event sources (2026-11-13) »). A source is a record you make: a name (the slug
-- of its kind, `custom.<name>`), the fields an event of it may carry, how it proves itself, and a secret that is NOT here: it is in
-- the encrypted store, under `event_source_<id>`. Its address is `POST /v1/events/<id>`; the id is random, so an address nobody was
-- given is a plain 404. Revoking a source deletes this row and its secret and touches nothing else.
CREATE TABLE event_sources (
    id              TEXT PRIMARY KEY CHECK (length(id) = 33 AND id GLOB 'k*'),  -- k + 32 random hex characters (128 bits)
    name            TEXT NOT NULL UNIQUE CHECK (name <> ''),                    -- a slug: lower-case letters, digits, '-'
    fields          TEXT NOT NULL DEFAULT '{}',        -- JSON: field -> 'text' | 'bool' (what an event may carry, what a filter may look at)
    proof           TEXT NOT NULL DEFAULT 'bearer',    -- how a call proves itself: 'bearer' (a secret in the Authorization header); room for later ones
    trusted         INTEGER NOT NULL DEFAULT 0 CHECK (trusted IN (0, 1)),   -- 1: a run its events start is not tainted
    failures        INTEGER NOT NULL DEFAULT 0,        -- calls that came with a wrong or no secret, in all
    last_failure_at TEXT,
    last_event_at   TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',          -- the name of the token that made it
    CHECK (trusted = 0 OR proof = 'bearer')            -- only a source whose sender you control (a bearer secret) may be trusted
);
