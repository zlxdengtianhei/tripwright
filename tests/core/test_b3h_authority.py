"""B3h (D-50): the authority to change a user lock follows the order the messages were received; a correction is asked
when it was proposed; a user part of an agent's decision replaces the user's lock; the first plan and a user decision
commit together.

The judge's counterexamples: A, a retried turn of an older message (same request_id, same receipt, a new context on the
current version) undid a later user decision; B, a correction citing an earlier event let a confirmation received before
the correction was proposed take effect; C, a user part admitted by B3g could not replace the user's lock; D, a user
decision could not be recorded with the first plan. Each has its bad sample and its legal control. Fictional people,
places and tickets only."""
from __future__ import annotations

from itertools import count

import pytest

from test_b3e_reference_graph import GROUP, LOCKED, agent, lock_conflicts, node, seed
from test_b3f_three_paths import (ENTRY, HALL, NEAR, active_decisions, commit, correction, fixture, locked, message, propose,  # noqa: F401
                                  report, with_entry, with_hall)
from tripwright import locks
from tripwright.trip import aware

LOCK = f"/blocks/{LOCKED}"
CAFE = "/blocks/d1-cafe"
KEYS = count()


def retry(service, turn):
    """The same message sent again under its request ID: the same receipt, a new context on the current version."""
    event = service.store.get("events", "alice", turn["receipt"]["event_id"])
    received = event["received"]
    return service.turn("alice", received["text"], event["request_id"],
                        **{key: received.get(key) for key in ("nature", "native_ref", "channel", "speaker")})


def decided(turn, plan, *, paths=(LOCK,), **extra):
    return dict(dict(question="门票怎么买", options=["这样"], choice="这样", decided_by="user", must=True, subject=GROUP,
                     source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                     locked_paths={path: locks.pointer(plan, path) for path in paths}), **extra)


def where(service, turn, value):
    """The user's decision in `turn` to buy the hall entry at `value`."""
    plan = with_entry(service, where=[value])
    return propose(service, turn, plan=plan, decisions=[decided(turn, plan)])


def bought(service, turn, quote="我买了票", old="on_site", new="bought"):
    return propose(service, turn, plan=with_entry(service, status=new), reports=[report(turn, quote=quote, old=old, new=new)])


SOUTH = [47.80, 13.04]


def entrance(service, turn, quote, old, new):
    """The user's report in `turn` that the hall's entrance is at `new`."""
    return propose(service, turn, plan=with_hall(service, coords=new), reports=[report(turn, quote=quote, target=HALL, field="coords",
                                                                                      old=old, new=new)])


# --- A: authority follows the order of the messages -------------------------------------------------------------------

@pytest.mark.parametrize("way", ["decision", "report"])
def test_counterexample_a_a_retried_older_message_cannot_undo_what_a_later_one_decided(locked, way):
    if way == "decision":
        first = message(locked, "门票从 A 窗口买，定了。")
        commit(locked, where(locked, first, "A counter"), accept_unverified=True)
        later = message(locked, "现在改从 B 窗口买，前面的 A 取消。")
        commit(locked, where(locked, later, "B counter"), accept_unverified=True)
    else:
        first = message(locked, "入口改到北门了。")
        commit(locked, entrance(locked, first, "入口改到北门", SOUTH, NEAR), accept_unverified=True)
        later = message(locked, "北门关了，入口又回到南门。")
        commit(locked, entrance(locked, later, "入口又回到南门", NEAR, SOUTH), accept_unverified=True)
    plan, decisions, version = locked.state("alice")["plan"], active_decisions(locked), locked.state("alice")["number"]
    again = retry(locked, first)
    assert again["receipt"] == first["receipt"] and again["ctx_id"] != first["ctx_id"] and again["base_version"] == version
    with pytest.raises(ValueError, match=rf"not admitted \(order\): the authority is out of date: lock {decisions[0]['id']} was changed "
                                         rf"by message {later['receipt']['event_id']}"):
        where(locked, again, "A counter") if way == "decision" else entrance(locked, again, "入口改到北门", SOUTH, NEAR)
    assert locked.state("alice")["plan"] == plan and active_decisions(locked) == decisions
    # The lock now records the later message as its authority.
    assert decisions[0]["authority"] == dict(event=later["receipt"]["event_id"], received_at=later["receipt"]["received_at"])


