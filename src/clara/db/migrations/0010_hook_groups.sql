-- Schema version 10: the conversations of a hook's runs form a group of their own in the web site's history, like a schedule's,
-- instead of filling a project made for the hook. The group has its own "Show in history" switch (hidden until you say so).
ALTER TABLE triggers ADD COLUMN in_history INTEGER NOT NULL DEFAULT 0;
CREATE INDEX trigger_runs_conversation ON trigger_runs (conversation_id);

-- The switch the hook's project had.
UPDATE triggers SET in_history = COALESCE((SELECT p.in_history FROM projects p WHERE p.id = triggers.project_id), 0);

-- The project made with a hook (named "Hook: <name>") goes when it was only that: no instructions, no file, no repository,
-- not pinned, used by that hook alone and holding nothing but that hook's runs. A project you filled or shared stays.
CREATE TEMP TABLE hook_projects AS
SELECT p.id FROM projects p JOIN triggers t ON t.project_id = p.id
WHERE p.name = 'Hook: ' || t.name AND p.instructions = '' AND p.pinned = 0
  AND (SELECT COUNT(*) FROM triggers o WHERE o.project_id = p.id) = 1
  AND NOT EXISTS (SELECT 1 FROM project_files f WHERE f.project_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM project_sources s WHERE s.project_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM schedules s WHERE s.project_id = p.id)
  AND NOT EXISTS (
      SELECT 1 FROM conversations c WHERE c.project_id = p.id
      AND c.id NOT IN (SELECT r.conversation_id FROM trigger_runs r WHERE r.conversation_id IS NOT NULL)
  );
UPDATE conversations SET project_id = NULL WHERE project_id IN (SELECT id FROM hook_projects);
UPDATE triggers SET project_id = NULL WHERE project_id IN (SELECT id FROM hook_projects);
DELETE FROM projects WHERE id IN (SELECT id FROM hook_projects);
DROP TABLE hook_projects;
