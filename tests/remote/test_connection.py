from __future__ import annotations

import asyncio
import stat
import subprocess

import httpx2
import pytest

from tripwright.remote.app import create_app
from tripwright.remote.config import RemoteConfig
from tripwright.remote.connection import connect, connection_file, mirror_path, read_connection, repo_key, trips
from tripwright.remote.tenants import PersonalRepository
from tripwright.repo.git import Git
from tripwright.repo.workspace import init


def bare(path):
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(path)], check=True)
    return path


def seeded(path, trip, remote=None):
    repository = init(path, trip=trip, remote=remote)
    if remote:
        repository.git.run("push", "--quiet", remote, "HEAD:main")
    return repository


def test_repo_key_is_stable_and_private(tmp_path, capsys, monkeypatch):
    from tripwright.cli import main

    data = tmp_path / "remote"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    assert main(["repo", "key"]) == 0
    first = capsys.readouterr().out.strip()
    assert main(["repo", "key"]) == 0
    second = capsys.readouterr().out.strip()
    assert first == second and first.startswith("ssh-ed25519 ")
    assert stat.S_IMODE((data / "ssh" / "deploy_key").stat().st_mode) == 0o600


def test_address_generates_by_default_and_allows_any_public_base(tmp_path, capsys, monkeypatch):
    from tripwright.cli import main

    data = tmp_path / "remote"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    monkeypatch.setenv("PORT", "18000")
    monkeypatch.delenv("TRIPWRIGHT_PUBLIC_URL", raising=False)
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_URL_KEY", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_URL_KEY_GENERATE", raising=False)
    assert main(["address"]) == 0
    first = capsys.readouterr().out.strip()
    assert first.startswith("http://localhost:18000/mcp/") and (data / "url-key").is_file()
    assert main(["address", "--base", "https://tunnel.example"]) == 0
    second = capsys.readouterr().out.strip()
    assert second == first.replace("http://localhost:18000", "https://tunnel.example")


def test_connect_pushes_existing_trips_to_an_empty_remote(tmp_path):
    data = tmp_path / "remote"
    seeded(mirror_path(data), "2030-06-local")
    remote = bare(tmp_path / "empty.git")
    result = connect(data, str(remote))
    assert "pushed the existing local history (2030-06-local)" in result
    assert trips(Git(remote)) == ["2030-06-local"]
    assert Git(mirror_path(data)).remote_url("origin") == str(remote)
    assert read_connection(data) == dict(repo_url=str(remote), branch="main")


def test_connect_adopts_a_remote_trip_on_the_next_open(tmp_path):
    data = tmp_path / "remote"
    seeded(mirror_path(data), None)
    remote = bare(tmp_path / "remote.git")
    seeded(tmp_path / "seed", "2030-07-remote", remote=str(remote))
    result = connect(data, str(remote))
    assert "next request or health check" in result
    assert trips(Git(mirror_path(data))) == []
    assert connection_file(data).is_file()
    registry = PersonalRepository(RemoteConfig(public_url="http://localhost", data_dir=data, repo_url=str(remote)))
    try:
        service = registry.open()
        assert service.current_trip("local") == "2030-07-remote"
        assert service.repository.root == mirror_path(data)
    finally:
        service.jobs.stop()
        service.committer.stop()
        registry.close()


def test_running_service_applies_an_empty_remote_without_restart_and_pushes(tmp_path):
    data = tmp_path / "remote"
    seeded(mirror_path(data), "2030-06-local")
    remote = bare(tmp_path / "empty.git")
    config = RemoteConfig(public_url="https://trip.example", data_dir=data)

    async def run():
        app = create_app(config, key="k" * 40, warm=False)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app)) as client:
                initial = (await client.get("/readyz")).json()
                assert initial["tenants"]["personal"]["storage"] == "local"
                assert app.state.remote.reloads == 0
                connect(data, str(remote))
                connected = (await client.get("/readyz")).json()
                assert connected["tenants"]["personal"]["storage"] == "remote"
                assert app.state.remote.reloads == 1
                await client.get("/readyz")
                assert app.state.remote.reloads == 1, "an unchanged connection does not reopen"
            service = app.state.remote.repository.service
            receipt = service.turn("local", "Write after live connect.", "live-connect", scenario="quick")
            service.committer.flush()
            return receipt["write"]["op_id"]
    operation = asyncio.run(run())
    assert operation in Git(remote).run("log", "--format=%B", "main")


def test_running_service_adopts_a_remote_trip_without_restart(tmp_path):
    data = tmp_path / "remote"
    seeded(mirror_path(data), None)
    remote = bare(tmp_path / "remote.git")
    seeded(tmp_path / "seed", "2030-07-remote", remote=str(remote))
    config = RemoteConfig(public_url="https://trip.example", data_dir=data)

    async def run():
        app = create_app(config, key="k" * 40, warm=False)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app)) as client:
                await client.get("/readyz")
                original = app.state.remote.repository.service
                connect(data, str(remote))
                connected = (await client.get("/readyz")).json()
                assert connected["tenants"]["personal"]["storage"] == "remote"
                service = app.state.remote.repository.service
                assert service is not original and service.current_trip("local") == "2030-07-remote"
                assert app.state.remote.reloads == 1
                await client.get("/readyz")
                assert app.state.remote.reloads == 1
    asyncio.run(run())


def test_connect_rejects_two_trip_histories_without_changing_either(tmp_path):
    data = tmp_path / "remote"
    local = seeded(mirror_path(data), "2030-06-local")
    remote = bare(tmp_path / "remote.git")
    seeded(tmp_path / "seed", "2030-07-remote", remote=str(remote))
    local_head, remote_head = local.git.rev(), Git(remote).rev()
    with pytest.raises(ValueError) as refused:
        connect(data, str(remote))
    message = str(refused.value)
    assert "Both sides have trips" in message and "2030-06-local" in message and "2030-07-remote" in message
    assert local.git.rev() == local_head and Git(remote).rev() == remote_head
    assert not connection_file(data).exists()


def test_connect_can_switch_from_one_remote_to_another(tmp_path):
    data = tmp_path / "remote"
    local = seeded(mirror_path(data), "2030-06-local")
    old = bare(tmp_path / "old.git")
    connect(data, str(old))
    old_head = Git(old).rev()
    new = bare(tmp_path / "new.git")
    connect(data, str(new))
    assert local.git.remote_url("origin") == str(new)
    assert Git(new).rev() == local.git.rev() and Git(old).rev() == old_head
    assert read_connection(data)["repo_url"] == str(new)