def test_a_newer_message_changes_the_lock_again_after_a_refused_retry(locked):
    first = message(locked, "门票从 A 窗口买，定了。")
    commit(locked, where(locked, first, "A counter"), accept_unverified=True)
    later = message(locked, "现在改从 B 窗口买。")
    commit(locked, where(locked, later, "B counter"), accept_unverified=True)
    with pytest.raises(ValueError, match=r"not admitted \(order\)"):
        where(locked, retry(locked, first), "A counter")
    newest = message(locked, "还是回到 A 窗口买吧，定了。")
    commit(locked, where(locked, newest, "A counter"), accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["where"] == ["A counter"]
    [current] = active_decisions(locked)
    assert current["authority"]["event"] == newest["receipt"]["event_id"]


def test_one_message_may_have_several_drafts_of_which_one_commits(locked):
    turn = message(locked, "门票从 A 或 C 窗口买都行，你比一下，定了。")
    one, other = where(locked, turn, "A counter"), where(locked, turn, "C counter")
    assert one["status"] == other["status"] == "proposed"
    commit(locked, one, accept_unverified=True)
    with pytest.raises(ValueError, match="Ledger changed|Stale"):
        commit(locked, other, accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["where"] == ["A counter"]


def test_retrying_a_turn_with_the_same_input_only_returns_it(locked):
    turn = message(locked, "我们几点出发？")
    events = len(locked.store.all("events", "alice"))
    again = retry(locked, turn)
    assert again["receipt"] == turn["receipt"] and len(locked.store.all("events", "alice")) == events


def test_the_same_message_that_set_a_lock_does_not_change_it_again(locked):
    """B3h let an equal message change the lock again in a second proposal; B3j (D-52) refuses it: a message changes a
    lock with one commit. Both changes in one proposal are the legal way (test_b3j_conserve)."""
    turn = message(locked, "门票从 A 窗口买，我买了票。")
    commit(locked, where(locked, turn, "A counter"), accept_unverified=True)
    draft = bought(locked, retry(locked, turn))
    with pytest.raises(ValueError, match=r"not admitted \(used\): message " + turn["receipt"]["event_id"]):
        commit(locked, draft, accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["status"] == "on_site"


def test_a_lock_from_before_b3h_takes_its_authority_from_its_amendment_or_its_message(locked):
    turn = message(locked, "我买了票。")
    commit(locked, bought(locked, turn), accept_unverified=True)
    [lock] = active_decisions(locked)
    lock.pop("authority")
    locked.store.put("decisions", "alice", lock)
    assert locked.ledger.authority("alice", lock)["event"] == turn["receipt"]["event_id"]
    original = next(decision for decision in locked.store.all("decisions", "alice") if decision["status"] == "superseded")
    assert locked.ledger.authority("alice", dict(original, authority=None))["event"] == original["source"]["event"]


# --- B: a correction is asked when it was proposed ----------------------------------------------------------------------

def open_correction(service, cited, turn):
    commit(service, propose(service, turn, corrections=[correction(turn, source=dict(event=cited["receipt"]["event_id"]))]))
    return next(question for question in service.store.all("undecided", "alice") if question.get("kind") == "correction")


def confirm(service, turn, question):
    return propose(service, turn, plan=with_hall(service, coords=NEAR), confirm_corrections=[dict(
        id=question["id"], answer="confirmed", quote="确认新入口", source=dict(event=turn["receipt"]["event_id"]))])


@pytest.mark.parametrize("asked_in", ["new turn", "retried old turn"])
def test_counterexample_b_a_confirmation_received_before_the_correction_was_proposed_is_refused(locked, asked_in):
    old = message(locked, "agent notes the map", nature="agent_inference", speaker=None)            # t0
    premature = message(locked, "确认新入口。")                                                      # t1
    turn = message(locked, "agent proposes the entrance", nature="agent_inference", speaker=None) if asked_in == "new turn" else retry(locked, old)
    draft = propose(locked, turn, corrections=[correction(turn, source=dict(event=old["receipt"]["event_id"]))])  # t2
    commit(locked, draft)
    question = next(entry for entry in locked.store.all("undecided", "alice") if entry.get("kind") == "correction")
    # Asked by the proposal: its message and the server time it was made, not the event its source cites.
    assert question["source_event"] == old["receipt"]["event_id"] and question["asked"]["event"] == turn["receipt"]["event_id"]
    assert aware(question["asked"]["at"]) > aware(premature["receipt"]["received_at"])
    with pytest.raises(ValueError, match="later message than the proposal that opened the correction"):
        confirm(locked, retry(locked, premature), question)
    assert node(locked.state("alice")["plan"], HALL)["coords"] == [47.80, 13.04]
    assert locked.store.get("undecided", "alice", question["id"])["status"] == "open"
    # The legal control: the user's next message confirms it.
    answer = message(locked, "对，确认新入口。")
    commit(locked, confirm(locked, answer, question), accept_unverified=True)
    assert node(locked.state("alice")["plan"], HALL)["coords"] == NEAR


def test_a_question_from_before_b3h_is_asked_when_it_was_stored(locked):
    old = message(locked, "agent notes the map", nature="agent_inference", speaker=None)
    premature = message(locked, "确认新入口。")
    turn = message(locked, "agent proposes the entrance", nature="agent_inference", speaker=None)
    question = open_correction(locked, old, turn)
    question.pop("asked")
    locked.store.put("undecided", "alice", question)
    with pytest.raises(ValueError, match="later message than the proposal"):
        confirm(locked, retry(locked, premature), question)


# --- C: the user part of an agent's decision replaces the user's lock -------------------------------------------------

def test_counterexample_c_a_user_part_of_an_agents_decision_replaces_the_users_lock(locked):
    [old] = active_decisions(locked)
    turn = message(locked, "门票这一部分我决定改为买好；其他由你定。")
    plan = with_entry(locked, status="bought")
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, decided_by="agent", parts={LOCK: "user"},
                                                                source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]
    version = commit(locked, draft, accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["status"] == "bought"
    [new] = active_decisions(locked)
    assert new["decided_by"] == "agent" and new["parts"] == {LOCK: "user"} and new["authority"]["event"] == turn["receipt"]["event_id"]
    assert version["lock_changes"]["decisions"][0]["replaces"] == [dict(decision=old["id"], locks=[LOCK])]
    # The new lock holds: the agent moving the ticket back is refused.
    assert lock_conflicts(agent(locked, with_entry(locked, status="on_site")))


def test_an_agents_part_still_cannot_change_a_users_lock(locked):
    turn = message(locked, "上午的咖啡我们定了；门票你看着办。")
    plan = with_entry(locked, status="bought")
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, paths=(LOCK, CAFE), decided_by="agent", parts={CAFE: "user"},
                                                                source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))])
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["details"]["changed"] == [f"{ENTRY}/status"]
    with pytest.raises(ValueError, match=f"{ENTRY}/status changed"):
        commit(locked, draft, accept_unverified=True)


