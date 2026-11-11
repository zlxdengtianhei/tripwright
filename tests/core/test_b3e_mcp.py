"""B3e end to end over real stdio and Streamable HTTP clients: the judge's counterexample A (the block and its fare stay
identical, the pass the fare is included in becomes another ticket) is refused at propose and at commit with
accept_unverified=True; a description update of the same pass commits."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx2
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from test_b3d_mcp import environment
from test_b3e_reference_graph import GROUP, LOCKED, node, trip


async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def scenario(session) -> dict:
    _, _, turn = await call(session, "turn", dict(text="Create", request_id="seed"))
    _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed"))
    _, _, committed = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
    block = next(entry for day in committed["plan"]["days"] for option in day["plans"] for track in option["tracks"]
                 for entry in track["blocks"] if entry["id"] == LOCKED)
    _, _, turn = await call(session, "turn", dict(text="饭后去岩馆 A，整个块和所用通票定了", request_id="lock", nature="user_verbatim",
                                                  native_ref="fixture:lock", speaker="u1"))
    decision = dict(question="饭后去哪、用哪张票", options=["岩馆 A 与通票"], choice="岩馆 A 与通票", subject=GROUP, decided_by="user", must=True,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"), locked_paths={f"/blocks/{LOCKED}": block})
    _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                                                      changes=dict(decisions=[decision]), idempotency_key="lock"))
    _, _, committed = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="lock"))
    results = {}
    for label, change in (("identity", dict(what="Museum-only entry", kind="museum_ticket")), ("description", dict(why_now="Collect at the counter"))):
        plan = deepcopy(committed["plan"])
        node(plan, "booking:pass2").update(change)
        _, _, turn = await call(session, "turn", dict(text="agent updates the pass", request_id=label, nature="agent_inference"))
        _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key=label))
        locked = [check for check in draft["checks"]["checks"] if check["id"].startswith("locked:")]
        error, text, version = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key=label, accept_unverified=True))
        results[label] = dict(status=draft["status"], lock=[(check["status"], check["details"]["changed"]) for check in locked],
                              commit_error=error, commit_text=text[:300], version=None if error else version["number"])
    _, _, state = await call(session, "state", {})
    results["stored_pass2"] = node(state["plan"], "booking:pass2")
    return results


async def over_stdio(directory: Path) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await scenario(session)


async def over_http(directory: Path) -> dict:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    key = "b3e-only-" + "k" * 40
    variables = dict(environment(), TRIPWRIGHT_URL_KEY=key)
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"), "serve", "--http", "--port", str(port)],
                               env=variables, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}/mcp"
    address = base + "/" + key
    try:
        async with httpx2.AsyncClient() as client:
            for attempt in range(200):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited: " + process.stderr.read())
                try:
                    if (await client.get(base)).status_code == 401:
                        break
                except httpx2.TransportError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("HTTP server did not start")
        async with httpx2.AsyncClient() as client:
            async with streamable_http_client(address, http_client=client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    return await scenario(session)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("transport", [over_stdio, over_http], ids=["stdio", "http"])
def test_counterexample_a_over_real_mcp_transports(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    identity, description = results["identity"], results["description"]
    assert identity["status"] == "rejected" and identity["lock"] == [("conflict", ["booking:pass2/kind", "booking:pass2/what"])], identity
    assert identity["commit_error"] and "booking:pass2/kind, booking:pass2/what changed" in identity["commit_text"], identity
    assert description["status"] == "proposed" and description["lock"] == [("passed", [])], description
    assert not description["commit_error"] and description["version"] == 3, description
    assert results["stored_pass2"]["what"] == "Lakeside pass 2" and results["stored_pass2"]["why_now"] == "Collect at the counter"
