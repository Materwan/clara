-- Schema version 39: dictation per client (DECISIONS.md, « Dictation goes into conversations, per client (2026-11-23) »). One row per
-- client that dictates (`web`, `island`, `phone`, `cli`, `discord`), made on first use: its CURRENT dictation conversation, whether the
-- transcribed text is sent at once (`auto_send`, off at first) and the approval mode and tool packs a new dictation conversation of
-- that client starts with. The conversation's deletion clears `conversation_id`, so the next dictation makes a new one.
CREATE TABLE dictation_clients (
    client          TEXT PRIMARY KEY CHECK (client IN ('web', 'island', 'phone', 'cli', 'discord')),
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    auto_send       INTEGER NOT NULL DEFAULT 0 CHECK (auto_send IN (0, 1)),
    mode            TEXT,                           -- a mode's name; NULL follows the default mode
    packs           TEXT NOT NULL DEFAULT '[]',     -- JSON list of pack ids; empty: the packs a new conversation starts with
    updated_at      TEXT NOT NULL
);
