"""Reading what strangers publish as a feed (RSS 2.0, RSS 1.0, Atom), and finding the feed of a page. Nothing here is trusted:

* **The address** is fetched only through the guard of `web_fetch` (`Web.get`: public addresses only, every redirect checked again,
  the size and the time capped). A feed address found in a page goes through it too, so a link to localhost is no feed.
* **The XML** is read by expat directly, with no entity expansion at all: a document that has a DOCTYPE, of any kind, is refused as
  soon as the parser meets it (a billion-laughs document and an external entity are both a DOCTYPE), and so is one with more than
  MAX_ELEMENTS elements or MAX_DEPTH levels. No external fetch ever follows from the content: an enclosure, an image and a link are
  only text here.
* **The text** of an item is cut (a title 300 characters, an author 200, a link 500 and `http(s)` only, a summary 2,000 after its
  markup is dropped) and cleaned as a mail's is (no hidden characters), and at most MAX_ITEMS items are looked at.

Finding a feed happens once, when it is added: the page's `<link rel="alternate" type="application/rss+xml|atom+xml">`, a YouTube
channel (its feed is built from the channel id), and a few well-known paths only if the page names none.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.parsers import expat

from .mail import clean_line, html_to_text
from .web import Web, WebError

MAX_BYTES = 2_000_000  # a feed or a page read
HEAD_BYTES = 300_000  # the part of a page looked at for its <link> tags
MAX_ELEMENTS = 100_000
MAX_DEPTH = 20
MAX_TEXT = 50_000  # characters kept of one element
MAX_ITEMS = 200  # items of a feed looked at
TITLE, AUTHOR, LINK, SUMMARY = 300, 200, 500, 2_000
RAW_SUMMARY = (
    10_000  # characters of an item's markup that are turned into text: 200 items of 50,000 would take a second
)
MAX_LINK_TAGS = 100  # <link> tags of a page looked at (a page may hold any number)
MAX_TRY = 3  # feed addresses a page names that are tried
FETCH_SECONDS = 15  # one address
FIND_SECONDS = 40  # the whole search for the feed of a page
ACCEPT = "application/atom+xml,application/rss+xml,application/rdf+xml;q=0.9,application/xml;q=0.8,text/xml;q=0.8,text/html;q=0.5,*/*;q=0.1"
WELL_KNOWN = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml")
FEED_TYPES = ("application/rss+xml", "application/atom+xml")
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_CHANNEL_PATH = re.compile(r"^/channel/(UC[0-9A-Za-z_-]{22})(?:/|$)")
# where a YouTube page says which channel it is of: its own metadata, the first thing it says
_CHANNEL_IN_PAGE = re.compile(
    rb'(?:"(?:channelId|externalId)"\s*:\s*"|itemprop="(?:channelId|identifier)"\s+content="|youtube\.com/channel/)'
    rb"(UC[0-9A-Za-z_-]{22})"
)

# What an error says, in fixed words: never the text of the site that was asked, which a stranger wrote.
NOT_A_FEED = "It is not a feed (RSS or Atom)."
BAD_XML = "It is not well-formed XML."
DOCTYPE = "It declares a DOCTYPE, which is refused."
TOO_BIG = "The feed is too large."
NO_FEED = "No feed was found at this address."
REFUSED = "The address is not a public one."
SLOW = "The site took too long."
UNREACHABLE = "The site could not be reached."

ATOM = "http://www.w3.org/2005/Atom"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RSS1 = "http://purl.org/rss/1.0/"
DC = "http://purl.org/dc/elements/1.1/"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
MEDIA = "http://search.yahoo.com/mrss/"


class FeedError(ValueError):
    """A feed that could not be found or read. The message is fixed words, safe to show and to store."""


def words(error: Exception) -> str:
    """What went wrong, in fixed words (never the text of an answer)."""
    if isinstance(error, FeedError):
        return str(error)
    if isinstance(error, WebError):
        if error.refused:
            return REFUSED
        if error.status:
            return f"The site answered HTTP {error.status}."
        return SLOW if error.slow else UNREACHABLE
    return UNREACHABLE


def q(namespace: str, name: str) -> str:
    """An element's name as the parser gives it."""
    return f"{namespace} {name}" if namespace else name


# ----------------------------------------------------------------------
# The XML
# ----------------------------------------------------------------------
class _Node:
    __slots__ = ("attrs", "kids", "name", "text")

    def __init__(self, name: str, attrs: dict[str, str]):
        self.name = name
        self.attrs = attrs
        self.text = ""
        self.kids: list[_Node] = []


