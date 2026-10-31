-- Schema version 26: a booked call remembers whether the turn that booked it had read untrusted content. At the due time the
-- rules are asked with that situation (a rule scoped to tainted turns decides it as it did when it was booked).
ALTER TABLE deferred_calls ADD COLUMN tainted INTEGER NOT NULL DEFAULT 0;
