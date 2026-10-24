-- Schema version 1: the whole of Clara's memory, for one person.
-- Conventions: instants are UTC ISO text with their offset; wall-clock moments are a naive local date-time with an
-- IANA zone name (never an offset); booleans are 0/1; JSON is stored as text.

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,  -- JSON
    updated_at TEXT NOT NULL
);

-- Client tokens: not accounts. All have the same power, except which approvals they may answer.
CREATE TABLE tokens (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    client       TEXT NOT NULL,  -- web, cli, console, discord, script: the label shown with what it sends
    secret_hash  TEXT NOT NULL,  -- SHA-256 of the secret part, hex
    approvals    TEXT NOT NULL DEFAULT 'all' CHECK (approvals IN ('all', 'safe', 'none')),
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

-- The browser's sign-in: a cookie that stands for one token, and dies with it.
CREATE TABLE web_sessions (
    id           INTEGER PRIMARY KEY,
    token_id     INTEGER NOT NULL REFERENCES tokens (id) ON DELETE CASCADE,
    secret_hash  TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX web_sessions_token ON web_sessions (token_id);

-- The encrypted secrets store (AES-GCM). The key is never here: only how it is derived and a value that proves it.
CREATE TABLE secrets_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    kdf         TEXT NOT NULL CHECK (kdf IN ('scrypt', 'raw')),
    salt        BLOB,
    n           INTEGER,
    r           INTEGER,
    p           INTEGER,
    check_nonce BLOB NOT NULL,
    check_value BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    rotated_at  TEXT
);
CREATE TABLE secrets (
    name       TEXT PRIMARY KEY,
    nonce      BLOB NOT NULL,
    ciphertext BLOB NOT NULL,  -- the secret's name is the associated data: a value cannot be moved to another name
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Knowledge: projects hold copies of files (uploads, zips, GitHub snapshots)
CREATE TABLE projects (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    pinned       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE project_sources (
    id         INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('github')),
    repo       TEXT NOT NULL,
    ref        TEXT NOT NULL DEFAULT '',
    folder     TEXT NOT NULL,
    commit_sha TEXT NOT NULL DEFAULT '',
    synced_at  TEXT,
    skipped    INTEGER NOT NULL DEFAULT 0,
    problem    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX project_sources_project ON project_sources (project_id);
CREATE TABLE project_files (
    id         INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    source_id  INTEGER REFERENCES project_sources (id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    size       INTEGER NOT NULL,
    added_at   TEXT NOT NULL,
    UNIQUE (project_id, path)
);
CREATE INDEX project_files_source ON project_files (source_id);

-- Conversations: one shared list for every client; the client that started each is a label
CREATE TABLE conversations (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    titled_by      TEXT NOT NULL DEFAULT '' CHECK (titled_by IN ('', 'clara', 'you')),
    client         TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat', 'schedule')),
    pinned         INTEGER NOT NULL DEFAULT 0,
    project_id     INTEGER REFERENCES projects (id) ON DELETE SET NULL,
    model          TEXT NOT NULL DEFAULT '',  -- provider:model of this conversation ('': the default model)
    incognito      INTEGER NOT NULL DEFAULT 0,  -- no facts read or written; deleted after CLARA_INCOGNITO_HOURS idle
    local_only     INTEGER NOT NULL DEFAULT 0,  -- answered by local Ollama hosts only, fallbacks and side jobs too
    vault_open     INTEGER NOT NULL DEFAULT 0,
    summary        TEXT NOT NULL DEFAULT '',
    summary_upto   INTEGER NOT NULL DEFAULT 0,  -- the last message id the summary stands for
    context_tokens INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX conversations_listing ON conversations (pinned, updated_at);
CREATE INDEX conversations_project ON conversations (project_id);

-- Messages form a tree (parent_id) so a question can be asked again; `active` marks the path the conversation follows
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    parent_id       INTEGER REFERENCES messages (id) ON DELETE SET NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content         TEXT NOT NULL,
    prefix          TEXT NOT NULL DEFAULT '',
    tool_calls      TEXT,
    tool_name       TEXT,
    thinking        TEXT,
    client          TEXT NOT NULL DEFAULT '',
    turn_id         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX messages_path ON messages (conversation_id, active, id);
CREATE INDEX messages_parent ON messages (parent_id);
CREATE VIRTUAL TABLE messages_fts USING fts5 (
    content, content = 'messages', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages WHEN new.role IN ('user', 'assistant') BEGIN
    INSERT INTO messages_fts (rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages WHEN old.role IN ('user', 'assistant') BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER messages_fts_update AFTER UPDATE OF content ON messages WHEN old.role IN ('user', 'assistant') BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts (rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE conversation_files (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('text', 'document', 'picture')),
    mime            TEXT NOT NULL DEFAULT '',
    digest          TEXT NOT NULL,
    size            INTEGER NOT NULL,
    data            BLOB NOT NULL,
    client          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, digest)
);

-- One global memory of facts, each with where it came from
CREATE TABLE facts (
    id              INTEGER PRIMARY KEY,
    text            TEXT NOT NULL,
    text_key        TEXT NOT NULL,  -- the text folded (case, accents, spaces) to find duplicates
    category        TEXT NOT NULL DEFAULT '',
    pinned          INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL CHECK (source IN ('you', 'model', 'tool')),
    confirmed       INTEGER NOT NULL DEFAULT 1,
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    confirmed_at    TEXT,
    expires_at      TEXT,
    superseded_by   INTEGER REFERENCES facts (id) ON DELETE SET NULL,
    superseded_at   TEXT
);
CREATE UNIQUE INDEX facts_live_key ON facts (text_key) WHERE superseded_by IS NULL;
CREATE VIRTUAL TABLE facts_fts USING fts5 (
    text, content = 'facts', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER facts_fts_insert AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER facts_fts_delete AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts (facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER facts_fts_update AFTER UPDATE OF text ON facts BEGIN
    INSERT INTO facts_fts (facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO facts_fts (rowid, text) VALUES (new.id, new.text);
END;
-- Texts you asked Clara to forget: she cannot save the same fact again by herself
CREATE TABLE forgotten (
    text_key     TEXT PRIMARY KEY,
    forgotten_at TEXT NOT NULL
);

-- Time
CREATE TABLE reminders (
    id              INTEGER PRIMARY KEY,
    text            TEXT NOT NULL,
    due_at          TEXT NOT NULL,  -- the next firing, UTC
    local_at        TEXT NOT NULL,  -- the first wall-clock occurrence: time of day, weekday, day of the month
    timezone        TEXT NOT NULL CHECK (timezone NOT LIKE '+%' AND timezone NOT LIKE '-%' AND timezone != ''),
    repeat          TEXT NOT NULL DEFAULT '' CHECK (repeat IN ('', 'daily', 'weekly', 'monthly')),
    targets         TEXT NOT NULL DEFAULT '',  -- client labels, '|'-separated ('': every client)
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    client          TEXT NOT NULL DEFAULT '',
    system          INTEGER NOT NULL DEFAULT 0,  -- set by Clara herself (the monthly restore drill)
    created_at      TEXT NOT NULL
);
CREATE INDEX reminders_due ON reminders (due_at);

CREATE TABLE tasks (
    id              INTEGER PRIMARY KEY,
    parent_id       INTEGER REFERENCES tasks (id) ON DELETE CASCADE,
    position        INTEGER NOT NULL DEFAULT 0,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    due_at          TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    reminders_sent  INTEGER NOT NULL DEFAULT 0,
    timezone        TEXT NOT NULL CHECK (timezone NOT LIKE '+%' AND timezone NOT LIKE '-%' AND timezone != ''),
    targets         TEXT NOT NULL DEFAULT '',
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    done_at         TEXT
);
CREATE INDEX tasks_parent ON tasks (parent_id, position);
CREATE INDEX tasks_status ON tasks (status);
CREATE TABLE task_reminders (
    id      INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    at      TEXT NOT NULL
);
CREATE INDEX task_reminders_at ON task_reminders (at);
CREATE INDEX task_reminders_task ON task_reminders (task_id);

CREATE TABLE schedules (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    documents       TEXT NOT NULL DEFAULT '[]',  -- JSON: [{"name", "text"}], put after the prompt
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    project_id      INTEGER REFERENCES projects (id) ON DELETE SET NULL,
    repeat          TEXT NOT NULL DEFAULT '' CHECK (repeat IN ('', 'daily', 'weekly', 'monthly')),
    weekdays        TEXT NOT NULL DEFAULT '',  -- weekly: 0 = Monday, ','-separated
    local_at        TEXT NOT NULL,
    timezone        TEXT NOT NULL CHECK (timezone NOT LIKE '+%' AND timezone NOT LIKE '-%' AND timezone != ''),
    next_at         TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    local_only      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX schedules_next ON schedules (enabled, next_at);
CREATE TABLE schedule_runs (
    id           INTEGER PRIMARY KEY,
    schedule_id  INTEGER NOT NULL REFERENCES schedules (id) ON DELETE CASCADE,
    due_at       TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'missed', 'skipped', 'interrupted')),
    summary      TEXT NOT NULL DEFAULT '',
    waiting      INTEGER NOT NULL DEFAULT 0,  -- approval requests the run left waiting
    late_seconds INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX schedule_runs_schedule ON schedule_runs (schedule_id, id);

-- Events: reminders fired, notifications, approvals, alerts, server states; kept until every listener read them
CREATE TABLE events (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    text            TEXT NOT NULL DEFAULT '',
    payload         TEXT NOT NULL DEFAULT '{}',
    targets         TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'server',
    conversation_id TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX events_created ON events (created_at);
CREATE TABLE event_cursors (
    token_id      INTEGER PRIMARY KEY REFERENCES tokens (id) ON DELETE CASCADE,
    last_event_id INTEGER NOT NULL,
    updated_at    TEXT NOT NULL
);

-- The rule gate
CREATE TABLE rules (
    id          INTEGER PRIMARY KEY,
    effect      TEXT NOT NULL CHECK (effect IN ('allow', 'ask', 'refuse')),
    tool        TEXT NOT NULL DEFAULT '',  -- a tool name or glob ('': any)
    target      TEXT NOT NULL DEFAULT '',  -- a target or glob ('': any)
    situation   TEXT NOT NULL DEFAULT '' CHECK (situation IN ('', 'interactive', 'unattended', 'tainted')),
    level       TEXT NOT NULL DEFAULT '' CHECK (level IN ('', 'read', 'write', 'destructive')),
    origin      TEXT NOT NULL CHECK (origin IN ('console', 'approval')),
    note        TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    hits        INTEGER NOT NULL DEFAULT 0,
    last_hit_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE approvals (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    turn_id         TEXT NOT NULL DEFAULT '',
    tool            TEXT NOT NULL,
    arguments       TEXT NOT NULL,  -- JSON, frozen: an approval runs exactly what was asked
    args_digest     TEXT NOT NULL,
    target          TEXT NOT NULL DEFAULT '',
    level           TEXT NOT NULL,
    action_class    TEXT NOT NULL,
    situation       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'done', 'failed')),
    result          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT NOT NULL DEFAULT '',  -- the name of the token that answered
    answer          TEXT NOT NULL DEFAULT '',  -- allow_once, allow_always, refuse_once, refuse_always
    rule_id         INTEGER REFERENCES rules (id) ON DELETE SET NULL,
    told            INTEGER NOT NULL DEFAULT 0,  -- the model was told how it ended
    notified        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX approvals_status ON approvals (status, created_at);
CREATE INDEX approvals_conversation ON approvals (conversation_id, status);
CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL,
    conversation_id TEXT,
    turn_id         TEXT NOT NULL DEFAULT '',
    tool            TEXT NOT NULL,
    target          TEXT NOT NULL DEFAULT '',
    level           TEXT NOT NULL,
    situation       TEXT NOT NULL,
    args_digest     TEXT NOT NULL,
    rule_id         INTEGER,
    effect          TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    approval_id     INTEGER,
    detail          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX audit_log_at ON audit_log (at);

-- Outside services: one GitHub and one Google connection; their credentials are in `secrets`
CREATE TABLE connections (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL UNIQUE CHECK (kind IN ('github', 'google')),
    account    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'needs_reconnect')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE resources (
    id            INTEGER PRIMARY KEY,
    connection_id INTEGER NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('github_repo', 'drive_file', 'drive_folder')),
    locator       TEXT NOT NULL,  -- owner/repo, or a Drive file id
    label         TEXT NOT NULL,
    branch        TEXT NOT NULL DEFAULT '',
    mime          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (kind, locator)
);

-- Models and usage
CREATE TABLE models (
    ref          TEXT PRIMARY KEY,  -- provider:model
    shown        INTEGER NOT NULL DEFAULT 0,  -- offered in the model pickers
    capabilities TEXT NOT NULL DEFAULT '{}',
    size_b       REAL,
    listed_at    TEXT
);
CREATE TABLE usage_log (
    id                INTEGER PRIMARY KEY,
    at                TEXT NOT NULL,
    kind              TEXT NOT NULL,  -- chat, title, summary, reminder, task, schedule, followup
    client            TEXT NOT NULL DEFAULT '',
    conversation_id   TEXT,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens     INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    outcome           TEXT NOT NULL DEFAULT 'ok',  -- ok, error, interrupted
    estimated         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX usage_log_at ON usage_log (at);

-- The workspace's script runs
CREATE TABLE ws_runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    command_digest  TEXT NOT NULL,
    workdir         TEXT NOT NULL DEFAULT '',
    network         INTEGER NOT NULL DEFAULT 0,
    exit_code       INTEGER,
    timed_out       INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    log_path        TEXT NOT NULL DEFAULT '',
    commit_sha      TEXT NOT NULL DEFAULT '',
    conversation_id TEXT,
    turn_id         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed', 'interrupted'))
);

-- The Obsidian vault's search index (the notes themselves stay files in the vault)
CREATE TABLE vault_notes (
    path  TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    mtime REAL NOT NULL,
    size  INTEGER NOT NULL
);
CREATE VIRTUAL TABLE vault_fts USING fts5 (
    path UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2'
);
