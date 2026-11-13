-- Schema version 31: modes (DECISIONS.md, « Modes (2026-11-13) »), which replace the auto levels, the rules and the tainted-turn rule.
-- A mode is a name, a list of tool packs and verdict lines (approve, ask or refuse, for a tool and/or an action class and/or a target,
-- in two flavours: normal and `(if tainted)`). The default mode is a file the server reads at start, not a row. A conversation,
-- a project, a schedule or a hook stores the NAME of its mode; NULL means « follow » (its automation, then its project, then
-- the default mode).
CREATE TABLE modes (
    name       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX modes_lower_name ON modes (lower(name));

CREATE TABLE mode_packs (
    mode TEXT NOT NULL REFERENCES modes (name) ON DELETE CASCADE,
    pack TEXT NOT NULL,
    PRIMARY KEY (mode, pack)
);

CREATE TABLE mode_verdicts (
    mode    TEXT NOT NULL REFERENCES modes (name) ON DELETE CASCADE,
    tool    TEXT NOT NULL DEFAULT '',  -- a tool name or glob ('': any tool)
    klass   TEXT NOT NULL DEFAULT '',  -- an action class ('': any class)
    target  TEXT NOT NULL DEFAULT '',  -- a target glob ('': any target)
    verdict TEXT NOT NULL CHECK (verdict IN ('approve', 'ask', 'refuse')),
    tainted TEXT NOT NULL CHECK (tainted IN ('approve', 'ask', 'refuse')),  -- the verdict of a tainted turn: never more permissive
    PRIMARY KEY (mode, tool, klass, target),
    CHECK (tool <> '' OR klass <> '' OR target <> '')
);

ALTER TABLE conversations ADD COLUMN mode TEXT REFERENCES modes (name) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN mode TEXT REFERENCES modes (name) ON DELETE SET NULL;
ALTER TABLE schedules ADD COLUMN mode TEXT REFERENCES modes (name) ON DELETE SET NULL;
ALTER TABLE triggers ADD COLUMN mode TEXT REFERENCES modes (name) ON DELETE SET NULL;

-- Every level of its own becomes « follow »: a level has no mode that means the same, and the default mode is what a level 3 did.
ALTER TABLE conversations DROP COLUMN auto_level;
ALTER TABLE projects DROP COLUMN auto_level;
ALTER TABLE schedules DROP COLUMN auto_level;
ALTER TABLE triggers DROP COLUMN auto_level;

-- What the levels and the rules said is kept, unread by the server, for `clara mode import-legacy`, which prints a default-mode
-- file made from it (a SQL migration cannot write a file). The three settings that stopped existing go with them.
ALTER TABLE auto_levels RENAME TO legacy_auto_levels;
ALTER TABLE tool_levels RENAME TO legacy_tool_levels;
ALTER TABLE rules RENAME TO legacy_rules;
CREATE TABLE legacy_settings AS
    SELECT key, value FROM settings WHERE key IN ('auto_level_default', 'auto_level_unattended', 'tainted_rule', 'approve_all_calls');
DELETE FROM settings WHERE key IN ('auto_level_default', 'auto_level_unattended', 'tainted_rule', 'approve_all_calls');
