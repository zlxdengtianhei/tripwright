"""One remote HTTP entry point; authentication chooses a server-configured tenant."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from tripwright.adapters.mcp import http_app, request_user
from tripwright.remote import urlkey
from tripwright.remote.config import RemoteConfig, credentials
from tripwright.remote.setup import GitHubSetup
from tripwright.remote.tenants import HINTS, TenantProblem, Tenants
from tripwright.remote.volume import check_volume
from tripwright.repo.git import GitError

log = logging.getLogger("tripwright.remote")
URL_KEY_MIN = 32  # characters; a connector URL's key is the whole credential


def url_keys() -> dict[str, str]:
    """The URL-key login, on only when the deployer sets it: TRIPWRIGHT_URL_KEYS (a key-to-identity JSON mapping, like
    TRIPWRIGHT_TOKENS) or TRIPWRIGHT_URL_KEY (one key for the identity `local`). A key the service generates itself
    (TRIPWRIGHT_URL_KEY_GENERATE=1) comes from remote.urlkey instead and is added by create_app(key_file=...)."""
    import json

    keys = {}
    if os.environ.get("TRIPWRIGHT_URL_KEYS"):
        try:
            keys = json.loads(os.environ["TRIPWRIGHT_URL_KEYS"])
            if not isinstance(keys, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("TRIPWRIGHT_URL_KEYS must contain a key-to-identity JSON mapping") from None
    if os.environ.get("TRIPWRIGHT_URL_KEY"):
        keys[os.environ["TRIPWRIGHT_URL_KEY"]] = "local"
    return keys


def _credential_map(config: RemoteConfig, given: dict[str, str], what: str, *, minimum: int = 1) -> dict[bytes, str]:
    if any(not isinstance(secret, str) or len(secret) < minimum or not isinstance(owner, str) or
           owner not in config.token_tenants for secret, owner in given.items()):
        raise ValueError(f"Every {what} needs at least {minimum} characters and a configured token_tenants identity")
    if what == "URL key" and any(not secret.replace("-", "").replace("_", "").isalnum() for secret in given):
        raise ValueError("A URL key may contain only letters, digits, '-' and '_'")
    return {hashlib.sha256(secret.encode()).digest(): config.token_tenants[owner] for secret, owner in given.items()}


def _match(stored: dict[bytes, str], secret: str) -> str | None:
    """Constant-time over every stored digest, so timing tells nothing about which or how much matched."""
    digest = hashlib.sha256(secret.encode()).digest()
    found = None
    for candidate, tenant in stored.items():
        if hmac.compare_digest(digest, candidate):
            found = tenant
    return found


class Remote:
    def __init__(self, config: RemoteConfig, tokens: dict[str, str], *, host: str = "127.0.0.1", github_client=None,
                 keys: dict[str, str] | None = None):
        if any(not isinstance(token, str) or not token or not isinstance(owner, str) or
               owner not in config.token_tenants for token, owner in tokens.items()):
            raise ValueError("Every bearer token needs a configured token_tenants identity")
        self.config = config
        self.tokens = {hashlib.sha256(token.encode()).digest(): config.token_tenants[owner] for token, owner in tokens.items()}
        self.url_keys = _credential_map(config, keys or {}, "URL key", minimum=URL_KEY_MIN)
        self.health = {tenant: dict(status="starting") for tenant in config.tenants}
        self.started = datetime.now(timezone.utc)
        self.volume: dict = {}
        self.host = host
        self.github_client = github_client
        self.tenants = Tenants(config)
        self.provider = None
        self.oauth_app = None
        self.apps = {}
        self.lock = asyncio.Lock()
        self.lifetimes: list[tuple[asyncio.Event, asyncio.Task]] = []
        self.hits: dict[str, deque] = {}
        self.setup = GitHubSetup(config, self.static_identity, http_client=github_client)

    def add_url_key(self, key: str) -> None:
        self.url_keys = {**self.url_keys, **_credential_map(self.config, {key: "local"}, "URL key", minimum=URL_KEY_MIN)}

    def static_identity(self, authorization: str) -> str | None:
        scheme, _, credential = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not credential:
            return None
        return _match(self.tokens, credential)

    def oauth(self):
        if self.provider is None and self.config.oauth:
            values = credentials(self.config)
            if values["TRIPWRIGHT_GITHUB_CLIENT_ID"] and values["TRIPWRIGHT_GITHUB_CLIENT_SECRET"]:
                from tripwright.remote.auth import create_provider

                self.provider = create_provider(self.config, values, github_client=self.github_client)
                self.oauth_app = Starlette(routes=self.provider.get_routes("/mcp"))
        return self.provider

    async def tenant_app(self, tenant_id):
        async with self.lock:
            ready = self.apps.get(tenant_id)
            if ready is None:
                ready = asyncio.get_running_loop().create_future()
                self.apps[tenant_id] = ready
                stop = asyncio.Event()

                async def lifetime():
                    try:
                        # Opening and the transport task group belong to this task.
                        # Cancelling an HTTP request cannot start a second writer.
                        try:
                            service = await asyncio.to_thread(self.tenants.open, tenant_id)
                        except Exception as error:
                            self.failed(tenant_id, error)
                            raise
                        if self.health[tenant_id].get("status") != "ok":
                            self.health[tenant_id] = dict(status="ok", checked_at=_stamp())
                        security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                            allowed_hosts=[urlsplit(self.config.public_url).netloc], allowed_origins=[self.config.public_url])
                        child = http_app(service, {}, host=self.host, authenticator=lambda app: app,
                                         include_web=False, transport_security=security)
                        async with child.router.lifespan_context(child):
                            ready.set_result(child)
                            await stop.wait()
                    except Exception as error:
                        if not ready.done():
                            ready.set_exception(error)
                            self.apps.pop(tenant_id, None)
                        else:
                            raise

                task = asyncio.create_task(lifetime(), name="tripwright-tenant-" + tenant_id)
                self.lifetimes = [(event, existing) for event, existing in self.lifetimes if not existing.done()]
                self.lifetimes.append((stop, task))
        return await asyncio.shield(ready)

    # --- startup checks and health ------------------------------------------------------

    def failed(self, tenant_id: str, error: Exception):
        kind = error.kind if isinstance(error, TenantProblem) else "other"
        detail = error.detail if isinstance(error, TenantProblem) else f"{type(error).__name__}: {error}"
        self.health[tenant_id] = dict(status="error", kind=kind, hint=HINTS.get(kind, HINTS["other"]),
                                      checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        log.error("tenant %s cannot open: %s: %s", tenant_id, kind, " | ".join(str(detail).strip().splitlines()[-3:])[-400:])

    async def warm(self, tenant_id: str):
        """Open the tenant now (its committer starts pushing writes left from before a restart, without waiting for a
        request), then check that the remote answers and takes a push. Each failure is logged once by kind."""
        try:
            await self.tenant_app(tenant_id)
        except Exception:
            return  # failed() logged it and keeps it for /readyz; the next request tries again
        service = self.tenants.opened[tenant_id][1]
        repository = service.repository
        if not repository.remote():
            self.health[tenant_id] = dict(status="ok", storage="local", checked_at=_stamp())
            log.info("tenant %s ready (local repository only)", tenant_id)
            return
        write = os.environ.get("TRIPWRIGHT_CHECK_PUSH", "1") != "0"
        kind, detail = await asyncio.to_thread(repository.git.probe, repository.remote(), repository.branch, write=write)
        if kind == "ok":
            self.health[tenant_id] = dict(status="ok", storage="remote", checked_at=_stamp())
            log.info("tenant %s ready: remote answers%s", tenant_id, " and accepts pushes" if write else "")
        else:
            self.health[tenant_id] = dict(status="error", kind=kind, hint=HINTS.get(kind, HINTS["other"]), checked_at=_stamp(),
                                          checked_mono=time.monotonic())
            log.error("tenant %s remote check failed: %s: %s", tenant_id, kind,
                      " | ".join(detail.strip().splitlines()[-3:])[-400:])

    def tenant_health(self, tenant_id: str) -> dict:
        view = dict(self.health[tenant_id])
        opened = self.tenants.opened.get(tenant_id)
        if opened is not None and view.get("status") != "starting":
            committer = opened[1].committer
            status = committer.status()
            oldest = status.get("oldest_unsynced_at")
            view["unsynced"] = status["unsynced"]
            view["oldest_unsynced_age_seconds"] = (
                int((datetime.now(timezone.utc) - datetime.fromisoformat(oldest)).total_seconds()) if oldest else None)
            if status.get("blocked"):
                view.update(status="blocked", kind=status["blocked"]["kind"], hint=status["blocked"]["hint"],
                            since=status["blocked"]["since"])
            elif view.get("status") == "error" and view.get("kind") in ("auth", "protected", "network", "behind", "other"):
                # The startup check failed and something later proved the remote works: a push for any kind, a fetch
                # for a network problem (a fetch proves nothing about write access).
                since = view.get("checked_mono", 0)
                if committer.pushed_at > since or (view["kind"] == "network" and committer.reachable and committer.last_poll > since):
                    view = dict(status="ok", storage="remote", recovered=True)
        view.pop("checked_mono", None)
        return view

    def readiness(self) -> dict:
        tenants = {tenant: self.tenant_health(tenant) for tenant in self.config.tenants}
        return dict(ready=all(view["status"] == "ok" for view in tenants.values()), tenants=tenants,
                    started_at=self.started.isoformat(timespec="seconds"), volume=self.volume.get("state"))

    async def healthz(self, request):
        """Liveness: 200 while the process serves; the body summarises each tenant (no secrets, no paths)."""
        report = self.readiness()
        return JSONResponse(dict(status="ok", ready=report["ready"],
                                 tenants={tenant: view["status"] for tenant, view in report["tenants"].items()}),
                            headers={"Cache-Control": "no-store"})

    async def readyz(self, request):
        """Readiness: 200 only when every tenant opened, its remote answered and no write is blocked; 503 otherwise,
        each tenant with its status, problem kind, fix and the age of its oldest unpushed write."""
        report = self.readiness()
        return JSONResponse(report, status_code=200 if report["ready"] else 503, headers={"Cache-Control": "no-store"})

    # --- requests -----------------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        tenant_id = None
        if scope["path"].startswith("/mcp/") and scope["path"] != "/mcp/" and self.url_keys:
            # URL-key login: /mcp/<key> is the whole credential. Never logged (uvicorn's access log is off).
            tenant_id = _match(self.url_keys, scope["path"][len("/mcp/"):].rstrip("/"))
            if tenant_id is None:
                return await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
            scope = dict(scope, path="/mcp", raw_path=b"/mcp")
        elif scope["path"] not in ("/mcp", "/mcp/"):
            if self.oauth() is not None:
                return await self.oauth_app(scope, receive, send)
            return await JSONResponse({"error": "GitHub OAuth is not configured"}, status_code=503)(scope, receive, send)
        authorization = request.headers.get("authorization", "")
        if tenant_id is None:
            tenant_id = self.static_identity(authorization)
        if tenant_id is None and self.oauth() is not None:
            scheme, _, token = authorization.partition(" ")
            if scheme.casefold() == "bearer" and token:
                verified = await self.provider.verify_token(token)
                login = (verified.claims or {}).get("login") if verified else None
                if isinstance(login, str):
                    tenant_id = self.config.accounts.get(login.casefold())
        if tenant_id is None:
            challenge = 'Bearer resource_metadata="' + self.config.public_url + '/.well-known/oauth-protected-resource/mcp"'
            return await PlainTextResponse("Bearer authentication required", status_code=401,
                                           headers={"WWW-Authenticate": challenge})(scope, receive, send)
        current = time.monotonic()
        hits = self.hits.setdefault(tenant_id, deque())
        while hits and hits[0] <= current - 60:
            hits.popleft()
        if len(hits) >= 120:
            return await PlainTextResponse("Rate limit exceeded", status_code=429,
                                           headers={"Retry-After": "60"})(scope, receive, send)
        hits.append(current)
        try:
            child = await self.tenant_app(tenant_id)
        except (GitError, ValueError, OSError, TimeoutError, subprocess.TimeoutExpired) as error:
            kind = error.kind if isinstance(error, TenantProblem) else "other"
            return await JSONResponse({"error": "Tenant repository is unavailable; ask the operator to check its deploy key and configuration",
                                       "kind": kind, "hint": HINTS.get(kind, HINTS["other"])},
                                      status_code=503)(scope, receive, send)
        identity = request_user.set(self.config.tenants[tenant_id].owner)
        try:
            await child(scope, receive, send)
        finally:
            request_user.reset(identity)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_app(config: RemoteConfig, tokens: dict[str, str], *, host: str = "127.0.0.1", github_client=None,
               keys: dict[str, str] | None = None, warm: bool = True, key_file: Path | None = None):
    """Serve setup, OAuth metadata, /healthz and /readyz. At startup every tenant opens in the background (so pending
    writes are pushed after a restart) and its remote is checked; a request for a tenant still opening waits for it.
    With key_file (TRIPWRIGHT_URL_KEY_GENERATE=1) the URL key of the identity `local` is read from that file, or created
    at startup just after the data directory is checked (so a first start still reports the directory as new)."""
    # Validate the file's permissions at startup, before accepting any request.
    values = credentials(config)
    remote = Remote(config, tokens, host=host, github_client=github_client, keys=keys)
    create_key = False
    if key_file is not None:
        if "local" not in config.token_tenants:
            raise ValueError(f"{urlkey.SWITCH} signs in as the identity `local`: map it in token_tenants")
        create_key = not key_file.exists()
        if not create_key:  # a broken file stops the start here, with the reason, before serving anything
            remote.add_url_key(urlkey.ensure(key_file))
    if config.oauth and not (values["TRIPWRIGHT_GITHUB_CLIENT_ID"] and values["TRIPWRIGHT_GITHUB_CLIENT_SECRET"]):
        log.warning("oauth is on but the GitHub client ID or secret is missing: GitHub sign-in answers 503 until they are set")
    if remote.url_keys or key_file is not None:
        log.warning("URL-key login is on: anyone with the connector URL has full access to its trips")

    @asynccontextmanager
    async def lifespan(app):
        remote.volume = check_volume(config.data_dir)
        if create_key:
            try:
                remote.add_url_key(urlkey.ensure(key_file))
            except (OSError, ValueError) as error:
                log.error("cannot create the URL key in %s: %s", key_file, getattr(error, "strerror", None) or error)
                raise
        warming = [asyncio.create_task(remote.warm(tenant), name="tripwright-warm-" + tenant)
                   for tenant in config.tenants] if warm else []
        try:
            yield
        finally:
            for task in warming:
                task.cancel()
            await asyncio.gather(*warming, return_exceptions=True)
            for stop, _ in remote.lifetimes:
                stop.set()
            try:
                await asyncio.gather(*(task for _, task in remote.lifetimes))
            finally:
                remote.tenants.close()

    app = Starlette(routes=[Route("/healthz", remote.healthz), Route("/readyz", remote.readyz), *remote.setup.routes(),
                            Mount("/", app=remote)], lifespan=lifespan)
    app.state.remote = remote
    return app
