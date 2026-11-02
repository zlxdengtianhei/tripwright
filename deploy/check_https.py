"""Verify a deployment's HTTPS endpoint and MCP tools without persisting or displaying the token.

    .venv/bin/python deploy/check_https.py --url https://trip.example.org [--ssh my-server] [--env-file PATH]

The token is TRIPWRIGHT_CHECK_TOKEN from this process's environment, or (with --ssh) the first token of the server's
root-only env file, read over SSH into memory. Arguments fall back to TRIPWRIGHT_PUBLIC_URL and TRIPWRIGHT_SSH.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED = {"turn", "query", "propose", "commit", "result", "state", "explain"}


def target(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("TRIPWRIGHT_PUBLIC_URL"), help="HTTPS origin, no path")
    parser.add_argument("--ssh", default=os.environ.get("TRIPWRIGHT_SSH"), help="SSH destination holding the env file")
    parser.add_argument("--env-file", default="/opt/tripwright/tripwright.env", help="env file path on the server")
    arguments = parser.parse_args(argv)
    if not arguments.url:
        parser.error("give --url (or TRIPWRIGHT_PUBLIC_URL)")
    arguments.url = arguments.url.rstrip("/")
    return arguments


def remote_token(arguments) -> str:
    if os.environ.get("TRIPWRIGHT_CHECK_TOKEN"):
        return os.environ["TRIPWRIGHT_CHECK_TOKEN"]
    if not arguments.ssh:
        raise SystemExit("Set TRIPWRIGHT_CHECK_TOKEN or give --ssh to read a token from the server")
    # SSH stdout is a private pipe captured in memory, never inherited by the
    # terminal. No secret appears in command arguments or a local file.
    source = f"""import json, shlex, sys
from pathlib import Path
line = next(line for line in Path({arguments.env_file!r}).read_text().splitlines() if line.startswith('TRIPWRIGHT_TOKENS='))
tokens = json.loads(shlex.split(line)[0].partition('=')[2])
sys.stdout.write(next(iter(tokens)))
"""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", arguments.ssh, "python3 -c " + shlex.quote(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if result.returncode or not result.stdout:
        raise SystemExit("Could not read the server token over SSH; no credential was displayed")
    return result.stdout.decode()


async def main(arguments) -> None:
    base = arguments.url
    async with httpx2.AsyncClient(timeout=30) as client:
        health = await client.get(base + "/healthz")
        assert health.status_code == 200 and health.json().get("status") == "ok", health.status_code
        ready = await client.get(base + "/readyz")
        rejected = await client.get(base + "/mcp")
        assert rejected.status_code == 401, rejected.status_code
        assert rejected.headers.get("www-authenticate", "").startswith("Bearer")
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + remote_token(arguments)}, timeout=30
    ) as client:
        async with streamable_http_client(base + "/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                listing = await session.list_tools()
                tools = sorted(tool.name for tool in listing.tools)
                assert set(tools) == EXPECTED, tools
                state = await session.call_tool("state", {})
                assert not state.is_error, "MCP state tool returned an error"
                print(json.dumps({
                    "https_verified": True,
                    "health_status": health.status_code,
                    "ready_status": ready.status_code,
                    "unauthenticated_status": rejected.status_code,
                    "server": initialized.server_info.name,
                    "tools": tools,
                    "state_ok": True,
                }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(target()))
