-- Schema version 13: the Discord mirror. Conversations of projects and automations (and the loose ones, and incognito ones)
-- are kept as threads in a forum per project or automation, in Discord. `archived_at` archives a conversation (it can be
-- read, but takes no new message). `mirror_rev` is the revision of the last change the mirror must see: every change
-- takes the next number of `mirror_clock`, so the bot asks for what changed since the last one it saw. A message that came
-- from Discord keeps its Discord id in `ref`, so it is not posted twice.
ALTER TABLE conversations ADD COLUMN archived_at TEXT;
ALTER TABLE conversations ADD COLUMN archived_by TEXT NOT NULL DEFAULT '';  -- '', 'you', 'discord' or 'group' (its switch)
ALTER TABLE conversations ADD COLUMN mirror_rev INTEGER NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN ref TEXT NOT NULL DEFAULT '';

CREATE TABLE mirror_clock (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL
);
-- every conversation that exists now is new to the mirror: the first sync asks for all of them
UPDATE conversations SET mirror_rev = rowid;
INSERT INTO mirror_clock (id, seq) VALUES (1, COALESCE((SELECT MAX(rowid) FROM conversations), 0));

-- a forum (and its category) per place: 'main', 'incognito', 'project:<id>', 'schedule:<id>' or 'hook:<id>'
CREATE TABLE discord_places (
    key         TEXT PRIMARY KEY,
    category_id TEXT NOT NULL DEFAULT '',  -- '' until the bot has made it
    forum_id    TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
-- one thread per conversation: the forum post that holds it
CREATE TABLE discord_threads (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    place_key       TEXT NOT NULL,
    thread_id       TEXT NOT NULL DEFAULT '',
    archived        INTEGER NOT NULL DEFAULT 0  -- as Discord has it, last time the bot said
);
-- the Discord messages written for one turn: its question ('user') and its answer ('reply'), with a digest of the text
CREATE TABLE discord_posts (
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    turn_key        TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('user', 'reply')),
    ids             TEXT NOT NULL,  -- JSON list of Discord message ids, in order
    digest          TEXT NOT NULL,
    PRIMARY KEY (conversation_id, turn_key, side)
);
-- threads of conversations that were deleted: the bot removes them
CREATE TABLE discord_trash (
    thread_id TEXT PRIMARY KEY
);
-- what the mirror remembers between starts: why it stopped, and the last error
CREATE TABLE discord_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
