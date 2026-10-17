-- Schema version 2: tell a model you hid from one you never decided about, so a provider can show some by default.
ALTER TABLE models ADD COLUMN chosen INTEGER NOT NULL DEFAULT 0;  -- 1: you showed or hid it yourself (/models show|hide)
UPDATE models SET chosen = 1 WHERE shown = 1;
