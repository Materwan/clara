-- Schema version 35: feeds (DECISIONS.md, « Feeds (2026-11-13) »). A feed is a record you add: the address of the feed itself (found
-- once, when it was added, from the address of a page or of a feed), the title it had then, and, while an automation waits for `feed.item`,
-- the place the watcher (connectors/feeds.py) had reached: the ETag and Last-Modified the site answered, and the keys of the latest items
-- seen (hashes, at most 400: enough to know an item again, not to read it). A feed nobody waits for has no place: `seen` is NULL, and the
-- first look of the next listener only notes where things stand. The place moves in the same transaction that stores the events and
-- plans the runs, so a crash loses nothing and repeats nothing. `last_error` is fixed words, never a site's text.
CREATE TABLE feeds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,                  -- never reused: an event key names it (feed:<id>:<hash>)
    url         TEXT NOT NULL UNIQUE CHECK (url <> ''),             -- the feed's own address, after discovery
    title       TEXT NOT NULL DEFAULT '',                           -- what it was called when you added it (300 characters at most)
    added_at    TEXT NOT NULL,
    etag        TEXT NOT NULL DEFAULT '',
    modified    TEXT NOT NULL DEFAULT '',                           -- the site's Last-Modified, as it wrote it
    seen        TEXT,                                               -- JSON list of item key hashes; NULL: no first look yet
    since       TEXT NOT NULL DEFAULT '',                           -- when the first look was made: no event for an item older than it
    checked_at  TEXT NOT NULL DEFAULT '',                           -- the last look, good or not
    last_error  TEXT NOT NULL DEFAULT '',                           -- fixed words; empty when the last look worked
    day         TEXT NOT NULL DEFAULT '',                           -- the day (local) that `today` counts
    today       INTEGER NOT NULL DEFAULT 0                          -- events made from this feed on that day
);
