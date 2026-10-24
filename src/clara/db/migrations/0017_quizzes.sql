-- Schema version 17: the progress of a QCM. A QCM is answered one question at a time (the side panel of the web chat): each
-- question of an attempt is answered or skipped, and keeps what was chosen. The form itself stays in the message that
-- holds Clara's `qcm` call. A retry is a new attempt of the same form; `closed` is set once the final score was sent to
-- Clara, so she is told it once.
CREATE TABLE quiz_attempts (
    conversation_id TEXT    NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    ref             TEXT    NOT NULL,
    attempt         INTEGER NOT NULL,
    started_at      TEXT    NOT NULL,
    closed          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conversation_id, ref, attempt)
);

CREATE TABLE quiz_answers (
    conversation_id TEXT    NOT NULL,
    ref             TEXT    NOT NULL,
    attempt         INTEGER NOT NULL,
    question        INTEGER NOT NULL,  -- from 0
    status          TEXT    NOT NULL CHECK (status IN ('answered', 'skipped')),
    answer          TEXT    NOT NULL,  -- JSON: the numbers of the chosen options, or the typed text
    answered_at     TEXT    NOT NULL,
    PRIMARY KEY (conversation_id, ref, attempt, question),
    FOREIGN KEY (conversation_id, ref, attempt) REFERENCES quiz_attempts (conversation_id, ref, attempt) ON DELETE CASCADE
);
