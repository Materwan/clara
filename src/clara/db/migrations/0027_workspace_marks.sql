-- Schema version 27: where the files of the workspace come from (DECISIONS.md, « Workspace provenance »). A file that arrived from
-- outside (an email attachment, what a script fetched from the network) or that a conversation wrote after reading untrusted
-- content is marked. `kind` is `quarantine` for text that came in as it was (a quarantined reader reads it) and `tainted`
-- for what Clara wrote after untrusted content (reading it taints the reader). The person marks a file trusted: the
-- digest of the content they trusted is kept, so a changed file is untrusted again.
CREATE TABLE workspace_marks (
    path            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('quarantine', 'tainted')),
    source          TEXT NOT NULL,
    conversation_id TEXT,
    marked_at       TEXT NOT NULL,
    trusted_digest  TEXT,
    trusted_at      TEXT
);

-- A conversation that read untrusted content through a tool whose name does not say so (a marked file of the workspace, a
-- script that used the network) stays tainted in all its later turns, as one that called a tool that taints always does.
CREATE TABLE conversation_taint (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    at              TEXT NOT NULL
);
