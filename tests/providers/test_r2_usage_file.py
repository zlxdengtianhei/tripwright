"""R2 item 2: the route-search counter cannot be written (read-only root file system) → an unverified answer with the
reason, not a crashed tool. No network: Transitous points at a closed local port; the counter is touched first."""
from __future__ import annotations

import asyncio

import pytest

from tripwright.adapters.mcp import create_server, request_user
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

ARGS = {"origin": "48.2085,16.3721", "destination": "48.1850,16.3760", "when": "2030-06-01T21:00:00+02:00"}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_TRANSITOUS_API", "http://127.0.0.1:9/api")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_TRANSITOUS_USAGE", raising=False)
    store = Store(tmp_path / "s.sqlite3")
    yield store
    store.close()


def transit(store):
    service = Tripwright(store, query_router=QueryRouter(store, mode="live"))
    try:
        result = asyncio.run(service.query("local", "transit", ARGS))
        server = create_server(service, local_user="local", manage_jobs=False)

        async def tool():
            token = request_user.set("local")
            try:
                return await server.call_tool("query", {"kind": "transit", "arguments": ARGS})
            finally:
                request_user.reset(token)
        return result, asyncio.run(tool())
    finally:
        service.jobs.stop()


def test_unwritable_counter_file_answers_unverified_with_the_reason(live, tmp_path, monkeypatch):
    # Root ignores permission bits, so the "read-only" HOME is a regular file: mkdir under it fails with an OSError.
    blocker = tmp_path / "home-is-a-file"
    blocker.write_text("")
    monkeypatch.setenv("HOME", str(blocker))
    result, over_mcp = transit(live)
    assert result["status"] == "unverified"
    text = str(result)
    assert "USAGE_FILE" in text and "TRIPWRIGHT_TRANSITOUS_USAGE" in text
    assert "USAGE_FILE" in str(over_mcp)  # the tool answered; the host sees why


def test_writable_counter_file_counts_and_reaches_the_upstream(live, tmp_path, monkeypatch):
    usage = tmp_path / "data" / "transitous-routing.log"
    monkeypatch.setenv("TRIPWRIGHT_TRANSITOUS_USAGE", str(usage))
    result, _ = transit(live)
    assert result["status"] == "unverified" and "USAGE_FILE" not in str(result)
    assert "UPSTREAM_UNREACHABLE" in str(result)
    assert len(usage.read_text().splitlines()) >= 1
