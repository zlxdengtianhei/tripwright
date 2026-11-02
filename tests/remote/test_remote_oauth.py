"""Real proxy/SDK HTTP flow, with only GitHub's HTTP responses replaced."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tripwright.remote.app import create_app
from tripwright.remote.auth import create_provider
from tripwright.remote.config import credentials
from tripwright.remote.setup import save_credentials

FAKE = {"TRIPWRIGHT_GITHUB_CLIENT_ID": "fixture-client", "TRIPWRIGHT_GITHUB_CLIENT_SECRET": "fixture-secret"}


class GitHub:
    def __init__(self):
        self.challenge = None
        self.refreshes = 0

    def __call__(self, request):
        if request.url.path == "/login/oauth/access_token":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [FAKE["TRIPWRIGHT_GITHUB_CLIENT_ID"]]
            assert form["client_secret"] == [FAKE["TRIPWRIGHT_GITHUB_CLIENT_SECRET"]]
            if form.get("grant_type") == ["refresh_token"]:
                self.refreshes += 1
                name = "owner"
            else:
                verifier = form["code_verifier"][0]
                assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == self.challenge
                name = form["code"][0]
            return httpx2.Response(200, json={"access_token": "ghu_" + name, "refresh_token": "ghr_fixture_" + str(self.refreshes),
                                             "expires_in": 28800, "refresh_expires_in": 2592000, "token_type": "bearer"})
        if request.url.path == "/user":
            name = request.headers["authorization"].removeprefix("Bearer ghu_")
            return httpx2.Response(200, json={"id": 123 if name == "owner" else 456, "login": name})
        if request.url.path == "/user/repos":
            return httpx2.Response(200, json=[])
        raise AssertionError("Unexpected fake GitHub endpoint: " + str(request.url))


def upstream_factory(github):
    from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client

    def factory():
        result = AsyncOAuth2Client(client_id=FAKE["TRIPWRIGHT_GITHUB_CLIENT_ID"],
                                   client_secret=FAKE["TRIPWRIGHT_GITHUB_CLIENT_SECRET"], token_endpoint_auth_method="client_secret_post")
        result._client = httpx2.AsyncClient(transport=httpx2.MockTransport(github))
        return result
    return factory


async def authorize(client, github, *, account="owner", redirect="https://claude.ai/api/mcp/auth_callback", bad_verifier=False):
    registration = await client.post("/register", json={"client_name": "Fixture MCP host", "redirect_uris": [redirect],
                                                        "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                                                        "response_types": ["code"]})
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = await client.get("/authorize", params={"client_id": client_id, "redirect_uri": redirect,
                                                      "response_type": "code", "state": "client-original-state",
                                                      "code_challenge": challenge, "code_challenge_method": "S256",
                                                      "resource": "https://trip.example/mcp", "scope": "user offline_access"})
    assert response.status_code == 302, response.text
    consent = await client.get(response.headers["location"])
    assert consent.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', consent.text)[1]
    txn = parse_qs(urlsplit(str(consent.url)).query)["txn_id"][0]
    approved = await client.post("/consent", data={"txn_id": txn, "action": "approve", "csrf_token": csrf},
                                 headers={"Origin": "https://trip.example"})
    assert approved.status_code == 302, approved.text
    upstream = parse_qs(urlsplit(approved.headers["location"]).query)
    github.challenge = upstream["code_challenge"][0]
    callback = await client.get("/auth/callback", params={"code": account, "state": upstream["state"][0]})
    assert callback.status_code == 302, callback.text
    redirected = parse_qs(urlsplit(callback.headers["location"]).query)
    assert redirected["iss"] == ["https://trip.example"]
    assert redirected["state"] == ["client-original-state"]
    token = await client.post("/token", data={"grant_type": "authorization_code", "client_id": client_id,
                                              "redirect_uri": redirect, "code": redirected["code"][0], "code_verifier": "b" * 64 if bad_verifier else verifier,
                                              "resource": "https://trip.example/mcp"})
    return token, client_id


async def tools(app, token):
    async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app),
                                  headers={"Authorization": "Bearer " + token}) as client:
        async with streamable_http_client("https://trip.example/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listing = await session.list_tools()
                assert {tool.name for tool in listing.tools} == {"turn", "query", "propose", "commit", "result", "state", "explain"}
                state = await session.call_tool("state", {})
                assert not state.is_error


def test_oauth_discovery_dcr_consent_pkce_tokens_mcp_refresh_and_restart(remote_config):
    async def run():
        save_credentials(remote_config.secrets_file, FAKE)
        github = GitHub()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as upstream:
            app = create_app(remote_config, {"pat-fixture": "local"}, github_client=upstream)
            provider = app.state.remote.oauth()
            provider._create_upstream_oauth_client = upstream_factory(github)
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app)) as client:
                    denied = await client.get("/mcp")
                    assert denied.status_code == 401
                    assert 'resource_metadata="https://trip.example/.well-known/oauth-protected-resource/mcp"' in denied.headers["www-authenticate"]
                    resource = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
                    assert resource["resource"] == remote_config.resource
                    assert resource["authorization_servers"] == [remote_config.public_url]
                    metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
                    assert metadata["code_challenge_methods_supported"] == ["S256"]
                    assert metadata["client_id_metadata_document_supported"] is True
                    assert "none" in metadata["token_endpoint_auth_methods_supported"]
                    assert metadata["authorization_response_iss_parameter_supported"] is True
                    token, client_id = await authorize(client, github)
                    assert token.status_code == 200, token.text
                    issued = token.json()
                    assert issued["expires_in"] == 3600 and issued["refresh_token"]
                    assert issued["access_token"] != "ghu_owner"
                    await tools(app, issued["access_token"])
                    await tools(app, "pat-fixture")
                    refreshed = await client.post("/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                                  "refresh_token": issued["refresh_token"], "resource": remote_config.resource})
                    assert refreshed.status_code == 200, refreshed.text
                    assert refreshed.json()["refresh_token"] != issued["refresh_token"]
                    reused = await client.post("/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                               "refresh_token": issued["refresh_token"]})
                    assert reused.status_code == 401 and reused.json()["error"] == "invalid_grant"
                    await tools(app, refreshed.json()["access_token"])
                    denied_account, _ = await authorize(client, github, account="stranger")
                    assert denied_account.status_code == 401 and denied_account.json()["error"] == "invalid_grant"
                    bad_pkce, _ = await authorize(client, github, bad_verifier=True)
                    assert bad_pkce.status_code == 401 and bad_pkce.json()["error"] == "invalid_grant"
                    bad_redirect = await client.get("/authorize", params={"client_id": client_id,
                        "redirect_uri": "https://unregistered.example/callback", "response_type": "code",
                        "code_challenge": "a" * 43, "code_challenge_method": "S256"})
                    assert bad_redirect.status_code == 400 and "unregistered.example" not in bad_redirect.headers.get("location", "")
                    assert await provider.verify_token("ghu_owner") is None, "Upstream tokens cannot bypass the proxy's audience check"
                    signed, signature = issued["access_token"].rsplit(".", 1)
                    altered = signed + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
                    assert await provider.verify_token(altered) is None
                    # A fresh provider reopens the same encrypted store and signing key.
                    restored = create_provider(remote_config, credentials(remote_config), github_client=upstream)
                    restored.get_routes("/mcp")
                    assert await restored.verify_token(refreshed.json()["access_token"]) is not None
                    wrong_resource = await client.get("/authorize", params={"client_id": client_id,
                        "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "response_type": "code",
                        "code_challenge": "a" * 43, "code_challenge_method": "S256", "resource": "https://elsewhere.example/mcp"})
                    assert "invalid_target" in wrong_resource.text or "invalid_target" in wrong_resource.headers.get("location", "")
        disk = "\n".join(path.read_text() for path in (remote_config.data_dir / "oauth").rglob("*.json"))
        assert "ghu_owner" not in disk and "ghr_fixture" not in disk and "fixture-secret" not in disk
    asyncio.run(run())


def test_manifest_requires_operator_browser_binding_allowed_owner_and_never_returns_secrets(remote_config):
    async def run():
        def github(request):
            assert request.url.path == "/app-manifests/oneusecode/conversions"
            return httpx2.Response(200, json={"client_id": "fixture-client", "client_secret": "fixture-secret",
                "owner": {"login": "owner"}, "id": 42, "pem": "fixture-pem\nsecond line", "webhook_secret": "fixture-webhook"})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as upstream:
            app = create_app(remote_config, {"pat-fixture": "local"}, github_client=upstream)
            async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app)) as client:
                assert (await client.post("/setup/github-app/session")).status_code == 401
                result = await client.post("/setup/github-app/session", headers={"Authorization": "Bearer pat-fixture"})
                assert result.status_code == 200
                link = result.json()["url"]
                state = link.rsplit("/", 1)[1]
                assert (await client.get("/setup/github-app/callback", params={"state": state, "code": "oneusecode"})).status_code == 400
                page = await client.get(link)
                assert page.status_code == 200 and 'name="manifest"' in page.text
                nonce = re.search(r'<script nonce="([^"]+)"', page.text)[1]
                assert "'nonce-" + nonce + "'" in page.headers["content-security-policy"]
                assert 'getElementById("github-manifest").submit()' in page.text
                callback = await client.get("/setup/github-app/callback", params={"state": state, "code": "oneusecode"})
                assert callback.status_code == 200
                assert "fixture-secret" not in callback.text and "fixture-pem" not in callback.text
                assert credentials(remote_config)["TRIPWRIGHT_GITHUB_CLIENT_SECRET"] == "fixture-secret"
                assert remote_config.secrets_file.stat().st_mode & 0o777 == 0o600
                assert (await client.get("/setup/github-app/callback", params={"state": state, "code": "oneusecode"})).status_code == 400
                assert (await client.post("/setup/github-app/session", headers={"Authorization": "Bearer pat-fixture"})).status_code == 409
                assert (await client.get("/.well-known/oauth-authorization-server")).status_code == 200, "No restart needed after manifest setup"
    asyncio.run(run())
