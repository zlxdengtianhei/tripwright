"""A3 (D-40): `Anchor.deadline` tells an external deadline (a counter closes, the last train leaves) from the plan's own
target that keeps a margin before it. The pack writes the three cases apart, as in A2; old data without the field
still reads; a wrong value is refused with its field path at propose, in a correction and over MCP; the field is the
user's choice for locks. Fictional people and places only."""
from __future__ import annotations

import asyncio
import sys
from copy import deepcopy

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_b3d_mcp import environment
from tripwright import locks
from tripwright.demo import example
from tripwright.render.labels import stamp_day_clock
from tripwright.repo import snapshot as snapshots
from tripwright.service import Tripwright
from tripwright.storage import Store
from tripwright.trip import Anchor, Trip

DAY = "2030-06-10"


def anchored_plan(**deadlines) -> dict:
    """The demo's first day with three late-evening hard nodes after the day's blocks; `deadlines` marks them by ID."""
    plan = deepcopy(example()["trip"])
    plan["days"] = [plan["days"][0]]
    plan["bookings"] = []
    plan["days"][0]["anchors"] = [
        dict(id="hn-counter", time="23:10", text="Ferry counter closes (fictional)", subject=["p1"], consequence="No boarding.",
             alternative="Next morning."),
        dict(id="hn-arrive", time="22:30", text="Be at the ferry counter", subject=["p1"], buffer_minutes=10, consequence="Less margin.",
             alternative="Go straight to the counter."),
        dict(id="hn-last-bus", time="23:40", text="Last bus to the hostel (fictional)", subject=["p1"], consequence="Walk 40 minutes.",
             alternative="Taxi."),
    ]
    for anchor in plan["days"][0]["anchors"]:
        if anchor["id"] in deadlines:
            anchor["deadline"] = deadlines[anchor["id"]]
    return plan


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "a3.sqlite3")
    service = Tripwright(store)
    yield service
    service.jobs.stop()
    store.close()


def committed(service, plan) -> dict:
    turn = service.turn("alice", "Create the trip.", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    return service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)


def test_old_data_without_the_field_reads_and_counts_as_not_marked():
    anchor = dict(id="hn", time="07:00", text="x", subject=["p1"])
    assert Anchor.model_validate(anchor).deadline is None
    plan = anchored_plan()
    assert all("deadline" not in anchor for anchor in plan["days"][0]["anchors"])
    assert {anchor["deadline"] for anchor in Trip.model_validate(plan).model_dump(mode="json")["days"][0]["anchors"]} == {None}


def test_the_pack_writes_external_plan_and_unmarked_apart(service, monkeypatch):
    version = committed(service, anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"}))
    monkeypatch.setattr(snapshots, "now", lambda: "2030-06-10T20:00:00+02:00")
    turn = service.turn("alice", "How late can I leave for the ferry?", "q", scenario="quick", scope={"start": DAY})
    records = turn["pack"]["meta"]["required"]["hard_nodes"]["ids"]
    assert records == ["hn-arrive", "hn-counter", "hn-last-bus"]
    lines = {line.split("]")[0].split("[")[1]: line for line in turn["pack"]["markdown"].splitlines() if line.startswith("硬节点：")}
    # the version is committed at the real clock, years before this turn's fixed moment, so the stored time is old
    stored = f"没核过：计划 V1 存的（{stamp_day_clock(version['committed_at'], 'Europe/Oslo')} 起），超过 6 小时，可能已变"
    assert lines["hn-counter"].endswith("｜最晚出发没核｜外部截止｜" + stored), lines["hn-counter"]
    assert "｜计划目标（不是外部截止）｜计划留 10 分钟余量｜" in lines["hn-arrive"]
    assert "｜没标是外部截止还是计划目标｜" in lines["hn-last-bus"]
    nodes = {node["id"]: node for node in turn["hard_nodes"]}
    assert {key: nodes[key]["deadline"] for key in nodes} == {"hn-counter": "external", "hn-arrive": "plan", "hn-last-bus": "unmarked"}
    assert all(node["stored"]["version"] == 1 and node["checked"] is None for node in nodes.values())


def test_a_wrong_deadline_is_refused_at_propose_with_its_field_path(service):
    committed(service, anchored_plan())
    turn = service.turn("alice", "Mark the counter.", "mark")
    for value, sent in (("hard", "string"), (True, "boolean"), (["external"], "array")):
        with pytest.raises(ValueError) as refused:
            service.propose("alice", ctx_id=turn["ctx_id"], base_version=1, plan=anchored_plan(**{"hn-counter": value}),
                            idempotency_key=f"bad-{sent}")
        assert str(refused.value) == f"Invalid plan: plan.days.0.anchors.0.deadline must be 'external' or 'plan', got {sent}"
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=1, plan=anchored_plan(**{"hn-counter": "external"}),
                            idempotency_key="good")
    assert draft["status"] == "proposed"
    service.commit("alice", draft["draft_id"], "good", accept_unverified=True)
    assert service.state("alice")["plan"]["days"][0]["anchors"][0]["deadline"] == "external"


