"""B3k over real stdio and Streamable HTTP clients (D-53). The user's choice that is not a must: the agent's change
without a reason is refused, with a reason it commits and the change log names the decision it revises. The user then
says it is a must: the agent cannot change it, and no revision is accepted for it. A bought ticket changes its channel
only from the user's report. The history view lists each version's change log."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_b3d_mcp import environment
from test_b3e_reference_graph import GROUP, LOCKED, node
from test_b3f_three_paths import ENTRY, fixture
from test_b3h_mcp import agent_turn, call, http_server, http_session, plan_of, submit, turn
from tripwright import locks

LOCK = f"/blocks/{LOCKED}"
BLOCK = f"block:{LOCKED}"


def choice(context, plan, *, must):
    return dict(question="饭后去哪", options=["岩馆 A"], choice="岩馆 A", decided_by="user", must=must, subject=GROUP,
                source=dict(event=context["receipt"]["event_id"], basis="user_explicit"), locked_paths={LOCK: locks.pointer(plan, LOCK)})


async def with_block(session, **fields):
    plan = await plan_of(session)
    node(plan, BLOCK).update(fields)
    return plan


async def scenario(session) -> dict:
    results = {}
    context = await turn(session, "first", "按这个计划走。")
    error, text, _, version = await submit(session, context, "first", plan=fixture())
    results["first-plan"] = (not error and version["changelog"][0]["change"] == "added", text[:300])

    context = await turn(session, "chosen", "第一天饭后去岩馆 A 吧。")
    error, text, _, version = await submit(session, context, "chosen", decisions=[choice(context, await plan_of(session), must=False)])
    decision = version["lock_changes"]["decisions"][0]["id"] if version else None
    results["chosen-not-a-must"] = (not error and version["lock_changes"]["decisions"][0]["must"] is False, text[:300])

    context = await agent_turn(session, "no-reason", "agent swaps the activity")
    error, text, draft, _ = await submit(session, context, "no-reason", plan=await with_block(session, text="Rope climbing"))
    results["agent-without-reason-refused"] = (error and "not recorded (reason)" in text and "fix: add changes.revisions" in draft["notice"]
                                               and node(await plan_of(session), BLOCK)["text"] != "Rope climbing", text[:300])

    context = await agent_turn(session, "reason", "agent swaps the activity, saying why")
    error, text, _, version = await submit(session, context, "reason", plan=await with_block(session, text="Rope climbing"),
                                           revisions=[dict(decision=decision, reason="岩馆 A 晚上满员")])
    line = next((entry for entry in (version or {}).get("changelog", []) if entry["target"] == BLOCK), {})
    results["agent-with-reason-recorded"] = (not error and line.get("revises") == decision and line.get("why") == "岩馆 A 晚上满员"
                                             and line.get("by") == "agent", text[:300])

    context = await turn(session, "must", "顶绳就这样，饭后必须去。")
    error, text, _, version = await submit(session, context, "must", decisions=[choice(context, await plan_of(session), must=True)])
    must = version["lock_changes"]["decisions"][0]["id"] if version else None
    results["user-marks-a-must"] = (not error and version["lock_changes"]["decisions"][0]["locks"] == [LOCK], text[:300])

    context = await agent_turn(session, "move-must", "agent swaps the activity again")
    error, text, _, _ = await submit(session, context, "move-must", plan=await with_block(session, text="Bouldering"))
    results["agent-cannot-change-a-must"] = (error and f"locked:{must}:{LOCK}" in text, text[:300])
    context = await agent_turn(session, "revise-must", "agent tries a revision for the must")
    error, text, _, _ = await submit(session, context, "revise-must", plan=await with_block(session, text="Bouldering"),
                                     revisions=[dict(decision=must, reason="想换")])
    results["no-revision-for-a-must"] = (error and "a must changes only by the user's new decision" in text, text[:300])

    context = await turn(session, "bought", "我买了票。")
    plan = await plan_of(session)
    node(plan, ENTRY)["status"] = "bought"
    error, text, _, _ = await submit(session, context, "bought", plan=plan, reports=[dict(
        target=ENTRY, field="status", old="on_site", new="bought", quote="我买了票", source=dict(event=context["receipt"]["event_id"]))])
    results["user-reports-bought"] = (not error, text[:300])
    context = await agent_turn(session, "channel", "agent moves the ticket to the online shop")
    plan = await plan_of(session)
    node(plan, ENTRY)["where"] = ["Online shop"]
    error, text, _, _ = await submit(session, context, "channel", plan=plan, why="网上便宜")
    results["bought-ticket-needs-a-source"] = (error and "paid-booking:hall-entry" in text, text[:300])

    _, text, history = await call(session, "state", dict(view="history"))
    numbers = [entry["version"] for entry in history["versions"]]
    results["history-by-version"] = (numbers == list(range(1, len(numbers) + 1)) and any(
        entry.get("revises") == decision for version in history["versions"] for entry in version["changelog"] or []), text[:300])
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
def test_records_reasons_musts_and_facts_over_real_mcp(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == 10, sorted(results)
    failed = {label: text for label, (ok, text) in results.items() if not ok}
    assert not failed, failed
