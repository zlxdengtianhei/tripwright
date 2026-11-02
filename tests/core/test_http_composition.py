import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from tripwright.adapters.mcp import http_app


def test_http_mounts_optional_web_below_r_without_changing_mcp_auth(service, monkeypatch):
    called = []

    def web_app(actual_service, tokens):
        called.append((actual_service, tokens))
        return Starlette(routes=[Route("/hello", lambda request: PlainTextResponse("web mounted"))])

    monkeypatch.setattr("tripwright.adapters.mcp.import_module", lambda name: SimpleNamespace(web_app=web_app))
    tokens = {"fixture-token": "alice"}
    app = http_app(service, tokens)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/r/hello")).text == "web mounted"
            assert (await client.get("/mcp")).status_code == 401

    asyncio.run(request())
    assert called == [(service, tokens)]


def test_http_starts_without_optional_web_module(service, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr("tripwright.adapters.mcp.import_module", missing)
    app = http_app(service, {"fixture-token": "alice"})
    assert [route.path for route in app.routes] == [""]


def test_http_does_not_hide_a_broken_web_dependency(service, monkeypatch):
    def broken(name):
        raise ModuleNotFoundError(name="missing-web-dependency")

    monkeypatch.setattr("tripwright.adapters.mcp.import_module", broken)
    with pytest.raises(ModuleNotFoundError) as error:
        http_app(service, {"fixture-token": "alice"})
    assert error.value.name == "missing-web-dependency"
