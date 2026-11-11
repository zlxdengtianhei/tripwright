"""R2 items 1, 3, 4, 5 and Q23 on the remote entry: live data by default, tenants open at startup and push what a
crash left, /healthz and /readyz say what is wrong, a leftover lock is cleared, the data directory is marked, a tenant
without a repository keeps a local one, and the URL-key login."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tripwright.remote.app import create_app
from tripwright.remote.config import RemoteConfig
from tripwright.remote.tenants import PersonalRepository
from tripwright.remote.volume import MARKER, check_volume

KEY = "k" * 20 + "-urlkey-fixture-0123456789"


def closed(registry: PersonalRepository):
    for _, service in registry.opened.values():
        service.jobs.stop()
        service.committer.stop()
    registry.close()


def remote_log(config: RemoteConfig) -> str:
    return subprocess.run(["git", "log", "--format=%B", "main"], cwd=config.repo_url,
                          capture_output=True, text=True, check=True).stdout


def test_tenants_serve_live_data_unless_synthetic_is_named(remote_config, monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_PROVIDERS", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_TRANSITOUS_USAGE", raising=False)
    registry = PersonalRepository(remote_config)
    try:
        service = registry.open()
        assert service.query_router.mode == "live"
        # The 720-a-day counter is on the data volume, not in a read-only HOME.
        assert service.query_router.transit.routing.path == remote_config.data_dir / "transitous-routing.log"
    finally:
        closed(registry)
    monkeypatch.setenv("TRIPWRIGHT_PROVIDERS", "synthetic")
    registry = PersonalRepository(remote_config.model_copy(update={"data_dir": remote_config.data_dir.with_name("other")}))
    try:
        assert registry.open().query_router.mode == "synthetic"
    finally:
        closed(registry)


def test_serve_http_without_remote_config_is_live_by_default(tmp_path, monkeypatch):
    from tripwright import cli

    seen = {}
    monkeypatch.delenv("TRIPWRIGHT_PROVIDERS", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_URL_KEY", raising=False)
    monkeypatch.setattr(cli, "serve_http", lambda app, arguments: seen.setdefault("app", app))
    original = cli.open_service

    def recorded(arguments, *, providers=None):
        seen["providers"] = providers
        return original(arguments, providers=providers)
    monkeypatch.setattr(cli, "open_service", recorded)
    assert cli.main(["--db", str(tmp_path / "data" / "t.sqlite3"), "serve", "--http"]) == 0

    async def started():
        async with seen["app"].router.lifespan_context(seen["app"]):
            pass
    asyncio.run(started())
    assert seen["providers"] == "live" and (tmp_path / "data" / MARKER).exists()


def test_startup_opens_the_tenant_and_pushes_what_a_crash_left_without_any_request(remote_config):
    crashed = PersonalRepository(remote_config)
    service = crashed.open()
    receipt = service.turn("local", "Acknowledged, then the process died.", "before-crash", scenario="quick")
    assert receipt["write"]["state"] == "received"
    crashed.close()  # killed: no committer stop, no flush
    assert receipt["write"]["op_id"] not in remote_log(remote_config)

    async def run():
        app = create_app(remote_config, key=KEY)
        async with app.router.lifespan_context(app):
            deadline = time.monotonic() + 10
            while receipt["write"]["op_id"] not in remote_log(remote_config) and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            assert receipt["write"]["op_id"] in remote_log(remote_config)
            async with httpx2.AsyncClient(base_url=remote_config.public_url, transport=httpx2.ASGITransport(app=app)) as client:
                for _ in range(50):  # the push landed; the fetch that settles it follows
                    ready = await client.get("/readyz")
                    if ready.json()["tenants"]["personal"].get("unsynced") == 0:
                        break
                    await asyncio.sleep(0.1)
                assert ready.status_code == 200, ready.text
                view = ready.json()["tenants"]["personal"]
                assert view["status"] == "ok" and view["unsynced"] == 0 and view["oldest_unsynced_age_seconds"] is None
    asyncio.run(run())


def test_a_broken_tenant_is_logged_by_kind_and_shown_on_readyz_while_healthz_stays_up(remote_config, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="tripwright")
    broken = remote_config.model_copy(update={"repo_url": str(tmp_path / "missing.git")})

    async def run():
        app = create_app(broken, key=KEY)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(base_url=remote_config.public_url, transport=httpx2.ASGITransport(app=app)) as client:
                for _ in range(50):
                    ready = await client.get("/readyz")
                    if ready.json()["tenants"]["personal"]["status"] != "starting":
                        break
                    await asyncio.sleep(0.1)
                assert ready.status_code == 503
                view = ready.json()["tenants"]["personal"]
                assert view["status"] == "error" and view["kind"] == "auth" and "repository address" in view["hint"]
                assert str(tmp_path) not in ready.text, "no local paths or Git output on a public endpoint"
                health = await client.get("/healthz", headers={"Host": "healthcheck.railway.app"})
                assert health.status_code == 200 and health.json()["tenants"] == {"personal": "error"}
                answer = await client.post("/mcp/" + KEY, json={})
                assert answer.status_code == 503 and answer.json()["kind"] == "auth"
    asyncio.run(run())
    lines = [record.message for record in caplog.records if "cannot open" in record.message]
    assert lines and "auth" in lines[0]


def test_the_startup_probe_finds_a_read_only_key_and_passes_a_writable_remote(remote_config, tmp_path):
    registry = PersonalRepository(remote_config)
    try:
        git = registry.open().repository.git
        assert git.probe("origin", "main") == ("ok", "")
        assert git.probe("origin", "no-such-branch")[0] == "branch"
        script = tmp_path / "fake-ssh"
        script.write_text("#!/bin/sh\necho 'ERROR: The key you are authenticating with has been marked as read only.' >&2\nexit 128\n")
        script.chmod(0o755)
        git.run("config", "core.sshCommand", str(script))
        git.run("remote", "set-url", "origin", "git@git.example:owner/trip.git")
        assert git.probe("origin", "main")[0] == "auth"
    finally:
        closed(registry)


def test_files_that_cannot_be_made_show_as_blocked_writes_until_a_commit_succeeds(remote_config, monkeypatch):
    tenants = PersonalRepository(remote_config)
    try:
        service = tenants.open()
        made = service.repository_files
        monkeypatch.setattr(service, "repository_files", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("no day")))
        service.turn("local", "Written while a view cannot be made.", "unrenderable", scenario="quick")
        with pytest.raises(TypeError):
            service.committer.flush()
        blocked = service.state("local", "sync")["blocked"]
        assert blocked["kind"] == "commit"
        monkeypatch.setattr(service, "repository_files", made)
        assert service.committer.flush()["pushed"] == "ok"
        assert "blocked" not in service.state("local", "sync")
    finally:
        closed(tenants)


def test_a_leftover_index_lock_blocks_commits_and_reopening_the_tenant_clears_it(remote_config):
    tenants = PersonalRepository(remote_config)
    service = tenants.open()
    lock = service.repository.root / ".git" / "index.lock"
    lock.write_text("")
    service.turn("local", "Written before the lock.", "locked", scenario="quick")
    with pytest.raises(Exception):
        service.committer.flush()
    assert service.state("local", "sync")["blocked"]["kind"] == "commit"
    assert service.turn("local", "And now?", "locked-2", scenario="quick")["write"]["blocked"]["kind"] == "commit"
    service.jobs.stop()
    tenants.close()  # the process dies; the lock stays on disk
    assert lock.exists()
    again = PersonalRepository(remote_config)
    try:
        reopened = again.open()
        assert not lock.exists()
        assert reopened.committer.flush()["pushed"] == "ok"
        assert "blocked" not in reopened.state("local", "sync")
    finally:
        closed(again)


def test_volume_marker_tells_a_new_directory_from_a_kept_one(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="tripwright")
    assert check_volume(tmp_path / "data")["state"] == "new"
    assert any("new and empty" in record.message for record in caplog.records)
    kept = check_volume(tmp_path / "data")
    assert kept["state"] == "kept" and kept["starts"] == 2
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "tripwright.sqlite3").write_text("")
    assert check_volume(tmp_path / "old")["state"] == "unmarked"


def test_a_tenant_without_a_repository_keeps_a_local_one_and_says_so(tmp_path):
    config = RemoteConfig(public_url="http://localhost:8000", data_dir=tmp_path / "data")
    registry = PersonalRepository(config)
    try:
        service = registry.open()
        noted = service.turn("local", "No GitHub here.", "local-only", scenario="quick")
        service.committer.flush()
        sync = service.state("local", "sync")
        assert sync["storage"]["kind"] == "local" and "Back it up yourself" in sync["storage"]["note"]
        assert service.outbox.ref("local", noted["write"] | {"id": noted["write"]["op_id"]})["state"] == "effective"
        log = service.repository.git.run("log", "--format=%B")
        assert noted["write"]["op_id"] in log
    finally:
        closed(registry)
    with pytest.raises(ValueError):
        RemoteConfig(public_url="http://trip.example", data_dir=tmp_path)


async def tools_at(app, url, headers=None):
    async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app),
                                  headers=headers or {}, timeout=30) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return sorted(tool.name for tool in (await session.list_tools()).tools)


def test_an_existing_personal_data_directory_opens_in_place_from_environment(remote_config):
    from tripwright.remote import urlkey
    from tripwright.remote.envconfig import config_from_env
    from tripwright.repo.workspace import init

    data = remote_config.data_dir
    mirror = data / "tenants" / "personal" / "mirror"
    init(mirror, remote=remote_config.repo_url)
    key_file = data / "url-key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(KEY + "\n")
    key_file.chmod(0o600)
    deploy_key, known_hosts = data / "ssh" / "deploy_key", data / "ssh" / "known_hosts"
    deploy_key.parent.mkdir(mode=0o700)
    deploy_key.write_text("fixture-private-key\n")
    known_hosts.write_text("example.test fixture-host-key\n")
    deploy_key.chmod(0o600)
    known_hosts.chmod(0o600)
    before = {path: path.read_bytes() for path in (key_file, deploy_key, known_hosts)}
    environ = {"TRIPWRIGHT_PUBLIC_URL": remote_config.public_url, "TRIPWRIGHT_DATA_DIR": str(data),
               "TRIPWRIGHT_REPO_URL": remote_config.repo_url, "TRIPWRIGHT_BRANCH": "main", urlkey.SWITCH: "1"}
    config = config_from_env(environ)

    async def run():
        app = create_app(config, warm=False, key_file=urlkey.key_file(data, environ))
        async with app.router.lifespan_context(app):
            assert "state" in await tools_at(app, f"https://trip.example/mcp/{KEY}")
            service = app.state.remote.repository.service
            assert service.repository.root == mirror
            service.committer.sync()
            receipt = service.turn("local", "Existing deployment still writes.", "old-deploy-write", scenario="quick")
            service.committer.flush()
            return receipt["write"]["op_id"]
    operation = asyncio.run(run())
    assert operation in remote_log(remote_config)
    assert mirror.is_dir() and all(path.read_bytes() == content for path, content in before.items())


def test_url_key_login_opens_the_tenant_and_a_wrong_or_missing_key_does_not(remote_config, caplog):
    caplog.set_level(logging.DEBUG)
    config = remote_config

    async def run():
        app = create_app(config, key=KEY, warm=False)
        async with app.router.lifespan_context(app):
            assert "state" in await tools_at(app, f"https://trip.example/mcp/{KEY}", headers={"Host": "anything.example"})
            async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app)) as client:
                wrong = await client.post("/mcp/" + KEY[:-1] + "x", json={})
                assert wrong.status_code == 404
                bare = await client.post("/mcp", json={})
                assert bare.status_code == 404
        plain = create_app(config.model_copy(update={"data_dir": config.data_dir.with_name("plain")}), warm=False)
        async with plain.router.lifespan_context(plain):
            async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=plain)) as client:
                assert (await client.post("/mcp/" + KEY, json={})).status_code != 200, "off unless the deployer sets a key"
    asyncio.run(run())
    server_side = [record.getMessage() for record in caplog.records
                   if record.name.startswith(("tripwright", "uvicorn", "mcp.server", "starlette"))]
    assert server_side and not any(KEY in line for line in server_side)
    with pytest.raises(ValueError):
        create_app(config, key="short")
