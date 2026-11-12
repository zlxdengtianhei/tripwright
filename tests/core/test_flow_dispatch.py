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
    for number in range(2):
        flow = service.turn("alice", f"第 {number} 个小问题：明天几点出门？", f"q{number}", scenario="plan")["flow"]
        lines.append("full" if "full" in flow else flow.get("reminder"))
        draft = service.propose("alice", ctx_id=service.store.latest("contexts", "alice")["id"],
                                base_version=service.versions.current("alice")["number"], idempotency_key=f"led{number}",
                                changes=dict(items=[dict(kind="preference", key=f"pace:{number}", quote="明天几点出门",
                                                         text="问出门时间", subject=["p1"], strength="prefer",
                                                         source=dict(event=service.store.latest("contexts", "alice")["source_event"]))]))
        service.commit("alice", draft["draft_id"], f"led{number}")
    assert "full" not in lines and all(lines)  # after the seed's whole flow, one short line per turn
    quick = service.turn("alice", "我在车站，下一班车几点？", "q-road", scenario="quick")["flow"]
    assert "full" in quick and "first_part" in quick["full"]


def test_a_missed_step_brings_the_whole_flow_back_but_not_on_every_turn(service, plan, monkeypatch):
    """Named the requirements role and nothing was recorded since: the whole flow again, at most once in 3 turns."""
    seeded(service, plan, monkeypatch)
    kinds = ["full" if "full" in service.turn("alice", f"第 {n} 件：慢一点。", f"m{n}", scenario="plan")["flow"] else "short"
             for n in range(5)]
    assert kinds[:2] == ["short", "short"] and kinds[2] == "full" and kinds[3:] == ["short", "short"]


def test_the_short_line_on_other_tools_comes_back_every_third_result(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    service.turn("alice", "看看现在的计划。", "look", scenario="status")
    service.turn("alice", "再看看。", "look2", scenario="status")
    lines = [asyncio.run(service.query("alice", "saved", dict(text="oslo")))["flow"]["reminder"] for _ in range(6)]
    assert lines.count(None) == 4 and lines[2] and lines[5]


def test_other_tools_carry_a_line_and_ask_for_turn_first_only_after_a_long_gap(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    service.turn("alice", "看看现在的计划。", "look", scenario="status")
    assert service.state("alice")["flow"]["reminder"]
    at(monkeypatch, "2030-06-10T10:00:00+02:00")
    late = asyncio.run(service.query("alice", "saved", dict(text="oslo")))
    assert "turn" in late["flow"]["reminder"]


def test_a_process_that_never_saw_a_turn_does_not_ask_for_one(service, plan, monkeypatch):
    """The command-line query a researcher uses runs in a new process: it must not be told to call turn."""
    from tripwright.flow import Flow
    from datetime import datetime, timezone

    line = Flow().tool("alice", datetime.now(timezone.utc), "query", language="zh")["reminder"]
    assert line and "turn" not in line


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


def test_a_transcription_the_requirements_role_returns_is_recorded_as_screenshots_without_naming_it_again(service, plan, monkeypatch):
    """Images read from files are recorded as their own message: the quote must be in it, and the records take its
    nature. The turn that carries the transcription and its record names nobody again."""
    seeded(service, plan, monkeypatch)
    service.turn("alice", "文件夹里是 2 张攻略截图，帮我整理进行程。", "ask", scenario="requirements")
    item = dict(kind="preference", key="saved:fish", quote="港口边的鱼汤店", text="截图里的鱼汤店", subject=["p1"],
                strength="prefer", source=dict(event="this"))
    recorded = service.turn("alice", SHOTS, "shots", nature="screenshot_extraction", scenario="requirements", record=dict(items=[item]))
    assert recorded["flow"]["dispatch"] == []
    [found] = [value for value in asyncio.run(service.query("alice", "saved", dict(text="鱼汤")))["evidence"]["value"]
               if value.get("found") == "item"]
    assert found["nature"] == "screenshot_extraction"


def test_while_planning_the_first_instruction_names_whom_to_dispatch(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "10 号晚饭后的咖啡不要了，改成去公园散步。", "edit", scenario="replan")
    assert turn["next"].startswith("先派 tripwright-requirements") and len(turn["next"]) <= 600
    road = service.turn("alice", "我在车站，下一班车几点？", "road", scenario="quick")
    assert "先派" not in road["next"][:10]


def test_while_planning_the_requirements_role_is_named_with_or_without_record(service, plan, monkeypatch):
    """One recorder while planning: the main agent's own record does not stop the requirements role from being
    named to record and link the message (review of 2026-11-12, block 1)."""
    seeded(service, plan, monkeypatch)
    item = dict(kind="preference", key="dinner:old_town", quote="晚饭想在老城吃", text="晚饭想在老城吃", subject=["p1"],
                strength="prefer", source=dict(event="this"))
    with_record = service.turn("alice", "晚饭想在老城吃。", "a", scenario="plan", record=dict(items=[item]))
    without = service.turn("alice", "晚饭想在老城吃，别太晚。", "b", scenario="plan")
    assert "tripwright-requirements" in roles(with_record["flow"]) and "tripwright-requirements" in roles(without["flow"])


def test_a_ticket_still_to_buy_names_the_researcher_for_fares(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "11 号的火车票还没买，帮我排一下。", "fare", scenario="plan")
    museum = dict(id="bk-museum", date="2030-06-10", kind="ticket", what="Fictional museum entry", subject=["p1"], status="to_buy",
                  why_now="Fictional: tickets sell out")
    changed = dict(plan, bookings=[*plan["bookings"], museum])
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=changed, idempotency_key="fare")
    named = [(entry["role"], entry.get("focus")) for entry in draft["flow"].get("dispatch", [])]
    assert ("tripwright-researcher", "fares") in named and ("tripwright-verifier", None) in named


def test_the_dispatch_lead_keeps_next_to_whole_sentences():
    from tripwright.service import whole_sentences

    text = "先派 tripwright-requirements（理由）。" + "第一句很长。" * 120
    cut = whole_sentences(text, 600)
    assert len(cut) <= 600 and cut.endswith("。") and whole_sentences("短。", 600) == "短。"
