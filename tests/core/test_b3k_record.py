"""B3k (D-53): only what the user marked a must is locked; everything else is recorded, and changes need a reason.

- A user decision without `must` is a record. The agent may change what it decided when `changes.revisions` names the
  decision with a reason. Without a reason the proposal is refused, and the refusal says what to add.
- A must decision is a lock: the agent cannot move or change it, and no revision is accepted for it. The user's own new
  decision in this turn changes it, and passes the admission even when it drops the must.
- A decision stored before the mark existed has no `must` and locks nothing.
- Bought or booked tickets and hard nodes are facts. They change only in the fields that the user's report or a
  confirmed correction names.
- Every proposal and commit carries a change log: each changed field, who changed it, on what basis and why. The history
  view and explain list it by version.

Fictional people, places and tickets only."""
from __future__ import annotations

import pytest

from test_b3e_reference_graph import GROUP, LOCKED, lock_conflicts, node, seed
from test_b3f_three_paths import ENTRY, active_decisions, commit, fixture, message, propose, report, with_entry
from tripwright import locks

LOCK = f"/blocks/{LOCKED}"
BLOCK = f"block:{LOCKED}"
BOAT = "anchor:last-boat"
OTHER = "Indoor rope climbing instead"


def decided(service, turn, *, must=False, text=None):
    """The user's decision on the climbing block in `turn`, with its current value or a new activity text."""
    plan = service.state("alice")["plan"]
    if text is not None:
        node(plan, BLOCK)["text"] = text
    decision = dict(question="饭后去哪", options=["岩馆 A"], choice="岩馆 A", decided_by="user", must=must, subject=GROUP,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"), locked_paths={LOCK: locks.pointer(plan, LOCK)})
    return plan, decision


@pytest.fixture
def chosen(service):
    """The user chose the climbing block after dinner, without saying it is a must."""
    seed(service, fixture())
    turn = message(service, "第一天饭后去岩馆 A 吧。")
    commit(service, propose(service, turn, decisions=[decided(service, turn)[1]]), accept_unverified=True)
    return service


@pytest.fixture
def required(service):
    """The user said the climbing block is a must."""
    seed(service, fixture())
    turn = message(service, "第一天饭后必须去岩馆 A。")
    commit(service, propose(service, turn, decisions=[decided(service, turn, must=True)[1]]), accept_unverified=True)
    return service


def by_agent(service, plan=None, **changes):
    turn = message(service, "agent adjusts the evening", nature="agent_inference", speaker=None)
    changes.setdefault("revisions", [])  # explicit empty: test the refusal, not service-filled revisions
    return turn, propose(service, turn, plan=plan, **changes)


def with_block(service, **fields):
    plan = service.state("alice")["plan"]
    node(plan, BLOCK).update(fields)
    return plan


def lines(draft, target):
    return [line for line in draft["changelog"] if line["target"] == target]


# --- not a must: a record the agent may change with a reason --------------------------------------------------------------

def test_the_agent_changes_a_choice_that_is_not_a_must_with_a_reason(chosen):
    [decision] = active_decisions(chosen)
    assert decision["must"] is False
    old = node(chosen.state("alice")["plan"], BLOCK)["text"]
    reason = "岩馆 A 晚上满员，改成同馆的顶绳"
    turn, draft = by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision=decision["id"], reason=reason)])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == [] and draft["notice"] is None
    assert lines(draft, BLOCK) == [dict(target=BLOCK, field="text", change="changed", old=old, new=OTHER, by="agent",
                                        basis=dict(event=turn["receipt"]["event_id"]), why=reason, revises=decision["id"])]
    version = commit(chosen, draft, accept_unverified=True)
    assert version["changelog"] == draft["changelog"] and node(chosen.state("alice")["plan"], BLOCK)["text"] == OTHER
    # The user's decision stays on record; the journal says the agent revised it and why.
    assert [entry["id"] for entry in active_decisions(chosen)] == [decision["id"]]
    [entry] = [entry for entry in chosen.explain("alice", decision["id"])["journal"] if entry["action"] == "revise_decision"]
    assert entry["details"]["reason"] == reason and entry["details"]["version"] == version["number"]


def test_without_a_reason_the_change_is_refused_and_the_fix_says_what_to_add(chosen):
    [decision] = active_decisions(chosen)
    _, draft = by_agent(chosen, with_block(chosen, text=OTHER))
    assert draft["status"] == "rejected" and draft["notice"].startswith("Refused, nothing will be recorded")
    assert f"not recorded (reason): the plan changes what the user decided in {decision['id']} at {LOCK} (block:{LOCKED}/text)" in draft["notice"]
    assert f'fix: add changes.revisions {{decision: "{decision["id"]}", reason: "<why>"}}' in draft["notice"]
    assert lock_conflicts(draft) == []  # not a lock: nothing in the checks
    with pytest.raises(ValueError, match=r"Commit refused: not recorded \(reason\)"):
        commit(chosen, draft, accept_unverified=True)
    assert node(chosen.state("alice")["plan"], BLOCK)["text"] != OTHER


