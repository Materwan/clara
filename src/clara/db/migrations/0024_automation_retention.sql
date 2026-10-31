-- Schema version 24: automatic deletion of the conversations automations make (DECISIONS.md, « Automation retention »). The
-- global limits are runtime settings (`automation_keep_days`, `automation_keep_hours`, `automation_keep_last`, all 0 = off);
-- a schedule or a hook may set its own. NULL means « follow the global setting ». The age is written as days and hours
-- together: both are NULL, or both are set (0 and 0: no age limit for this automation). `keep_last` is the number of
-- conversations kept for this automation (0: no count limit).
ALTER TABLE schedules ADD COLUMN keep_days  INTEGER;
ALTER TABLE schedules ADD COLUMN keep_hours INTEGER;
ALTER TABLE schedules ADD COLUMN keep_last  INTEGER;
ALTER TABLE triggers  ADD COLUMN keep_days  INTEGER;
ALTER TABLE triggers  ADD COLUMN keep_hours INTEGER;
ALTER TABLE triggers  ADD COLUMN keep_last  INTEGER;
