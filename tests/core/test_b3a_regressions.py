from copy import deepcopy

import pytest

from tripwright.storage import now
from core_helpers import decide, package, usage_of


def propose(service, *, plan=None, changes=None, usages=None, key="change", text="Update", explicit=False):
    turn = service.turn("alice", text, key, nature="user_verbatim" if explicit else "host_restatement",
                        native_ref=f"fixture:{key}" if explicit else None)
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                           changes=changes, usages=usages, idempotency_key=key)


def record_preference(seeded, item, *, accept_unverified=False, **updates):
    item = dict(item, **updates)
    draft = propose(seeded, changes={"items": [item]}, key="preference")
    seeded.commit("alice", draft["draft_id"], "preference", accept_unverified=accept_unverified)
    return seeded.ledger.current("alice")["items"][0]


def test_s1_false_used_without_matching_activity_is_host_reported(seeded, item):
    source = seeded.events.receive("alice", "抱石能去就去", "climbing")
    preference = record_preference(seeded, item, key="climbing", quote="抱石能去就去", text="抱石能去就去",
                                   strength="optional", source={"event": source["event_id"]})
    draft = propose(seeded, plan=seeded.state("alice")["plan"], usages=[{"item_id": preference["id"], "status": "used"}])
    usage = usage_of(draft, preference["id"])
    assert usage["status"] == "host_reported" and usage["verification"] == "unverified"
    version = seeded.commit("alice", draft["draft_id"], "self-report")
    assert seeded.explain("alice", version=version["number"])["version_usage"][0]["usages"][0]["status"] == "host_reported"


@pytest.mark.parametrize("mutation", ["kind", "missing", "person", "date", "time"])
def test_s1_used_block_references_validate_activity_people_and_scope(seeded, item, mutation):
    source = seeded.events.receive("alice", "Prefer art.", "art")
    preference = record_preference(seeded, item, key="art", quote="Prefer art.", text="Prefer art.", source={"event": source["event_id"]},
                                   scope={"start": "2030-06-10", "end": "2030-06-10", "time_from": "09:00", "time_to": "12:00"})
    plan = seeded.state("alice")["plan"]
    block = plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0]
    reference = block["id"]
    if mutation == "kind":
        block["place"] = "oslo-cafe"
    elif mutation == "missing":
        reference = "absent"
    elif mutation == "person":
        plan["days"][0]["plans"][0]["tracks"][0]["subject"] = ["p2"]
    elif mutation == "date":
        next_day = deepcopy(plan["days"][0])
        next_day["date"] = "2030-06-11"
        for row in next_day["plans"][0]["tracks"][0]["blocks"]:
            row["id"] = "next-" + row["id"]
        plan["days"].append(next_day)
        reference = next_day["plans"][0]["tracks"][0]["blocks"][0]["id"]
    else:
        block.update(start="08:00", end="08:30")
    draft = propose(seeded, plan=plan, usages=[dict(item_id=preference["id"], status="used", block_ids=[reference])])
    assert draft["status"] == "rejected"
    assert any(check["id"].startswith("usage:") for check in draft["checks"]["conflicts"])
    assert usage_of(draft, preference["id"])["status"] == "invalid"


def test_s1_matching_activity_is_server_checked(seeded, item):
    preference = record_preference(seeded, item, key="art", quote="Prefer art.", text="Prefer art.")
    plan = seeded.state("alice")["plan"]
    block_id = plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0]["id"]
    draft = propose(seeded, plan=plan, usages=[dict(item_id=preference["id"], status="used", block_ids=[block_id])])
    usage = usage_of(draft, preference["id"])
    assert usage["status"] == "used" and usage["verification"] == "server_checked"
    assert seeded.commit("alice", draft["draft_id"], "matched")["usages"][0] == usage


def test_s1_forbidden_activity_cannot_be_claimed_used_while_it_is_scheduled(seeded, item):
    source = seeded.events.receive("alice", "Do not visit museums.", "forbid-museum")
    preference = record_preference(seeded, item, key="museums", quote="Do not visit museums.", text="Do not visit museums.",
                                   strength="forbid", source={"event": source["event_id"]}, accept_unverified=True)
    plan = seeded.state("alice")["plan"]
    block = plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0]
    usages = [dict(item_id=preference["id"], status="used", block_ids=[block["id"]])]
    assert propose(seeded, plan=plan, usages=usages)["status"] == "rejected"
    block.update(kind="flex", place="oslo-cafe")
    good = propose(seeded, plan=plan, usages=usages, key="museum-omitted")
    assert good["status"] == "proposed"
    assert [usage for usage in good["usages"] if usage.get("verification") == "server_checked"][0]["status"] == "used"


