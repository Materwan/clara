-- Schema version 29: the GitHub pack. The five GitHub tools are no longer offered in every conversation: the "+" menu switches
-- them on (a pack, like the web and Mail). A conversation or a schedule that has called a GitHub tool before keeps them: the pack
-- is added to it once, from the audit log. Every other conversation starts with it off. A hook needs nothing here: its runs get
-- the pack from the tools its allow-list names.
UPDATE conversations
   SET packs = json_insert(packs, '$[#]', 'github')
 WHERE json_valid(packs)
   AND NOT EXISTS (SELECT 1 FROM json_each(conversations.packs) WHERE value = 'github')
   AND id IN (SELECT conversation_id FROM audit_log WHERE tool LIKE 'github\_%' ESCAPE '\');

UPDATE schedules
   SET packs = json_insert(packs, '$[#]', 'github')
 WHERE json_valid(packs)
   AND NOT EXISTS (SELECT 1 FROM json_each(schedules.packs) WHERE value = 'github')
   AND id IN (
       SELECT r.schedule_id
         FROM schedule_runs r
         JOIN audit_log a ON a.conversation_id = r.conversation_id
        WHERE a.tool LIKE 'github\_%' ESCAPE '\'
   );
