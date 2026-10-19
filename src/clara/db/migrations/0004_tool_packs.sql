-- Schema version 4: tool packs (special tools a conversation or a schedule is granted), schedules that can wait for an
-- answer, and the daily recap's table gone (the daily note is a schedule now).
ALTER TABLE conversations ADD COLUMN packs TEXT NOT NULL DEFAULT '["web"]';  -- JSON: the packs switched on
ALTER TABLE schedules ADD COLUMN packs TEXT NOT NULL DEFAULT '["web"]';
ALTER TABLE schedules ADD COLUMN per_run INTEGER NOT NULL DEFAULT 0;  -- 1: every run has its own conversation
ALTER TABLE schedules ADD COLUMN missed TEXT NOT NULL DEFAULT 'skip' CHECK (missed IN ('skip', 'latest', 'all'));
ALTER TABLE schedules ADD COLUMN overlap TEXT NOT NULL DEFAULT 'skip' CHECK (overlap IN ('skip', 'replace', 'queue'));
ALTER TABLE schedules ADD COLUMN model TEXT NOT NULL DEFAULT '';

-- A run can wait for an answer (waiting), be closed without one (closed) or wait its turn (queued); it knows its conversation.
CREATE TABLE schedule_runs_next (
    id              INTEGER PRIMARY KEY,
    schedule_id     INTEGER NOT NULL REFERENCES schedules (id) ON DELETE CASCADE,
    due_at          TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN
                        ('queued', 'running', 'waiting', 'ok', 'closed', 'failed', 'missed', 'skipped', 'interrupted')),
    summary         TEXT NOT NULL DEFAULT '',
    waiting         INTEGER NOT NULL DEFAULT 0,  -- approval requests the run left waiting
    late_seconds    INTEGER NOT NULL DEFAULT 0,
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    waiting_since   TEXT,  -- when it asked and began to wait
    reminded_at     TEXT
);
INSERT INTO schedule_runs_next (id, schedule_id, due_at, started_at, finished_at, status, summary, waiting, late_seconds)
    SELECT id, schedule_id, due_at, started_at, finished_at, status, summary, waiting, late_seconds FROM schedule_runs;
DROP TABLE schedule_runs;
ALTER TABLE schedule_runs_next RENAME TO schedule_runs;
CREATE INDEX schedule_runs_schedule ON schedule_runs (schedule_id, id);
CREATE INDEX schedule_runs_conversation ON schedule_runs (conversation_id);

-- Files Clara made outside the workspace and the vault, which have no history of their own to read back
CREATE TABLE created_files (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('drive')),
    locator         TEXT NOT NULL,  -- the Drive file id
    name            TEXT NOT NULL,
    mime            TEXT NOT NULL DEFAULT '',
    conversation_id TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX created_files_at ON created_files (created_at);

-- The daily recap's own table: kept for one start, so its days can become runs of the daily-note schedule
ALTER TABLE recaps RENAME TO recaps_legacy;
