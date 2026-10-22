"""The web: search through the Brave Search API or a self-hosted SearXNG, and read pages.

**Search** asks Brave first when an API key is set (the secret `brave_api_key`), then SearXNG (CLARA_SEARXNG_URL) when
Brave cannot answer; the addresses it returns become trusted for this turn. A service that answers with no result is
believed; one that cannot answer (a refused key, a spent monthly credit, a rate limit, every search engine of SearXNG
blocked) is told with its reason, so "nothing matches" is never confused with "the search is down".

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
import html
import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..knowledge.ingest import IngestError, extract

USER_AGENT = "Mozilla/5.0 (compatible; Clara personal agent)"
TIMEOUT = httpx.Timeout(10.0, read=20.0)
MAX_REDIRECTS = 5
MAX_LINKS = 40
TEXT_TYPES = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/json",
    "text/markdown",
    "text/csv",
    "application/xml",
    "text/xml",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)
TAILNET = ipaddress.ip_network("100.64.0.0/10")
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_MAX_RESULTS = 20  # per request
ENGINES_SHOWN = 5


class WebError(ValueError):
    pass


class SearchUnavailable(Exception):
    """A search service could not answer. Not the same as an answer with no result."""


class KeyRefused(SearchUnavailable):
    """The service does not accept the API key."""


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 4 and ip in TAILNET:
        return False
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


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
    BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
        "header",
        "footer",
        "main",
        "table",
        "ul",
        "ol",
    }

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


def _plain(text: object) -> str:
    """Brave marks the words searched for with <strong>: text only, one line."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).split())


def _result(title: object, url: object, snippet: object) -> dict:
    return {"title": _plain(title)[:200], "url": str(url or ""), "snippet": _plain(snippet)[:400]}


class Brave:
    """The Brave Search API (https://api.search.brave.com): a key in a header, JSON back."""

    name = "Brave"

    def __init__(self, key: Callable[[], str | None], transport: httpx.AsyncBaseTransport | None = None):
        self._key = key
        self._transport = transport

    async def search(self, query: str, count: int) -> list[dict]:
        key = self._key()
        if not key:
            raise SearchUnavailable("no API key is set")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.get(
                    BRAVE_URL,
                    params={"q": query, "count": max(1, min(count, BRAVE_MAX_RESULTS))},
                    headers={"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": USER_AGENT},
                )
        except httpx.HTTPError as error:
            raise SearchUnavailable(f"could not be reached ({error or type(error).__name__})") from None
        if response.status_code in (401, 403):
            raise KeyRefused(f"the API key was refused (HTTP {response.status_code})")
        if response.status_code == 429:
            raise SearchUnavailable("rate limit reached or monthly credit used up (HTTP 429)")
        if response.is_error:
            raise SearchUnavailable(f"answered HTTP {response.status_code}")
        try:
            found = (response.json().get("web") or {}).get("results") or []
        except ValueError:
            raise SearchUnavailable("answered something that is not JSON") from None
        return [
            _result(item.get("title"), item.get("url"), item.get("description"))
            for item in found[: max(1, min(count, 10))]
            if item.get("url")
        ]


class SearXNG:
    """A self-hosted SearXNG with its JSON format on."""

    name = "SearXNG"

    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None):
        self._url = url.rstrip("/")
        self._transport = transport

    async def search(self, query: str, count: int) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.get(
                    f"{self._url}/search",
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": USER_AGENT},
                )
        except httpx.HTTPError as error:
            raise SearchUnavailable(f"could not be reached ({error or type(error).__name__})") from None
        if response.is_error:
            raise SearchUnavailable(f"answered HTTP {response.status_code} (is the JSON format enabled?)")
        try:
            data = response.json()
        except ValueError:
            raise SearchUnavailable("answered something that is not JSON") from None
        found = data.get("results") or []
        if not found and data.get("unresponsive_engines"):  # no result because its engines were refused or timed out
            engines = ", ".join(
                f"{item[0]}: {item[1]}"
                for item in data["unresponsive_engines"][:ENGINES_SHOWN]
                if isinstance(item, list) and len(item) >= 2
            )
            raise SearchUnavailable(f"no search engine answered ({engines or 'all failed'})")
        return [
            _result(item.get("title"), item.get("url"), item.get("content"))
            for item in found[: max(1, min(count, 10))]
            if item.get("url")
        ]


class Web:
    def __init__(
        self,
        searxng_url: str,
        max_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver=None,
        brave_key: Callable[[], str | None] | None = None,
        on_key_refused: Callable[[str], None] | None = None,
    ):
        self.searxng_url = searxng_url.rstrip("/")
        self.max_bytes = max_bytes
        self._transport = transport
        self._resolve = resolver or resolve_public
        self._brave_key = brave_key
        self._on_key_refused = on_key_refused

    def can_search(self) -> bool:
        return bool(self.searxng_url) or bool(self._brave_key and self._brave_key())

    def _providers(self) -> list[Brave | SearXNG]:
        found: list[Brave | SearXNG] = []
        if self._brave_key is not None and self._brave_key():
            found.append(Brave(self._brave_key, self._transport))
        if self.searxng_url:
            found.append(SearXNG(self.searxng_url, self._transport))
        return found

    async def search(self, query: str, count: int = 5) -> list[dict]:
        """The results of the first service that can answer. [] is a real answer: nothing matches."""
        if not query.strip():
            raise WebError("The query is empty.")
        providers = self._providers()
        if not providers:
            raise WebError("No search service is set up: set brave_api_key (Admin, Secrets), or CLARA_SEARXNG_URL.")
        failures = []
        for provider in providers:
            try:
                return await provider.search(query, count)
            except KeyRefused as error:
                if self._on_key_refused is not None:
                    self._on_key_refused(provider.name)
                failures.append(f"{provider.name}: {error}")
            except SearchUnavailable as error:
                failures.append(f"{provider.name}: {error}")
        raise WebError(
            "The web search is not working right now ("
            + "; ".join(failures)
            + "). Trying again at once will not help: "
            "tell the person, or answer from what you know and say so."
        )

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
            pinned = urlunsplit(
                (
                    parts.scheme,
                    f"[{address}]:{port}" if ":" in address else f"{address}:{port}",
                    parts.path or "/",
                    parts.query,
                    "",
                )
            )
            headers = {
                "Host": parts.netloc.rsplit("@", 1)[-1],
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            }
            try:
                async with (
                    httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport, follow_redirects=False) as http,
                    http.stream(
                        "GET",
                        pinned,
                        headers=headers,
                        extensions={"sni_hostname": parts.hostname} if parts.scheme == "https" else {},
                    ) as response,
                ):
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
