-- Schema version 11: hooks on GitHub. Each repository Clara may use is looked at on a poll (never called by GitHub), one
-- *stream* at a time: its pull requests and its branches. `state` is where the stream had reached (a time, or the head
-- of each branch) and `etag` lets GitHub answer "nothing changed" for free. Like mail_cursors, the place moves in the
-- same transaction that stores the events, so a crash loses nothing and repeats nothing.
CREATE TABLE github_cursors (
    repo       TEXT NOT NULL,
    stream     TEXT NOT NULL CHECK (stream IN ('pulls', 'branches')),
    state      TEXT NOT NULL DEFAULT '{}',
    etag       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repo, stream)
);
