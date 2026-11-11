"""B3f over real stdio and Streamable HTTP clients: an agent's change of a formerly exempt field is refused naming it;
the user's report marks the ticket bought; a correction changes nothing until the user confirms it in a later message.
The typed `changes` of the MCP schema carries reports, corrections and confirmations unchanged."""
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
from test_b3e_reference_graph import GROUP, LOCKED, node
from test_b3f_three_paths import ENTRY, HALL, NEAR, fixture


async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def turn(session, key, text, **arguments):
    error, text, data = await call(session, "turn", dict(text=text, request_id=key, **arguments))
    assert not error, text
    return data


async def propose(session, context, key, **arguments):
    return await call(session, "propose", dict(ctx_id=context["ctx_id"], base_version=context["base_version"], idempotency_key=key, **arguments))


async def scenario(session) -> dict:
    context = await turn(session, "seed", "Create")
    _, _, draft = await propose(session, context, "seed", plan=fixture())
    _, _, committed = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
    block = next(block for day in committed["plan"]["days"] for option in day["plans"] for track in option["tracks"]
                 for block in track["blocks"] if block["id"] == LOCKED)
    context = await turn(session, "lock", "整个活动和门票都定了：现场买，官方窗口，没赶上可退。", nature="user_verbatim", native_ref="fixture:lock", speaker="u1")
    _, _, draft = await propose(session, context, "lock", changes=dict(decisions=[dict(
        question="活动与购票", options=["现场买"], choice="现场买", decided_by="user", must=True, subject=GROUP,
        source=dict(event=context["receipt"]["event_id"], basis="user_explicit"), locked_paths={f"/blocks/{LOCKED}": block})]))
    await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="lock"))
    results = {}

    async def plan_with(key, **fields):
        state = (await call(session, "state", {}))[2]
        plan = deepcopy(state["plan"])
        node(plan, key).update(fields)
        return plan

    plan = await plan_with(ENTRY, if_missed="No refund; buy another ticket")
    context = await turn(session, "agent-refund", "agent updates the refund terms", nature="agent_inference")
    _, _, draft = await propose(session, context, "agent-refund", plan=plan)
    error, text, _ = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="agent-refund", accept_unverified=True))
    results["agent refund"] = (draft["status"] == "rejected" and error, "booking:hall-entry/if_missed changed" in text, text[:300])

    context = await turn(session, "bought", "我买了票。", nature="user_verbatim", native_ref="fixture:bought", speaker="u1")
    error, text, draft = await propose(session, context, "bought", plan=await plan_with(ENTRY, status="bought"), changes=dict(reports=[dict(
        target=ENTRY, field="status", old="on_site", new="bought", quote="我买了票", source=dict(event=context["receipt"]["event_id"]))]))
    error_commit, text_commit, version = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="bought", accept_unverified=True)) if not error else (True, text, None)
    results["user report"] = (error or error_commit, bool(version) and version["lock_changes"]["amended"][0]["via"] == "user_report"
                              and draft["lock_changes"]["reports"][0]["event"] == context["receipt"]["event_id"], (text + text_commit)[:300])

    context = await turn(session, "fix", "agent checks the entrance", nature="agent_inference")
    error, text, draft = await propose(session, context, "fix", changes=dict(corrections=[dict(
        target=HALL, field="coords", old=[47.80, 13.04], new=NEAR, basis="The map shows the north entrance",
        ask="Do you mean the north entrance?", source=dict(event=context["receipt"]["event_id"]))]))
    _, _, version = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="fix"))
    question = version["lock_changes"]["corrections"][0]["id"]
    context = await turn(session, "early", "agent applies the correction", nature="agent_inference")
    _, _, draft = await propose(session, context, "early", plan=await plan_with(HALL, coords=NEAR))
    error, text, _ = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="early", accept_unverified=True))
    results["before confirmation"] = (error, "place:gym-a/coords changed" in text, text[:300])

    context = await turn(session, "yes", "对，北门。", nature="user_verbatim", native_ref="fixture:yes", speaker="u2")
    error, text, draft = await propose(session, context, "yes", plan=await plan_with(HALL, coords=NEAR), changes=dict(confirm_corrections=[dict(
        id=question, answer="confirmed", quote="北门", source=dict(event=context["receipt"]["event_id"]))]))
    error_commit, text_commit, version = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="yes", accept_unverified=True)) if not error else (True, text, None)
    state = (await call(session, "state", {}))[2]
    results["after confirmation"] = (error or error_commit, bool(version) and node(state["plan"], HALL)["coords"] == NEAR
                                     and version["lock_changes"]["amended"][0]["via"] == "confirmed_correction", (text + text_commit)[:300])
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
    key = "b3f-paths-" + "k" * 40
    variables = dict(environment(), TRIPWRIGHT_URL_KEY=key)
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"), "serve", "--http", "--port", str(port)],
                               env=variables, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}/mcp"
    address = base + "/" + key
    try:
        async with httpx2.AsyncClient() as client:
            for _ in range(1200):  # up to a minute on a loaded machine
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
def test_three_paths_over_real_mcp_transports(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == 4, results
    for label, (error, expected, text) in results.items():
        if label in ("agent refund", "before confirmation"):
            assert error and expected, (label, text)
        else:
            assert not error and expected, (label, text)