def test_a_correction_of_the_mark_is_typed_and_needs_the_users_answer_not_evidence(service):
    committed(service, anchored_plan(**{"hn-arrive": "external"}))
    turn = service.turn("alice", "Lock this day.", "lock", nature="user_verbatim", native_ref="fixture:lock", speaker="p1")
    plan = service.state("alice")["plan"]
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="lock", changes=dict(decisions=[dict(
        question="这天定了", options=["照现在"], choice="照现在", decided_by="user", must=True, subject=["p1"],
        source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
        locked_paths={f"/days/{DAY}": locks.pointer(plan, f"/days/{DAY}")})]))
    service.commit("alice", draft["draft_id"], "lock", accept_unverified=True)
    turn = service.turn("alice", "The counter time is our own margin.", "fix")
    source = dict(event=turn["receipt"]["event_id"], basis="agent_inference")

    def correct(new, **extra):
        return service.propose("alice", ctx_id=turn["ctx_id"], base_version=service.state("alice")["number"], idempotency_key=f"fix-{new}",
                               changes=dict(corrections=[dict(target="anchor:hn-arrive", field="deadline", old="external", new=new,
                                                              basis="22:30 is our margin before the 23:10 closing", ask="Is 22:30 your own margin?",
                                                              source=source, **extra)]))

    with pytest.raises(ValueError, match=r"\.new must be 'external' or 'plan', got string"):
        correct("hard")
    with pytest.raises(ValueError, match="hn-arrive/deadline is the user's choice, not an external fact"):
        correct("plan", evidence_ids=["EV-anything"])
    opened = correct("plan")
    assert opened["status"] == "proposed" and opened["lock_changes"]


def test_a_locked_days_mark_changes_only_through_the_user(service):
    committed(service, anchored_plan())
    assert locks.field_class("Anchor", "deadline") == locks.CHOICE
    turn = service.turn("alice", "Lock this day.", "lock", nature="user_verbatim", native_ref="fixture:lock", speaker="p1")
    plan = service.state("alice")["plan"]
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="lock", changes=dict(decisions=[dict(
        question="这天定了", options=["照现在"], choice="照现在", decided_by="user", must=True, subject=["p1"],
        source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
        locked_paths={f"/days/{DAY}": locks.pointer(plan, f"/days/{DAY}")})]))
    service.commit("alice", draft["draft_id"], "lock", accept_unverified=True)
    turn = service.turn("alice", "Mark it.", "agent")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=service.state("alice")["number"],
                            plan=anchored_plan(**{"hn-counter": "external"}), idempotency_key="agent")
    assert draft["status"] == "rejected"
    assert [check["details"]["changed"] for check in draft["checks"]["conflicts"]] == [["value/anchors/hn-counter/deadline"]]
    with pytest.raises(ValueError, match=r"Commit refused: .*locked:"):
        service.commit("alice", draft["draft_id"], "agent", accept_unverified=True)


async def over_stdio(directory) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "a3.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = {tool.name: tool.input_schema for tool in (await session.list_tools()).tools}
            turn = (await session.call_tool("turn", dict(text="Create", request_id="seed"))).structured_content
            bad = await session.call_tool("propose", dict(ctx_id=turn["ctx_id"], base_version=0, idempotency_key="bad",
                                                          plan=anchored_plan(**{"hn-counter": "hard"})))
            good = await session.call_tool("propose", dict(ctx_id=turn["ctx_id"], base_version=0, idempotency_key="good",
                                                           plan=anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"})))
            return dict(tools=tools, bad=(bad.is_error, " ".join(getattr(part, "text", "") for part in bad.content)),
                        good=(good.is_error, good.structured_content))


def test_the_mcp_schema_carries_the_field_and_a_wrong_value_names_its_path(tmp_path):
    result = asyncio.run(over_stdio(tmp_path))
    schema = result["tools"]["propose"]["properties"]["plan"]
    assert any(branch.get("type") == "object" and "properties" not in branch for branch in schema["anyOf"])
    error, text = result["bad"]
    assert error and "deadline" in text and "anchors" in text and ("'external' or 'plan'" in text), text
    error, data = result["good"]
    assert not error and data["status"] == "proposed"
