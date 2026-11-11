from copy import deepcopy

import pytest
from core_helpers import decide, package, usage_of


def propose(seeded, plan=None, changes=None, key="change", usages=None):
    received = seeded.turn("alice", "Update the plan.", key)
    return seeded.propose("alice", ctx_id=received["ctx_id"], base_version=received["base_version"],
                          plan=plan, changes=changes, usages=usages, idempotency_key=key)


def test_vertical_slice_preference_turn_answer_commit(seeded, item):
    recorded = propose(seeded, changes={"items": [item]}, key="preference")
    assert seeded.ledger.current("alice")["items"] == []
    seeded.commit("alice", recorded["draft_id"], "preference")
    turn = seeded.turn("alice", "Plan a relaxed day for me.", "plan", subject=["p1"],
                       scope={"start": "2030-06-10", "end": "2030-06-10"})
    assert package(seeded, turn)["items"][0]["quote"] == item["quote"]
    item_id = package(seeded, turn)["items"][0]["id"]
    assert f"「{item['quote']}」" in turn["pack"]["markdown"]
    current = seeded.state("alice")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=2, plan=current["plan"],
                           usages=[{"item_id": item_id, "status": "used"}], idempotency_key="plan")
    usage = usage_of(draft, item_id)
    assert usage["status"] == "host_reported" and usage["verification"] == "unverified"
    seeded.jobs.run_once("alice")
    assert seeded.result("alice", draft["job_id"])["status"] == "completed"
    committed = seeded.commit("alice", draft["draft_id"], "plan")
    assert committed["number"] == 3
    assert seeded.commit("alice", draft["draft_id"], "plan") == committed


def test_user_decided_fields_cannot_be_changed_even_with_unknowns_accepted(seeded):
    current = seeded.state("alice")["plan"]
    path = "/days/0/plans/0/tracks/0/blocks/2/place"
    decision = dict(question="After dinner?", options=["Café", "Park"], choice="Café", decided_by="user", must=True,
                    subject=["p1", "p2", "p3"], locked_paths={path: "oslo-cafe"})
    draft = decide(seeded, decision, "decision")
    seeded.commit("alice", draft["draft_id"], "decision")
    current["days"][0]["plans"][0]["tracks"][0]["blocks"][2]["place"] = "oslo-park"
    bad = propose(seeded, current, key="bad-lock")
    assert bad["status"] == "rejected"
    with pytest.raises(ValueError, match="locked"):
        seeded.commit("alice", bad["draft_id"], "bad-lock", accept_unverified=True)
    assert seeded.state("alice")["number"] == 2
    assert seeded.state("alice")["plan"]["days"][0]["plans"][0]["tracks"][0]["blocks"][2]["place"] == "oslo-cafe"


def test_stale_draft_and_commit_key_reuse_refused(seeded):
    first = propose(seeded, key="first")
    later = propose(seeded, key="later")
    seeded.commit("alice", later["draft_id"], "later")
    with pytest.raises(ValueError, match="Stale"):
        seeded.commit("alice", first["draft_id"], "first")
    with pytest.raises(ValueError, match="different"):
        seeded.commit("alice", first["draft_id"], "later")


def test_missing_usage_is_a_commit_failure(seeded, item):
    recorded = propose(seeded, changes={"items": [item]}, key="pref")
    seeded.commit("alice", recorded["draft_id"], "pref")
    missing = propose(seeded, seeded.state("alice")["plan"], usages=[], key="missing")
    assert any(check["id"].startswith("usage:") for check in missing["checks"]["conflicts"])
    with pytest.raises(ValueError, match="usage:"):
        seeded.commit("alice", missing["draft_id"], "missing")


def test_unverified_critical_fact_never_becomes_passed(seeded):
    plan = seeded.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0]["critical_facts"] = ["opening_hours"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0]["evidence_ids"] = []
    draft = propose(seeded, plan, key="unverified")
    assert not draft["checks"]["verified"]
    with pytest.raises(ValueError, match="remain unverified"):
        seeded.commit("alice", draft["draft_id"], "unverified")
    version = seeded.commit("alice", draft["draft_id"], "unverified", accept_unverified=True)
    assert not version["checks"]["verified"]
    evidence_checks = [check for check in version["checks"]["checks"] if check["id"].startswith("evidence:")]
    assert evidence_checks[0]["status"] == "unverified"
    assert evidence_checks[0]["consequence"] and evidence_checks[0]["alternative"]


def test_failed_commit_rolls_back_ledger_changes(seeded, item):
    plan = seeded.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][1]["start"] = "10:30"
    bad = propose(seeded, plan, {"items": [item]}, key="rollback")
    with pytest.raises(ValueError, match="Commit refused"):
        seeded.commit("alice", bad["draft_id"], "rollback")
    assert seeded.ledger.current("alice")["items"] == []


def test_handles_cannot_access_another_user(seeded):
    draft = propose(seeded, key="private")
    with pytest.raises(ValueError, match="Unknown"):
        seeded.commit("bob", draft["draft_id"], "stolen")
    with pytest.raises(ValueError, match="Unknown"):
        seeded.result("bob", draft["job_id"])
    assert seeded.state("bob")["number"] == 0


def test_existing_bookings_and_hard_nodes_cannot_be_removed(service):
    from tripwright.demo import example

    turn = service.turn("alice", "Create fictional trip", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=example()["trip"], idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    plan = service.state("alice")["plan"]
    plan["days"][1]["anchors"] = []
    plan["bookings"] = []
    draft = propose(service, plan, key="delete-hard")
    conflicts = draft["checks"]["conflicts"]
    assert any(check["id"].startswith("paid-booking:") for check in conflicts)
    assert any(check["id"].startswith("preserved-anchor:") for check in conflicts)
    with pytest.raises(ValueError, match="Commit refused"):
        service.commit("alice", draft["draft_id"], "delete-hard", accept_unverified=True)
