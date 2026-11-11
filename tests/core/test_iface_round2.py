"""Second chat-interface round: service-filled mechanics and permissive locations."""
from __future__ import annotations

from copy import deepcopy

import pytest


def decision_for(turn, track, block, *, must=False):
    return dict(question="Keep this block", options=["yes"], choice="yes", subject=track["subject"], decided_by="user",
                must=must, source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                locked_paths={f"/blocks/{block['id']}": block})


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



def test_omitted_usages_are_service_filled_but_an_explicit_empty_list_is_not(seeded):
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
    refused = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="empty-usage",
                             days=[day], usages=[])
    assert refused["status"] == "rejected" and any(check["id"].startswith("usage:") for check in refused["checks"]["conflicts"])


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


def test_shape_refusal_collects_all_argument_groups(seeded):
    turn = seeded.turn("alice", "Bad proposal")
    with pytest.raises(ValueError) as caught:
        seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="all-bad",
                       plan={}, changes=dict(items=[dict(kind="fact")]), usages=[dict(item_id="x", status="used", reason={})])
    text = str(caught.value)
    assert "Invalid plan" in text and "Invalid changes" in text and "Invalid usages" in text
    assert "changes.items.0.quote is required" in text and "usages.0.reason" in text
