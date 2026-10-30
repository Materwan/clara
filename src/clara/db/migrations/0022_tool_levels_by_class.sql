-- Schema version 22: the thresholds of a tool are kept per action class (DECISIONS.md, « Auto levels »). A tool such as `ws_run` or
-- `web_fetch` has several classes (a script with or without network, a fetch of a trusted or of any address), each with its own
-- default; one pair of thresholds for the whole tool could only be the strictest of them. A row now names the class it is for;
-- the empty class is a setting for every class of the tool that has none of its own (what version 21 stored, and what
-- `/level tool <name> <ask> <run>` still writes).
CREATE TABLE tool_levels_by_class (
    tool         TEXT    NOT NULL,
    klass        TEXT    NOT NULL DEFAULT '',  -- an action class, or '' for every class of the tool
    ask_from     INTEGER NOT NULL,  -- the first level at which the tool asks (below it: refused)
    approve_from INTEGER NOT NULL,  -- the first level at which the tool runs without asking
    PRIMARY KEY (tool, klass),
    CHECK (ask_from >= 1 AND ask_from <= approve_from)
);

INSERT INTO tool_levels_by_class (tool, klass, ask_from, approve_from)
    SELECT tool, '', ask_from, approve_from FROM tool_levels;

DROP TABLE tool_levels;
ALTER TABLE tool_levels_by_class RENAME TO tool_levels;
