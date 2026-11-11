"""R2 part three (Railway): the whole configuration from environment variables, GitHub over port 443 with pinned host
keys, $PORT, and an idle server that sends nothing until the next request (which fetches once before answering)."""
from __future__ import annotations

import asyncio
import stat
import time

import httpx2
import pytest

from tripwright.remote.envconfig import config_from_env, github_repo, wanted

URL_KEY = "u" * 40
HOST_KEYS = ["ssh-ed25519 AAAAC3fixture", "ecdsa-sha2-nistp256 AAAAE2fixture"]


@pytest.mark.parametrize("given", ["git@github.com:owner/trip.git", "git@github.com:owner/trip", "https://github.com/owner/trip",
                                   "ssh://git@github.com/owner/trip.git", "ssh://git@ssh.github.com:443/owner/trip.git"])
def test_github_addresses_go_over_port_443(given):
    assert github_repo(given) == "ssh://git@ssh.github.com:443/owner/trip.git"


def test_other_hosts_keep_their_address():
    assert github_repo("git@gitlab.com:owner/trip.git") is None


def test_railway_variables_make_a_github_tenant_with_a_600_key_and_pinned_443_host_keys(tmp_path):
    environ = {"RAILWAY_PUBLIC_DOMAIN": "trip-production.up.railway.app", "TRIPWRIGHT_DATA_DIR": str(tmp_path / "remote"),
               "TRIPWRIGHT_REPO_URL": "https://github.com/owner/trip"}
    assert wanted(environ)
    config = config_from_env(environ, fetch_keys=lambda: HOST_KEYS)
    assert config.public_url == "https://trip-production.up.railway.app"
    assert config.repo_url == "ssh://git@ssh.github.com:443/owner/trip.git"
    assert config.ssh_key.is_file() and stat.S_IMODE(config.ssh_key.stat().st_mode) == 0o600
    assert (tmp_path / "remote" / "ssh" / "deploy_key.pub").read_text().startswith("ssh-ed25519 ")
    hosts = config.known_hosts.read_text().splitlines()
    assert "[ssh.github.com]:443 ssh-ed25519 AAAAC3fixture" in hosts and "github.com ssh-ed25519 AAAAC3fixture" in hosts


def test_without_a_repository_the_variables_make_a_local_repository(tmp_path):
    config = config_from_env({"TRIPWRIGHT_PUBLIC_URL": "https://trip.example", "TRIPWRIGHT_DATA_DIR": str(tmp_path)})
    assert config.repo_url is None


def test_no_variables_are_needed_and_an_old_private_key_variable_is_ignored(tmp_path, caplog):
    assert not wanted({"TRIPWRIGHT_URL_KEY": URL_KEY})
    config = config_from_env({"TRIPWRIGHT_DATA_DIR": str(tmp_path), "PORT": "18000",
                              "TRIPWRIGHT_DEPLOY_KEY": "old-unused-private-value"})
    assert config.public_url == "http://localhost:18000" and config.repo_url is None
    assert not (tmp_path / "ssh" / "deploy_key").exists()
    assert "TRIPWRIGHT_DEPLOY_KEY is ignored" in caplog.text and "old-unused-private-value" not in caplog.text


def test_serve_reads_port_and_starts_the_remote_app_from_variables(tmp_path, monkeypatch):
    from tripwright import cli

    seen = {}
    for name in ("TRIPWRIGHT_REPO_URL", "TRIPWRIGHT_URL_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORT", "18999")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "trip.up.railway.app")
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(tmp_path / "remote"))
    monkeypatch.setenv("TRIPWRIGHT_URL_KEY", URL_KEY)
    monkeypatch.setattr(cli, "serve_http", lambda app, arguments: seen.update(app=app, port=arguments.port))
    assert cli.main(["serve", "--http"]) == 0
    assert seen["port"] == 18999 and seen["app"].state.remote.config.public_url == "https://trip.up.railway.app"


def test_an_idle_server_stops_polling_and_the_next_request_fetches_once(remote_config, monkeypatch):
    from tripwright.remote.app import create_app
    from tripwright.remote.tenants import PersonalRepository

    monkeypatch.setenv("TRIPWRIGHT_ACTIVE_WINDOW", "1")
    registry = PersonalRepository(remote_config)
    service = registry.open()
    fetched = []
    original = service.repository.git.fetch

    def counted(*args):
        fetched.append(time.monotonic())
        return original(*args)
    monkeypatch.setattr(service.repository.git, "fetch", counted)
    app = create_app(remote_config, key=URL_KEY, warm=False, service=service)
    service.committer.poll_seconds = 0.2

    async def run():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(1.5)
            assert fetched == [], "no request yet: no outbound traffic"
            async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.ASGITransport(app=app)) as client:
                await client.post("/mcp", json={})  # unauthenticated: stays idle
                assert fetched == []
                await client.post("/mcp/" + URL_KEY, json={})
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
    from tripwright.remote.tenants import PersonalRepository

    registry = PersonalRepository(remote_config)
    try:
        committer = registry.open().committer
        assert committer.active_window is None and committer.polling() and committer.activity() is False
    finally:
        for _, service in registry.opened.values():
            service.jobs.stop()
        registry.close()
