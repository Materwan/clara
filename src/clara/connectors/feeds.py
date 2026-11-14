"""The feeds you follow (RSS and Atom), for automations: the records, the preview before one is added, and a poll on its own loop
(ops/upkeep.py). A feed cannot call Clara, so she looks, only while an enabled automation waits for `feed.item`, one feed at a time,
the one looked at longest ago first, with a pause between two, the whole poll stopped after DEADLINE seconds.

* **A feed is a record** (table `feeds`): the address of the feed itself, found once, when it was added (feed_parse.py reads the page
  or the feed you gave, through the guard of `web_fetch`), the title it had then, and, while it is waited for, the place: the ETag and
  Last-Modified the site answered and the hashes of the keys of the latest items seen. At most MAX_FEEDS.
* **Each look asks again with `If-None-Match` and `If-Modified-Since`**: a 304 changes nothing but the time of the look.
* **An item is new** when its key (its guid, else its link, else a hash of its title and date) was not seen. The first look of a feed only
  notes where things stand: no event, and no event for an item dated before that look (never replay history; an hour of grace, as
  sites publish late). An event is dated the item's date when it is sane (not in the future), else the look. At most MAX_EVENTS events
  from one feed in one poll (the newest; the rest are noted as seen and said in the log).
* **The place moves in the same transaction that stores the events and plans the runs** (`Automations.ingest`), so a stop loses none
  and repeats none; with no automation waiting the place is forgotten.
* **A feed that fails** says why in fixed words (`last_error`: never the site's text), is logged once per distinct message and tried again at
  the next poll. After many failures nothing special happens: no alert, the owner sees the error on the feed.

What an item says was written by a stranger: it is cut and cleaned (feed_parse.py), tainted (`EventKind.tainted`), and it never goes
through the quarantined reader, as GitHub text does not (DECISIONS.md, « Feeds »).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from ..automations.events import SourceEvent
from ..automations.service import STALE_AFTER
from ..gate.actions import Action, Decision, Situation
from ..gate.outside import one_line
from ..timeutil import iso, now_iso, parse_iso, zone
from . import feed_parse
from .feed_parse import FeedError, key_hash, latest, parse_feed
from .web import WebError

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

KIND = "feed.item"
MAX_FEEDS = 50
DEADLINE = 600  # seconds for a whole poll: what is not finished is looked at first at the next one
PAUSE = 1.0  # seconds between two feeds: the load is spread, never several sites at once
MAX_EVENTS = 20  # events one feed hands over in one poll (a feed that was restored has every item new)
MAX_SEEN = 400  # keys kept in a feed's place
PREVIEW = 5  # items shown before a feed is added
LAG = timedelta(hours=1)  # sites publish late: an item may be dated an hour before the look that finds it
HEADER = 200  # characters kept of an ETag or a Last-Modified
ADDRESS_SHOWN = 300  # characters of a feed's address that a request shows: a longer one is not followed (what you read is what is stored)


class FeedsError(ValueError):
    """A refusal, with the HTTP status it answers (422 unless told)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def normal(url: str) -> str:
    """An address as stored: lower-case scheme and host, no fragment (what makes two addresses one feed)."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


class Feeds:
    def __init__(self, services: Services):
        self.s = services
        self._last_error: dict[int, str] = {}  # what was last logged per feed: an outage is one line

    # ------------------------------------------------------------------
    # The records
    # ------------------------------------------------------------------
    def now(self) -> datetime:
        return self.s.automations.local_now("UTC")

    def count(self) -> int:
        return int(self.s.db.value("SELECT COUNT(*) FROM feeds", (), 0))

    def row(self, feed_id: int):
        return self.s.db.one("SELECT * FROM feeds WHERE id = ?", (feed_id,))

    def today(self, moment: datetime) -> str:
        """The day (the server's) that the count of a feed's items today is about."""
        return moment.astimezone(zone(self.s.automations.home_zone)).date().isoformat()

    def describe(self, row) -> dict:
        day = self.today(self.now())
        return {
            "id": row["id"],
            "url": row["url"],
            "title": row["title"],
            "added_at": row["added_at"],
            "checked_at": row["checked_at"] or None,
            "error": row["last_error"],
            "today": row["today"] if row["day"] == day else 0,
            "watching_since": row["since"] or None,
        }

    def listing(self) -> list[dict]:
        return [self.describe(row) for row in self.s.db.all("SELECT * FROM feeds ORDER BY id")]

    def _audit(self, detail: str) -> None:
        """Who followed or dropped a feed, in the audit log. Never an address."""
        self.s.audit.record(
            Action("feeds", "write", "memory", ("feeds",), detail),
            Situation("interactive"),
            "",
            Decision("allow", "you changed it"),
            "feeds",
            detail=detail,
        )

    # ------------------------------------------------------------------
    # Adding one
    # ------------------------------------------------------------------
    async def find(self, text: str) -> feed_parse.Found:
        """The feed at an address, or of a page (nothing is stored). FeedsError says why there is none, in fixed words."""
        try:
            return await feed_parse.find(self.s.web, text)
        except FeedError as error:
            raise FeedsError(str(error)) from None

    @staticmethod
    def title_of(found: feed_parse.Found) -> str:
        return found.feed.title or urlsplit(found.url).hostname or found.url

    async def preview(self, text: str) -> dict:
        """What you read before you confirm: the feed's address and title and its latest items. Nothing is stored."""
        found = await self.find(text)
        return {
            "feed_url": found.url,
            "title": self.title_of(found),
            "items": [
                {
                    "title": item.title,
                    "link": item.link,
                    "published": item.published.isoformat() if item.published else "",
                }
                for item in latest(found.feed.items, PREVIEW)
            ],
        }

    def _room(self) -> None:
        if self.count() >= MAX_FEEDS:
            raise FeedsError(f"At most {MAX_FEEDS} feeds: remove one first.", 409)

    def check(self, found: feed_parse.Found) -> None:
        """Can this feed be added? FeedsError says why not: the 51st, or one you follow already (both 409)."""
        self._room()
        if self.s.db.one("SELECT 1 FROM feeds WHERE url = ?", (normal(found.url),)) is not None:
            raise FeedsError("You follow this feed already.", 409)

    async def add(self, text: str, by: str = "", shown: str | None = None) -> dict:
        """Look at the address again, then store the feed. The first look at the first poll only notes where things stand.
        `shown` is the address a request showed you (how `one_line` writes it): the feed found now must be that one, else
        nothing is stored (a page that changed its link since you read it cannot make Clara follow another feed)."""
        self._room()  # before the look: a full list asks nothing of anyone
        found = await self.find(text)
        self.check(found)  # the look took time: another may have been added
        if shown is not None and one_line(normal(found.url), ADDRESS_SHOWN) != shown:
            raise FeedsError(
                "The page now points to another feed than the one you were shown: nothing was followed. "
                "Ask again, so that the request shows it.",
                409,
            )
        address = normal(found.url)
        title = self.title_of(found)[:300]
        feed_id = self.s.db.insert(
            "INSERT INTO feeds (url, title, added_at) VALUES (?, ?, ?)", (address, title, now_iso())
        )
        self._audit(f"{by or 'someone'} followed the feed {feed_id}")
        return self.describe(self.row(feed_id))

    def remove(self, feed_id: int, by: str = "") -> bool:
        """Stop following a feed: its record and its place go. The events it made stay, as any event does, 30 days."""
        removed = self.s.db.execute("DELETE FROM feeds WHERE id = ?", (feed_id,)).rowcount > 0
        if removed:
            self._last_error.pop(feed_id, None)
            self._audit(f"{by or 'someone'} stopped following the feed {feed_id}")
        return removed

    # ------------------------------------------------------------------
    # The poll
    # ------------------------------------------------------------------
    def _forget(self) -> None:
        """Nobody waits for feed items: no feed has a place, so the next listener starts from then, not from an old place."""
        self.s.db.execute(
            "UPDATE feeds SET etag = '', modified = '', seen = NULL, since = ''"
            " WHERE seen IS NOT NULL OR etag <> '' OR modified <> '' OR since <> ''"
        )

    async def poll(self) -> None:
        if not self.s.automations.wants(KIND):
            self._forget()
            return
        rows = self.s.db.all("SELECT * FROM feeds ORDER BY checked_at, id")
        try:
            async with asyncio.timeout(DEADLINE):
                for index, row in enumerate(rows):
                    if index:
                        await asyncio.sleep(PAUSE)
                    await self._look(row)
        except TimeoutError:
            log.warning(
                "feeds: a poll took longer than %d s and was stopped; the rest is looked at first at the next one",
                DEADLINE,
            )

    def _failed(self, row, why: str) -> None:
        if self._last_error.get(row["id"]) != why:
            self._last_error[row["id"]] = why
            log.warning("feed %s: %s", row["id"], why)
        self.s.db.execute("UPDATE feeds SET checked_at = ?, last_error = ? WHERE id = ?", (now_iso(), why, row["id"]))

    async def _look(self, row) -> None:
        try:
            await self._ask(row)
        except Exception:
            log.exception("feed %s: the look failed", row["id"])

    async def _ask(self, row) -> None:
        headers = {}
        if row["seen"] is not None and row["etag"]:
            headers["If-None-Match"] = row["etag"]
        if row["seen"] is not None and row["modified"]:
            headers["If-Modified-Since"] = row["modified"]
        try:
            got = await self.s.web.get(
                row["url"],
                headers=headers,
                accept=feed_parse.ACCEPT,
                types=None,
                limit=feed_parse.MAX_BYTES,
                deadline=feed_parse.FETCH_SECONDS,
            )
        except WebError as error:
            self._failed(row, feed_parse.words(error))
            return
        now = self.now()
        if got.status == 304:  # nothing changed: it costs nothing and changes nothing but the time of the look
            self._last_error.pop(row["id"], None)
            self.s.db.execute("UPDATE feeds SET checked_at = ?, last_error = '' WHERE id = ?", (iso(now), row["id"]))
            return
        try:
            if got.cut:
                raise FeedError(feed_parse.TOO_BIG)
            feed = await asyncio.to_thread(parse_feed, got.body, got.url)  # a hostile document is slow: not on the loop
        except FeedError as error:
            self._failed(row, str(error))
            return
        self._last_error.pop(row["id"], None)
        items = {}
        for item in feed.items:
            items.setdefault(key_hash(item.key), item)
        etag, modified = got.headers.get("etag", "")[:HEADER], got.headers.get("last-modified", "")[:HEADER]
        day = self.today(now)

        def move(seen: list[str], since: str, made: int) -> None:
            today = (row["today"] if row["day"] == day else 0) + made
            self.s.db.execute(
                "UPDATE feeds SET etag = ?, modified = ?, seen = ?, since = ?, checked_at = ?, last_error = '',"
                " day = ?, today = ? WHERE id = ?",
                (etag, modified, json.dumps(seen), since, iso(now), day, today, row["id"]),
            )

        if row["seen"] is None:  # the first look only notes where things stand
            move(list(items)[:MAX_SEEN], iso(now), 0)
            return
        before: list[str] = json.loads(row["seen"])
        began = parse_iso(row["since"])
        events = []
        for key, item in items.items():
            if key in before:
                continue
            if item.published is not None and item.published < began - LAG:
                continue  # dated before the first look: an old item that came back, not news
            at = item.published if item.published is not None and item.published <= now else now
            events.append(
                SourceEvent(
                    source="feed",
                    kind=KIND,
                    key=f"feed:{row['id']}:{key}",
                    occurred_at=iso(at),
                    actor=item.author,
                    title=f"{row['title']}: {item.title}"[:300],
                    data={
                        "feed": row["title"],
                        "title": item.title,
                        "author": item.author,
                        "link": item.link,
                        "summary": item.summary,
                    },
                )
            )
        events.sort(key=lambda event: event.occurred_at)
        if len(events) > MAX_EVENTS:
            log.warning(
                "feed %s: %d new items at once; only the newest %d became events", row["id"], len(events), MAX_EVENTS
            )
            events = events[-MAX_EVENTS:]
        seen = list(dict.fromkeys([*items, *before]))[:MAX_SEEN]
        if self.row(row["id"]) is None:  # it was removed while it was being asked
            return
        if events:  # an item found within an interval (and the lag of sites) after its date is on time, not a backlog
            late = timedelta(seconds=self.s.settings.feeds_poll_seconds) + LAG
            self.s.automations.ingest(
                events, also=lambda: move(seen, row["since"], len(events)), stale_after=max(STALE_AFTER, late)
            )
        else:
            move(seen, row["since"], 0)
