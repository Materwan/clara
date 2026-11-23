-- Schema version 38: goals (DECISIONS.md, « /goal (2026-11-23) »). A goal is a condition the owner set on one web conversation: Clara keeps
-- taking turns, without a new message, until a judge finds the condition met or impossible, or a limit is reached. One goal per
-- conversation, so the conversation is the key; `/goal clear` deletes the row and the conversation's deletion takes it too.
CREATE TABLE goals (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    condition       TEXT NOT NULL CHECK (condition <> ''),
    model           TEXT NOT NULL,                  -- provider:model, fixed when the goal starts; the judge uses it too
    max_turns       INTEGER NOT NULL CHECK (max_turns >= 1),
    max_seconds     INTEGER NOT NULL CHECK (max_seconds >= 1),   -- model time only: a pause for an approval does not count
    turns_done      INTEGER NOT NULL DEFAULT 0,
    seconds_used    REAL NOT NULL DEFAULT 0,        -- the turns' model time and the judge's
    state           TEXT NOT NULL CHECK (state IN ('active', 'paused', 'met', 'impossible')),
    reason          TEXT NOT NULL DEFAULT '',       -- why it is paused, or the judge's one sentence (met, impossible, not yet met)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