def test_what_the_choice_reaches_needs_a_reason_too(chosen):
    """The comparator's object graph: the booking the chosen block uses is part of the choice."""
    [decision] = active_decisions(chosen)
    _, draft = by_agent(chosen, with_entry(chosen, where=["Online shop"]))
    assert "booking:hall-entry/where" in draft["notice"]
    _, draft = by_agent(chosen, with_entry(chosen, where=["Online shop"]), revisions=[dict(decision=decision["id"], reason="柜台关了")])
    commit(chosen, draft, accept_unverified=True)
    assert lines(draft, ENTRY)[0]["revises"] == decision["id"]


def test_changes_no_user_decision_holds_need_no_reason_and_carry_the_proposals_why(chosen):
    plan = chosen.state("alice")["plan"]
    plan["title"] = "Fictional lake weekend"
    turn, draft = by_agent(chosen, plan, why="标题写清楚是哪次旅行")
    assert draft["status"] == "proposed"
    assert draft["changelog"] == [dict(target="trip:graph-trip", field="title", change="changed", old="Fictional reference graph",
                                       new="Fictional lake weekend", by="agent", basis=dict(event=turn["receipt"]["event_id"]),
                                       why="标题写清楚是哪次旅行")]


def test_a_revision_must_name_a_current_decision_that_is_not_a_must(chosen):
    with pytest.raises(ValueError, match="changes.revisions: D-nothing is not a current decision of the user without must"):
        by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision="D-nothing", reason="x")])
    with pytest.raises(ValueError, match="reason"):
        by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision=active_decisions(chosen)[0]["id"], reason="")])


def test_a_revision_naming_a_recorded_condition_says_how_to_replace_it(chosen):
    # DeepSeek Flash, 2026-11-12: to narrow “no peanuts” to “no peanut butter” it sent revisions naming the item I-…
    # five times over and gave up; an item is replaced by a new statement with exclusive_with, and the user is asked
    with pytest.raises(ValueError, match="I-peanuts is a recorded condition, not a decision: to replace or narrow it, record "
                                         "the new statement in items with exclusive_with naming I-peanuts"):
        by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision="I-peanuts", reason="x")])
    # by its key: the hint names the item's ID
    quote = "我对花生过敏"
    item = dict(kind="constraint", key="peanut_allergy", quote=quote, text="No peanuts", subject=["u1"], strength="forbid",
                source=dict(event="this", basis="user_explicit"))
    item_id = chosen.turn("alice", quote, "allergy", record=dict(items=[item]))["recorded"]["changed"][0]
    with pytest.raises(ValueError, match=f"peanut_allergy is a recorded condition, not a decision: .* exclusive_with naming {item_id}"):
        by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision="peanut_allergy", reason="x")])


# --- a must: a lock ---------------------------------------------------------------------------------------------------------

def test_the_agent_cannot_move_or_change_a_must(required):
    [decision] = active_decisions(required)
    plan = required.state("alice")["plan"]
    for day in plan["days"]:
        for option in day["plans"]:
            for track in option["tracks"]:
                track["blocks"] = [block for block in track["blocks"] if block["id"] != LOCKED]
    _, draft = by_agent(required, plan)
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["details"]["changed"] == ["missing"]
    with pytest.raises(ValueError, match=f"Commit refused: locked:{decision['id']}:{LOCK}"):
        commit(required, draft, accept_unverified=True)
    _, draft = by_agent(required, with_block(required, text=OTHER))
    assert lock_conflicts(draft)[0]["details"]["changed"] == ["value/text"]
    with pytest.raises(ValueError, match="a must changes only by the user's new decision"):
        by_agent(required, with_block(required, text=OTHER), revisions=[dict(decision=decision["id"], reason="想换")])


def test_the_users_new_decision_in_this_turn_changes_a_must(required):
    [old] = active_decisions(required)
    turn = message(required, "饭后改成顶绳吧，也不用非去不可了。")
    plan, decision = decided(required, turn, text=OTHER)
    version = commit(required, propose(required, turn, plan=plan, decisions=[decision]), accept_unverified=True)
    assert version["lock_changes"]["decisions"][0]["replaces"] == [dict(decision=old["id"], locks=[LOCK])]
    [line] = lines(version, BLOCK)
    recorded = version["lock_changes"]["decisions"][0]["id"]
    assert (line["by"], line["basis"], line["new"]) == ("user", dict(event=turn["receipt"]["event_id"], decision=recorded), OTHER)
    # No longer a must: the agent may change it with a reason.
    [new] = active_decisions(required)
    _, draft = by_agent(required, with_block(required, text="Bouldering after all"), revisions=[dict(decision=new["id"], reason="顶绳满了")])
    assert draft["status"] == "proposed"


