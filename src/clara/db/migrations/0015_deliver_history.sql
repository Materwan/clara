-- Schema version 15: a schedule or a hook that delivers to Discord lives in a forum thread, and the forum is made from "Show
-- in history": it is switched on for the ones that already deliver to Discord (a conversation that the switch had archived
-- is unarchived, and the mirror told, as the switch does it).
UPDATE schedules SET in_history = 1 WHERE deliver IN ('discord', 'both');
UPDATE triggers SET in_history = 1 WHERE deliver IN ('discord', 'both');

CREATE TEMP TABLE forum_runs AS
    SELECT conversation_id FROM schedules WHERE deliver IN ('discord', 'both') AND conversation_id IS NOT NULL
    UNION
    SELECT r.conversation_id FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id
        WHERE s.deliver IN ('discord', 'both') AND r.conversation_id IS NOT NULL
    UNION
    SELECT r.conversation_id FROM trigger_runs r JOIN triggers t ON t.id = r.trigger_id
        WHERE t.deliver IN ('discord', 'both') AND r.conversation_id IS NOT NULL;

UPDATE mirror_clock SET seq = seq + 1 WHERE id = 1;
UPDATE conversations SET mirror_rev = (SELECT seq FROM mirror_clock WHERE id = 1)
    WHERE archived_by = 'group' AND id IN (SELECT conversation_id FROM forum_runs);
UPDATE conversations SET archived_at = NULL, archived_by = ''
    WHERE archived_by = 'group' AND id IN (SELECT conversation_id FROM forum_runs);
DROP TABLE forum_runs;