def _tree(data: bytes) -> _Node:
    """The document as a tree of `_Node`s, or FeedError. No entity is ever expanded: a DOCTYPE is refused, and without one an
    entity reference that is not one of the five predefined ones is an error of the parser."""
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    stack: list[_Node] = []
    roots: list[_Node] = []
    count = 0

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal count
        count += 1
        if count > MAX_ELEMENTS or len(stack) >= MAX_DEPTH:
            raise FeedError(TOO_BIG)
        node = _Node(name, attrs)
        (stack[-1].kids if stack else roots).append(node)
        stack.append(node)

    def end(name: str) -> None:
        stack.pop()

    def text(chunk: str) -> None:
        if stack and len(stack[-1].text) < MAX_TEXT:
            stack[-1].text += chunk

    def refuse(*_: object) -> None:
        raise FeedError(DOCTYPE)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = refuse
    try:
        parser.Parse(data.lstrip(), True)  # (a feed that starts with a blank line is common, and the parser refuses it)
    except expat.ExpatError:
        raise FeedError(BAD_XML) from None
    if not roots:
        raise FeedError(NOT_A_FEED)
    return roots[0]


def _kid(node: _Node | None, name: str) -> _Node | None:
    return next((kid for kid in node.kids if kid.name == name), None) if node else None


def _kids(node: _Node | None, name: str) -> list[_Node]:
    return [kid for kid in node.kids if kid.name == name] if node else []


def _text(node: _Node | None) -> str:
    """The text of an element and of what is inside it (an Atom `xhtml` content), up to MAX_TEXT characters."""
    if node is None:
        return ""
    parts, size, pending = [], 0, [node]
    while pending and size < MAX_TEXT:
        current = pending.pop()
        parts.append(current.text)
        size += len(current.text)
        pending.extend(reversed(current.kids))
    return "".join(parts)


# ----------------------------------------------------------------------
# A feed
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Item:
    key: str  # what tells it from the others: its guid, else its link, else a hash of its title and date
    title: str
    author: str
    link: str
    summary: str
    published: datetime | None  # UTC


@dataclass(frozen=True)
class Feed:
    title: str
    items: list[Item]


def key_hash(key: str) -> str:
    """What the place of a feed keeps of an item's key: enough to know it again, not to read it."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _web_link(value: str, base: str = "") -> str:
    """An address as a link: `http(s)` only, one piece, at most LINK characters; else nothing."""
    value = value.strip()
    if base and value:
        value = urljoin(base, value)
    if len(value) > LINK or re.search(r"[\s\x00-\x1f\x7f]", value):
        return ""
    parts = urlsplit(value)
    return value if parts.scheme in ("http", "https") and parts.hostname else ""


def _when(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            moment = parsedate_to_datetime(value)
        return (moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment).astimezone(UTC)
    except (ValueError, OverflowError, TypeError, IndexError):
        return None


def _atom_link(entry: _Node) -> str:
    links = [kid for kid in _kids(entry, q(ATOM, "link")) if kid.attrs.get("href")]
    best = next((kid for kid in links if kid.attrs.get("rel", "alternate") == "alternate"), links[0] if links else None)
    return best.attrs["href"] if best else ""


def _item(node: _Node, ns: str, base: str, fallback_author: str) -> Item:
    def field(name: str, space: str | None = None) -> str:
        return _text(_kid(node, q(ns if space is None else space, name)))

    if ns == ATOM:
        link = _atom_link(node)
        guid = field("id")
        author = _text(_kid(_kid(node, q(ATOM, "author")), q(ATOM, "name"))) or fallback_author
        raw = field("summary") or field("content")
        if not raw:  # YouTube puts the description in media:group
            raw = _text(_kid(_kid(node, q(MEDIA, "group")), q(MEDIA, "description")))
        date = field("published") or field("updated")
    else:
        link = field("link")
        guid_node = _kid(node, q(ns, "guid"))
        guid = _text(guid_node) or node.attrs.get(q(RDF, "about"), "")
        if not link and guid_node is not None and guid_node.attrs.get("isPermaLink", "true") != "false":
            link = _text(guid_node)
        author = field("author") or field("creator", DC)
        raw = field("description") or field("encoded", CONTENT)
        date = field("pubDate") or field("date", DC)
    title = clean_line(html_to_text(field("title")), TITLE)
    link = _web_link(link, base)
    published = _when(date)
    stamp = published.isoformat() if published else ""
    key = guid.strip()[:LINK] or link or hashlib.sha256(f"{title}\x1f{stamp}".encode()).hexdigest()[:32]
    return Item(key, title, clean_line(author, AUTHOR), link, html_to_text(raw[:RAW_SUMMARY])[:SUMMARY], published)


def parse_feed(data: bytes, base: str = "") -> Feed:
    """RSS 2.0, RSS 1.0 (RDF) or Atom, or FeedError. `base` is the feed's own address, which a relative link is made from."""
    root = _tree(data)
    if root.name == "rss":
        channel = _kid(root, "channel")
        ns, entries = "", _kids(channel, "item")
    elif root.name == q(RDF, "RDF"):
        channel = _kid(root, q(RSS1, "channel"))
        ns, entries = RSS1, _kids(root, q(RSS1, "item"))
    elif root.name == q(ATOM, "feed"):
        channel = root
        ns, entries = ATOM, _kids(root, q(ATOM, "entry"))
    else:
        raise FeedError(NOT_A_FEED)
    if channel is None:
        raise FeedError(NOT_A_FEED)
    owner = clean_line(_text(_kid(_kid(channel, q(ATOM, "author")), q(ATOM, "name"))), AUTHOR) if ns == ATOM else ""
    title = clean_line(html_to_text(_text(_kid(channel, q(ns, "title")))), TITLE)
    return Feed(title, [_item(entry, ns, base, owner) for entry in entries[:MAX_ITEMS]])