def test_dropping_a_must_needs_the_users_words_in_this_turn(required):
    """A decision that meets a must passes the admission even without `must`, so it is no way around the lock: here it
    cites an earlier message of the user, which a record without a must could cite."""
    earlier = message(required, "饭后随便吧。")
    now = message(required, "嗯。")
    plan, decision = decided(required, earlier, text=OTHER)
    with pytest.raises(ValueError, match=r"not admitted \(this turn\)"):
        propose(required, now, plan=plan, decisions=[decision])
    assert [entry["must"] for entry in active_decisions(required)] == [True]


def test_dropping_a_must_by_deciding_its_day_again_keeps_no_remainder(required):
    """Decided again without a must, the day releases the block's must whole: no /timezone lock is kept (B3j keeps one
    only when the new decision is a must)."""
    [old] = active_decisions(required)
    turn = message(required, "这一天就这样，不用非去哪了。")
    plan = required.state("alice")["plan"]
    day = f"/days/{plan['days'][0]['date']}"
    decision = dict(question="这一天", options=["这样"], choice="这样", decided_by="user", must=False, subject=GROUP,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"), locked_paths={day: locks.pointer(plan, day)})
    version = commit(required, propose(required, turn, decisions=[decision]), accept_unverified=True)
    assert version["lock_changes"]["decisions"][0]["replaces"] == [dict(decision=old["id"], locks=[LOCK])]
    assert not any(entry["must"] for entry in active_decisions(required))


# --- old data -------------------------------------------------------------------------------------------------------------

def test_a_decision_stored_before_the_mark_locks_nothing(chosen):
    [decision] = active_decisions(chosen)
    decision.pop("must")
    chosen.store.put("decisions", "alice", decision)
    _, draft = by_agent(chosen, with_block(chosen, text=OTHER))
    assert lock_conflicts(draft) == [] and "not recorded (reason)" in draft["notice"]
    _, draft = by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision=decision["id"], reason="旧记录，没说必须")])
    commit(chosen, draft, accept_unverified=True)
    assert node(chosen.state("alice")["plan"], BLOCK)["text"] == OTHER


# --- facts: a bought ticket and a hard node ---------------------------------------------------------------------------------

@pytest.fixture
def bought(service):
    seed(service, fixture())
    turn = message(service, "我买了票。")
    commit(service, propose(service, turn, plan=with_entry(service, status="bought"), reports=[report(turn, quote="我买了票")]),
           accept_unverified=True)
    return service


def test_a_bought_ticket_changes_its_channel_only_from_a_source(bought):
    _, draft = by_agent(bought, with_entry(bought, where=["Online shop"]), why="网上便宜")
    assert draft["status"] == "rejected" and [check["id"] for check in draft["checks"]["conflicts"]] == ["paid-booking:hall-entry"]
    with pytest.raises(ValueError, match="Commit refused: paid-booking:hall-entry"):
        commit(bought, draft, accept_unverified=True)
    turn = message(bought, "我换到 B 窗口取票了。")
    version = commit(bought, propose(bought, turn, plan=with_entry(bought, where=["B counter"]), reports=[report(
        turn, quote="我换到 B 窗口取票了", field="where", old=["Official counter"], new=["B counter"])]), accept_unverified=True)
    [line] = lines(version, ENTRY)
    assert (line["field"], line["by"], line["why"]) == ("where", "user", "the user said: 我换到 B 窗口取票了")
    assert line["basis"]["event"] == turn["receipt"]["event_id"] and line["basis"]["report"].startswith("R-")
    # The report names one field; another one in the same plan stays refused.
    turn = message(bought, "我改到 C 窗口取票了。")
    plan = with_entry(bought, where=["C counter"], if_missed="No refund")
    draft = propose(bought, turn, plan=plan, reports=[report(turn, quote="我改到 C 窗口取票了", field="where", old=["B counter"], new=["C counter"])])
    assert [check["id"] for check in draft["checks"]["conflicts"]] == ["paid-booking:hall-entry"]


