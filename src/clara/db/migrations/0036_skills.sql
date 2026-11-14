-- Schema version 36: skills (DECISIONS.md, « Skills (2026-11-13) »). A skill is text: a SKILL.md (frontmatter and instructions) and the other
-- files of its folder, stored here so that they are in Clara's backups with the database. Nothing in a skill changes after it was added:
-- adding it again under the same name (a deliberate replace) deletes these rows and writes new ones in one transaction.
CREATE TABLE skills (
    name                     TEXT PRIMARY KEY CHECK (length(name) BETWEEN 1 AND 40 AND name NOT GLOB '*[^a-z0-9-]*' AND name NOT GLOB '-*'),
    description              TEXT NOT NULL CHECK (description <> ''),
    argument_hint            TEXT NOT NULL DEFAULT '',
    packs                    TEXT NOT NULL DEFAULT '[]',   -- JSON list of pack ids the skill needs (shown to you; a skill switches none on by itself)
    disable_model_invocation INTEGER NOT NULL DEFAULT 0 CHECK (disable_model_invocation IN (0, 1)),   -- kept for the phase where Clara runs skills
    skill_md                 TEXT NOT NULL,                -- the whole SKILL.md
    body                     TEXT NOT NULL,                -- its instructions: the SKILL.md without the frontmatter, what a command expands to
    scope                    TEXT NOT NULL CHECK (scope IN ('global', 'projects')),
    size                     INTEGER NOT NULL,             -- bytes in all: SKILL.md and the other files
    created_at               TEXT NOT NULL,
    created_by               TEXT NOT NULL DEFAULT ''      -- the name of the token that added it
);

-- The projects a skill of scope 'projects' belongs to: in any other conversation it does not exist.
CREATE TABLE skill_projects (
    skill      TEXT NOT NULL REFERENCES skills (name) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    PRIMARY KEY (skill, project_id)
);

-- The other files of a skill's folder, as bytes (never executed, never served, never put in a prompt in this version).
CREATE TABLE skill_files (
    skill  TEXT NOT NULL REFERENCES skills (name) ON DELETE CASCADE,
    path   TEXT NOT NULL,
    data   BLOB NOT NULL,
    size   INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    kind   TEXT NOT NULL CHECK (kind IN ('text', 'binary')),
    PRIMARY KEY (skill, path)
);