def latest(items: list[Item], count: int) -> list[Item]:
    """The newest `count` items by date (an item with no date after the ones that have one, the feed's own order otherwise)."""
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(items, key=lambda item: item.published or epoch, reverse=True)[:count]


# ----------------------------------------------------------------------
# Finding the feed of a page
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Found:
    url: str  # the feed's own address (where it ended up after redirects)
    feed: Feed


class _Links(HTMLParser):
    """The `<link>` tags of a page (a page may hold thousands: only the first MAX_LINK_TAGS are kept)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "link" and len(self.links) < MAX_LINK_TAGS:
            self.links.append(dict(attrs))


def alternates(page: str, base: str) -> list[str]:
    """The feed addresses a page names (`<link rel="alternate" type="application/rss+xml|atom+xml" href>`), in its order, made
    absolute, `http(s)` only, without repeats."""
    parser = _Links()
    with contextlib.suppress(Exception):  # broken markup gives what was read so far
        parser.feed(page)
        parser.close()
    found: list[str] = []
    for link in parser.links:
        rel = (link.get("rel") or "").lower().split()
        kind = (link.get("type") or "").split(";")[0].strip().lower()
        address = _web_link(link.get("href") or "", base)
        if "alternate" in rel and kind in FEED_TYPES and address and address not in found:
            found.append(address)
    return found


def start_address(text: str) -> str:
    """What a person pasted, as an address: `youtube.com/@x` is `https://youtube.com/@x`."""
    text = text.strip()
    if not text or len(text) > 2_000 or re.search(r"\s", text):
        raise FeedError("Give the address of a feed or of a page.")
    return text if "://" in text else "https://" + text


def _is_page(kind: str, body: bytes) -> bool:
    """A page rather than a feed: by what it starts with first (some servers call a feed `text/html`, and an XHTML page starts like
    XML), then by its content type."""
    head = body[:2_048].lstrip().lower()
    if b"<html" in head or head.startswith(b"<!doctype html"):
        return True
    if head.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf")):
        return False
    return "html" in kind


async def _read(web: Web, address: str):
    try:
        return await web.get(address, accept=ACCEPT, types=None, limit=MAX_BYTES, deadline=FETCH_SECONDS)
    except WebError as error:
        raise FeedError(words(error)) from None


async def _feed(web: Web, address: str) -> Found:
    got = await _read(web, address)
    if got.cut:
        raise FeedError(TOO_BIG)
    return Found(got.url, await asyncio.to_thread(parse_feed, got.body, got.url))


async def find(web: Web, text: str) -> Found:
    """The feed at an address, or of a page: FeedError (in fixed words) when there is none. Every address asked goes through the
    guard; the first feed that reads is the answer."""
    try:
        async with asyncio.timeout(FIND_SECONDS):
            return await _find(web, start_address(text))
    except TimeoutError:
        raise FeedError(SLOW) from None


async def _find(web: Web, address: str) -> Found:
    parts = urlsplit(address)
    youtube = (parts.hostname or "").lower() in YOUTUBE_HOSTS
    channel = _CHANNEL_PATH.match(parts.path) if youtube else None
    if channel:  # a channel address says its channel id: no page to read
        return await _feed(web, YOUTUBE_FEED.format(channel.group(1)))
    got = await _read(web, address)
    if not _is_page(got.kind, got.body):
        if got.cut:
            raise FeedError(TOO_BIG)
        return Found(got.url, await asyncio.to_thread(parse_feed, got.body, got.url))
    try:
        page = got.body[:HEAD_BYTES].decode(got.charset, errors="replace")
    except LookupError:  # a charset the page invented
        page = got.body[:HEAD_BYTES].decode("utf-8", errors="replace")
    named = alternates(page, got.url)
    if not named and (urlsplit(got.url).hostname or "").lower() in YOUTUBE_HOSTS:
        found = _CHANNEL_IN_PAGE.search(got.body)
        named = [YOUTUBE_FEED.format(found.group(1).decode())] if found else []
    if not named:  # a few well-known paths, only when the page names none
        base = urlsplit(got.url)
        named = [urlunsplit((base.scheme, base.netloc, path, "", "")) for path in WELL_KNOWN]
        limit = len(named)
    else:
        limit = MAX_TRY
    for candidate in named[:limit]:
        try:
            return await _feed(web, candidate)
        except FeedError:
            continue
    raise FeedError(NO_FEED)
