"""What the user asks for is committed before the plan draft that uses it (2026-11-12).

The user: "these requirements must never be lost". Live runs: when the main agent put a new requirement and the plan
change in one draft and the verifier found the plan incomplete, nothing was committed, and the requirement was in the
ledger 4 times in 10. Now the service commits the message's records first, as their own ledger-only version, and the
plan draft is made on top of it against the same context."""
import time


def seeded(service, plan, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-09T20:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)


def edited(plan):
    days = [dict(day, title=day["title"] + " (walk)") if day["date"] == "2030-06-10" else day for day in plan["days"]]
    return dict(plan, days=days)


def ask(service, plan, key):
    turn = service.turn("alice", "10 号晚饭后那段咖啡不要了，18:00 改成去公园散步一小时。", key, scenario="replan")
    item = dict(kind="preference", key=f"evening:{key}", quote="咖啡不要了", text="10 号晚饭后不要咖啡", subject=["p1"],
                strength="prefer", source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    return turn, service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=edited(plan),
                                 changes=dict(items=[item]), idempotency_key=key)


def test_the_requirement_is_in_the_ledger_even_when_the_plan_draft_is_never_committed(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    before = service.versions.current("alice")["number"]
    turn, draft = ask(service, plan, "walk")
    assert draft["recorded_first"]["committed"] is True and draft["recorded_first"]["parts"] == ["items"]
    stored = [item for item in service.store.all("items", "alice") if item["key"] == "evening:walk"]
    assert stored and stored[0]["status"] == "active" and stored[0]["source"]["basis"] == "user_explicit"
    current = service.versions.current("alice")
    assert current["number"] == before + 1 and current["plan"]["days"] == service.store.get(
        "versions", "alice", current["id"])["plan"]["days"]
    assert all("(walk)" not in day["title"] for day in current["plan"]["days"])  # the plan itself is not changed yet


def test_the_plan_draft_on_top_commits_and_the_requirement_is_not_recorded_twice(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn, draft = ask(service, plan, "walk2")
    version = service.commit("alice", draft["draft_id"], "walk2-plan", accept_unverified=True)
    assert version["accepted"] and any("(walk)" in day["title"] for day in version["plan"]["days"])
    assert len([item for item in service.store.all("items", "alice") if item["key"] == "evening:walk2"]) == 1


def test_a_ledger_only_draft_is_unchanged(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "我们都想慢一点。", "slow", scenario="plan")
    item = dict(kind="preference", key="pace", quote="慢一点", text="慢一点", subject=["p1"], strength="prefer",
                source=dict(event=turn["receipt"]["event_id"]))
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(items=[item]),
                            idempotency_key="slow")
    assert "recorded_first" not in draft and draft["status"] == "proposed"


def test_a_context_moves_only_over_its_own_ledger_commits(service, plan, monkeypatch):
    """Another message's commit in between still makes the old context stale."""
    seeded(service, plan, monkeypatch)
    old = service.turn("alice", "10 号的安排再想想。", "old", scenario="plan")
    other = service.turn("alice", "我们都想慢一点。", "other", scenario="plan")
    item = dict(kind="preference", key="pace2", quote="慢一点", text="慢一点", subject=["p1"], strength="prefer",
                source=dict(event=other["receipt"]["event_id"]))
    led = service.propose("alice", ctx_id=other["ctx_id"], base_version=other["base_version"], changes=dict(items=[item]),
                          idempotency_key="other")
    service.commit("alice", led["draft_id"], "other")
    import pytest

    with pytest.raises(ValueError, match="Stale|changed"):
        service.propose("alice", ctx_id=old["ctx_id"], base_version=old["base_version"], plan=edited(plan), idempotency_key="old")


def test_a_plan_draft_with_nothing_of_the_message_recorded_names_the_requirements_role_first(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "10 号晚饭后那段咖啡不要了。", "bare", scenario="replan")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=edited(plan), idempotency_key="bare")
    assert [entry["role"] for entry in draft["flow"]["dispatch"]][:2] == ["tripwright-requirements", "tripwright-verifier"]
    turn2, recorded = ask(service, plan, "with")
    assert [entry["role"] for entry in recorded["flow"]["dispatch"]][0] == "tripwright-verifier"


def test_the_same_propose_sent_again_returns_the_same_draft(service, plan, monkeypatch):
    """Review point 2: a client retrying the same call with the same key gets the first result, not an error."""
    seeded(service, plan, monkeypatch)
    turn, first = ask(service, plan, "retry")
    item = dict(kind="preference", key="evening:retry", quote="咖啡不要了", text="10 号晚饭后不要咖啡", subject=["p1"],
                strength="prefer", source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    again = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=edited(plan),
                            changes=dict(items=[item]), idempotency_key="retry")
    assert again["draft_id"] == first["draft_id"] and again["recorded_first"]["draft_id"] == first["recorded_first"]["draft_id"]
    assert len([entry for entry in service.store.all("items", "alice") if entry["key"] == "evening:retry"]) == 1
