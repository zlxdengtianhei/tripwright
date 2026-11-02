"""User-initiated GitHub App manifest registration; credentials return only to the server."""
from __future__ import annotations

import hmac
import html
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx2
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from tripwright.remote.config import credentials


def save_credentials(path: Path, values: dict[str, str]):
    """Atomically append server-owned dotenv values, with mode 600 before the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and path.stat().st_mode & 0o077:
        raise ValueError("Remote secrets file must have mode 600")
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    keys = set(values)
    previous = "\n".join(line for line in previous.splitlines() if line.partition("=")[0] not in keys)
    body = previous.rstrip() + "\n" + "\n".join(name + "=" + json.dumps(value) for name, value in values.items()) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as sink:
            os.fchmod(sink.fileno(), 0o600)
            sink.write(body)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class GitHubSetup:
    def __init__(self, config, identify_static, *, http_client=None):
        self.config = config
        self.identify_static = identify_static
        self.http_client = http_client
        self.sessions: dict[str, tuple[float, str]] = {}

    def configured(self) -> bool:
        values = credentials(self.config)
        return bool(values["TRIPWRIGHT_GITHUB_CLIENT_ID"] or values["TRIPWRIGHT_GITHUB_CLIENT_SECRET"])

    async def session(self, request):
        """The operator obtains one expiring setup link with an existing header token."""
        tenant = self.identify_static(request.headers.get("authorization", ""))
        if tenant is None:
            return JSONResponse({"error": "Bearer authentication required"}, status_code=401)
        if not self.config.oauth or self.configured():
            return JSONResponse({"error": "GitHub app setup is disabled or already complete"}, status_code=409)
        current = time.monotonic()
        self.sessions = {key: value for key, value in self.sessions.items() if value[0] > current}
        if len(self.sessions) >= 8:
            return JSONResponse({"error": "Too many pending setup sessions"}, status_code=429)
        state = secrets.token_urlsafe(32)
        self.sessions[state] = (current + 600, tenant)
        return JSONResponse({"url": self.config.public_url + "/setup/github-app/" + state, "expires_in": 600},
                            headers={"Cache-Control": "no-store"})

    async def page(self, request):
        state = request.path_params["ticket"]
        session = self.sessions.get(state)
        if self.configured() or session is None or session[0] < time.monotonic():
            return HTMLResponse("This setup link has expired or was already used.", status_code=410)
        manifest = {
            "name": "Tripwright-" + state[:10],
            "url": self.config.public_url,
            "redirect_url": self.config.public_url + "/setup/github-app/callback",
            "callback_urls": [self.config.public_url + "/auth/callback"],
            "public": False,
            "hook_attributes": {"url": self.config.public_url + "/github/events", "active": False},
            "default_permissions": {},
            "default_events": [],
            "request_oauth_on_install": False,
        }
        action = "https://github.com/settings/apps/new?" + urlencode({"state": state})
        body = '<!doctype html><meta charset="utf-8"><title>Connect GitHub to Tripwright</title>'
        body += '<h1>Connect your GitHub account</h1><p>GitHub will ask you to create a private login app. '
        body += 'It requests no repository permissions; your trip repository uses a separate deploy key. '
        body += 'The credentials return directly to this server.</p>'
        nonce = secrets.token_urlsafe(16)
        body += f'<form id="github-manifest" method="post" action="{html.escape(action, quote=True)}">'
        body += f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest), quote=True)}">'
        body += '<button type="submit">Continue to GitHub</button></form>'
        body += f'<script nonce="{nonce}">document.getElementById("github-manifest").submit()</script>'
        response = HTMLResponse(body, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                             "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; form-action https://github.com; frame-ancestors 'none'"})
        response.set_cookie("tripwright_setup", state, secure=True, httponly=True, samesite="lax", max_age=600,
                            path="/setup/github-app")
        return response

    async def callback(self, request):
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        session = self.sessions.get(state)
        cookie = request.cookies.get("tripwright_setup", "")
        if (self.configured() or session is None or session[0] < time.monotonic() or
                not cookie or not hmac.compare_digest(cookie, state) or not re.fullmatch(r"[a-zA-Z0-9]{1,200}", code)):
            return HTMLResponse("Invalid or expired GitHub setup session.", status_code=400)
        # Consume before external I/O: no second browser/request can convert this session concurrently.
        self.sessions.pop(state)
        try:
            if self.http_client is None:
                async with httpx2.AsyncClient(timeout=10) as client:
                    result = await self.convert(client, code)
            else:
                result = await self.convert(self.http_client, code)
            login = result.get("owner", {}).get("login", "").casefold()
            if self.config.accounts.get(login) != session[1]:
                return HTMLResponse("This GitHub account is not allowed to configure this tenant.", status_code=403)
            values = {
                "TRIPWRIGHT_GITHUB_CLIENT_ID": str(result["client_id"]),
                "TRIPWRIGHT_GITHUB_CLIENT_SECRET": str(result["client_secret"]),
                "TRIPWRIGHT_GITHUB_APP_ID": str(result["id"]),
                "TRIPWRIGHT_GITHUB_APP_PRIVATE_KEY": str(result["pem"]),
                "TRIPWRIGHT_GITHUB_WEBHOOK_SECRET": str(result["webhook_secret"]),
            }
            if not values["TRIPWRIGHT_GITHUB_CLIENT_ID"] or not values["TRIPWRIGHT_GITHUB_CLIENT_SECRET"]:
                raise ValueError("Missing app credentials")
            save_credentials(self.config.secrets_file, values)
        except (httpx2.HTTPError, ValueError, KeyError, OSError, TypeError):
            return HTMLResponse("GitHub app setup failed. Create a new setup link and retry; no credentials were displayed.", status_code=502)
        response = HTMLResponse("<h1>GitHub login is ready</h1><p>Return to your MCP host and add " +
                                html.escape(self.config.resource) + ". Application credentials were stored on this server.</p>",
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.delete_cookie("tripwright_setup", path="/setup/github-app")
        return response

    async def convert(self, client, code):
        response = await client.post("https://api.github.com/app-manifests/" + code + "/conversions",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "tripwright"})
        response.raise_for_status()
        return response.json()

    def routes(self):
        return [Route("/setup/github-app/session", self.session, methods=["POST"]),
                Route("/setup/github-app/callback", self.callback),
                Route("/setup/github-app/{ticket}", self.page)]
