"""B3f (D-36): a protected field of a user lock changes only three ways, each with its source in the tool's answer.

1. The user's new decision. 2. A report of what the user's own message says happened ("I bought the tickets"): the
message has nature user_verbatim or host_restatement and a trip person as speaker, and the quote is in it. 3. A
correction the agent proposes: an open question that changes nothing until the user confirms it in a later message.
Each path has legal cases and refused ones; everything else is refused by the lock, also with accept_unverified. The
judge's counterexamples A1 (an entrance 67 m away) and A2 (channel, refund terms, deadline, status) are included.
Fictional people, places and tickets only."""
from __future__ import annotations

from itertools import count

import pytest

from test_b3e_reference_graph import D1, D2, GROUP, LOCKED, agent, lock, lock_conflicts, node, seed, trip
from tripwright import locks
from tripwright.storage import now

KEYS = count()
ENTRY = "booking:hall-entry"
HALL = "place:gym-a"
NEAR = [47.8006, 13.04]


def fixture() -> dict:
    """The B3e trip, with the hall entry chosen as an on-site purchase at the official counter, refundable, paid by day two."""
    plan = trip()
    entry = next(booking for booking in plan["bookings"] if booking["id"] == "hall-entry")
    entry.update(status="on_site", where=["Official counter"], if_missed="Unused admission is refundable", due=D2)
    return plan


@pytest.fixture
def locked(service):
    seed(service, fixture())
    lock(service, f"/blocks/{LOCKED}")
    return service


def message(service, text, *, nature="user_verbatim", speaker="u1"):
    key = f"m{next(KEYS)}"
    return service.turn("alice", text, key, nature=nature, native_ref=f"fixture:{key}" if nature == "user_verbatim" else None,
                        speaker=speaker)


def propose(service, turn, *, plan=None, **changes):
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, changes=changes or None,
                           idempotency_key=f"p{next(KEYS)}")


def commit(service, draft, **options):
    return service.commit("alice", draft["draft_id"], draft["draft_id"], **options)


def with_entry(service, **fields):
    plan = service.state("alice")["plan"]
    node(plan, ENTRY).update(fields)
    return plan


def with_hall(service, **fields):
    plan = service.state("alice")["plan"]
    node(plan, HALL).update(fields)
    return plan


def report(turn, *, quote, target=ENTRY, field="status", old="on_site", new="bought", **extra):
    return dict(target=target, field=field, old=old, new=new, quote=quote, source=dict(event=turn["receipt"]["event_id"]), **extra)


def correction(turn, *, target=HALL, field="coords", old=None, new=None, **extra):
    return dict(dict(target=target, field=field, old=[47.80, 13.04] if old is None else old, new=NEAR if new is None else new,
                     basis="The map shows the north entrance", ask="Do you mean the north entrance?",
                     source=dict(event=turn["receipt"]["event_id"])), **extra)


def active_decisions(service):
    return [decision for decision in service.store.all("decisions", "alice") if decision["status"] == "active"]


# --- path 1: the user's new decision ----------------------------------------------------------------------------------

def test_path1_the_users_new_decision_takes_a_formerly_exempt_change(locked):
    plan = with_entry(locked, where=["Private reseller only"])
    assert lock_conflicts(agent(locked, plan))[0]["details"]["changed"] == [f"{ENTRY}/where"]
    turn = message(locked, "改从转售处买，我们定了。")
    draft = propose(locked, turn, plan=plan, decisions=[dict(
        question="从哪买", options=["转售处"], choice="转售处", decided_by="user", must=True, subject=GROUP,
        source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
        locked_paths={f"/blocks/{LOCKED}": locks.pointer(plan, f"/blocks/{LOCKED}")})])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []
    commit(locked, draft, accept_unverified=True)
    assert [decision["status"] for decision in locked.store.all("decisions", "alice")] == ["superseded", "active"]


# --- path 2: what the user's own message says happened ----------------------------------------------------------------

