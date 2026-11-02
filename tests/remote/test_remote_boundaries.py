from __future__ import annotations

import asyncio
import threading
from urllib.parse import urlsplit

import httpx2

from tripwright.remote.app import create_app


def test_cancelled_first_request_does_not_create_another_writer(remote_config, monkeypatch):
    async def run():
        app = create_app(remote_config, {"fixture-pat": "local"})
        remote = app.state.remote
        opened = threading.Event()
        release = threading.Event()
        calls = []
        original = remote.tenants.open

        def delayed(tenant):
            calls.append(tenant)
            opened.set()
            assert release.wait(5)
            return original(tenant)

        monkeypatch.setattr(remote.tenants, "open", delayed)
        async with app.router.lifespan_context(app):
            first = asyncio.create_task(remote.tenant_app("personal"))
            assert await asyncio.to_thread(opened.wait, 5)
            first.cancel()
            try:
                await first
            except asyncio.CancelledError:
                pass
            second = asyncio.create_task(remote.tenant_app("personal"))
            release.set()
            await asyncio.wait_for(second, 10)
            assert calls == ["personal"] and len(remote.tenants.opened) == 1
    asyncio.run(run())


def test_manifest_from_an_account_outside_the_allowlist_is_rejected_without_saving(remote_config):
    async def run():
        def upstream(request):
            return httpx2.Response(200, json={"owner": {"login": "stranger"}, "client_id": "fixture-id",
                "client_secret": "fixture-secret", "id": 42, "pem": "fixture-pem", "webhook_secret": "fixture-webhook"})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)) as github:
            app = create_app(remote_config, {"fixture-pat": "local"}, github_client=github)
            async with httpx2.AsyncClient(base_url=remote_config.public_url, transport=httpx2.ASGITransport(app=app)) as client:
                created = await client.post("/setup/github-app/session", headers={"Authorization": "Bearer fixture-pat"})
                link = created.json()["url"]
                ticket = urlsplit(link).path.rsplit("/", 1)[1]
                assert (await client.get(link)).status_code == 200
                denied = await client.get("/setup/github-app/callback", params={"state": ticket, "code": "fixturecode"})
                assert denied.status_code == 403
                assert "fixture-secret" not in denied.text and not remote_config.secrets_file.exists()
    asyncio.run(run())
