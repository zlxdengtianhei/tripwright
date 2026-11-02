"""R2 part three (Railway): the whole configuration from environment variables, GitHub over port 443 with pinned host
keys, $PORT, and an idle server that sends nothing until the next request (which fetches once before answering)."""
from __future__ import annotations

import asyncio
import json
import stat
import time

import httpx2
import pytest

from tripwright.remote.envconfig import config_from_env, github_repo, wanted

KEY = "fixture-deploy-key-line-1\nfixture-deploy-key-line-2"  # stand-in text: the service stores it as given, never parses it
HOST_KEYS = ["ssh-ed25519 AAAAC3fixture", "ecdsa-sha2-nistp256 AAAAE2fixture"]


@pytest.mark.parametrize("given", ["git@github.com:owner/trip.git", "git@github.com:owner/trip", "https://github.com/owner/trip",
                                   "ssh://git@github.com/owner/trip.git", "ssh://git@ssh.github.com:443/owner/trip.git"])
def test_github_addresses_go_over_port_443(given):
    assert github_repo(given) == "ssh://git@ssh.github.com:443/owner/trip.git"


def test_other_hosts_keep_their_address():
    assert github_repo("git@gitlab.com:owner/trip.git") is None


def test_railway_variables_make_a_github_tenant_with_a_600_key_and_pinned_443_host_keys(tmp_path):
    environ = {"RAILWAY_PUBLIC_DOMAIN": "trip-production.up.railway.app", "TRIPWRIGHT_DATA_DIR": str(tmp_path / "remote"),
               "TRIPWRIGHT_REPO_URL": "https://github.com/owner/trip", "TRIPWRIGHT_GITHUB_LOGINS": "Owner, friend",
               "TRIPWRIGHT_DEPLOY_KEY": KEY.replace("\n", "\\n")}  # a one-line web form keeps literal \n
    assert wanted(environ)
    config = config_from_env(environ, fetch_keys=lambda: HOST_KEYS)
    tenant = config.tenants["personal"]
    assert config.public_url == "https://trip-production.up.railway.app" and config.oauth
    assert config.accounts == {"owner": "personal", "friend": "personal"}
    assert tenant.repo_url == "ssh://git@ssh.github.com:443/owner/trip.git"
    assert tenant.ssh_key.read_text() == KEY + "\n" and stat.S_IMODE(tenant.ssh_key.stat().st_mode) == 0o600
    hosts = tenant.known_hosts.read_text().splitlines()
    assert "[ssh.github.com]:443 ssh-ed25519 AAAAC3fixture" in hosts and "github.com ssh-ed25519 AAAAC3fixture" in hosts
    assert config.secrets_file == tmp_path / "remote-secrets.env"


def test_without_a_repository_the_variables_make_a_local_tenant_and_no_github_sign_in(tmp_path):
    config = config_from_env({"TRIPWRIGHT_PUBLIC_URL": "https://trip.example", "TRIPWRIGHT_DATA_DIR": str(tmp_path)})
    assert config.tenants["personal"].repo_url is None and config.oauth is False and config.accounts == {}


def test_missing_pieces_are_refused_with_what_to_set(tmp_path):
    assert not wanted({"TRIPWRIGHT_TOKENS": "{}"})
    with pytest.raises(ValueError, match="TRIPWRIGHT_DEPLOY_KEY"):
        config_from_env({"TRIPWRIGHT_PUBLIC_URL": "https://trip.example", "TRIPWRIGHT_DATA_DIR": str(tmp_path),
                         "TRIPWRIGHT_REPO_URL": "git@github.com:owner/trip.git"})
    with pytest.raises(ValueError, match="TRIPWRIGHT_PUBLIC_URL"):
        config_from_env({"TRIPWRIGHT_REPO_URL": "git@github.com:owner/trip.git"})


def test_serve_reads_port_and_starts_the_remote_app_from_variables(tmp_path, monkeypatch):
    from tripwright import cli

    seen = {}
    for name in ("TRIPWRIGHT_REMOTE_CONFIG", "TRIPWRIGHT_REPO_URL", "TRIPWRIGHT_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORT", "18999")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "trip.up.railway.app")
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(tmp_path / "remote"))
    monkeypatch.setattr(cli, "serve_http", lambda app, arguments: seen.update(app=app, port=arguments.port))
    assert cli.main(["serve", "--http"]) == 0
    assert seen["port"] == 18999 and seen["app"].state.remote.config.public_url == "https://trip.up.railway.app"


def test_an_idle_server_stops_polling_and_the_next_request_fetches_once(remote_config, monkeypatch):
    from tripwright.adapters.mcp import http_app
    from tripwright.remote.tenants import Tenants

    monkeypatch.setenv("TRIPWRIGHT_ACTIVE_WINDOW", "1")
    registry = Tenants(remote_config)
    service = registry.open("personal")
    fetched = []
    original = service.repository.git.fetch

    def counted(*args):
        fetched.append(time.monotonic())
        return original(*args)
    monkeypatch.setattr(service.repository.git, "fetch", counted)
    app = http_app(service, {"t" * 40: "local"}, include_web=False)
    service.committer.poll_seconds = 0.2

    async def run():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(1.5)
            assert fetched == [], "no request yet: no outbound traffic"
            async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.ASGITransport(app=app)) as client:
                await client.post("/mcp", json={})  # unauthenticated: stays idle
                assert fetched == []
                await client.post("/mcp", json={}, headers={"Authorization": "Bearer " + "t" * 40})
                assert len(fetched) == 1, "the first request after idling fetches before it is answered"
                await asyncio.sleep(0.6)
                assert len(fetched) >= 2, "polls while active"
                await asyncio.sleep(1.5)
                settled = len(fetched)
                await asyncio.sleep(1.0)
                assert len(fetched) == settled, "stops after the window"
    try:
        asyncio.run(run())
    finally:
        registry.close()


def test_without_a_window_the_local_committer_polls_as_before(remote_config):
    from tripwright.remote.tenants import Tenants

    registry = Tenants(remote_config)
    try:
        committer = registry.open("personal").committer
        assert committer.active_window is None and committer.polling() and committer.activity() is False
    finally:
        for _, service in registry.opened.values():
            service.jobs.stop()
        registry.close()
