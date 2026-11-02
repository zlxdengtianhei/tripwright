"""B3j (D-52): the commit checks user locks against the version it is based on, whatever the ledger recorded in between.

Conservation: a part a base lock protects changes only with the user's authority for it (a decision this proposal
admitted that decides all the lock decided, or the user's report or confirmed correction of that field), and a part
still in the plan stays protected by some lock after the commit. Authority used once: a message changes a lock (and
what replaced it) with one commit. The judge's counterexamples: A, the same message, retried for a new context, changed
the same lock in a second commit (report→report, decision→report, report→decision); B, after the user decided the day
holding a locked block again, the agent changed the trip's timezone, which the block lock had protected and the day lock
did not. Legal controls stay: one proposal changing several fields; the same message changing two unrelated locks in
two commits; the same draft committed twice; a new message; the day decided again without one of its blocks.
Fictional people, places and tickets only."""
from __future__ import annotations

import sys

import pytest

from test_b3e_reference_graph import D1, GROUP, LOCKED, agent, lock_conflicts, node
from test_b3f_three_paths import ENTRY, active_decisions, commit, locked, message, propose, with_entry  # noqa: F401
from test_b3h_authority import retry
from tripwright import locks
from tripwright.ledger import Ledger

LOCK = f"/blocks/{LOCKED}"
DAY = f"/days/{D1}"
CAFE = "/blocks/d1-cafe"
BLOCK = f"block:{LOCKED}"
SAID = "官方窗口 A 或 B 都行，我决定购票渠道。"
QUOTE = "官方窗口 A 或 B"


def decided(turn, plan, paths, how="user"):
    decision = dict(question="饭后怎么安排", options=["这样"], choice="这样", decided_by="user", must=True, subject=GROUP,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={path: locks.pointer(plan, path) for path in paths})
    if how == "mixed":
        decision.update(decided_by="agent", parts={path: "user" for path in paths},
                        source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))
    return decision


def channel(service, turn, way, new):
    """The user's change of the hall entry's channel to `new`, as a report or as a decision on the block."""
    plan = with_entry(service, where=[new])
    if way == "decision":
        return propose(service, turn, plan=plan, decisions=[decided(turn, plan, [LOCK])])
    old = node(service.state("alice")["plan"], ENTRY)["where"]
    return propose(service, turn, plan=plan, reports=[dict(target=ENTRY, field="where", old=old, new=[new], quote=QUOTE,
                                                            source=dict(event=turn["receipt"]["event_id"]))])


def with_block(service, **fields):
    plan = service.state("alice")["plan"]
    node(plan, BLOCK).update(fields)
    return plan


# --- A: a message changes a lock with one commit ------------------------------------------------------------------------

