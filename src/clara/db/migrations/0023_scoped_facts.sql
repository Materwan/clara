-- Schema version 23: a memory of its own for each project, schedule and hook (DECISIONS.md, « A memory for a project or an
-- automation »). A fact belongs to a scope: '' is the memory of everything (all the facts so far), 'project:<id>',
-- 'schedule:<id>' or 'hook:<id>' the memory common to the conversations and runs of that project or automation. The same
-- sentence may stand in several scopes, so the "one live fact per sentence" rule and the tombstones are kept per scope.
ALTER TABLE facts ADD COLUMN scope TEXT NOT NULL DEFAULT '';

DROP INDEX facts_live_key;
CREATE UNIQUE INDEX facts_live_key ON facts (scope, text_key) WHERE superseded_by IS NULL;
CREATE INDEX facts_scope ON facts (scope);

CREATE TABLE forgotten_by_scope (
    scope        TEXT NOT NULL DEFAULT '',
    text_key     TEXT NOT NULL,
    forgotten_at TEXT NOT NULL,
    PRIMARY KEY (scope, text_key)
);
INSERT INTO forgotten_by_scope (scope, text_key, forgotten_at) SELECT '', text_key, forgotten_at FROM forgotten;
DROP TABLE forgotten;
ALTER TABLE forgotten_by_scope RENAME TO forgotten;

-- The memory goes with what it belongs to, whichever way that is deleted (the API, a deleted Discord forum, the console).
CREATE TRIGGER facts_of_deleted_project AFTER DELETE ON projects BEGIN
    DELETE FROM facts WHERE scope = 'project:' || old.id;
    DELETE FROM forgotten WHERE scope = 'project:' || old.id;
END;
CREATE TRIGGER facts_of_deleted_schedule AFTER DELETE ON schedules BEGIN
    DELETE FROM facts WHERE scope = 'schedule:' || old.id;
    DELETE FROM forgotten WHERE scope = 'schedule:' || old.id;
END;
CREATE TRIGGER facts_of_deleted_hook AFTER DELETE ON triggers BEGIN
    DELETE FROM facts WHERE scope = 'hook:' || old.id;
    DELETE FROM forgotten WHERE scope = 'hook:' || old.id;
END;
