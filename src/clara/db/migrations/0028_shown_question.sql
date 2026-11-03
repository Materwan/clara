-- Schema version 28: a question may carry the shorter text the Discord mirror shows in its place. The first message of an
-- automation run is its instructions and the information the run works on (a mail, a pull request): Discord shows only where
-- the run came from. The model is still given the whole message, and `content` stays whole; '' (every message made before, and
-- every message of a chat) shows `content`.
ALTER TABLE messages ADD COLUMN shown TEXT NOT NULL DEFAULT '';