@pytest.mark.parametrize("first,second", [("report", "report"), ("decision", "report"), ("report", "decision"), ("decision", "decision")])
def test_counterexample_a_the_same_message_retried_cannot_change_the_lock_again(locked, first, second):
    turn = message(locked, SAID)
    version = commit(locked, channel(locked, turn, first, "A"), accept_unverified=True)
    again = retry(locked, turn)
    assert again["receipt"] == turn["receipt"] and again["base_version"] == version["number"]
    draft = channel(locked, again, second, "B")
    assert draft["status"] == "rejected" and any(f"not admitted (used): message {turn['receipt']['event_id']}" in line
                                                 for line in draft["lock_changes"]["refused"]), draft["lock_changes"]
    with pytest.raises(ValueError, match=rf"Commit refused: .*not admitted \(used\): message {turn['receipt']['event_id']} already "
                                         rf"changed lock \S+ in version {version['number']}"):
        commit(locked, draft, accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["where"] == ["A"] and locked.state("alice")["number"] == version["number"]
    # The user's next message changes it.
    newer = message(locked, "还是改到 B 吧。官方窗口 A 或 B 选 B。")
    commit(locked, channel(locked, newer, second, "B"), accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["where"] == ["B"]


def test_the_same_draft_committed_again_returns_its_version(locked):
    turn = message(locked, SAID)
    draft = channel(locked, turn, "report", "A")
    assert commit(locked, draft, accept_unverified=True) == commit(locked, draft, accept_unverified=True)


def test_one_proposal_may_change_several_fields_of_a_lock(locked):
    turn = message(locked, "我买了票，官方窗口 A 或 B 选了 A。")
    plan = with_entry(locked, status="bought", where=["A"])
    draft = propose(locked, turn, plan=plan, reports=[
        dict(target=ENTRY, field="status", old="on_site", new="bought", quote="我买了票", source=dict(event=turn["receipt"]["event_id"])),
        dict(target=ENTRY, field="where", old=["Official counter"], new=["A"], quote=QUOTE, source=dict(event=turn["receipt"]["event_id"]))])
    assert draft["status"] == "proposed" and "refused" not in draft["lock_changes"]
    version = commit(locked, draft, accept_unverified=True)
    assert version["authorized"]["event"] == turn["receipt"]["event_id"] and len(version["lock_changes"]["amended"]) == 2


def test_one_message_may_change_two_unrelated_locks_in_two_commits(locked):
    turn = message(locked, "上午咖啡定了；饭后抱石也照现在定，开始改到 19:40。")
    plan = locked.state("alice")["plan"]
    commit(locked, propose(locked, turn, decisions=[decided(turn, plan, [CAFE])]), accept_unverified=True)
    again = retry(locked, turn)
    plan = with_block(locked, start="19:40")
    commit(locked, propose(locked, again, plan=plan, decisions=[decided(again, plan, [LOCK])]), accept_unverified=True)
    assert node(locked.state("alice")["plan"], BLOCK)["start"] == "19:40"


# --- B: what a replaced lock protected stays protected -------------------------------------------------------------------

@pytest.mark.parametrize("how", ["user", "mixed"])
def test_counterexample_b_the_timezone_stays_protected_after_the_day_is_decided_again(locked, how):
    [old] = active_decisions(locked)
    zone = locked.state("alice")["plan"]["timezone"]
    turn = message(locked, "这一天就这样，开始改到 19:40，定了。")
    plan = with_block(locked, start="19:40")
    version = commit(locked, propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [DAY], how)]), accept_unverified=True)
    [line] = version["lock_changes"]["decisions"][0]["replaces"]
    assert line["decision"] == old["id"] and line["kept"]["locks"] == ["/timezone"]
    remainder = locked.store.get("decisions", "alice", line["kept"]["decision"])
    assert remainder["locked_paths"] == {"/timezone": zone} and remainder["authority"] == old["authority"]
    plan = locked.state("alice")["plan"]
    plan["timezone"] = "Asia/Tokyo"
    draft = agent(locked, plan)
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["id"] == f"locked:{remainder['id']}:/timezone"
    with pytest.raises(ValueError, match="/timezone"):
        commit(locked, draft, accept_unverified=True)
    assert locked.state("alice")["plan"]["timezone"] == zone


def test_the_day_decided_again_cannot_change_the_timezone_at_the_same_time(locked):
    turn = message(locked, "这一天就这样，开始改到 19:40，定了。")
    plan = with_block(locked, start="19:40")
    plan["timezone"] = "Asia/Tokyo"
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [DAY])])
    assert draft["status"] == "rejected" and draft["notice"].startswith("Refused, nothing will be recorded")
    assert any("not admitted (conservation)" in line and "trip/timezone changed" in line for line in draft["lock_changes"]["refused"]), draft
    # At commit the remainder lock on /timezone refuses it first.
    with pytest.raises(ValueError, match=r"Commit refused: .*/timezone"):
        commit(locked, draft, accept_unverified=True)
    assert locked.state("alice")["plan"]["timezone"] != "Asia/Tokyo"


def test_a_part_no_lock_path_can_hold_refuses_the_replacement(locked):
    """A plan holds its blocks but not the plan's position among the day's alternatives, which the block lock held."""
    turn = message(locked, "推荐方案这一套就这样，定了。")
    plan = locked.state("alice")["plan"]
    path = f"{DAY}/plans/rec"
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [path])])
    assert draft["status"] == "rejected"
    assert any(f"not admitted (conservation)" in line and f"where:{BLOCK} would be protected by no lock" in line
               for line in draft["lock_changes"]["refused"]), draft["lock_changes"]
    with pytest.raises(ValueError, match=r"not admitted \(conservation\)"):
        commit(locked, draft, accept_unverified=True)


