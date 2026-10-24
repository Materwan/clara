-- Schema version 7: how many times a scheduled run is tried when the model is busy or unreachable (1 to 3). Three by
-- default; 1 means no retry.
ALTER TABLE schedules ADD COLUMN attempts INTEGER NOT NULL DEFAULT 3;
