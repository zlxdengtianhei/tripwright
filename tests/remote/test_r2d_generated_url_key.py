"""R2d: the service makes its own URL key (TRIPWRIGHT_URL_KEY_GENERATE=1) so nobody types it into a command line or a
chat. The file is 600 in the data directory, a restart reads it instead of making another, a key given explicitly
wins, and the key appears in no log line and no health answer."""
from __future__ import annotations

import asyncio
import logging
import stat

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tripwright.remote import urlkey
from tripwright.remote.app import create_app
from tripwright.remote.volume import MARKER

EXPLICIT = "e" * 20 + "-explicit-fixture-0123456789"
SWITCH_ON = {urlkey.SWITCH: "1"}


def server_lines(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records
            if record.name.startswith(("tripwright", "uvicorn", "mcp.server", "starlette"))]


async def tools_at(app, url):
    async with httpx2.AsyncClient(base_url=url.split("/mcp/")[0], transport=httpx2.ASGITransport(app=app),
                                  timeout=30) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return sorted(tool.name for tool in (await session.list_tools()).tools)


async def health(app) -> str:
    async with httpx2.AsyncClient(base_url="https://trip.example", transport=httpx2.ASGITransport(app=app)) as client:
        return (await client.get("/healthz")).text + (await client.get("/readyz")).text


def started(config, key_file, *, keys=None):
    """One start: run the lifespan, sign in with whatever key the file holds, return (key, health bodies)."""
    async def run():
        app = create_app(config, {}, keys=keys, warm=False, key_file=key_file)
        async with app.router.lifespan_context(app):
            key = key_file.read_text().strip()
            assert "state" in await tools_at(app, f"https://trip.example/mcp/{key}")
            return key, await health(app), app.state.remote
    return asyncio.run(run())


def test_the_first_start_generates_a_600_key_that_signs_in_and_is_never_logged(remote_config, caplog):
    caplog.set_level(logging.DEBUG)
    config = remote_config.model_copy(update={"oauth": False})
    key_file = urlkey.key_file(config.data_dir, SWITCH_ON)
    assert key_file == config.data_dir / "url-key" and not key_file.exists()
    key, bodies, remote = started(config, key_file)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert len(key) >= 32 and key.replace("-", "").replace("_", "").isalnum()
    # The key file is written after the data directory check, so a first start still says the directory is new.
    assert remote.volume["state"] == "new" and (config.data_dir / MARKER).exists()
    lines = server_lines(caplog)
    made = [line for line in lines if line.startswith("generated a URL key")]
    assert len(made) == 1 and str(key_file) in made[0] and "railway ssh -- cat" in made[0] and "docker exec" in made[0]
    assert not any(key in line for line in lines), "the key itself is never logged"
    assert key not in bodies, "/healthz and /readyz never carry it"
    assert not list(config.data_dir.glob(".url-key.*")), "no scratch file is left behind"


def test_a_restart_reads_the_same_key_and_a_deleted_file_gives_a_new_one(remote_config, caplog):
    config = remote_config.model_copy(update={"oauth": False})
    key_file = urlkey.key_file(config.data_dir, SWITCH_ON)
    first, _, _ = started(config, key_file)
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    again, _, remote = started(config, key_file)
    assert again == first and len(remote.url_keys) == 1
    lines = server_lines(caplog)
    assert not any(line.startswith("generated a URL key") for line in lines)
    assert any("uses the generated key" in line for line in lines) and not any(first in line for line in lines)
    key_file.unlink()  # changing the key: delete the file and restart
    replaced, _, _ = started(config, key_file)
    assert replaced != first


