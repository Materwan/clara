-- Schema version 16: the Mail pack. The mail tools are no longer offered in every conversation: the "+" menu switches them on
-- (a pack, like the web). A schedule that has called a mail tool before keeps them: the pack is added to it once, from the
-- audit log of its runs. A hook needs nothing here: its runs get the pack from the tools its allow-list names.
UPDATE schedules
   SET packs = json_insert(packs, '$[#]', 'mail')
 WHERE json_valid(packs)
   AND NOT EXISTS (SELECT 1 FROM json_each(schedules.packs) WHERE value = 'mail')
   AND id IN (
       SELECT r.schedule_id
         FROM schedule_runs r
         JOIN audit_log a ON a.conversation_id = r.conversation_id
        WHERE a.tool LIKE 'mail\_%' ESCAPE '\'
   );
