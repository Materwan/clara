-- Schema version 5: which projects and schedules show their conversations in the web site's history. Hidden until you say so.
ALTER TABLE projects ADD COLUMN in_history INTEGER NOT NULL DEFAULT 0;
ALTER TABLE schedules ADD COLUMN in_history INTEGER NOT NULL DEFAULT 0;
CREATE INDEX schedules_conversation ON schedules (conversation_id);