@pytest.mark.parametrize("nature", ["user_verbatim", "host_restatement"])
def test_path2_the_users_report_marks_the_ticket_bought(locked, nature):
    turn = message(locked, "我买了票。", nature=nature)
    draft = propose(locked, turn, plan=with_entry(locked, status="bought"), reports=[report(turn, quote="我买了票")])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]
    said = draft["lock_changes"]["reports"][0]
    assert said["target"] == ENTRY and said["field"] == "status" and (said["old"], said["new"]) == ("on_site", "bought")
    assert said["event"] == turn["receipt"]["event_id"] and said["takes_effect"] == "on commit" and said["locks"][0]["path"] == f"/blocks/{LOCKED}"
    version = commit(locked, draft, accept_unverified=True)
    amended = version["lock_changes"]["amended"][0]
    assert amended["via"] == "user_report" and amended["event"] == turn["receipt"]["event_id"] and amended["new"] == "bought"
    [current] = active_decisions(locked)
    assert current["id"] == amended["decision"] and current["amends"]["replaces"] == amended["replaces"]
    assert current["amends"]["via"] == "user_report" and current["placement"][f"/blocks/{LOCKED}"]["references"][ENTRY]["status"] == "bought"
    assert [entry["action"] for entry in locked.ledger.explain("alice")["journal"]][-2:] == ["amend_lock", "user_report"]
    # The lock now holds the reported value: an agent moving it back is refused naming the field.
    assert lock_conflicts(agent(locked, with_entry(locked, status="on_site")))[0]["details"]["changed"] == [f"{ENTRY}/status"]


@pytest.mark.parametrize("nature,speaker,quote,reason", [
    ("agent_inference", "u1", "我买了票", "must come from the user's own message"),
    ("screenshot_extraction", "u1", "我买了票", "must come from the user's own message"),
    ("user_verbatim", None, "我买了票", "speaker of message"),
    ("user_verbatim", "agent", "我买了票", "speaker of message"),
    ("user_verbatim", "u1", "我们已经付款", "Quote is absent"),
])
def test_path2_only_the_users_own_words_can_report(locked, nature, speaker, quote, reason):
    turn = message(locked, "我买了票。", nature=nature, speaker=speaker)
    with pytest.raises(ValueError, match=reason):
        propose(locked, turn, plan=with_entry(locked, status="bought"), reports=[report(turn, quote=quote)])
    # Without a report, the same plan is the agent marking the ticket bought.
    draft = agent(locked, with_entry(locked, status="bought"))
    assert lock_conflicts(draft)[0]["details"]["changed"] == [f"{ENTRY}/status"]


@pytest.mark.parametrize("change,reason", [
    (dict(old="to_buy"), "holds booking:hall-entry/status = \"on_site\", not \"to_buy\"; it is stale"),
    (dict(field="why_now", old=None, new="x"), "is a description field"),
    (dict(field="id", old="hall-entry", new="other"), "names the object"),
    (dict(field="fares", old=[], new=[]), "holds other plan objects"),
    (dict(target="plan:rec", field="name", old="Recommended", new="x"), "target must be 'kind:id'"),
    (dict(target="booking:", field="status"), "target must be 'kind:id'"),
    (dict(field="colour", old=None, new="red"), "Booking has no field 'colour'"),
    (dict(new="paid"), r"\.new must be"),
    (dict(new="on_site"), "new equals old"),
])
def test_path2_a_stale_or_malformed_report_is_refused(locked, change, reason):
    turn = message(locked, "我买了票。")
    with pytest.raises(ValueError, match=reason):
        propose(locked, turn, plan=with_entry(locked, status="bought"), reports=[dict(report(turn, quote="我买了票"), **change)])


def test_path2_an_earlier_user_message_cannot_be_reused_as_a_report(locked):
    """The lock's own message says the tickets are bought on site; an agent's later proposal quoting it as the user's
    report is refused, and so is a user's earlier message cited from a later turn."""
    lock_event = next(event for event in locked.store.all("events", "alice") if "received" in event and "整个块" in event["received"]["text"])
    later = message(locked, "agent marks the ticket bought", nature="agent_inference", speaker=None)
    with pytest.raises(ValueError, match="the message this proposal answers"):
        propose(locked, later, plan=with_entry(locked, status="bought"), reports=[dict(
            target=ENTRY, field="status", old="on_site", new="bought", quote="都定了", source=dict(event=lock_event["id"]))])
    said = message(locked, "我买了票。")
    afterwards = message(locked, "那我们几点出发？")
    with pytest.raises(ValueError, match="the message this proposal answers"):
        propose(locked, afterwards, plan=with_entry(locked, status="bought"), reports=[report(said, quote="我买了票")])


