"""The tool of the Automations pack that follows a feed: `feed_follow(url)`.

feed_follow            write       class feed_follow, target domain:<the feed's host>

The address is the feed's own or a page's (a YouTube channel, a blog); the feed is found as the Feeds tab finds it (connectors/feed_parse.py,
through the guard of `web_fetch`) and followed exactly as the tab adds it. **A page must not be able to make Clara follow something you
did not see**, so the request shows what will be followed (the feed's title, its address and its latest titles) and nothing is followed
without your answer: in the default mode the class asks, also after untrusted content, never approves, and a local-only conversation
refuses it. The look at the feed is made *before* the request (`describe` is not async: `prepare` does it), so it is a fetch like
`web_fetch`'s, and it is made only where a `web_fetch` of that address would run without asking: an address nobody gave, in a turn that
read untrusted content, is not looked at, and Clara is told to ask you for it. What you read is what is stored: when it is allowed,
the feed is found again, and one that is not the one the request showed is not followed.

The titles are written by a stranger: in the request each is one line, cut, in quotes with no quote or bracket inside, last on its
line (gate/outside.py). Clara is told the summary without them, and the tool's answer does not repeat them. She has no tool to remove a
feed or to list yours.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from urllib.parse import urlsplit

from ..connectors.feed_parse import latest
from ..connectors.feeds import ADDRESS_SHOWN, PREVIEW, FeedsError, normal
from ..connectors.tools import _describe_fetch
from ..gate.actions import Action
from ..gate.engine import args_digest
from ..gate.outside import named, one_line
from ..tools.base import Tool, ToolContext, ToolError, acts_as

log = logging.getLogger(__name__)

SHOWN = re.compile(r"\[([^\[\]]*)\]$")  # the address a request shows: what ends its first line
FEED = "feed"


def _url(arguments: dict) -> str:
    url = str(arguments.get("url") or "").strip()
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        raise ToolError("Give a full http or https address: the feed's, or the page's (a YouTube channel, a blog).")
    return url


async def _prepare(context: ToolContext, arguments: dict) -> None:
    """Find the feed, so that the request can show it. Looking is a fetch: it is made only where `web_fetch` of this address would
    run with no card in this conversation (the same gate, the same mode, the same taint)."""
    url = _url(arguments)
    s = context.services
    fetch = replace(_describe_fetch(context, {"url": url}), tool="web_fetch")
    situation = replace(context.turn.situation, mode_name=s.modes.resolve(context.turn.conversation_id))
    decision = s.gate.decide(fetch, situation)
    if decision.effect != "allow":
        raise ToolError(
            f"This address was not looked at: reading it needs the person's permission here ({decision.reason}). "
            "Ask the person to paste the feed's address (or the page's) in the chat."
        )
    try:
        found = await s.feeds.find(url)
    except FeedsError as error:
        raise ToolError(str(error)) from None
    except Exception:  # a call must never crash the turn, and what the site did is not for the model to read
        log.exception("feed_follow: looking at an address failed")
        raise ToolError("The address could not be looked at.") from None
    if len(normal(found.url)) > ADDRESS_SHOWN:
        raise ToolError(
            f"The feed's address is longer than {ADDRESS_SHOWN} characters, which a request cannot show in full."
        )
    try:
        s.feeds.check(found)  # the 51st, or one that is followed already: said before the person is asked
    except FeedsError as error:
        raise ToolError(str(error)) from None
    context.looked[f"{FEED}:{url}"] = found


def _summary(url: str, found) -> str:
    """What the request says: the feed's title, its address and its latest titles. Each title is a line of its own, in quotes, last."""
    if found is None:
        return f"Follow a feed (not looked at yet) from {one_line(url, ADDRESS_SHOWN)}"
    lines = [
        f"Follow the feed {named(found.feed.title, empty='(untitled)')} [{one_line(normal(found.url), ADDRESS_SHOWN)}]"
    ]
    items = latest(found.feed.items, PREVIEW)
    lines.append("Its latest items:" if items else "It has no item yet.")
    for item in items:
        when = item.published.date().isoformat() if item.published else "no date"
        lines.append(f" - {named(item.title, empty='(untitled)')} [{when}]")
    lines.append("Allowing it makes Clara look at the feed while an automation waits for feed items.")
    return "\n".join(lines)


@acts_as("feed_follow")
def _describe(context: ToolContext, arguments: dict) -> Action:
    url = _url(arguments)
    found = context.looked.get(f"{FEED}:{url}")
    host = (urlsplit(found.url if found else url).hostname or "").lower()
    return Action("", "write", "feed_follow", (f"domain:{host}",), _summary(url, found))


def _told(context: ToolContext, arguments: dict) -> str | None:
    """The address the request you allowed showed, or None when no request was made for this call (a mode allowed it)."""
    row = context.services.db.one(
        "SELECT summary FROM approvals WHERE tool = 'feed_follow' AND args_digest = ? AND status = 'approved'"
        " ORDER BY id DESC LIMIT 1",
        (args_digest(arguments),),
    )
    if row is None:
        return None
    found = SHOWN.search(row["summary"].split("\n", 1)[0])
    return found.group(1) if found else ""


async def _follow(context: ToolContext, url: str = "") -> str:
    s = context.services
    arguments = {"url": url}
    _url(arguments)
    try:
        await s.feeds.add(url, by="Clara", shown=_told(context, arguments))
    except FeedsError as error:
        raise ToolError(str(error)) from None
    off = " (feeds are not looked at now: CLARA_FEEDS_POLL_SECONDS is 0)" if not s.settings.feeds_poll_seconds else ""
    return (
        "Now following it. It is looked at while an automation waits for feed items (kind feed.item); the first look only notes where "
        "the feed stands, so nothing that is already in it starts a run. To start something from its new items, make an "
        f"automation on feed.item.{off}"
    )


def feed_tools() -> list[Tool]:
    return [
        Tool(
            "feed_follow",
            "Follow a feed (RSS or Atom: a YouTube channel, a blog, a podcast, arXiv) so that an automation can start from its new "
            "items. Give the address of the feed or of the page (a YouTube channel page, a blog). The person is shown the feed that "
            "was found and its latest titles and must allow it: nothing is followed otherwise. Use it only when the person asked to "
            "follow something, with the address they gave. You cannot remove a feed or list the ones followed.",
            _follow,
            _describe,
            {
                "url": {
                    "type": "string",
                    "description": "The address of the feed, or of a page that names it (https://...).",
                }
            },
            ("url",),
            prepare=_prepare,
        )
    ]
