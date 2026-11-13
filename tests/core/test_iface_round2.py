"""Second chat-interface round: service-filled mechanics and permissive locations."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest


def decision_for(turn, track, block, *, must=False):
    return dict(question="Keep this block", options=["yes"], choice="yes", subject=track["subject"], decided_by="user",
                must=must, source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                locked_paths={f"/blocks/{block['id']}": block})


def test_english_message_gets_english_first_part_and_next(seeded):
    place = seeded.state("alice", "plan")["plan"]["places"][0]
    result = seeded.turn("alice", "What about this place?", scenario="quick",
                         points=[dict(kind="do", said=place["name_local"], place=place["id"])])
    assert result["first_part"]["lines"][0].startswith("From the stored plan first")
    assert "按现在" not in result["first_part"]["text"] and "第二段" not in result["first_part"]["text"]
    assert result["next"].startswith("Send first_part.text")


def test_turn_resolves_people_names_in_speaker_subject_and_record(seeded):
    result = seeded.turn("alice", "Mira is allergic to peanuts", speaker="mira", subject=["Mira"],
                         record=dict(items=[dict(kind="constraint", key="peanut", quote="allergic to peanuts",
                                                 text="No peanuts for Mira", subject=["Mira"], strength="forbid",
                                                 source=dict(event="this", basis="user_explicit"))]))
    event = seeded.store.get("events", "alice", result["receipt"]["event_id"])
    assert event["received"]["speaker"] == "p1"
    item = seeded.store.all("items", "alice")[-1]
    assert item["subject"] == ["p1"]
    with pytest.raises(ValueError, match=r"p1 \(Mira\).*p2 \(Noah\).*p3 \(Lin\)"):
        seeded.turn("alice", "Unknown speaker", speaker="Alex")



def test_omitted_usages_and_an_explicit_empty_list_are_both_service_filled(seeded):
    turn = seeded.turn("alice", "Mira wants a relaxed day", speaker="Mira",
                       record=dict(items=[dict(kind="preference", key="pace", quote="relaxed day", text="Relaxed day",
                                               subject=["Mira"], strength="prefer", source=dict(event="this", basis="user_explicit"))]))
    assert seeded.store.all("items", "alice")[-1]["subject"] == ["p1"]
    day = deepcopy(seeded.state("alice", "day", date="2030-06-10")["json"])
    day["title"] = "Changed"
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="auto-usage",
                           days=[day], changes=dict(why="User requested it"))
    assert draft["status"] == "proposed"
    assert draft["usages"] and all(usage["filled_by"] == "service" for usage in draft["usages"])
    turn = seeded.turn("alice", "Explicit empty usages")
    empty = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="empty-usage",
                           days=[day], usages=[])
    # until 2026-11-13 an explicit [] refused the plan; a condition left out is now filled like an omitted list
    assert empty["status"] == "proposed" and not any(check["id"].startswith("usage:") for check in empty["checks"]["conflicts"])
    assert empty["usages"] and all(usage["filled_by"] == "service" for usage in empty["usages"])


def test_omitted_revisions_are_filled_from_actual_decision_changes(seeded):
    current = seeded.state("alice", "plan")["plan"]
    day = current["days"][0]
    track = day["plans"][0]["tracks"][0]
    block = track["blocks"][0]
    decided = seeded.turn("alice", "I choose this block", speaker="Mira", nature="user_verbatim", native_ref="fixture:flex")
    made = seeded.propose("alice", ctx_id=decided["ctx_id"], base_version=decided["base_version"], idempotency_key="flex",
                          changes=dict(decisions=[decision_for(decided, track, block)]))
    seeded.commit("alice", made["draft_id"], "flex", accept_unverified=True)
    turn = seeded.turn("alice", "Move the block")
    changed = deepcopy(seeded.state("alice", "day", date="2030-06-10")["json"])
    changed["plans"][0]["tracks"][0]["blocks"][0].update(start="23:00", end="23:30")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="auto-revision",
                           days=[changed], changes=dict(why="Agent moved it"))
    assert draft["status"] == "proposed"
    revisions = seeded.store.get("drafts", "alice", draft["draft_id"])["changes"]["revisions"]
    assert len(revisions) == 1 and revisions[0]["filled_by"] == "service"
    assert "no preference precedence" in revisions[0]["reason"]


def test_competing_preferences_hold_plan_changes_until_user_chooses(seeded):
    scope = dict(start="2030-06-12", end="2030-06-12", time_from="09:00", time_to="12:00")
    items = [dict(kind="preference", key=key, quote=text, text=text, subject=["Mira"], strength="prefer", scope=scope,
                  source=dict(event="this", basis="user_explicit"))
             for key, text in (("pace", "easy-going morning"), ("climb", "steep two-hour hike"))]
    result = seeded.turn("alice", "I want an easy-going morning and a steep two-hour hike", speaker="Mira",
                         scenario="plan", record=dict(items=items))
    assert result["recorded"].get("crowding")
    assert "do not change the plan until the user chooses" in result["next"]
    assert "propose(days" not in result["next"]
    for item in items:
        item["source"]["event"] = result["receipt"]["event_id"]
    day = deepcopy(seeded.state("alice", "day", date="2030-06-10")["json"])
    day["title"] = "Agent's choice"
    draft = seeded.propose("alice", ctx_id=result["ctx_id"], base_version=result["base_version"],
                           idempotency_key="options-plan", days=[day], changes=dict(items=items))
    assert draft["status"] == "rejected" and seeded.store.get("drafts", "alice", draft["draft_id"])["options_pending"]
    with pytest.raises(ValueError, match="waiting for the user's choice"):
        seeded.commit("alice", draft["draft_id"], "options-plan", accept_unverified=True)
    ledger = seeded.propose("alice", ctx_id=result["ctx_id"], base_version=result["base_version"],
                            idempotency_key="options-ledger", changes=dict(items=items))
    assert ledger["status"] == "proposed"


def test_query_locations_accept_stored_ids_names_and_coordinate_objects(service, plan):
    for index, place in enumerate(plan["places"]):
        place["coords"] = [59.91 + index / 100, 10.75]
    created = service.turn("alice", "Create")
    draft = service.propose("alice", ctx_id=created["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    place = plan["places"][0]
    for value in (place["id"], place["name"], place["name_local"]):
        result = asyncio.run(service.query("alice", "weather", dict(place=value, date="2030-06-10")))
        assert "synthetic" in result["evidence"]["source"]
    result = asyncio.run(service.query("alice", "weather", dict(place=dict(name="Custom", lat=59.91, lon=10.75), date="2030-06-10")))
    assert "synthetic" in result["evidence"]["source"]
    result = asyncio.run(service.query("alice", "transit", dict(origin=dict(name="Custom", lat=59.91, lon=10.75),
                                                                  destination=place["id"], when="2030-06-10T20:30:00+02:00")))
    assert result["connections"]
    with pytest.raises(ValueError, match="unknown stored place.*oslo-art"):
        asyncio.run(service.query("alice", "weather", dict(place="not-a-place", date="2030-06-10")))


def test_shape_refusal_collects_all_argument_groups(seeded):
    turn = seeded.turn("alice", "Bad proposal")
    with pytest.raises(ValueError) as caught:
        seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="all-bad",
                       plan={}, changes=dict(items=[dict(kind="fact")]), usages=[dict(item_id="x", status="used", reason={})])
    text = str(caught.value)
    assert "Invalid plan" in text and "Invalid changes" in text and "Invalid usages" in text
    assert "changes.items.0.quote is required" in text and "usages.0.reason" in text