def test_path2_a_report_releases_only_its_own_field(locked):
    turn = message(locked, "我买了票。")
    draft = propose(locked, turn, plan=with_entry(locked, status="bought", where=["Private reseller only"]), reports=[report(turn, quote="我买了票")])
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["details"]["changed"] == [f"{ENTRY}/where"]


def test_path2_a_report_without_the_plan_marks_the_unchanged_plan_for_replanning(locked):
    turn = message(locked, "我买了票。")
    draft = propose(locked, turn, reports=[report(turn, quote="我买了票")])
    assert draft["status"] == "proposed" and [check["details"]["changed"] for check in draft["warnings"]] == [[f"{ENTRY}/status"]]
    version = commit(locked, draft)
    assert version["needs_replan"] and version["lock_changes"]["amended"][0]["field"] == "status"
    draft = agent(locked, with_entry(locked, status="bought"))
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []


def test_reports_and_corrections_go_through_propose_not_turn(locked):
    # S5: the turn is not refused for its record; the same words come back in recorded.not_recorded
    turned = locked.turn("alice", "我买了票。", "record-report", nature="user_verbatim", native_ref="fixture:rr", speaker="u1",
                         record=dict(reports=[dict(target=ENTRY, field="status", old="on_site", new="bought", quote="我买了票",
                                                   source=dict(event="this"))]))
    assert "send reports with propose and commit" in turned["recorded"]["not_recorded"][0]["reason"]
    assert not turned["recorded"]["changed"]


def test_two_reports_on_one_field_apply_in_order(locked):
    turn = message(locked, "我在现场买了票，又改签成了预订。")
    draft = propose(locked, turn, plan=with_entry(locked, status="booked"), reports=[
        report(turn, quote="我在现场买了票"), report(turn, quote="又改签成了预订", old="bought", new="booked")])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []
    commit(locked, draft, accept_unverified=True)
    assert [entry["new"] for entry in (decision["amends"] for decision in locked.store.all("decisions", "alice") if decision.get("amends"))] == ["bought", "booked"]


# --- path 3: a correction the user confirms in a later message ---------------------------------------------------------

def propose_correction(service, **extra):
    turn = message(service, "agent checks the entrance on the map", nature="agent_inference", speaker=None)
    draft = propose(service, turn, corrections=[correction(turn, **extra)])
    return turn, draft


def test_path3_a_correction_waits_for_the_users_confirmation_and_then_takes_effect(locked):
    turn, draft = propose_correction(locked)
    pending = draft["lock_changes"]["corrections"][0]
    assert draft["status"] == "proposed" and pending["locks"][0]["path"] == f"/blocks/{LOCKED}"
    assert "confirm" in pending["takes_effect"] and (pending["old"], pending["new"]) == ([47.80, 13.04], NEAR)
    version = commit(locked, draft)
    [opened] = version["lock_changes"]["corrections"]
    assert opened["status"] == "open" and "place:gym-a/coords" in opened["question"] and "north entrance" in opened["question"]
    # Before the user answers: nothing changed, a plan with the new value is refused, also with accept_unverified.
    refused = agent(locked, with_hall(locked, coords=NEAR))
    assert lock_conflicts(refused)[0]["details"]["changed"] == [f"{HALL}/coords"]
    with pytest.raises(ValueError, match="place:gym-a/coords changed"):
        commit(locked, refused, accept_unverified=True)
    # The question is in every package for the locked people until answered.
    answer = message(locked, "对，用北门那个入口。", speaker="u2")
    assert opened["id"] in answer["pack"]["meta"]["required"]["questions"]["ids"]
    draft = propose(locked, answer, plan=with_hall(locked, coords=NEAR), confirm_corrections=[dict(
        id=opened["id"], answer="confirmed", quote="用北门那个入口", source=dict(event=answer["receipt"]["event_id"]))])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]
    assert draft["lock_changes"]["confirmations"][0]["locks"][0]["path"] == f"/blocks/{LOCKED}"
    version = commit(locked, draft, accept_unverified=True)
    amended = version["lock_changes"]["amended"][0]
    assert amended["via"] == "confirmed_correction" and amended["change"] == opened["id"] and amended["event"] == answer["receipt"]["event_id"]
    question = locked.store.get("undecided", "alice", opened["id"])
    assert question["status"] == "answered" and question["answer"] == "confirmed"
    assert opened["id"] not in message(locked, "下一步？")["pack"]["meta"]["required"]["questions"]["ids"]


