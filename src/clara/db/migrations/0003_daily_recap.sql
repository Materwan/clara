-- Schema version 3: the daily recap. One row per local day: the check-in Clara opened, and how it ended.
CREATE TABLE recaps (
    day             TEXT PRIMARY KEY,  -- the local date being recapped, YYYY-MM-DD
    status          TEXT NOT NULL CHECK (status IN ('asking', 'asked', 'written', 'closed', 'failed', 'skipped')),
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,  -- the check-in conversation
    timezone        TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    note_path       TEXT NOT NULL DEFAULT '',  -- the daily note the recap went into
    asked_at        TEXT,
    reminded_at     TEXT,
    finished_at     TEXT,  -- written, closed, failed or skipped
    created_at      TEXT NOT NULL
);
CREATE INDEX recaps_conversation ON recaps (conversation_id);
CREATE INDEX recaps_status ON recaps (status);
