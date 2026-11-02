"""One fetch of a web page to see whether a quoted sentence is on it (A5 web evidence).

The host reads rules, opening hours and events on the web and records them with the sentence it read. With network,
the service may fetch the page once and say whether that sentence is there: `true`, `false`, or `unchecked` with the
reason (no network, a page that needs scripts, a PDF). Only http and https to public addresses; private, loopback and
link-local addresses are refused, redirects included, so a remote service cannot be pointed at its own network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from .common import user_agent


class PageUnavailable(Exception):
    """The page could not be read; `reason` is shown with `quote_found: unchecked`."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def public_url(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http or https address with a host")
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


async def refuse_private(host: str, port: int):
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=0)
    except OSError as error:
        raise PageUnavailable(f"cannot resolve {host}: {error}") from None
    for *_, address in addresses:
        ip = ipaddress.ip_address(address[0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise PageUnavailable(f"{host} is not a public address; the page was not fetched")


class Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def page_text(body: str, content_type: str) -> str:
    if "html" not in content_type:
        return body
    parser = Text()
    parser.feed(body)
    parser.close()
    return " ".join(parser.parts)


SAME = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
                      "–": "-", "—": "-", "−": "-", " ": " ", " ": " "})


def plain(text: str) -> str:
    """Text compared loosely: compatible forms, case, typographic quotes and dashes, and runs of spaces are the same."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).translate(SAME).casefold()).strip()


def quote_on(quote: str, text: str) -> bool:
    return plain(quote) in plain(text)


class PageReader:
    def __init__(self, *, client: httpx.AsyncClient | None = None, timeout: float = 10, max_bytes: int = 3_000_000,
                 redirects: int = 4, check_address: bool = True):
        self.client = client or httpx.AsyncClient()
        self.owns_client = client is None
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.redirects = redirects
        self.check_address = check_address
        self.user_agent = user_agent()

    async def text(self, url: str) -> str:
        for _ in range(self.redirects + 1):
            try:
                host, port = public_url(url)
            except ValueError as error:
                raise PageUnavailable(f"{url}: {error}") from None
            if self.check_address:
                await refuse_private(host, port)
            try:
                async with self.client.stream("GET", url, timeout=self.timeout, follow_redirects=False,
                                              headers={"User-Agent": self.user_agent, "Accept": "text/html,text/plain;q=0.9"}) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise PageUnavailable(f"the page answered HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    if not any(kind in content_type for kind in ("html", "text/plain", "xml")):
                        raise PageUnavailable(f"not an HTML or text page ({content_type or 'no content type'})")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise PageUnavailable(f"the page is larger than {self.max_bytes} bytes")
                    return page_text(body.decode(response.encoding or "utf-8", errors="replace"), content_type)
            except httpx.HTTPError as error:
                raise PageUnavailable(f"the page could not be fetched: {type(error).__name__}") from None
        raise PageUnavailable("too many redirects")

    async def aclose(self):
        if self.owns_client:
            await self.client.aclose()