def test_path3_the_proposing_message_or_the_agent_cannot_confirm(locked):
    # A correction proposed in a user's message still needs a later message to answer it: the same message, received
    # again under its request ID, opens a new context but cannot confirm.
    turn = locked.turn("alice", "地图上入口在北边。", "same-message", nature="host_restatement", speaker="u1")
    commit(locked, propose(locked, turn, corrections=[correction(turn)]))
    [question] = [entry for entry in locked.store.all("undecided", "alice") if entry.get("kind") == "correction"]
    same = locked.turn("alice", "地图上入口在北边。", "same-message", nature="host_restatement", speaker="u1")
    assert same["receipt"]["event_id"] == turn["receipt"]["event_id"]
    with pytest.raises(ValueError, match="later message"):
        propose(locked, same, plan=with_hall(locked, coords=NEAR), confirm_corrections=[dict(
            id=question["id"], answer="confirmed", quote="入口在北边", source=dict(event=turn["receipt"]["event_id"]))])
    agent_turn = message(locked, "确认改到北门", nature="agent_inference", speaker=None)
    with pytest.raises(ValueError, match="must come from the user's own message"):
        propose(locked, agent_turn, plan=with_hall(locked, coords=NEAR), confirm_corrections=[dict(
            id=question["id"], answer="confirmed", quote="确认改到北门", source=dict(event=agent_turn["receipt"]["event_id"]))])
    assert locked.store.get("undecided", "alice", question["id"])["status"] == "open"


def test_path3_a_rejected_correction_changes_nothing_and_closes(locked):
    _, draft = propose_correction(locked)
    opened = commit(locked, draft)["lock_changes"]["corrections"][0]
    answer = message(locked, "不对，还是原来那个入口。")
    draft = propose(locked, answer, confirm_corrections=[dict(id=opened["id"], answer="rejected", quote="还是原来那个入口",
                                                              source=dict(event=answer["receipt"]["event_id"]))])
    assert draft["lock_changes"]["confirmations"][0]["takes_effect"] == "nothing changes"
    commit(locked, draft)
    assert locked.store.get("undecided", "alice", opened["id"])["answer"] == "rejected"
    assert lock_conflicts(agent(locked, with_hall(locked, coords=NEAR)))[0]["details"]["changed"] == [f"{HALL}/coords"]
    again = message(locked, "其实用北门吧，确认。")
    with pytest.raises(ValueError, match="already answered"):
        propose(locked, again, confirm_corrections=[dict(id=opened["id"], answer="confirmed", quote="确认",
                                                         source=dict(event=again["receipt"]["event_id"]))])


def evidence(service, evidence_id, **fields):
    record = dict(id=evidence_id, source="fixture:first-hand map", fetched_at=now(), valid_until="2100-01-01T00:00:00+00:00",
                  stale=False, kind="geocode", date=D1, facts=[], value={"lat": NEAR[0], "lon": NEAR[1]})
    service.store.put("evidence", "alice", dict(record, **fields), insert=True)
    return evidence_id


@pytest.mark.parametrize("case,expected", [
    ("real evidence for an external fact", None),
    ("synthetic evidence", "stale, synthetic or has unknown validity"),
    ("stale evidence", "stale, synthetic or has unknown validity"),
    ("evidence of unknown validity", "stale, synthetic or has unknown validity"),
    ("unknown evidence", "Unknown|missing|not found"),
    ("evidence for the user's choice", "is the user's choice, not an external fact"),
])
def test_path3_evidence_backs_only_external_facts_and_only_real_sources(locked, case, expected):
    ids = {"real evidence for an external fact": [evidence(locked, "EV-b3f-real")],
           "synthetic evidence": [evidence(locked, "EV-b3f-demo", source="demo:synthetic — fictional data", synthetic=True)],
           "stale evidence": [evidence(locked, "EV-b3f-stale", stale=True)],
           "evidence of unknown validity": [evidence(locked, "EV-b3f-open", valid_until=None)],
           "unknown evidence": ["EV-b3f-nowhere"],
           "evidence for the user's choice": [evidence(locked, "EV-b3f-shop")]}[case]
    extra = dict(target=ENTRY, field="where", old=["Official counter"], new=["Online shop"]) if case == "evidence for the user's choice" else {}
    if expected is None:
        _, draft = propose_correction(locked, evidence_ids=ids, **extra)
        commit(locked, draft)
        [question] = [entry for entry in locked.store.all("undecided", "alice") if entry.get("kind") == "correction"]
        assert "EV-b3f-real" in question["question"] and question["status"] == "open"
        # Evidence does not replace the user's answer.
        assert lock_conflicts(agent(locked, with_hall(locked, coords=NEAR)))[0]["details"]["changed"] == [f"{HALL}/coords"]
    else:
        with pytest.raises(ValueError, match=expected):
            propose_correction(locked, evidence_ids=ids, **extra)


