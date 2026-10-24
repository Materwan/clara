-- Schema version 18: the special tools (packs) a project's new conversations start with. A project made before keeps what every
-- conversation had: web search. The QCM is a pack now ("qcm", off until switched on): no conversation is migrated, as with Mail (16).
ALTER TABLE projects ADD COLUMN packs TEXT NOT NULL DEFAULT '["web"]';  -- JSON: the packs a new conversation of the project starts with
