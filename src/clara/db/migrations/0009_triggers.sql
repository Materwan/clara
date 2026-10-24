-- Schema version 9: hooks (triggers). Something happens outside (a mail arrives): it is stored once as an *event*; each
-- *trigger* you wrote that matches it plans a *run* (a turn of Clara's, or a notification); runs are worked off at the
-- pace the trigger allows. The three tables are what makes a stop harmless: an event and the runs it planned are written in
-- one transaction with the place the source had reached, so a crash loses neither and repeats neither.

-- What happened. (source, key) is unique: the same event seen twice (a restart, a replay) is one event.
CREATE TABLE trigger_events (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,                 -- mail
    kind        TEXT NOT NULL,                 -- mail.received
    key         TEXT NOT NULL,                 -- stable inside the source: gmail:<message id>
    actor       TEXT NOT NULL DEFAULT '',      -- who caused it (a sender's address)
    self_caused INTEGER NOT NULL DEFAULT 0,    -- caused by Clara's own account: dropped unless a trigger asks for it
    title       TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',    -- JSON written by someone else: never trusted
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (source, key)
);
CREATE INDEX trigger_events_received ON trigger_events (received_at);

-- What you wrote: when an event of this kind matches this filter, do this.
CREATE TABLE triggers (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    enabled           INTEGER NOT NULL DEFAULT 1,
    event_kind        TEXT NOT NULL,           -- mail.received
    filter            TEXT NOT NULL DEFAULT '{}',   -- JSON: field -> {eq|contains|starts_with|ends_with|not_contains: ...}
    action            TEXT NOT NULL CHECK (action IN ('run', 'notify')),
    prompt            TEXT NOT NULL DEFAULT '',     -- run: what to do; notify: the text, with {from} {subject}…
    tools             TEXT NOT NULL DEFAULT '[]',   -- JSON: names or globs a run may use ([]: the source's read tools + notify)
    project_id        INTEGER REFERENCES projects (id) ON DELETE SET NULL,   -- the "folder" of the hook's run conversations
    model             TEXT NOT NULL DEFAULT '',
    local_only        INTEGER NOT NULL DEFAULT 0,
    catch_up          TEXT NOT NULL DEFAULT 'all' CHECK (catch_up IN ('all', 'latest', 'digest', 'skip')),
    max_per_hour      INTEGER NOT NULL DEFAULT 20,
    max_age_hours     INTEGER NOT NULL DEFAULT 0,   -- 0: an event never gets too old to run
    include_self      INTEGER NOT NULL DEFAULT 0,
    rerun_interrupted INTEGER NOT NULL DEFAULT 0,
    attempts          INTEGER NOT NULL DEFAULT 3,
    announce          INTEGER NOT NULL DEFAULT 1,   -- tell you when a run ends (a failed run is always told)
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- What was planned and how it went. A digest run has several events.
CREATE TABLE trigger_runs (
    id              INTEGER PRIMARY KEY,
    trigger_id      INTEGER NOT NULL REFERENCES triggers (id) ON DELETE CASCADE,
    event_ids       TEXT NOT NULL,             -- JSON list of trigger_events ids
    status          TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ok', 'failed', 'interrupted', 'skipped', 'expired')),
    note            TEXT NOT NULL DEFAULT '',  -- why it was skipped or expired
    summary         TEXT NOT NULL DEFAULT '',
    queued_at       TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL
);
CREATE INDEX trigger_runs_trigger ON trigger_runs (trigger_id, id);
CREATE INDEX trigger_runs_status ON trigger_runs (status);
