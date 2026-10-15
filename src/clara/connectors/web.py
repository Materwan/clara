"""The web: search through a self-hosted SearXNG, and read pages.

**Search** asks SearXNG (CLARA_SEARXNG_URL) for JSON results; the addresses it returns become trusted for this turn.

**Fetch** reads one http(s) page, and refuses anything that is not on the public internet: the host's addresses are
resolved first and every one must be public (no loopback, private, link-local, multicast, reserved, or the
100.64.0.0/10 range Tailscale uses), then the request goes to that checked address (with the name kept for TLS), so a
DNS answer cannot change between the check and the connection. Redirects are followed by hand, at most five, each
checked again. At most CLARA_FETCH_MAX_SIZE bytes are read. HTML becomes plain text with its headings and links; PDF
and Word files become their text.

What a page says is data, not instructions: a turn that read one is *tainted* (the gate raises outside writes).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..knowledge.ingest import IngestError, extract

USER_AGENT = "Mozilla/5.0 (compatible; Clara personal agent)"
TIMEOUT = httpx.Timeout(10.0, read=20.0)
MAX_REDIRECTS = 5
MAX_LINKS = 40
TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml", "application/json", "text/markdown", "text/csv",
              "application/xml", "text/xml", "application/pdf",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
TAILNET = ipaddress.ip_network("100.64.0.0/10")


class WebError(ValueError):
    pass


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 4 and ip in TAILNET:
        return False
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                 or ip.is_reserved or ip.is_unspecified)


async def resolve_public(host: str, port: int) -> str:
    """One public address of `host` (all of them must be public), else WebError."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not public_address(str(literal)):
            raise WebError(f"{host} is not a public address.")
        return str(literal)
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), 10)
    except (OSError, TimeoutError):
        raise WebError(f"{host} could not be resolved.") from None
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise WebError(f"{host} could not be resolved.")
    for address in addresses:
        if not public_address(address):
            raise WebError(f"{host} points to a private or local address ({address}): not fetched.")
    return addresses[0]


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
    BLOCK = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
             "blockquote", "header", "footer", "main", "table", "ul", "ol"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                self._href = urljoin(self.base, href)
                self._anchor = []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href:
            text = " ".join("".join(self._anchor).split())
            if text and len(self.links) < MAX_LINKS * 4:
                self.links.append((text[:80], self._href))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [" ".join(line.split()) for line in joined.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    links: list[tuple[str, str]]


class Web:
    def __init__(self, searxng_url: str, max_bytes: int, transport: httpx.AsyncBaseTransport | None = None,
                 resolver=None):
        self.searxng_url = searxng_url.rstrip("/")
        self.max_bytes = max_bytes
        self._transport = transport
        self._resolve = resolver or resolve_public

    async def search(self, query: str, count: int = 5) -> list[dict]:
        if not query.strip():
            raise WebError("The query is empty.")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.get(f"{self.searxng_url}/search", params={"q": query, "format": "json"},
                                          headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as error:
            raise WebError(f"The search service (SearXNG) could not be reached: {error}") from None
        if response.is_error:
            raise WebError(f"The search service answered HTTP {response.status_code} (is the JSON format enabled?).")
        results = response.json().get("results") or []
        return [{"title": str(item.get("title") or "")[:200], "url": str(item.get("url") or ""),
                 "snippet": " ".join(str(item.get("content") or "").split())[:400]}
                for item in results[: max(1, min(count, 10))] if item.get("url")]

    async def fetch(self, url: str) -> Page:
        current = url.strip()
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise WebError("Only http and https addresses can be read.")
            if parts.username or parts.password:
                raise WebError("Addresses with a user name or password are not fetched.")
            port = parts.port or (443 if parts.scheme == "https" else 80)
            address = await self._resolve(parts.hostname, port)
            pinned = urlunsplit((parts.scheme, f"[{address}]:{port}" if ":" in address else f"{address}:{port}",
                                 parts.path or "/", parts.query, ""))
            headers = {"Host": parts.netloc.rsplit("@", 1)[-1], "User-Agent": USER_AGENT,
                       "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"}
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport, follow_redirects=False) as http, \
                        http.stream("GET", pinned, headers=headers,
                                    extensions={"sni_hostname": parts.hostname} if parts.scheme == "https" else {}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebError("A redirect without a destination.")
                        current = urljoin(current, location)
                        continue
                    if response.is_error:
                        raise WebError(f"The page answered HTTP {response.status_code}.")
                    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if kind and not kind.startswith(TEXT_TYPES):
                        raise WebError(f"Not a page to read ({kind}).")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            break
                    charset = response.charset_encoding or "utf-8"
            except httpx.HTTPError as error:
                raise WebError(f"The page could not be read: {error}") from None
            return self._page(current, kind, bytes(body[: self.max_bytes]), charset)
        raise WebError("Too many redirects.")

    @staticmethod
    def _page(url: str, kind: str, body: bytes, charset: str) -> Page:
        if kind in ("application/pdf",) or kind.endswith("wordprocessingml.document"):
            name = "page.pdf" if kind == "application/pdf" else "page.docx"
            try:
                return Page(url, url, extract(name, body).text, [])
            except IngestError as error:
                raise WebError(f"The document could not be read: {error}") from None
        text = body.decode(charset, errors="replace")
        if kind in ("text/html", "application/xhtml+xml") or "<html" in text[:2000].lower():
            parser = _Text(url)
            parser.feed(text)
            seen: set[str] = set()
            links = [(label, href) for label, href in parser.links if not (href in seen or seen.add(href))]
            return Page(url, " ".join(parser.title.split())[:200] or url, parser.text(), links[:MAX_LINKS])
        return Page(url, url, text, [])
