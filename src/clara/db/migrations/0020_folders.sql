-- Schema version 20: folders on the person's computer (DECISIONS.md, « Folders on your computer »). A grant is one folder the
-- web chat opened in one conversation; its budget counts the bytes of file content Clara read through it. A new grant
-- replaces the row (a new budget). An allowance is one hidden or ignored file the person let Clara read again in that
-- conversation (« Allow for this conversation » on its card); both go with the conversation.
CREATE TABLE folder_grants (
    conversation_id TEXT    PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    grant_id        TEXT    NOT NULL,
    folder_name     TEXT    NOT NULL,
    budget_bytes    INTEGER NOT NULL,
    used_bytes      INTEGER NOT NULL DEFAULT 0,
    granted_at      TEXT    NOT NULL
);

CREATE TABLE conversation_allowances (
    conversation_id TEXT    NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    tool            TEXT    NOT NULL,
    target          TEXT    NOT NULL,
    approval_id     INTEGER NOT NULL,
    granted_at      TEXT    NOT NULL,
    PRIMARY KEY (conversation_id, tool, target)
);