def test_an_explicit_key_wins_and_the_log_says_so(remote_config, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    environ = {**SWITCH_ON, "TRIPWRIGHT_URL_KEY": EXPLICIT}
    assert urlkey.key_file(remote_config.data_dir, environ) is None
    said = [line for line in server_lines(caplog) if "takes precedence" in line]
    assert len(said) == 1 and "TRIPWRIGHT_URL_KEY" in said[0] and str(remote_config.data_dir / "url-key") in said[0]
    assert not any(EXPLICIT in line for line in server_lines(caplog))

    # The same through `serve --http` with Railway-style variables: only the explicit key opens, no file is made.
    from tripwright import cli

    seen = {}
    for name in ("TRIPWRIGHT_REMOTE_CONFIG", "TRIPWRIGHT_REPO_URL", "TRIPWRIGHT_TOKENS", "TRIPWRIGHT_URL_KEYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "trip.up.railway.app")
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(tmp_path / "railway"))
    monkeypatch.setenv(urlkey.SWITCH, "1")
    monkeypatch.setenv("TRIPWRIGHT_URL_KEY", EXPLICIT)
    monkeypatch.setattr(cli, "serve_http", lambda app, arguments: seen.update(app=app))
    assert cli.main(["serve", "--http"]) == 0

    async def run(app):
        async with app.router.lifespan_context(app):
            return await tools_at(app, f"https://trip.up.railway.app/mcp/{EXPLICIT}")
    assert "state" in asyncio.run(run(seen["app"]))
    assert len(seen["app"].state.remote.url_keys) == 1 and not (tmp_path / "railway" / "url-key").exists()


def test_serve_with_only_the_switch_generates_the_key_on_railway(tmp_path, monkeypatch, caplog):
    from tripwright import cli

    seen = {}
    for name in ("TRIPWRIGHT_REMOTE_CONFIG", "TRIPWRIGHT_REPO_URL", "TRIPWRIGHT_TOKENS", "TRIPWRIGHT_URL_KEY",
                 "TRIPWRIGHT_URL_KEYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "trip.up.railway.app")
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(tmp_path / "remote"))
    monkeypatch.setenv(urlkey.SWITCH, "1")
    monkeypatch.setattr(cli, "serve_http", lambda app, arguments: seen.update(app=app))
    assert cli.main(["serve", "--http"]) == 0
    key_file = tmp_path / "remote" / "url-key"
    assert not key_file.exists(), "made at startup, not when the app is built"
    caplog.set_level(logging.DEBUG)

    async def run(app):
        async with app.router.lifespan_context(app):
            key = key_file.read_text().strip()
            async with httpx2.AsyncClient(base_url="https://trip.up.railway.app",
                                          transport=httpx2.ASGITransport(app=app)) as client:
                wrong = await client.post("/mcp/" + key[:-1] + ("x" if key[-1] != "x" else "y"), json={})
            return key, wrong.status_code
    key, wrong = asyncio.run(run(seen["app"]))
    assert wrong == 404 and stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert not any(key in line for line in server_lines(caplog))


@pytest.mark.parametrize("value", ["generate-" + "x" * 40, "2", "maybe"])
def test_the_switch_is_a_switch_not_a_key(value):
    with pytest.raises(ValueError, match="switch, not the key"):
        urlkey.key_file("/data/remote", {urlkey.SWITCH: value})


@pytest.mark.parametrize("value", ["", "0", "false", "off"])
def test_the_switch_off_reads_and_makes_nothing(value, tmp_path):
    assert urlkey.key_file(tmp_path, {urlkey.SWITCH: value}) is None
    assert urlkey.key_file(tmp_path, {}) is None


@pytest.mark.parametrize("content", ["", "short-key\n", "x" * 31, "a" * 40 + " b", "ключ" * 10])
def test_a_broken_key_file_stops_the_start_with_the_fix_and_without_its_content(remote_config, content):
    config = remote_config.model_copy(update={"oauth": False})
    key_file = config.data_dir / "url-key"
    config.data_dir.mkdir(parents=True)
    key_file.write_text(content, encoding="utf-8")
    key_file.chmod(0o600)
    with pytest.raises(ValueError, match="delete the file and restart") as refused:
        create_app(config, {}, warm=False, key_file=key_file)
    assert not content.strip() or content.strip() not in str(refused.value)


def test_a_key_file_others_can_read_is_narrowed_to_600(remote_config, caplog):
    caplog.set_level(logging.DEBUG)
    config = remote_config.model_copy(update={"oauth": False})
    key_file = config.data_dir / "url-key"
    config.data_dir.mkdir(parents=True)
    key_file.write_text("k" * 43 + "\n")
    key_file.chmod(0o644)
    app = create_app(config, {}, warm=False, key_file=key_file)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600 and len(app.state.remote.url_keys) == 1
    assert any("permissions are now 600" in line for line in server_lines(caplog))


def test_the_generated_key_needs_the_local_identity(remote_config):
    config = remote_config.model_copy(update={"oauth": False, "token_tenants": {"someone": "personal"}})
    with pytest.raises(ValueError, match="identity `local`"):
        create_app(config, {}, warm=False, key_file=config.data_dir / "url-key")
    assert not (config.data_dir / "url-key").exists()


def test_an_unwritable_data_directory_fails_the_start_and_says_why(remote_config, caplog):
    caplog.set_level(logging.DEBUG)
    config = remote_config.model_copy(update={"oauth": False})
    # Root ignores chmod, so the data directory is made unusable by being a regular file.
    config.data_dir.write_text("not a directory")
    key_file = config.data_dir / "url-key"
    app = create_app(config, {}, warm=False, key_file=key_file)

    async def run():
        async with app.router.lifespan_context(app):
            pass
    with pytest.raises(OSError):
        asyncio.run(run())
    assert any(line.startswith("cannot create the URL key") for line in server_lines(caplog))