@pytest.mark.parametrize("decided_by", ["user", "agent"])
def test_a_replacement_keeps_the_old_locks_other_paths(service, decided_by):
    seed(service, fixture())
    plan = service.state("alice")["plan"]
    first = message(service, "饭后抱石和上午咖啡都定了。")
    commit(service, propose(service, first, decisions=[decided(first, plan, paths=(LOCK, CAFE))]), accept_unverified=True)
    [old] = active_decisions(service)
    turn = message(service, "门票这一部分改成已经买好。")
    plan = with_entry(service, status="bought")
    parts = {} if decided_by == "user" else dict(parts={LOCK: "user"}, source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))
    version = commit(service, propose(service, turn, plan=plan, decisions=[decided(turn, plan, decided_by=decided_by, **parts)]),
                     accept_unverified=True)
    [line] = version["lock_changes"]["decisions"][0]["replaces"]
    assert line["decision"] == old["id"] and line["locks"] == [LOCK] and line["kept"]["locks"] == [CAFE]
    remainder = service.store.get("decisions", "alice", line["kept"]["decision"])
    assert list(remainder["locked_paths"]) == [CAFE] and remainder["authority"]["event"] == first["receipt"]["event_id"]
    # The café the user decided stays locked.
    cafe = service.state("alice")["plan"]
    node(cafe, "block:d1-cafe").update(start="10:30", end="11:30")
    assert lock_conflicts(agent(service, cafe))


# --- D: the first plan and a user decision commit together ------------------------------------------------------------

def test_counterexample_d_the_first_plan_and_a_users_decision_commit_together(service):
    turn = message(service, "第一天饭后的抱石和门票定了。")
    plan = fixture()
    draft = propose(service, turn, plan=plan, decisions=[decided(turn, plan)])
    assert draft["status"] == "proposed", draft["checks"]["conflicts"]
    assert draft["lock_changes"]["decisions"][0]["people_from"] == "the first plan (no plan was committed yet)"
    version = commit(service, draft, accept_unverified=True)
    assert version["number"] == 1 and version["lock_changes"]["decisions"][0]["speaker"] == "u1"
    [lock] = active_decisions(service)
    assert lock["authority"]["event"] == turn["receipt"]["event_id"]
    assert lock_conflicts(agent(service, with_entry(service, status="bought")))


def test_the_first_plans_people_count_only_before_a_plan_is_committed(service):
    stranger = message(service, "抱石定了。", speaker="stranger")
    plan = fixture()
    with pytest.raises(ValueError, match=r"not admitted \(speaker\)"):
        propose(service, stranger, plan=plan, decisions=[decided(stranger, plan)])
    seed(service, fixture())
    # A committed plan: a person the candidate adds is not yet a person of the trip.
    newcomer = message(service, "抱石定了。", speaker="u9")
    plan = service.state("alice")["plan"]
    plan["people"].append(dict(id="u9", name="Newcomer"))
    with pytest.raises(ValueError, match=r"not admitted \(speaker\).*\(u1, u2, u3, u4\), got 'u9'"):
        propose(service, newcomer, plan=plan, decisions=[decided(newcomer, plan)])
    later = message(service, "抱石定了。")
    assert "people_from" not in propose(service, later, plan=plan, decisions=[decided(later, plan)])["lock_changes"]["decisions"][0]
