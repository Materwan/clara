-- Schema version 19: a conversation no longer belongs to another one (0006 was for the sub agents of a client that is gone).
-- A sub agent's conversation made before stays, now an ordinary incognito one: the incognito sweep deletes it after its day.
DROP INDEX conversations_parent;
ALTER TABLE conversations DROP COLUMN parent_id;
