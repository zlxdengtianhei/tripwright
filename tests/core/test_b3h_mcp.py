"""B3h over real stdio and Streamable HTTP clients (D-50), and across sessions: the first plan commits with the user's
lock (D); a retried older message cannot undo a later decision, also from a new client session or after the server
restarts on the same database, while the user's next message can (A); a confirmation received before the correction was
proposed is refused and a later one takes effect (B); the user part of an agent's decision replaces the lock (C)."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
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
from tripwright import locks

LOCK = f"/blocks/{LOCKED}"
TOKEN = "tw-b3h-a"


async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def turn(session, key, text, *, nature="user_verbatim", speaker="u1"):
    error, text, data = await call(session, "turn", dict(text=text, request_id=key, nature=nature, speaker=speaker,
                                                         **(dict(native_ref=f"fixture:{key}") if nature == "user_verbatim" else {})))
    assert not error, text
    return data


async def agent_turn(session, key, text):
    error, text, data = await call(session, "turn", dict(text=text, request_id=key, nature="agent_inference"))
    assert not error, text
    return data


async def plan_of(session):
    return deepcopy((await call(session, "state", {}))[2]["plan"])


def decision(context, plan, **extra):
    return dict(dict(question="门票怎么买", options=["这样"], choice="这样", decided_by="user", must=True, subject=GROUP,
                     source=dict(event=context["receipt"]["event_id"], basis="user_explicit"),
                     locked_paths={LOCK: locks.pointer(plan, LOCK)}), **extra)


async def submit(session, context, key, *, plan=None, **changes):
    """propose, then commit if it was accepted; returns (error, text, draft, version)."""
    error, text, draft = await call(session, "propose", dict(ctx_id=context["ctx_id"], base_version=context["base_version"],
                                                             idempotency_key=key, plan=plan, changes=changes))
    if error:
        return error, text, None, None
    error, text, version = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key=key, accept_unverified=True))
    return error, text, draft, version


async def set_where(session, context, key, value):
    plan = await plan_of(session)
    node(plan, ENTRY)["where"] = [value]
    return await submit(session, context, key, plan=plan, decisions=[decision(context, plan)])


async def first_plan(session, results):
    """D: the first plan and the user's lock in one commit."""
    context = await turn(session, "first", "第一天饭后的抱石和门票定了。")
    plan = fixture()
    error, text, draft, version = await submit(session, context, "first", plan=plan, decisions=[decision(context, plan)])
    results["D-first-plan-with-lock"] = (not error and version["number"] == 1
                                         and draft["lock_changes"]["decisions"][0].get("people_from", "").startswith("the first plan"), text[:300])


async def a_then_b(session, results):
    first = await turn(session, "where-a", "门票从 A 窗口买，定了。")
    error, text, _, _ = await set_where(session, first, "where-a", "A counter")
    results["A-first"] = (not error, text[:300])
    later = await turn(session, "where-b", "现在改从 B 窗口买，前面的 A 取消。")
    error, text, _, _ = await set_where(session, later, "where-b", "B counter")
    results["A-later"] = (not error, text[:300])


async def retry_a(session, results, label):
    again = await turn(session, "where-a", "门票从 A 窗口买，定了。")
    error, text, _, _ = await set_where(session, again, f"{label}-retry", "A counter")
    results[f"A-retry-{label}"] = (error and "not admitted (order)" in text and node(await plan_of(session), ENTRY)["where"] == ["B counter"],
                                   text[:300])


