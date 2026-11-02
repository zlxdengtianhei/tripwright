"""B3i (D-51): a user decision replaces an old user lock only when it decides everything the lock decided, judged on
the comparator's objects and references; a decision that only touches the lock (a field inside it, or a field of an
object the lock reaches) refuses the whole proposal and says where to decide again.

The judge's counterexamples: A, a decision locking only /blocks/<id>/start superseded the whole-block lock, after which
the agent changed the block's activity and the booking's channel without the user (also in the same candidate); B, a
decision locking only /bookings/<id>/where, a field the block lock holds through its reference, was refused by the old
lock with no way to say so. Legal controls: a decision covering the lock replaces it; one sharing nothing with it
stands beside it; another block's lock stays. Fictional people, places and tickets only."""
from __future__ import annotations

import pytest

from test_b3e_reference_graph import D1, GROUP, LOCKED, agent, lock_conflicts, node, seed
from test_b3f_three_paths import (ENTRY, HALL, active_decisions, commit, fixture, locked, message, propose,  # noqa: F401
                                  with_entry, with_hall)
from tripwright import locks

LOCK = f"/blocks/{LOCKED}"
START = f"{LOCK}/start"
WHERE = "/bookings/hall-entry/where"
CAFE = "/blocks/d1-cafe"
BLOCK = f"block:{LOCKED}"


def decided(turn, plan, paths, how="user"):
    """The user's decision on `paths` in `turn`: a user decision, or the user part of an agent's decision."""
    decision = dict(question="饭后怎么安排", options=["这样"], choice="这样", decided_by="user", must=True, subject=GROUP,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={path: locks.pointer(plan, path) for path in paths})
    if how == "mixed":
        decision.update(decided_by="agent", parts={path: "user" for path in paths},
                        source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))
    return decision


def with_block(service, **fields):
    plan = service.state("alice")["plan"]
    node(plan, BLOCK).update(fields)
    return plan


def refused_for(draft, old, paths, decides):
    """The proposal is refused, naming the old lock, its paths and where to decide again."""
    assert draft["status"] == "rejected" and draft["notice"].startswith("Refused, nothing will be recorded")
    [line] = draft["lock_changes"]["decisions"]
    [refusal] = line["refused"]
    assert refusal["decision"] == old["id"] and refusal["locks"] == paths and refusal["decides"] == decides
    assert f"decides only a part of what lock {old['id']} holds at {', '.join(paths)}" in refusal["reason"]
    assert refusal["fix"] == f"record the decision on {', '.join(paths)}: the user decides that whole part again"
    assert line["replaces"] == [] and line["takes_effect"] == "nothing: the proposal is refused"


def still_locked(service, old):
    """The old lock stands, and the agent still cannot change the block's activity or the booking's channel."""
    assert [decision["id"] for decision in active_decisions(service)] == [old["id"]]
    for plan, field in ((with_block(service, text="Unchosen different activity"), f"value/text"),
                        (with_entry(service, where=["Unchosen private reseller"]), f"{ENTRY}/where")):
        draft = agent(service, plan)
        assert draft["status"] == "rejected" and field in lock_conflicts(draft)[0]["details"]["changed"]
        with pytest.raises(ValueError, match="Commit refused"):
            commit(service, draft, accept_unverified=True)


# --- A: a decision inside the lock does not release it ----------------------------------------------------------------

@pytest.mark.parametrize("how", ["user", "mixed"])
@pytest.mark.parametrize("candidate", ["start only", "start and the rest at once"])
def test_counterexample_a_a_decision_on_a_field_inside_a_block_lock_is_refused(locked, how, candidate):
    [old] = active_decisions(locked)
    turn = message(locked, "我只决定开始时间延后到 19:40，其他照旧。")
    plan = with_block(locked, start="19:40")
    if candidate == "start and the rest at once":
        node(plan, BLOCK)["text"] = "Unchosen different activity"
        node(plan, ENTRY)["where"] = ["Unchosen private reseller"]
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [START], how)])
    refused_for(draft, old, [LOCK], [START])
    with pytest.raises(ValueError, match=rf"Commit refused: .*user decision \S+ decides only a part of what lock {old['id']} holds at {LOCK}"):
        commit(locked, draft, accept_unverified=True)
    still_locked(locked, old)


def test_a_ledger_only_decision_inside_a_block_lock_is_refused_too(locked):
    [old] = active_decisions(locked)
    turn = message(locked, "开始时间就按 19:30，定了。")
    plan = locked.state("alice")["plan"]
    draft = propose(locked, turn, decisions=[decided(turn, plan, [START])])
    refused_for(draft, old, [LOCK], [START])
    with pytest.raises(ValueError, match="Commit refused"):
        commit(locked, draft)
    still_locked(locked, old)


