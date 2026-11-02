"""B3j over real stdio and Streamable HTTP clients (D-52), and across sessions. A: a message that changed the hall
entry's channel once, sent again under its request ID for a new context, cannot change it a second time (report→report,
decision→report, report→decision), also from a new client session or after the server restarts on the same database,
while the user's next message can. B: after the user (or the user part of an agent's decision) decides the day holding
the locked block again, the trip's timezone the block lock protected stays locked, and the agent cannot change it."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_b3d_mcp import environment
from test_b3e_reference_graph import D1, GROUP, LOCKED, node
from test_b3f_three_paths import ENTRY
from test_b3h_mcp import agent_turn, decision, first_plan, http_server, http_session, plan_of, submit, turn
from tripwright import locks

DAY = f"/days/{D1}"
QUOTE = "官方窗口 A 或 B"
COMBOS = [("report", "report"), ("decision", "report"), ("report", "decision")]


async def channel(session, context, key, way, value):
    """The user's change of the entry's channel, as a report or as a decision on the whole block."""
    plan = await plan_of(session)
    old = node(plan, ENTRY)["where"]
    node(plan, ENTRY)["where"] = [value]
    if way == "decision":
        return await submit(session, context, key, plan=plan, decisions=[decision(context, plan)])
    return await submit(session, context, key, plan=plan, reports=[dict(target=ENTRY, field="where", old=old, new=[value], quote=QUOTE,
                                                                        source=dict(event=context["receipt"]["event_id"]))])


def said(index):
    return f"官方窗口 A 或 B 都行，第 {index} 次我决定购票渠道。"


async def changed_once(session, results, index, first):
    context = await turn(session, f"channel-{index}", said(index))
    error, text, _, version = await channel(session, context, f"channel-{index}", first, f"A{index}")
    results[f"A{index}-{first}"] = (not error, text[:300])
    return context, version


async def retried(session, results, index, second, number, label):
    again = await turn(session, f"channel-{index}", said(index))
    error, text, draft, _ = await channel(session, again, f"channel-{index}-{label}", second, f"B{index}")
    event = again["receipt"]["event_id"]
    results[f"A{index}-retry-{second}-{label}"] = (
        error and f"not admitted (used): message {event} already changed lock" in text and f"in version {number}" in text
        and any("not admitted (used)" in line for line in draft["lock_changes"]["refused"])
        and node(await plan_of(session), ENTRY)["where"] == [f"A{index}"], text[:300])


async def newer(session, results, index, second, label=""):
    context = await turn(session, f"channel-{index}-newer", f"还是改到 B 吧。{QUOTE} 第 {index} 次选 B。")
    error, text, _, _ = await channel(session, context, f"channel-{index}-newer", second, f"B{index}")
    results[f"A{index}-newer{label}"] = (not error and node(await plan_of(session), ENTRY)["where"] == [f"B{index}"], text[:300])


async def scenario_a(session) -> dict:
    results = {}
    await first_plan(session, results)
    for index, (first, second) in enumerate(COMBOS):
        _, version = await changed_once(session, results, index, first)
        await retried(session, results, index, second, version["number"] if version else None, "same-session")
        await newer(session, results, index, second)
    return results


def day_decision(context, plan, how):
    made = dict(question="这一天怎么安排", options=["这样"], choice="这样", decided_by="user", must=True, subject=GROUP,
                source=dict(event=context["receipt"]["event_id"], basis="user_explicit"), locked_paths={DAY: locks.pointer(plan, DAY)})
    if how == "mixed":
        made.update(decided_by="agent", parts={DAY: "user"}, source=dict(event=context["receipt"]["event_id"], basis="agent_inference"))
    return made


async def scenario_b(session, how) -> dict:
    results = {}
    await first_plan(session, results)
    zone = (await plan_of(session))["timezone"]
    context = await turn(session, "day", "这一天就这样，饭后抱石开始改到 19:40，定了。")
    plan = await plan_of(session)
    node(plan, f"block:{LOCKED}")["start"] = "19:40"
    error, text, _, version = await submit(session, context, "day", plan=plan, decisions=[day_decision(context, plan, how)])
    kept = version["lock_changes"]["decisions"][0]["replaces"][0].get("kept", {}) if version else {}
    results[f"B-{how}-day-decided-again"] = (not error and kept.get("locks") == ["/timezone"], text[:300])
    context = await agent_turn(session, "zone", "agent moves the trip to another timezone")
    plan = await plan_of(session)
    plan["timezone"] = "Asia/Tokyo"
    error, text, draft, _ = await submit(session, context, "zone", plan=plan)
    results[f"B-{how}-agent-timezone"] = (error and "Commit refused" in text and "/timezone" in text and draft["status"] == "rejected"
                                          and (await plan_of(session))["timezone"] == zone, text[:300])
    return results


async def over_stdio(directory: Path, scenario, *arguments) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await scenario(session, *arguments)


async def over_http(directory: Path, scenario, *arguments) -> dict:
    async with http_server(directory / "http.sqlite3") as address:
        async with http_session(address) as session:
            return await scenario(session, *arguments)


async def across_sessions(directory: Path) -> dict:
    """The change in one client session; the retry from a new client session; the retry again after the server restarted
    on the same database; then the user's next message."""
    results = {}
    database = directory / "sessions.sqlite3"
    async with http_server(database) as address:
        async with http_session(address) as session:
            await first_plan(session, results)
            _, version = await changed_once(session, results, 0, "decision")
        async with http_session(address) as session:
            await retried(session, results, 0, "report", version["number"] if version else None, "new-client")
    async with http_server(database) as address:
        async with http_session(address) as session:
            await retried(session, results, 0, "decision", version["number"] if version else None, "restarted-server")
            await newer(session, results, 0, "report", "-after-restart")
    return results


def passed(results, expected):
    assert len(results) == expected, sorted(results)
    failed = {label: text for label, (ok, text) in results.items() if not ok}
    assert not failed, failed


@pytest.mark.parametrize("transport", [over_stdio, over_http], ids=["stdio", "http"])
def test_counterexample_a_a_retried_message_changes_a_lock_once_over_real_mcp(tmp_path, transport):
    passed(asyncio.run(transport(tmp_path, scenario_a)), 1 + 3 * len(COMBOS))


def test_counterexample_a_across_client_sessions_and_a_server_restart(tmp_path):
    passed(asyncio.run(across_sessions(tmp_path)), 5)


@pytest.mark.parametrize("how", ["user", "mixed"])
@pytest.mark.parametrize("transport", [over_stdio, over_http], ids=["stdio", "http"])
def test_counterexample_b_the_timezone_stays_locked_after_the_day_is_decided_again_over_real_mcp(tmp_path, transport, how):
    passed(asyncio.run(transport(tmp_path, scenario_b, how)), 3)
