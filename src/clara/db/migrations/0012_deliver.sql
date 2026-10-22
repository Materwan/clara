-- Schema version 12: where the result of an automation goes. `notice` is what schedules and hooks always did (a short
-- notification to every client); `web` leaves Discord out; `discord` and `both` also put the whole answer in Discord's
-- #general as a message you can answer (`discord`: nothing else; `both`: the short notification for the other clients).
ALTER TABLE schedules ADD COLUMN deliver TEXT NOT NULL DEFAULT 'notice' CHECK (deliver IN ('notice', 'web', 'discord', 'both'));
ALTER TABLE triggers ADD COLUMN deliver TEXT NOT NULL DEFAULT 'notice' CHECK (deliver IN ('notice', 'web', 'discord', 'both'));
