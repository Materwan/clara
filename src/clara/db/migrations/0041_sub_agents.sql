-- Schema version 41: sub agents (DECISIONS.md, « Sub agents (2026-11-26) »). A sub agent is a conversation like any other, kept in the
-- same tables: `parent_id` is the conversation that started it. A conversation with a parent is left out of the history and of the Discord
-- mirror, and is deleted with its parent (0006 had the same column for the console's sub agents; 0019 dropped it, and this is a new design).
-- Its mode, packs, model, project, incognito and local-only choice are the conversation's own columns, copied from the parent when it starts.
ALTER TABLE conversations ADD COLUMN parent_id TEXT REFERENCES conversations (id) ON DELETE CASCADE;
CREATE INDEX conversations_parent ON conversations (parent_id);

-- What only a sub agent has. `state`: running (a task is being worked on or is queued behind the running one: the queue itself lives in
-- the server's memory, because the parent's call that waits for a task does not outlive a restart), done (it ended with a report), asked
-- (it ended with a question for the parent) or failed (the turn failed or was stopped). `report` is the last report or question as the parent
-- received it (cut at 20,000 characters); the whole text is in the sub agent's conversation.
CREATE TABLE sub_agents (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    state           TEXT NOT NULL CHECK (state IN ('running', 'done', 'asked', 'failed')),
    report          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