def test_a_bought_ticket_changes_after_the_user_confirms_a_correction(bought):
    turn = message(bought, "agent checks the counter", nature="agent_inference", speaker=None)
    opened = commit(bought, propose(bought, turn, corrections=[dict(
        target=ENTRY, field="where", old=["Official counter"], new=["Online shop"], basis="The desk moved online",
        ask="改到网上取票吗？", source=dict(event=turn["receipt"]["event_id"]))]))["lock_changes"]["corrections"][0]
    answer = message(bought, "对，改到网上取票。")
    version = commit(bought, propose(bought, answer, plan=with_entry(bought, where=["Online shop"]), confirm_corrections=[dict(
        id=opened["id"], answer="confirmed", quote="改到网上取票", source=dict(event=answer["receipt"]["event_id"]))]),
        accept_unverified=True)
    [line] = lines(version, ENTRY)
    assert (line["by"], line["basis"]["correction"]) == ("user", opened["id"]) and "The desk moved online" in line["why"]


def test_a_hard_node_changes_only_from_a_source(service):
    seed(service, fixture())
    plan = service.state("alice")["plan"]
    plan["days"][0]["anchors"][0]["time"] = "23:00"
    _, draft = by_agent(service, plan)
    assert [check["id"] for check in draft["checks"]["conflicts"]] == ["preserved-anchor:last-boat"]
    turn = message(service, "末班船改到 23:00 了。")
    version = commit(service, propose(service, turn, plan=plan, reports=[dict(
        target=BOAT, field="time", old="22:30", new="23:00", quote="末班船改到 23:00", source=dict(event=turn["receipt"]["event_id"]))]),
        accept_unverified=True)
    assert lines(version, BOAT)[0]["by"] == "user"


# --- the change log ---------------------------------------------------------------------------------------------------------

def test_the_first_plan_and_the_users_records_are_logged_as_theirs(service):
    seed(service, fixture())
    [first] = service.state("alice", view="history")["versions"]
    assert first["changelog"] == [dict(target="trip:graph-trip", field=None, change="added", old=None, new=None, by="agent",
                                       basis=dict(event=first["source_event"]), why=None)]
    turn = message(service, "饭后改成顶绳。")
    plan, decision = decided(service, turn, text=OTHER)
    version = commit(service, propose(service, turn, plan=plan, decisions=[decision]), accept_unverified=True)
    assert lines(version, BLOCK)[0]["why"] == "饭后去哪: 岩馆 A" and lines(version, BLOCK)[0]["by"] == "user"


def test_history_and_explain_list_the_changes_by_version(chosen):
    [decision] = active_decisions(chosen)
    _, draft = by_agent(chosen, with_block(chosen, text=OTHER), revisions=[dict(decision=decision["id"], reason="满员")])
    version = commit(chosen, draft, accept_unverified=True)
    history = chosen.state("alice", view="history")["versions"]
    assert [entry["version"] for entry in history] == list(range(1, version["number"] + 1))
    assert history[-1]["changelog"] == version["changelog"] and history[1]["changelog"] == []  # the decision changed no plan field
    [explained] = chosen.explain("alice", version=version["number"])["version_usage"]
    assert explained["changelog"] == version["changelog"]


def test_a_ledger_only_proposal_logs_no_plan_change(chosen):
    turn = message(chosen, "我们都喜欢安静的地方。")
    draft = propose(chosen, turn, items=[dict(kind="preference", key="quiet", quote="都喜欢安静的地方", text="Quiet places", subject=GROUP,
                                             strength="prefer", source=dict(event=turn["receipt"]["event_id"]))])
    assert draft["changelog"] == [] and commit(chosen, draft)["changelog"] == []


@pytest.mark.parametrize("path,expected", [
    ("text", [("block:d1-climb", "text", "block:d1-climb/text")]),
    ("position", [("block:d1-cafe", "position", "where:block:d1-cafe"), ("block:d1-climb", "position", "where:block:d1-climb")]),
])
def test_plan_changes_name_each_field_by_object(path, expected):
    before = fixture()
    after = fixture()
    if path == "text":
        node(after, BLOCK)["text"] = OTHER
    else:
        after["days"][0]["plans"][0]["tracks"][0]["subject"] = ["u1", "u2"]
    assert [(line["target"], line["field"], line["name"]) for line in locks.plan_changes(before, after)] == expected


def test_a_hand_edit_of_a_choice_that_is_not_a_must_is_taken_in_as_the_users(chosen):
    """A plan edited outside the tool is the person's own change: not a must, it is adopted and logged as theirs."""
    plan = with_block(chosen, text=OTHER)
    adopted = chosen.versions.adopt("alice", plan, author="u1")
    assert adopted["adopted"] is True
    assert lines(adopted["version"], BLOCK) == [dict(target=BLOCK, field="text", change="changed", old="饭后抱石（用户已定）", new=OTHER,
                                                     by="user", basis=dict(edit="external", author="u1"), why=None)]
