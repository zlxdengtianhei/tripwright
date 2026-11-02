from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


EXPECTED = {"turn", "query", "propose", "commit", "result", "state", "explain"}


async def check(read_stream, write_stream, label: str):
    async with ClientSession(read_stream, write_stream) as session:
        await session.initialize()
        listing = await session.list_tools()
        assert {tool.name for tool in listing.tools} == EXPECTED
        for tool in listing.tools:
            document = tool.model_dump(by_alias=True)
            assert document.get("title")
            assert "user_id" not in document["inputSchema"].get("properties", {})
            assert "readOnlyHint" in document["annotations"] or "destructiveHint" in document["annotations"]
        receipt = await session.call_tool("turn", {"text": "Smoke test one-off question.", "request_id": "smoke", "scenario": "quick"})
        assert not receipt.is_error
        assert receipt.structured_content["receipt"]["nature"] == "host_restatement"
        refused = await session.call_tool("turn", {"text": "Changed smoke text", "request_id": "smoke"})
        assert refused.is_error
        reason = " ".join(content.text for content in refused.content if hasattr(content, "text"))
        assert "different" in reason.lower(), reason
        from tripwright.demo import example
        bad_zone = await session.call_tool("propose", {"ctx_id": receipt.structured_content["ctx_id"], "base_version": 0,
                                                       "idempotency_key": "smoke-zone", "plan": dict(example()["trip"], timezone="Europe/Viena")})
        assert bad_zone.is_error
        zone_reason = " ".join(content.text for content in bad_zone.content if hasattr(content, "text"))
        assert "Unknown IANA timezone" in zone_reason and "Europe/Viena" in zone_reason, zone_reason
        missing = await session.call_tool("commit", {"draft_id": "missing-draft", "idempotency_key": "missing"})
        assert missing.is_error
        assert any("Unknown drafts handle" in content.text for content in missing.content if hasattr(content, "text"))
        state = await session.call_tool("state", {})
        assert not state.is_error and state.structured_content["number"] == 0
        prompts = await session.list_prompts()
        assert any(prompt.name == "tripwright-main" for prompt in prompts.prompts)
        prompt = await session.get_prompt("tripwright-main")
        assert prompt.messages
        resources = await session.list_resources()
        assert resources.resources
        await session.read_resource("tripwright://skills/tripwright-main")
        print(f"{label}: seven tools, annotations, turn, state, prompt, skill resource and readable refusal reasons OK")


async def stdio(directory: Path):
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"])
    async with stdio_client(parameters) as streams:
        await check(*streams, "stdio")


async def http(directory: Path):
    import json

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ, TRIPWRIGHT_TOKENS=json.dumps({"smoke-only-a": "alice", "smoke-only-b": "bob"}))
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"),
                                "serve", "--http", "--port", str(port)], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    address = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx2.AsyncClient() as client:
            for attempt in range(100):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited: " + process.stderr.read())
                try:
                    response = await client.get(address)
                except httpx2.TransportError:
                    await asyncio.sleep(0.05)
                else:
                    assert response.status_code == 401
                    break
            else:
                raise RuntimeError("HTTP server did not start")
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer smoke-only-a"}) as client:
            async with streamable_http_client(address, http_client=client) as streams:
                await check(*streams, "http")
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer smoke-only-b"}) as client:
            async with streamable_http_client(address, http_client=client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    state = await session.call_tool("state", {"view": "export"})
                    assert state.structured_content["records"]["events"] == []
        print("http: unauthenticated request rejected; bearer users remain isolated across connections")
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["both", "stdio", "http"], default="both")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tripwright-smoke-") as directory:
        if arguments.transport in ("both", "stdio"):
            await stdio(Path(directory))
        if arguments.transport in ("both", "http"):
            await http(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