# --- B: a field of an object the lock reaches -------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["user", "mixed"])
def test_counterexample_b_a_decision_on_a_field_the_lock_reaches_says_to_decide_the_block_again(locked, how):
    [old] = active_decisions(locked)
    turn = message(locked, "门票改到 B 窗口买，定了。")
    plan = with_entry(locked, where=["B counter"])
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [WHERE], how)])
    refused_for(draft, old, [LOCK], [WHERE])
    # Decided again on the block's path, the same change replaces the lock.
    again = message(locked, "门票改到 B 窗口买，这一整块就这样定了。")
    draft = propose(locked, again, plan=plan, decisions=[decided(again, plan, [LOCK], how)])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]
    version = commit(locked, draft, accept_unverified=True)
    assert version["lock_changes"]["decisions"][0]["replaces"] == [dict(decision=old["id"], locks=[LOCK])]
    assert node(locked.state("alice")["plan"], ENTRY)["where"] == ["B counter"]
    # The user's other choices stay protected by the new lock.
    for plan, field in ((with_block(locked, text="Unchosen different activity"), "value/text"),
                        (with_hall(locked, coords=[47.81, 13.04]), f"{HALL}/coords")):
        assert field in lock_conflicts(agent(locked, plan))[0]["details"]["changed"]


# --- legal controls ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [LOCK, f"/days/{D1}"], ids=["same path", "the day holding it"])
def test_a_decision_deciding_the_whole_lock_replaces_it(locked, path):
    [old] = active_decisions(locked)
    turn = message(locked, "饭后这样安排，开始改到 19:40，定了。")
    plan = with_block(locked, start="19:40")
    version = commit(locked, propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [path])]), accept_unverified=True)
    [line] = version["lock_changes"]["decisions"][0]["replaces"]
    assert (line["decision"], line["locks"]) == (old["id"], [LOCK])
    # B3j (D-52): the day does not hold the timezone the block's times are read in; it stays locked in a remainder.
    assert line.get("kept", {}).get("locks") == (["/timezone"] if path != LOCK else None)
    assert node(locked.state("alice")["plan"], BLOCK)["start"] == "19:40"


def test_a_block_decision_replaces_a_lock_on_one_of_its_fields(service):
    seed(service, fixture())
    first = message(service, "开始时间定在 19:30。")
    plan = service.state("alice")["plan"]
    commit(service, propose(service, first, decisions=[decided(first, plan, [START])]), accept_unverified=True)
    [old] = active_decisions(service)
    turn = message(service, "整块改成 19:40 开始，定了。")
    plan = with_block(service, start="19:40")
    version = commit(service, propose(service, turn, plan=plan, decisions=[decided(turn, plan, [LOCK])]), accept_unverified=True)
    assert version["lock_changes"]["decisions"][0]["replaces"] == [dict(decision=old["id"], locks=[START])]


def test_decisions_sharing_nothing_stand_side_by_side(service):
    seed(service, fixture())
    first = message(service, "开始时间定在 19:30。")
    plan = service.state("alice")["plan"]
    commit(service, propose(service, first, decisions=[decided(first, plan, [START])]), accept_unverified=True)
    turn = message(service, "结束时间定在 21:00。")
    version = commit(service, propose(service, turn, decisions=[decided(turn, plan, [f"{LOCK}/end"])]), accept_unverified=True)
    assert version["lock_changes"]["decisions"][0]["replaces"] == []
    assert sorted(path for decision in active_decisions(service) for path in decision["locked_paths"]) == [f"{LOCK}/end", START]


def test_another_blocks_lock_stays(locked):
    first = message(locked, "上午的咖啡也定了。")
    plan = locked.state("alice")["plan"]
    commit(locked, propose(locked, first, decisions=[decided(first, plan, [CAFE])]), accept_unverified=True)
    block_lock = next(decision for decision in active_decisions(locked) if LOCK in decision["locked_paths"])
    turn = message(locked, "咖啡改到 10:30，定了。")
    plan = locked.state("alice")["plan"]
    node(plan, "block:d1-cafe").update(start="10:30", end="11:30")
    version = commit(locked, propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [CAFE])]), accept_unverified=True)
    [line] = version["lock_changes"]["decisions"][0]["replaces"]
    assert line["locks"] == [CAFE] and block_lock in active_decisions(locked)


# --- the judge on objects ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claims,expected", [
    ({f"block:{LOCKED}"}, "covers"),
    ({f"day:{D1}", f"block:{LOCKED}", "block:d1-cafe"}, "covers"),
    ({f"block:{LOCKED}/start"}, "touches"),
    ({"booking:hall-entry/where"}, "touches"),
    ({"booking:hall-entry"}, "touches"),
    ({"block:d1-cafe"}, None),
    ({f"block:{LOCKED}-other"}, None),
])
def test_how_a_decision_meets_a_block_lock(locked, claims, expected):
    [old] = active_decisions(locked)
    assert locks.reach(claims, LOCK, old["locked_paths"][LOCK], old["placement"][LOCK]) == expected


@pytest.mark.parametrize("path,name", [(START, f"block:{LOCKED}/start"), (WHERE, "booking:hall-entry/where"),
                                       ("/bookings/ride/fares/F0/ticket", "fare:F0/ticket"), (f"/days/{D1}/plans/rec", f"day:{D1}/plans/rec"),
                                       ("/timezone", "trip/timezone"), ("/places/gym-a", "place:gym-a")])
def test_lock_paths_are_named_by_object(path, name):
    assert locks.identity(path) == name