async def scenario(session) -> dict:
    results = {}
    await first_plan(session, results)
    await a_then_b(session, results)
    await retry_a(session, results, "same-session")
    newest = await turn(session, "where-a-again", "还是回 A 窗口买吧，定了。")
    error, text, _, _ = await set_where(session, newest, "where-a-again", "A counter")
    results["A-newest"] = (not error and node(await plan_of(session), ENTRY)["where"] == ["A counter"], text[:300])

    # B: t0 agent, t1 the user's premature confirmation, t2 the correction citing t0.
    old = await agent_turn(session, "map", "agent notes the map")
    premature = await turn(session, "premature", "确认新入口。")
    asked = await agent_turn(session, "propose-fix", "agent proposes the entrance")
    error, text, _, version = await submit(session, asked, "fix", corrections=[dict(
        target=HALL, field="coords", old=[47.80, 13.04], new=NEAR, basis="The map shows the north entrance",
        ask="确认新入口吗？", source=dict(event=old["receipt"]["event_id"]))])
    question = version["lock_changes"]["corrections"][0]["id"] if version else None
    plan = await plan_of(session)
    node(plan, HALL)["coords"] = NEAR
    again = await turn(session, "premature", "确认新入口。")
    error, text, _, _ = await submit(session, again, "premature-confirm", plan=plan, confirm_corrections=[dict(
        id=question, answer="confirmed", quote="确认新入口", source=dict(event=premature["receipt"]["event_id"]))])
    results["B-premature"] = (error and "later message than the proposal" in text and node(await plan_of(session), HALL)["coords"] == [47.80, 13.04],
                              text[:300])
    answer = await turn(session, "answer", "对，确认新入口。")
    error, text, _, _ = await submit(session, answer, "answer", plan=plan, confirm_corrections=[dict(
        id=question, answer="confirmed", quote="确认新入口", source=dict(event=answer["receipt"]["event_id"]))])
    results["B-later-confirmation"] = (not error and node(await plan_of(session), HALL)["coords"] == NEAR, text[:300])

    # C: the user part of an agent's decision replaces the lock.
    context = await turn(session, "part", "门票这一部分我决定改为买好；其他由你定。")
    plan = await plan_of(session)
    node(plan, ENTRY)["status"] = "bought"
    error, text, _, version = await submit(session, context, "part", plan=plan, decisions=[decision(
        context, plan, decided_by="agent", parts={LOCK: "user"}, source=dict(event=context["receipt"]["event_id"], basis="agent_inference"))])
    results["C-user-part"] = (not error and node(await plan_of(session), ENTRY)["status"] == "bought"
                              and len(version["lock_changes"]["decisions"][0]["replaces"]) == 1, text[:300])
    return results


async def over_stdio(directory: Path) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await scenario(session)


@asynccontextmanager
async def http_server(database: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    key = "b3h-only-" + "k" * 40
    variables = dict(environment(), TRIPWRIGHT_URL_KEY=key)
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(database), "serve", "--http", "--port", str(port)],
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
        yield address
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


@asynccontextmanager
async def http_session(address: str):
    async with httpx2.AsyncClient() as client:
        async with streamable_http_client(address, http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session


async def over_http(directory: Path) -> dict:
    async with http_server(directory / "http.sqlite3") as address:
        async with http_session(address) as session:
            return await scenario(session)


async def across_sessions(directory: Path) -> dict:
    """A and B in one client session; the retry from a new client session; the retry again after the server restarted
    on the same database; then the user's next message."""
    results = {}
    database = directory / "sessions.sqlite3"
    async with http_server(database) as address:
        async with http_session(address) as session:
            await first_plan(session, results)
            await a_then_b(session, results)
        async with http_session(address) as session:
            await retry_a(session, results, "new-client")
    async with http_server(database) as address:
        async with http_session(address) as session:
            await retry_a(session, results, "restarted-server")
            newest = await turn(session, "where-a-again", "还是回 A 窗口买吧，定了。")
            error, text, _, _ = await set_where(session, newest, "where-a-again", "A counter")
            results["A-newest-after-restart"] = (not error and node(await plan_of(session), ENTRY)["where"] == ["A counter"], text[:300])
    return results


@pytest.mark.parametrize("transport,expected", [(over_stdio, 8), (over_http, 8), (across_sessions, 6)], ids=["stdio", "http", "across-sessions"])
def test_authority_order_correction_time_user_parts_and_first_plan_over_real_mcp(tmp_path, transport, expected):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == expected, sorted(results)
    failed = {label: text for label, (passed, text) in results.items() if not passed}
    assert not failed, failed
