"""The service names whom to dispatch and reminds the flow on every tool result (2026-11-12, flow.py).

The user: a multi-agent system with clear jobs; on hosts without hooks the server reminds the agent of the flow,
"only as a reminder, without repeating it too often". The removed roles were never dispatched (5e37b5c); now the
service names the role at the moment it is needed."""
import asyncio
import time

SHOTS = "【截图 1/2】虚构攻略：港口边的鱼汤店。\n【截图 2/2】虚构攻略：早上 8 点开门的面包店。"


def at(monkeypatch, moment):
    monkeypatch.setenv("TRIPWRIGHT_NOW", moment)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))


def seeded(service, plan, monkeypatch):
    at(monkeypatch, "2030-06-10T09:00:00+02:00")
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def roles(flow):
    return [entry["role"] for entry in flow["dispatch"]]


def test_screenshots_name_the_requirements_role_and_its_own_record_turn_names_nobody_again(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    first = service.turn("alice", SHOTS, "shots", nature="screenshot_extraction", scenario="requirements")
    assert roles(first["flow"]) == ["tripwright-requirements"]
    item = dict(kind="preference", key="food:fish_soup", quote="港口边的鱼汤店", text="想去港口边的鱼汤店",
                subject=["p1"], strength="prefer", source=dict(event="this"))
    again = service.turn("alice", SHOTS, "shots", nature="screenshot_extraction", scenario="requirements", record=dict(items=[item]))
    assert again["flow"]["dispatch"] == []


def test_the_whole_flow_comes_once_then_one_short_line_and_again_when_the_mode_changes(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    lines = []
    for number in range(3):
        item = dict(kind="preference", key=f"pace:{number}", quote="慢一点", text="慢一点", subject=["p1"], strength="prefer",
                    source=dict(event="this"))
        flow = service.turn("alice", f"第 {number} 件：慢一点。", f"q{number}", scenario="plan", record=dict(items=[item]))["flow"]
        lines.append("full" if "full" in flow else flow.get("reminder"))
    assert "full" not in lines and all(lines)  # after the seed's whole flow, one short line per turn
    assert service.state("alice")["flow"]["reminder"] is None  # just said by the turn: other tools leave it out
    quick = service.turn("alice", "我在车站，下一班车几点？", "q-road", scenario="quick")["flow"]
    assert "full" in quick and "first_part" in quick["full"]


def test_other_tools_carry_a_line_and_ask_for_turn_first_when_the_message_was_not_recorded(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    service.turn("alice", "看看现在的计划。", "look", scenario="status")
    assert service.state("alice")["flow"]["reminder"]
    at(monkeypatch, "2030-06-10T10:00:00+02:00")
    late = asyncio.run(service.query("alice", "saved", dict(text="oslo")))
    assert "turn" in late["flow"]["reminder"]


def test_a_plan_draft_while_planning_names_the_verifier(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "把第一天的博物馆换到下午。", "edit", scenario="replan")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="edit")
    assert "tripwright-verifier" in [entry["role"] for entry in draft["flow"].get("dispatch", [])]
    road = service.turn("alice", "我在车站，下一班车几点？", "road", scenario="quick")
    ledger = service.propose("alice", ctx_id=road["ctx_id"], base_version=road["base_version"], idempotency_key="road",
                             changes=dict(items=[dict(kind="preference", key="pace", quote="车站", text="在车站",
                                                      subject=["p1"], strength="prefer", source=dict(event=road["receipt"]["event_id"]))]))
    assert "dispatch" not in ledger["flow"]


def test_a_decision_whose_upstream_was_replaced_needs_review_and_shows_in_the_graph(service, plan, monkeypatch):
    """The user: new requirements go into the decision dependency graph. A decision that depends on a record no longer
    in force is listed for review, the requirements role is named, and the graph view shows the link."""
    seeded(service, plan, monkeypatch)
    stay = dict(kind="preference", key="stay:old_town", quote="住在老城", text="住在老城", subject=["p1"], strength="prefer",
                source=dict(event="this"))
    first = service.turn("alice", "我们住在老城。", "stay", record=dict(items=[stay]))
    [stay_id] = [entry for entry in first["recorded"]["changed"] if entry.startswith("I")]
    second = service.turn("alice", "既然住在老城，晚饭就近。", "dinner")
    dinner = dict(id="D-dinner-near", question="晚饭在哪吃", options=["老城附近", "随便"], choice="老城附近", decided_by="agent",
                  subject=["p1"], source=dict(event=second["receipt"]["event_id"], basis="agent_inference"), depends_on=[stay_id])
    draft = service.propose("alice", ctx_id=second["ctx_id"], base_version=second["base_version"], idempotency_key="dinner",
                            changes=dict(decisions=[dinner]))
    service.commit("alice", draft["draft_id"], "dinner", accept_unverified=True)
    graph = service.state("alice", view="graph")
    assert dict(source="D-dinner-near", target=stay_id, relation="depends_on") in graph["edges"] and graph["review"] == []
    from tripwright.consider import one_hop
    items, decisions, linked = one_hop(service.store, "alice", [], ["D-dinner-near"])
    assert items == [stay_id] and linked[stay_id] == dict(relation="depends_on", of="D-dinner-near")
    assert one_hop(service.store, "alice", [stay_id], [])[2]["D-dinner-near"] == dict(relation="depended_on_by", of=stay_id)
    old = service.store.get("items", "alice", stay_id)
    service.store.put("items", "alice", dict(old, status="superseded"))  # however it was replaced
    third = service.turn("alice", "明天的行程呢？", "next", scenario="status")
    assert [entry["decision"] for entry in third["review"]] == ["D-dinner-near"]
    assert "tripwright-requirements" in roles(third["flow"])
    assert "! review D-dinner-near" in service.state("alice", view="graph")["text"]
    fourth = service.turn("alice", "后天呢？", "later", scenario="status")
    assert "tripwright-requirements" not in roles(fourth["flow"])  # named once, not on every turn
