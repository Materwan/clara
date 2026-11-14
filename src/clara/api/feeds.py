"""The feeds you follow (RSS and Atom), for automations that start from their new items. See connectors/feeds.py and DECISIONS.md,
« Feeds (2026-11-13) ».

    GET /v1/feeds              the feeds: id, address, title, when added, last look, last error (fixed words), items today,
                               since when it is waited for; with the limit (50) and the poll interval (0: never)
    POST /v1/feeds/find        {url}: the feed at an address, or of a page: `{feed_url, title, items: [{title, link, published}]}`,
                               the first 5. Nothing is stored. 422 says why there is none, in fixed words
    POST /v1/feeds             {url}: add it: the address is looked at again and the feed's own address is stored. 201 `{feed}`.
                               409 for a feed you follow already and for the 51st
    DELETE /v1/feeds/{id}      stop following it

The address is fetched by the server through the guard of `web_fetch` (public addresses only, redirects checked, size and time
capped), so looking, like changing the list, needs a token that may answer every request (scope `all`): a script and the Discord
bot may only list. Clara has no tool to remove a feed; `feed_follow` (the Automations pack) adds one only after a request that
shows the feed.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..connectors.feeds import MAX_FEEDS, FeedsError
from .common import ApiError, caller, services
from .modes import settled_by_you

router = APIRouter(prefix="/v1", tags=["feeds"])

BY_YOU = "This token may not look at or change the feeds: the server fetches the address you give."


def _failed(error: FeedsError) -> ApiError:
    return ApiError(error.status, str(error))


class FeedAddress(BaseModel):
    url: str = Field(min_length=1, max_length=2000, description="The address of a feed, or of a page that names one")


@router.get("/feeds")
def listing(request: Request) -> dict:
    s = services(request)
    return {"feeds": s.feeds.listing(), "limit": MAX_FEEDS, "poll_seconds": s.settings.feeds_poll_seconds}


@router.post("/feeds/find")
async def find(body: FeedAddress, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    try:
        return await services(request).feeds.preview(body.url)
    except FeedsError as error:
        raise _failed(error) from None


@router.post("/feeds", status_code=201)
async def add(body: FeedAddress, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    try:
        return {"feed": await services(request).feeds.add(body.url, by=caller(request).name)}
    except FeedsError as error:
        raise _failed(error) from None


@router.delete("/feeds/{feed_id}")
async def remove(feed_id: int, request: Request) -> dict:
    settled_by_you(request, BY_YOU)
    if not services(request).feeds.remove(feed_id, by=caller(request).name):
        raise ApiError(404, f"No feed {feed_id}.")
    return {"removed": True}