def test_path3_a_correction_no_lock_holds_is_refused(locked):
    # B3k (D-53): a correction also reaches a bought or booked booking and a hard node; this place is neither.
    with pytest.raises(ValueError, match="no must lock, bought or booked booking or hard node holds place:hill/coords"):
        propose_correction(locked, target="place:hill", old=[47.90, 13.20], new=[47.91, 13.20])


def test_path3_a_confirmation_after_the_user_decided_otherwise_is_stale(locked):
    _, draft = propose_correction(locked, target=ENTRY, field="where", old=["Official counter"], new=["Online shop"])
    opened = commit(locked, draft)["lock_changes"]["corrections"][0]
    plan = with_entry(locked, where=["Hotel desk"])
    turn = message(locked, "改在酒店前台买，定了。")
    commit(locked, propose(locked, turn, plan=plan, decisions=[dict(
        question="从哪买", options=["酒店前台"], choice="酒店前台", decided_by="user", must=True, subject=GROUP,
        source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
        locked_paths={f"/blocks/{LOCKED}": locks.pointer(plan, f"/blocks/{LOCKED}")})]), accept_unverified=True)
    answer = message(locked, "网店也行，确认。")
    with pytest.raises(ValueError, match=r"holds booking:hall-entry/where = \[\"Hotel desk\"\], not \[\"Official counter\"\]; it is stale"):
        propose(locked, answer, plan=with_entry(locked, where=["Online shop"]), confirm_corrections=[dict(
            id=opened["id"], answer="confirmed", quote="网店也行", source=dict(event=answer["receipt"]["event_id"]))])


# --- the judge's counterexamples A1 and A2 -----------------------------------------------------------------------------

def test_judge_five_a1_an_entrance_67_metres_away_is_refused_until_the_user_says_so(locked):
    assert round(locks_distance([47.80, 13.04], NEAR)) == 67
    draft = agent(locked, with_hall(locked, coords=NEAR))
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["details"]["changed"] == [f"{HALL}/coords"]
    with pytest.raises(ValueError, match="place:gym-a/coords changed"):
        commit(locked, draft, accept_unverified=True)
    turn = message(locked, "改到北纬 47.8006 那个北门入口。")
    draft = propose(locked, turn, plan=with_hall(locked, coords=NEAR), reports=[report(
        turn, quote="改到北纬 47.8006 那个北门入口", target=HALL, field="coords", old=[47.80, 13.04], new=NEAR)])
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []


@pytest.mark.parametrize("field,value", [("if_missed", "No refund; buy another ticket"), ("where", ["Private reseller only"]),
                                         ("due", D1), ("status", "bought")])
def test_judge_five_a2_terms_of_the_chosen_ticket_are_refused(locked, field, value):
    draft = agent(locked, with_entry(locked, **{field: value}))
    assert draft["status"] == "rejected" and lock_conflicts(draft)[0]["details"]["changed"] == [f"{ENTRY}/{field}"]
    with pytest.raises(ValueError, match=f"{ENTRY}/{field} changed"):
        commit(locked, draft, accept_unverified=True)
    assert node(locked.state("alice")["plan"], ENTRY)[field] == fixture()["bookings"][0][field]


def locks_distance(first, second) -> float:
    from math import asin, cos, radians, sin, sqrt
    (latitude, longitude), (other_latitude, other_longitude) = first, second
    haversine = sin(radians(other_latitude - latitude) / 2) ** 2 + cos(radians(latitude)) * cos(radians(other_latitude)) * sin(radians(other_longitude - longitude) / 2) ** 2
    return 6_371_000 * 2 * asin(min(1, sqrt(haversine)))
