"""R2 item 4: an error a tool did not expect is one log line for the operator and a refusal that says what happened,
instead of the SDK's bare "Error executing tool"; the expected kinds keep their own words."""
from __future__ import annotations

import asyncio
import logging

from tripwright.adapters.mcp import create_server, request_user
from tripwright.service import Tripwright
from tripwright.storage import Store


def call(service, name, arguments):
    server = create_server(service, local_user="local", manage_jobs=False)

    async def run():
        token = request_user.set("local")
        try:
            return await server.call_tool(name, arguments)
        except Exception as error:
            return error
        finally:
            request_user.reset(token)
    return asyncio.run(run())


def test_an_unexpected_error_is_logged_once_and_explained_to_the_host(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="tripwright")
    store = Store(tmp_path / "s.sqlite3")
    service = Tripwright(store)

    def broken(*args, **kwargs):
        raise PermissionError(13, "Permission denied", "/data/somewhere")
    monkeypatch.setattr(service, "state", broken)
    try:
        outcome = str(call(service, "state", {}))
        assert "Unexpected server error in state (PermissionError)" in outcome and "operator" in outcome
        lines = [record.getMessage() for record in caplog.records if record.name == "tripwright.tools"]
        assert len(lines) == 1 and "PermissionError" in lines[0]
        monkeypatch.setattr(service, "state", lambda *args: (_ for _ in ()).throw(ValueError("Unknown view")))
        expected = str(call(service, "state", {}))
        assert "Unknown view" in expected and "Unexpected" not in expected
    finally:
        service.jobs.stop()
        store.close()
