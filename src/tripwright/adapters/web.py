from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping
from datetime import datetime

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Route

from tripwright.service import Tripwright
from tripwright.site import result_html


class ResultLinks:
    """Separate read-only capabilities. Issue/revoke only through trusted server code.

    Links live in memory, expire after ttl seconds, and disappear on restart.
    Only SHA-256 digests are retained; these credentials never grant MCP access.
    """

    def __init__(self):
        self._links: dict[bytes, tuple[str, float]] = {}

    def issue(self, owner: str, *, ttl: float = 7 * 24 * 60 * 60) -> str:
        if not owner or ttl <= 0:
            raise ValueError("Owner and positive lifetime required")
        link = secrets.token_urlsafe(18)
        self._links[hashlib.sha256(link.encode()).digest()] = (owner, time.time() + ttl)
        return link

    def revoke(self, link: str) -> None:
        self._links.pop(hashlib.sha256(link.encode()).digest(), None)

    def resolve(self, link: str) -> str | None:
        record = self._links.get(hashlib.sha256(link.encode()).digest())
        return record[0] if record and record[1] > time.time() else None


def web_app(service: Tripwright, tokens: ResultLinks | Mapping[str, str], *, now: datetime | None = None):
    """Serve /r/<link> read-only, also mountable under /r with path=/<link>.

    tokens must be a dedicated result-link registry, never the MCP bearer map.
    A supplied mapping stays live so deleting a key revokes it immediately.
    """
    headers = {"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer",
               "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex, nofollow, noarchive",
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}

    async def result(request):
        link = request.path_params["link"]
        owner = tokens.resolve(link) if isinstance(tokens, ResultLinks) else tokens.get(link)
        if owner is None:
            return PlainTextResponse("Not found", status_code=404, headers=headers)
        try:
            html = result_html(service, owner, now=now)
        except ValueError as error:
            if str(error) != "No committed trip":
                raise
            return PlainTextResponse("No committed trip", status_code=404, headers=headers)
        return HTMLResponse(html, headers=headers)

    return Starlette(routes=[Route("/r/{link}", result, methods=["GET"]), Route("/{link}", result, methods=["GET"])])
