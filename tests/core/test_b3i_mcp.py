"""B3i over real stdio and Streamable HTTP clients (D-51): a user decision (or a user part) on a field inside a block
lock is refused and the agent still cannot change the block's activity or the booking's channel (A); a decision on a
field the lock reaches is refused with the path to decide again, and decided on the block it replaces the lock (B)."""
from __future__ import annotations

import asyncio
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_b3d_mcp import environment
from test_b3e_reference_graph import GROUP, LOCKED, node
from test_b3f_three_paths import ENTRY, fixture
from test_b3h_mcp import call, http_server, http_session, submit, turn
from tripwright import locks

LOCK = f"/blocks/{LOCKED}"
BLOCK = f"block:{LOCKED}"


async def plan_of(session):
    return deepcopy((await call(session, "state", {}))[2]["plan"])


def decided(context, plan, paths, how="user"):
    decision = dict(question="饭后怎么安排", options=["这样"], choice="这样", decided_by="user", must=True, subject=GROUP,
                    source=dict(event=context["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={path: locks.pointer(plan, path) for path in paths})
    if how == "mixed":
        decision.update(decided_by="agent", parts={path: "user" for path in paths},
                        source=dict(event=context["receipt"]["event_id"], basis="agent_inference"))
    return decision


async def refused(session, context, key, plan, decision):
    """propose answers rejected with the refusal in lock_changes; commit is refused."""
    error, text, draft = await call(session, "propose", dict(ctx_id=context["ctx_id"], base_version=context["base_version"],
                                                             idempotency_key=key, plan=plan, changes=dict(decisions=[decision])))
    if error:
        return False, text
    line = draft["lock_changes"]["decisions"][0]
    error, text, _ = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key=key, accept_unverified=True))
    return (draft["status"] == "rejected" and line.get("refused", [{}])[0].get("locks") == [LOCK]
            and line["refused"][0]["fix"].startswith(f"record the decision on {LOCK}") and error and "decides only a part" in text), text[:300]


async def agent_cannot_change(session, key):
    context = (await call(session, "turn", dict(text="agent replans", request_id=key, nature="agent_inference")))[2]
    plan = await plan_of(session)
    node(plan, BLOCK)["text"] = "Unchosen different activity"
    node(plan, ENTRY)["where"] = ["Unchosen private reseller"]
    error, text, _, _ = await submit(session, context, key, plan=plan)
    return error and "value/text" in text and f"{ENTRY}/where" in text, text[:300]


async def scenario(session) -> dict:
    results = {}
    context = await turn(session, "first", "第一天饭后的抱石和门票定了。")
    plan = fixture()
    error, text, _, _ = await submit(session, context, "first", plan=plan, decisions=[decided(context, plan, [LOCK])])
    results["lock"] = (not error, text[:300])
    for how in ("user", "mixed"):
        context = await turn(session, f"start-{how}", "我只决定开始时间延后到 19:40。")
        plan = await plan_of(session)
        node(plan, BLOCK)["start"] = "19:40"
        node(plan, BLOCK)["text"] = "Unchosen different activity" if how == "mixed" else node(plan, BLOCK)["text"]
        results[f"A-start-{how}"] = await refused(session, context, f"start-{how}", plan, decided(context, plan, [f"{LOCK}/start"], how))
        results[f"A-agent-after-{how}"] = await agent_cannot_change(session, f"agent-{how}")
    context = await turn(session, "where", "门票改到 B 窗口买，定了。")
    plan = await plan_of(session)
    node(plan, ENTRY)["where"] = ["B counter"]
    results["B-where-field"] = await refused(session, context, "where", plan, decided(context, plan, ["/bookings/hall-entry/where"]))
    context = await turn(session, "where-block", "门票改到 B 窗口买，这一整块就这样定了。")
    error, text, _, version = await submit(session, context, "where-block", plan=plan, decisions=[decided(context, plan, [LOCK])])
    results["B-on-the-block"] = (not error and node(await plan_of(session), ENTRY)["where"] == ["B counter"]
                                 and len(version["lock_changes"]["decisions"][0]["replaces"]) == 1, text[:300])
    return results


async def over_stdio(directory: Path) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await scenario(session)


async def over_http(directory: Path) -> dict:
    async with http_server(directory / "http.sqlite3") as address:
        async with http_session(address) as session:
            return await scenario(session)


@pytest.mark.parametrize("transport", [over_stdio, over_http], ids=["stdio", "http"])
def test_partial_decisions_are_refused_over_real_mcp(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == 7, sorted(results)
    failed = {label: text for label, (passed, text) in results.items() if not passed}
    assert not failed, failed
