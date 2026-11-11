"""One personal HTTP entry point: /mcp/<URL key>."""
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
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from tripwright.adapters.mcp import http_app, request_user
from tripwright.remote import urlkey
from tripwright.remote.config import RemoteConfig
from tripwright.remote.tenants import HINTS, PersonalRepository, TenantProblem
from tripwright.remote.volume import check_volume
from tripwright.repo.git import GitError

log = logging.getLogger("tripwright.remote")
URL_KEY_MIN = 32  # characters; a connector URL's key is the whole credential


def url_key() -> str | None:
    """The one key from TRIPWRIGHT_URL_KEY, or None when it is generated from the data volume instead."""
    return os.environ.get("TRIPWRIGHT_URL_KEY") or None


def _match(stored: dict[bytes, str], secret: str) -> bool:
    digest = hashlib.sha256(secret.encode()).digest()
    return any(hmac.compare_digest(digest, candidate) for candidate in stored)


class Remote:
    def __init__(self, config: RemoteConfig, *, host: str = "127.0.0.1", key: str | None = None, service=None):
        self.config = config
        self.url_keys: dict[bytes, str] = {}
        if key is not None:
            self.add_url_key(key)
        self.health = dict(status="starting")
        self.started = datetime.now(timezone.utc)
        self.volume: dict = {}
        self.host = host
        self.repository = PersonalRepository(config, service=service)
        self.ready = None
        self.stop = None
        self.lifetime = None
        self.lock = asyncio.Lock()
        self.hits: deque = deque()

    def add_url_key(self, key: str) -> None:
        if not isinstance(key, str) or len(key) < URL_KEY_MIN:
            raise ValueError(f"A URL key needs at least {URL_KEY_MIN} characters")
        if not key.isascii() or not key.replace("-", "").replace("_", "").isalnum():
            raise ValueError("A URL key may contain only ASCII letters, digits, '-' and '_'")
        self.url_keys = {hashlib.sha256(key.encode()).digest(): "personal"}

    async def personal_app(self):
        async with self.lock:
            ready = self.ready
            if ready is None:
                ready = asyncio.get_running_loop().create_future()
                self.ready = ready
                self.stop = asyncio.Event()

                async def lifetime():
                    try:
                        # Opening and the transport task group belong to this task.
                        # Cancelling an HTTP request cannot start a second writer.
                        try:
                            service = await asyncio.to_thread(self.repository.open)
                        except Exception as error:
                            self.failed(error)
                            raise
                        if self.health.get("status") != "ok":
                            self.health = dict(status="ok", checked_at=_stamp())
                        security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                            allowed_hosts=[urlsplit(self.config.public_url).netloc], allowed_origins=[self.config.public_url])
                        child = http_app(service, host=self.host, transport_security=security)
                        async with child.router.lifespan_context(child):
                            ready.set_result(child)
                            await self.stop.wait()
                    except Exception as error:
                        if not ready.done():
                            ready.set_exception(error)
                            self.ready = None
                        else:
                            raise

                self.lifetime = asyncio.create_task(lifetime(), name="tripwright-personal")
        return await asyncio.shield(ready)

    # --- startup checks and health ------------------------------------------------------

    def failed(self, error: Exception):
        kind = error.kind if isinstance(error, TenantProblem) else "other"
        detail = error.detail if isinstance(error, TenantProblem) else f"{type(error).__name__}: {error}"
        self.health = dict(status="error", kind=kind, hint=HINTS.get(kind, HINTS["other"]),
                           checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        log.error("personal repository cannot open: %s: %s", kind, " | ".join(str(detail).strip().splitlines()[-3:])[-400:])

    async def warm(self):
        """Open the repository now, then check that its remote answers and takes a push."""
        try:
            await self.personal_app()
        except Exception:
            return  # failed() logged it and keeps it for /readyz; the next request tries again
        repository = self.repository.service.repository
        if not repository.remote():
            self.health = dict(status="ok", storage="local", checked_at=_stamp())
            log.info("personal repository ready (local repository only)")
            return
        write = os.environ.get("TRIPWRIGHT_CHECK_PUSH", "1") != "0"
        kind, detail = await asyncio.to_thread(repository.git.probe, repository.remote(), repository.branch, write=write)
        if kind == "ok":
            self.health = dict(status="ok", storage="remote", checked_at=_stamp())
            log.info("personal repository ready: remote answers%s", " and accepts pushes" if write else "")
        else:
            self.health = dict(status="error", kind=kind, hint=HINTS.get(kind, HINTS["other"]), checked_at=_stamp(),
                               checked_mono=time.monotonic())
            log.error("personal remote check failed: %s: %s", kind, " | ".join(detail.strip().splitlines()[-3:])[-400:])

    def personal_health(self) -> dict:
        view = dict(self.health)
        service = self.repository.service
        if service is not None and view.get("status") != "starting":
            status = service.committer.status()
            oldest = status.get("oldest_unsynced_at")
            view["unsynced"] = status["unsynced"]
            view["oldest_unsynced_age_seconds"] = (
                int((datetime.now(timezone.utc) - datetime.fromisoformat(oldest)).total_seconds()) if oldest else None)
            if status.get("blocked"):
                view.update(status="blocked", kind=status["blocked"]["kind"], hint=status["blocked"]["hint"],
                            since=status["blocked"]["since"])
            elif view.get("status") == "error" and view.get("kind") in ("auth", "protected", "network", "behind", "other"):
                # A later push or fetch proved that the remote works again.
                since = view.get("checked_mono", 0)
                if service.committer.pushed_at > since or (view["kind"] == "network" and service.committer.reachable
                                                           and service.committer.last_poll > since):
                    view = dict(status="ok", storage="remote", recovered=True)
        view.pop("checked_mono", None)
        return view

    def readiness(self) -> dict:
        view = self.personal_health()
        return dict(ready=view["status"] == "ok", tenants={"personal": view},
                    started_at=self.started.isoformat(timespec="seconds"), volume=self.volume.get("state"))

    async def healthz(self, request):
        """Liveness: 200 while the process serves; the body keeps the existing health shape."""
        report = self.readiness()
        return JSONResponse(dict(status="ok", ready=report["ready"],
                                 tenants={tenant: view["status"] for tenant, view in report["tenants"].items()}),
                            headers={"Cache-Control": "no-store"})

    async def readyz(self, request):
        """Readiness: 200 only when the repository opened, its remote answered and no write is blocked."""
        report = self.readiness()
        return JSONResponse(report, status_code=200 if report["ready"] else 503, headers={"Cache-Control": "no-store"})

    # --- requests -----------------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope["path"]
        if path in ("/mcp", "/mcp/"):
            return await PlainTextResponse("URL key required", status_code=401)(scope, receive, send)
        if not path.startswith("/mcp/"):
            return await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
        # /mcp/<key> is the whole credential. Never logged (uvicorn's access log is off).
        key = path[len("/mcp/"):].rstrip("/")
        if not key or "/" in key or not _match(self.url_keys, key):
            return await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
        scope = dict(scope, path="/mcp", raw_path=b"/mcp")
        current = time.monotonic()
        while self.hits and self.hits[0] <= current - 60:
            self.hits.popleft()
        if len(self.hits) >= 120:
            return await PlainTextResponse("Rate limit exceeded", status_code=429,
                                           headers={"Retry-After": "60"})(scope, receive, send)
        self.hits.append(current)
        try:
            child = await self.personal_app()
        except (GitError, ValueError, OSError, TimeoutError, subprocess.TimeoutExpired) as error:
            kind = error.kind if isinstance(error, TenantProblem) else "other"
            return await JSONResponse({"error": "Trip repository is unavailable; ask the operator to check its deploy key and configuration",
                                       "kind": kind, "hint": HINTS.get(kind, HINTS["other"])},
                                      status_code=503)(scope, receive, send)
        identity = request_user.set("local")
        try:
            await child(scope, receive, send)
        finally:
            request_user.reset(identity)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_app(config: RemoteConfig, *, host: str = "127.0.0.1", key: str | None = None, warm: bool = True,
               key_file: Path | None = None, service=None):
    """Serve /mcp/<key>, /healthz and /readyz for one person, with the same entry for env-configured and local HTTP use."""
    remote = Remote(config, host=host, key=key, service=service)
    create_key = False
    if key_file is not None:
        create_key = not key_file.exists()
        if not create_key:  # a broken file stops the start here, with the reason, before serving anything
            remote.add_url_key(urlkey.ensure(key_file))
    if remote.url_keys or key_file is not None:
        log.warning("URL-key login is on: anyone with the connector URL has full access to the trips")

    @asynccontextmanager
    async def lifespan(app):
        remote.volume = check_volume(config.data_dir)
        if create_key:
            try:
                remote.add_url_key(urlkey.ensure(key_file))
            except (OSError, ValueError) as error:
                log.error("cannot create the URL key in %s: %s", key_file, getattr(error, "strerror", None) or error)
                raise
        warming = asyncio.create_task(remote.warm(), name="tripwright-warm-personal") if warm else None
        try:
            yield
        finally:
            if warming is not None:
                warming.cancel()
                await asyncio.gather(warming, return_exceptions=True)
            if remote.stop is not None:
                remote.stop.set()
            if remote.lifetime is not None:
                await asyncio.gather(remote.lifetime, return_exceptions=True)
            remote.repository.close()

    app = Starlette(routes=[Route("/healthz", remote.healthz), Route("/readyz", remote.readyz), Mount("/", app=remote)],
                    lifespan=lifespan)
    app.state.remote = remote
    return app
