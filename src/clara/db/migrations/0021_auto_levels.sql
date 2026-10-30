-- Schema version 21: auto levels (DECISIONS.md, « Auto levels »). A ladder of levels from 0 (no tool) to the last one (every tool
-- runs); each tool is refused, asked or run according to the level of its conversation. `auto_levels` is the ladder (the owner may add,
-- rename and remove the levels between the first and the last; `key` names the five built-in ones so the defaults of the action
-- classes find them again), `tool_levels` holds the tools whose thresholds the owner changed (the others follow the default of their
-- action class). A level is stored as its position; NULL on a conversation, a project, a schedule or a hook means « follow »:
-- the conversation follows its automation, then its project, then the server default (runtime settings `auto_level_default` and
-- `auto_level_unattended`).
CREATE TABLE auto_levels (
    position INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    key      TEXT    NOT NULL DEFAULT ''
);

INSERT INTO auto_levels (position, name, key) VALUES
    (0, 'No tool', 'none'),
    (1, 'Trusted read', 'trusted_read'),
    (2, 'Any read', 'any_read'),
    (3, 'Files and vault', 'files_vault'),
    (4, 'Mail', 'mail'),
    (5, 'Everything', 'all');

CREATE TABLE tool_levels (
    tool         TEXT    PRIMARY KEY,
    ask_from     INTEGER NOT NULL,  -- the first level at which the tool asks (below it: refused)
    approve_from INTEGER NOT NULL,  -- the first level at which the tool runs without asking
    CHECK (ask_from >= 1 AND ask_from <= approve_from)
);

ALTER TABLE conversations ADD COLUMN auto_level INTEGER;
ALTER TABLE projects ADD COLUMN auto_level INTEGER;
ALTER TABLE schedules ADD COLUMN auto_level INTEGER;
ALTER TABLE triggers ADD COLUMN auto_level INTEGER;
