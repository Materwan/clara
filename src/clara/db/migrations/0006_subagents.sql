-- Schema version 6: a conversation may belong to another one. A sub agent of the console works in a conversation of its own,
-- shown inside the conversation that started it (never in the lists), kept and deleted with it.
ALTER TABLE conversations ADD COLUMN parent_id TEXT REFERENCES conversations (id) ON DELETE CASCADE;
CREATE INDEX conversations_parent ON conversations (parent_id);