def test_the_day_decided_again_without_a_block_releases_its_lock(locked):
    turn = message(locked, "这一天重新定：饭后的抱石不去了。")
    plan = locked.state("alice")["plan"]
    for option in next(day for day in plan["days"] if day["date"] == D1)["plans"]:
        for track in option["tracks"]:
            track["blocks"] = [block for block in track["blocks"] if block["id"] != LOCKED]
    version = commit(locked, propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [DAY])]), accept_unverified=True)
    assert version["lock_changes"]["decisions"][0]["replaces"][0]["locks"] == [LOCK]
    assert all(LOCK not in decision["locked_paths"] for decision in active_decisions(locked))


def test_an_agent_cannot_drop_a_locked_block(locked):
    plan = locked.state("alice")["plan"]
    for option in next(day for day in plan["days"] if day["date"] == D1)["plans"]:
        for track in option["tracks"]:
            track["blocks"] = [block for block in track["blocks"] if block["id"] != LOCKED]
    draft = agent(locked, plan)
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["details"]["changed"] == ["missing"]


# --- the commit holds whatever the ledger records ---------------------------------------------------------------------

def test_the_commit_refuses_what_the_ledger_left_unprotected(locked, monkeypatch):
    """A ledger that keeps no remainder when the day replaces the block lock: the commit refuses instead of leaving the
    timezone unprotected."""
    monkeypatch.setattr(Ledger, "_keep_unclaimed", staticmethod(lambda *arguments: None))
    [old] = active_decisions(locked)
    turn = message(locked, "这一天就这样，开始改到 19:40，定了。")
    plan = with_block(locked, start="19:40")
    draft = propose(locked, turn, plan=plan, decisions=[decided(turn, plan, [DAY])])
    assert draft["status"] == "rejected" and any(f"lock {old['id']} at {LOCK}: trip/timezone would be protected by no lock" in line
                                                 for line in draft["lock_changes"]["refused"]), draft["lock_changes"]
    with pytest.raises(ValueError, match=r"not admitted \(conservation\)"):
        commit(locked, draft, accept_unverified=True)
    assert active_decisions(locked) == [old]


def test_the_commit_checks_the_order_when_the_ledger_does_not(locked, monkeypatch):
    """A ledger that forgets the order check (D-50) when it amends a lock: the commit refuses the older message."""
    original = Ledger.in_order

    def ledger_forgets(self, *arguments):
        if sys._getframe(1).f_code.co_name in ("_amend", "_replace"):
            return None
        return original(self, *arguments)

    monkeypatch.setattr(Ledger, "in_order", ledger_forgets)
    older = message(locked, SAID)
    newer = message(locked, "还是改到 B 吧。官方窗口 A 或 B 选 B。")
    commit(locked, channel(locked, newer, "report", "B"), accept_unverified=True)
    draft = channel(locked, retry(locked, older), "report", "A")
    assert draft["status"] == "rejected" and any("not admitted (order)" in line for line in draft["lock_changes"]["refused"]), draft
    with pytest.raises(ValueError, match=r"not admitted \(order\)"):
        commit(locked, draft, accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)["where"] == ["B"]


# --- the check on its own --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,changes,expected", [
    ("/blocks/d1/start", ["value"], {"block:d1/start"}),
    ("/blocks/d1", ["value/start", "date", "timezone", "missing"], {"block:d1/start", "where:block:d1", "trip/timezone", "block:d1"}),
    ("/blocks/d1", ["booking:entry/where", "booking:ride/fares/F0/ticket", "fare:F0/ticket"],
     {"booking:entry/where", "fare:F0/ticket"}),
    (DAY, ["value/plans/rec/tracks/0/blocks/d1/text"], {"block:d1/text"}),
])
def test_comparator_changes_are_named_by_object(path, changes, expected):
    assert locks.changed_items(path, changes) == expected


def test_a_day_holds_where_its_blocks_sit_and_a_plan_does_not(locked):
    plan = locked.state("alice")["plan"]
    assert f"where:{BLOCK}" in locks.required(plan, DAY) and f"where:{BLOCK}" not in locks.required(plan, f"{DAY}/plans/rec")
    assert "trip/timezone" in locks.required(plan, LOCK) and "trip/timezone" not in locks.required(plan, DAY)
