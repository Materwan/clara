"""A stand-in internet for the feed tests: pages and feeds by host and path, a log of every request the server made, and a resolver
that knows which names are private (nothing here reaches a real address)."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from xml.sax.saxutils import escape

import httpx

from clara.connectors.web import Web, WebError, resolve_public

PUBLIC = "93.184.216.34"


class Net:
    def __init__(self) -> None:
        self.pages: dict[tuple[str, str], dict] = {}
        self.log: list[str] = []  # "host path" of each request that reached a page
        self.private = {"internal.example", "localhost"}

    def put(self, url: str, body: str | bytes = b"", kind: str = "text/xml", status: int = 200, **more) -> None:
        """A page at an address. `etag` and `modified` make it answer a conditional request with 304; `location` redirects;
        `then` (a callable) gives the next body each time it is asked."""
        parts = httpx.URL(url)
        key = (parts.host, parts.raw_path.decode())
        self.pages[key] = {"body": body.encode() if isinstance(body, str) else body, "kind": kind, "status": status, **more}

    def change(self, url: str, body: str | bytes, **more) -> None:
        page = self.pages[(httpx.URL(url).host, httpx.URL(url).raw_path.decode())]
        page["body"] = body.encode() if isinstance(body, str) else body
        page.update(more)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"].split(":")[0].lower()
        path = request.url.raw_path.decode()
        self.log.append(f"{host} {path}")
        page = self.pages.get((host, path))
        if page is None:
            return httpx.Response(404, text="Not found")
        headers = {"content-type": page["kind"]}
        if page.get("location"):
            return httpx.Response(page["status"] if page["status"] in (301, 302, 307, 308) else 302, headers={"location": page["location"]})
        if page.get("etag"):
            headers["etag"] = page["etag"]
            if request.headers.get("if-none-match") == page["etag"]:
                return httpx.Response(304, headers=headers)
        if page.get("modified"):
            headers["last-modified"] = page["modified"]
            if request.headers.get("if-modified-since") == page["modified"]:
                return httpx.Response(304, headers=headers)
        return httpx.Response(page["status"], content=page["body"], headers=headers)

    async def resolve(self, host: str, port: int) -> str:
        if host in self.private:
            raise WebError(f"{host} points to a private or local address (10.0.0.1): not fetched.", refused=True)
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return PUBLIC  # a name: any public address
        return await resolve_public(host, port)  # an address written out is checked as the guard checks it

    def web(self, limit: int = 5_000_000) -> Web:
        return Web("", limit, transport=httpx.MockTransport(self.handle), resolver=self.resolve)

    def install(self, web: Web) -> Web:
        """Make a server's own `Web` (the one its tools and its feeds use) ask this internet."""
        web._transport = httpx.MockTransport(self.handle)
        web._resolve = self.resolve
        return web

    def asked(self, host: str) -> list[str]:
        return [line.split(" ", 1)[1] for line in self.log if line.split(" ")[0] == host]


def rss(title: str, items: list[dict], *, extra: str = "") -> str:
    """An RSS 2.0 feed. An item is {title, link, guid, date (a datetime), author, summary}; a missing key is left out."""
    body = []
    for item in items:
        tags = []
        if "title" in item:
            tags.append(f"<title>{escape(item['title'])}</title>")
        if "link" in item:
            tags.append(f"<link>{escape(item['link'])}</link>")
        if "guid" in item:
            tags.append(f'<guid isPermaLink="false">{escape(item["guid"])}</guid>')
        if "date" in item:
            tags.append(f"<pubDate>{format_datetime(item['date'])}</pubDate>")
        if "author" in item:
            tags.append(f"<author>{escape(item['author'])}</author>")
        if "summary" in item:
            tags.append(f"<description>{escape(item['summary'])}</description>")
        body.append("<item>" + "".join(tags) + "</item>")
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(title)}</title>{extra}{"".join(body)}</channel></rss>'


def atom(title: str, entries: list[dict]) -> str:
    """An Atom feed (the entries are written as a channel's: {title, link, guid, date, author, summary})."""
    body = []
    for entry in entries:
        tags = []
        if "guid" in entry:
            tags.append(f"<id>{escape(entry['guid'])}</id>")
        if "title" in entry:
            tags.append(f"<title>{escape(entry['title'])}</title>")
        if "link" in entry:
            tags.append(f'<link rel="alternate" href="{escape(entry["link"])}"/>')
        if "date" in entry:
            tags.append(f"<published>{entry['date'].isoformat()}</published>")
        if "author" in entry:
            tags.append(f"<author><name>{escape(entry['author'])}</name></author>")
        if "summary" in entry:
            tags.append(f"<summary>{escape(entry['summary'])}</summary>")
        body.append("<entry>" + "".join(tags) + "</entry>")
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>{escape(title)}</title>{"".join(body)}</feed>'


def page(*links: tuple[str, str], body: str = "") -> str:
    """An HTML page whose head names these feeds ((type, href))."""
    tags = "".join(f'<link rel="alternate" type="{kind}" href="{href}">' for kind, href in links)
    return f"<!doctype html><html><head><title>A page</title>{tags}</head><body>{body}</body></html>"


def item(n: int, at: datetime | None = None, **more) -> dict:
    found = {"title": f"Item {n}", "link": f"https://blog.example/post/{n}", "guid": f"post-{n}"}
    if at is not None:
        found["date"] = at
    return {**found, **more}


class Clock:
    """The server's clock, which a test moves."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)

    def ago(self, **delta) -> datetime:
        return self.now - timedelta(**delta)
