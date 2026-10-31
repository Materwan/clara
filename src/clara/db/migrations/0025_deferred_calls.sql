-- Schema version 25: tool calls booked for later (DECISIONS.md, « Deferred tool calls »). Clara books a call in a chat, you
-- allow the exact call and its date once, and the server runs it at the due time with these arguments (no model turn).
-- `summary` is what the call said it would do when you read it: it is compared again at the due time. A booked call
-- that a rule asks about at the due time becomes an ordinary approval request (`request_id`); the request that booked it
-- is `approval_id` (NULL when the auto level ran the booking without asking).

-- The time zone of the turn that asked: a request answered after its turn ended is run by the server, and a date in it
-- (a booked call's) must mean what you read ('' for a request that no turn made).
ALTER TABLE approvals ADD COLUMN timezone TEXT NOT NULL DEFAULT '';

CREATE TABLE deferred_calls (
    id              INTEGER PRIMARY KEY,
    tool            TEXT NOT NULL,
    arguments       TEXT NOT NULL,  -- JSON, exactly as you read them
    target          TEXT NOT NULL DEFAULT '',
    level           TEXT NOT NULL CHECK (level IN ('read', 'write', 'destructive')),
    action_class    TEXT NOT NULL,
    summary         TEXT NOT NULL,
    due_at          TEXT NOT NULL,  -- UTC
    timezone        TEXT NOT NULL CHECK (timezone NOT LIKE '+%' AND timezone NOT LIKE '-%' AND timezone != ''),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'held', 'done', 'failed', 'refused', 'expired', 'cancelled')),
    result          TEXT NOT NULL DEFAULT '',
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    local_only      INTEGER NOT NULL DEFAULT 0,
    incognito       INTEGER NOT NULL DEFAULT 0,
    project_id      INTEGER,
    approval_id     INTEGER REFERENCES approvals (id) ON DELETE SET NULL,
    request_id      INTEGER REFERENCES approvals (id) ON DELETE SET NULL,
    booked_at       TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX deferred_calls_due ON deferred_calls (status, due_at);
