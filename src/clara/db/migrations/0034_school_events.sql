-- Schema version 34: school events (DECISIONS.md, « School events (2026-11-13) »). Moodle and Auriga cannot call Clara, so the watcher
-- (connectors/school_watch.py) looks, one *stream* of a site at a time: the quizzes' times, the activities of the courses and the grades
-- of Moodle; the planning and the grades of Auriga. `state` is where the stream had reached: a time, the ids of what was seen, a hash per
-- grade (never the text of a grade) and, for the planning, what the events will say. Like mail_cursors and github_cursors, the place moves
-- in the same transaction that stores the events, so a crash loses nothing and repeats nothing.
CREATE TABLE school_cursors (
    site       TEXT NOT NULL CHECK (site IN ('moodle', 'auriga')),
    stream     TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (site, stream)
);