def test_s2_baggage_task_cannot_enter_another_persons_track(seeded):
    plan = seeded.state("alice")["plan"]
    track = plan["days"][0]["plans"][0]["tracks"][0]
    track["subject"] = ["p1"]
    track["blocks"][0]["baggage_holder"] = "p2"
    draft = propose(seeded, plan=plan)
    assert draft["status"] == "rejected"
    with pytest.raises(ValueError, match="person:task"):
        seeded.commit("alice", draft["draft_id"], "bad", accept_unverified=True)
    track["blocks"][0]["baggage_holder"] = "p1"
    assert propose(seeded, plan=plan, key="right-holder")["status"] == "proposed"


def lock_cafe(seeded):
    block = seeded.state("alice")["plan"]["days"][0]["plans"][0]["tracks"][0]["blocks"][2]
    decision = dict(question="After dinner?", options=["Cafe", "Park"], choice="Cafe", decided_by="user",
                    subject=["p1", "p2", "p3"], locked_paths={"/days/0/plans/0/tracks/0/blocks/2/place": block["place"]})
    draft = decide(seeded, decision, "lock")
    seeded.commit("alice", draft["draft_id"], "lock")
    return block, decision


def test_s6_inserting_before_locked_block_does_not_change_its_identity(seeded):
    block, decision = lock_cafe(seeded)
    assert list(seeded.ledger.current("alice")["decisions"][0]["locked_paths"]) == [f"/blocks/{block['id']}/place"]
    plan = seeded.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"].insert(0, dict(id="breakfast", start="08:00", end="08:30", kind="flex", text="Breakfast"))
    draft = propose(seeded, plan=plan, key="insert")
    assert draft["status"] == "proposed"
    seeded.commit("alice", draft["draft_id"], "insert", accept_unverified=True)
    plan = seeded.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][3]["place"] = "oslo-park"
    assert propose(seeded, plan=plan, key="still-locked")["status"] == "rejected"


def test_s6_legacy_whole_block_lock_accepts_new_schema_defaults_and_migrates(seeded):
    block = seeded.state("alice")["plan"]["days"][0]["plans"][0]["tracks"][0]["blocks"][2]
    old_shape = {key: value for key, value in block.items() if key not in ("origin", "destination", "line")}
    decision = dict(question="After dinner?", options=["Cafe", "Park"], choice="Cafe", decided_by="user",
                    subject=["p1", "p2", "p3"], locked_paths={"/days/0/plans/0/tracks/0/blocks/2": old_shape})
    draft = decide(seeded, decision, "old-shape")
    assert draft["status"] == "proposed"
    seeded.commit("alice", draft["draft_id"], "old-shape")
    stored = seeded.store.all("decisions", "alice")[0]
    stored["locked_paths"] = {"/days/0/plans/0/tracks/0/blocks/2": old_shape}
    seeded.store.put("decisions", "alice", stored)
    plan = seeded.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"].insert(0, dict(id="earlier", start="08:00", end="08:30", kind="flex", text="Earlier"))
    inserted = propose(seeded, plan=plan, key="legacy-insert")
    assert inserted["status"] == "proposed"
    seeded.commit("alice", inserted["draft_id"], "legacy-insert", accept_unverified=True)
    assert list(seeded.ledger.current("alice")["decisions"][0]["locked_paths"]) == [f"/blocks/{block['id']}"]


def test_s6_new_user_decision_supersedes_old_but_agent_cannot(seeded, source):
    block, decision = lock_cafe(seeded)
    plan = seeded.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][2]["place"] = "oslo-park"
    turn = seeded.turn("alice", "I changed my mind: park.", "mind", nature="user_verbatim", native_ref="fixture:mind", speaker="p1")
    replacement = dict(decision, choice="Park", source={"event": turn["receipt"]["event_id"], "basis": "user_explicit"},
                       locked_paths={f"/blocks/{block['id']}/place": "oslo-park"})
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                           changes={"decisions": [replacement]}, idempotency_key="mind")
    assert draft["status"] == "proposed"
    seeded.commit("alice", draft["draft_id"], "mind", accept_unverified=True)
    decisions = seeded.store.all("decisions", "alice")
    assert [decision["status"] for decision in decisions] == ["superseded", "active"]
    assert decisions[0]["superseded_by"] == decisions[1]["id"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][2]["place"] = "oslo-cafe"
    replacement.update(decided_by="agent", source=source, locked_paths={f"/blocks/{block['id']}/place": "oslo-cafe"})
    assert propose(seeded, plan=plan, changes={"decisions": [replacement]}, key="agent-change")["status"] == "rejected"


