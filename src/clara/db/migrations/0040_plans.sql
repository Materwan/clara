-- Schema version 40: plans (DECISIONS.md, « The plan pack (2026-11-24) »). Clara keeps one plan per conversation with the Plan
-- pack's tools. It is stored here, not only in messages: a summary cuts tool results short and the prompt replays only the last turns.
-- `steps` is a JSON list of {text, status, note, outside}; `outside` says the step was written after the turn read outside content.
-- An empty plan is no row, and the conversation's deletion takes the row.
CREATE TABLE plans (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    steps           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