def test_s3_fact_only_solo_report_is_recorded_and_marks_current_plan_for_replan(seeded, source):
    before = seeded.state("alice")["plan"]
    fact = dict(key="companions", value=[], text="Today I am alone", subject=["p1"],
                scope={"start": "2030-06-10", "end": "2030-06-10"}, true_from=now(), invalidated_by="Day ends", source=source)
    draft = propose(seeded, changes={"facts": [fact]})
    assert draft["status"] == "proposed" and draft["warnings"]
    version = seeded.commit("alice", draft["draft_id"], "status-only")
    assert version["needs_replan"] and version["planning_status"] == "needs_replan"
    assert version["plan"] == before and not version["checks"]["verified"]
    assert len(seeded.ledger.compile("alice", subject=["p1"])["facts"]) == 1
    assert seeded.ledger.compile("alice", subject=["p2"])["facts"] == []
    assert "需重排" in seeded.state("alice", "day", "2030-06-10")["text"]
    assert propose(seeded, plan=before, key="cannot-reuse-conflicting-plan")["status"] == "rejected"


def test_s4_declared_exclusive_keys_in_same_message_raise_one_pending_question_with_both_readings(seeded, item):
    receipt = seeded.events.receive("alice", "晚饭后去渔人堡。晚饭后去抱石。", "contradiction")
    common = dict(item, source={"event": receipt["event_id"]}, scope={"start": "2030-06-10", "end": "2030-06-10", "time_from": "19:00", "time_to": "22:00"})
    first = dict(common, key="after_dinner_fort", quote="晚饭后去渔人堡", text="晚饭后去渔人堡")
    second = dict(common, key="after_dinner_climb", quote="晚饭后去抱石", text="晚饭后去抱石", exclusive_with=["after_dinner_fort"])
    draft = propose(seeded, changes={"items": [first, second]})
    assert len(draft["checks"]["undecided"]) == 1
    seeded.commit("alice", draft["draft_id"], "contradiction")
    questions = seeded.store.all("undecided", "alice")
    assert len(questions) == 1 and set(questions[0]["options"]) == {first["text"], second["text"]}
    assert "neither" in questions[0]["default"]
    assert {item["status"] for item in seeded.store.all("items", "alice")} == {"pending"}
    turn = seeded.turn("alice", "What now?", "after-conflict")
    assert package(seeded, turn)["undecided"] == questions
    assert questions[0]["id"] in turn["pack"]["meta"]["required"]["questions"]["ids"] and f"[{questions[0]['id']}]" in turn["pack"]["markdown"]


def test_s4_broad_independent_preferences_remain_applicable_without_a_question(seeded, item):
    receipt = seeded.events.receive("alice", "抱石能去就去。明天轻松一点。", "independent")
    source = {"event": receipt["event_id"]}
    first = dict(item, key="climbing", quote="抱石能去就去", text="抱石能去就去", strength="optional", source=source)
    second = dict(item, key="pace", quote="明天轻松一点", text="明天轻松一点", source=source,
                  scope={"start": "2030-06-10", "end": "2030-06-10"})
    draft = propose(seeded, changes={"items": [first, second]})
    seeded.commit("alice", draft["draft_id"], "independent")
    bundle = package(seeded, seeded.turn("alice", "Plan", "independent-plan"))
    assert {item["key"] for item in bundle["items"]} == {"climbing", "pace"}
    assert bundle["undecided"] == []
    usages = [dict(item_id=item["id"], status="traded_off" if item["key"] == "climbing" else "used", reason="Relaxed pace first")
              for item in bundle["items"]]
    assert propose(seeded, plan=seeded.state("alice")["plan"], usages=usages, key="independent-good")["status"] == "proposed"


def test_preview_does_not_mutate_relations_or_duplicate_supersedes(seeded, item):
    record_preference(seeded, item)
    receipt = seeded.events.receive("alice", "Prefer parks.", "replace")
    replacement = dict(item, quote="Prefer parks.", text="Prefer parks.", source={"event": receipt["event_id"], "basis": "user_explicit"})
    draft = propose(seeded, changes={"items": [replacement]})
    seeded.commit("alice", draft["draft_id"], "replacement")
    assert len(seeded.ledger.current("alice")["items"][0]["relations"]["supersedes"]) == 1


def test_day_view_omits_empty_hard_node_heading_and_duplicate_unknown_lines(seeded):
    text = seeded.state("alice", "day", "2030-06-10")["text"]
    assert "Hard nodes" not in text
    unknown = [line for line in text.splitlines() if line.startswith("unverified:")]
    assert len(unknown) == len(set(unknown))


def test_turn_skeleton_omits_empty_hard_node_heading(seeded):
    turn = seeded.turn("alice", "Next action", "no-hard-nodes")
    assert turn["pack"]["meta"]["required"]["hard_nodes"]["ids"] == []
    assert not any(line.startswith("硬节点：") for line in turn["pack"]["markdown"].splitlines())
