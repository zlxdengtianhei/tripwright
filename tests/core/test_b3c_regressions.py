from copy import deepcopy

import pytest
from pydantic import ValidationError

from tripwright.storage import now
from tripwright.trip import Trip
from core_helpers import decide, package, usage_of


def propose(service, *, plan=None, changes=None, usages=None, key="change", text="Update", explicit=False):
    turn = service.turn("alice", text, f"turn-{key}", nature="user_verbatim" if explicit else "host_restatement",
                        native_ref=f"fixture:{key}" if explicit else None)
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                           changes=changes, usages=usages, idempotency_key=key)


def blocks(plan, day=0, track=0):
    return plan["days"][day]["plans"][0]["tracks"][track]["blocks"]


def with_second_day(plan):
    second = deepcopy(plan["days"][0])
    second.update(date="2030-06-11", title="Second day")
    second["plans"][0]["tracks"][0]["blocks"] = [dict(id="d2-walk", start="10:00", end="11:00", kind="flex", place="oslo-park")]
    plan["days"].append(second)
    return plan


@pytest.fixture
def locked(seeded):
    """The group's after-dinner café block on 2030-06-10, locked whole by a user decision, on a two-day plan."""
    turn = seeded.turn("alice", "Add day two", "day-two")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                           plan=with_second_day(seeded.state("alice")["plan"]), idempotency_key="day-two")
    seeded.commit("alice", draft["draft_id"], "day-two", accept_unverified=True)
    block = blocks(seeded.state("alice")["plan"])[2]
    decision = dict(question="After dinner?", options=["Café", "Park"], choice="Café", decided_by="user", must=True,
                    subject=["p1", "p2", "p3"], locked_paths={f"/blocks/{block['id']}": block})
    draft = decide(seeded, decision, "lock")
    seeded.commit("alice", draft["draft_id"], "lock")
    return seeded, block["id"]


def lock_check(draft):
    return next(check for check in draft["checks"]["checks"] if check["id"].startswith("locked:"))


def test_s6_lock_records_the_blocks_date_plan_and_people(locked):
    service, block_id = locked
    decision = service.ledger.current("alice")["decisions"][0]
    # B3d: the plan is identified by its stable ID and position instead of its display name, and the snapshot also
    # carries the people's names, the referenced place's identity and the timezone.
    recorded = decision["placement"][f"/blocks/{block_id}"]
    assert list(decision["placement"]) == [f"/blocks/{block_id}"]
    assert {key: recorded[key] for key in ("date", "plan", "rank", "subject", "timezone")} == dict(
        date="2030-06-10", plan="P1", rank=0, subject=["p1", "p2", "p3"], timezone="Europe/Oslo")
    assert set(recorded["references"]) == {"person:p1", "person:p2", "person:p3", "place:oslo-cafe"}


def test_s6_moving_a_locked_block_to_another_day_is_refused(locked):
    service, block_id = locked
    plan = service.state("alice")["plan"]
    blocks(plan, day=1).append(blocks(plan).pop(2))
    draft = propose(service, plan=plan, key="move-day")
    assert draft["status"] == "rejected"
    assert lock_check(draft)["status"] == "conflict" and lock_check(draft)["details"]["changed"] == ["date"]
    with pytest.raises(ValueError, match=r"locked:.*\(date changed\)"):
        service.commit("alice", draft["draft_id"], "move-day", accept_unverified=True)


def test_s6_moving_a_locked_block_into_a_track_with_fewer_people_is_refused(locked):
    service, block_id = locked
    plan = service.state("alice")["plan"]
    moved = blocks(plan).pop(2)
    plan["days"][0]["plans"][0]["tracks"].append(dict(subject=["p1"], blocks=[moved]))
    plan["days"][0]["plans"][0]["tracks"][0]["subject"] = ["p2", "p3"]
    draft = propose(service, plan=plan, key="move-person")
    assert draft["status"] == "rejected"
    assert lock_check(draft)["details"]["changed"] == ["subject"]


def test_s6_field_lock_keeps_placement_but_allows_unlocked_fields(seeded):
    block = blocks(seeded.state("alice")["plan"])[2]
    decision = dict(question="After dinner?", options=["Café", "Park"], choice="Café", decided_by="user", must=True, subject=["p1", "p2", "p3"],
                    locked_paths={f"/blocks/{block['id']}/place": block["place"]})
    seeded.commit("alice", decide(seeded, decision, "field-lock")["draft_id"], "field-lock")
    plan = seeded.state("alice")["plan"]
    blocks(plan)[2].update(start="18:15", end="19:15", text="Café, a little later")
    assert propose(seeded, plan=plan, key="later")["status"] == "proposed"
    plan = with_second_day(seeded.state("alice")["plan"])
    blocks(plan, day=1).append(blocks(plan).pop(2))
    assert propose(seeded, plan=plan, key="field-move")["status"] == "rejected"


def test_s6_legacy_lock_without_placement_is_backfilled_from_the_committed_plan(locked):
    service, block_id = locked
    stored = service.store.all("decisions", "alice")[0]
    stored.pop("placement")
    service.store.put("decisions", "alice", stored)
    plan = service.state("alice")["plan"]
    blocks(plan, day=1).append(blocks(plan).pop(2))
    assert propose(service, plan=plan, key="legacy-move")["status"] == "rejected"


def test_s6_users_own_new_decision_can_move_the_locked_block(locked, source):
    service, block_id = locked
    plan = service.state("alice")["plan"]
    moved = blocks(plan).pop(2)
    blocks(plan, day=1).append(moved)
    turn = service.turn("alice", "I changed my mind: café on day two.", "mind", nature="user_verbatim", native_ref="fixture:mind",
                        speaker="p1")
    decision = dict(question="After dinner?", options=["Café"], choice="Café on day two", decided_by="user", must=True, subject=["p1", "p2", "p3"],
                    source={"event": turn["receipt"]["event_id"], "basis": "user_explicit"}, locked_paths={f"/blocks/{block_id}": moved})
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                            changes={"decisions": [decision]}, idempotency_key="mind")
    assert draft["status"] == "proposed"
    service.commit("alice", draft["draft_id"], "mind", accept_unverified=True)
    assert [decision["status"] for decision in service.store.all("decisions", "alice")] == ["superseded", "active"]
    assert service.ledger.current("alice")["decisions"][0]["placement"][f"/blocks/{block_id}"]["date"] == "2030-06-11"


def forbid_museum(seeded):
    receipt = seeded.events.receive("alice", "Do not visit museums.", "forbid")
    item = dict(kind="constraint", key="museums", quote="Do not visit museums.", text="Do not visit museums.", subject=["p1"],
                strength="forbid", source={"event": receipt["event_id"]})
    draft = propose(seeded, changes={"items": [item]}, key="forbid")
    seeded.commit("alice", draft["draft_id"], "forbid")
    return seeded.ledger.current("alice")["items"][0]


def test_s1_forbidden_activity_labelled_not_applicable_is_a_conflict(seeded):
    forbidden = forbid_museum(seeded)
    assert seeded.state("alice")["needs_replan"]
    plan = seeded.state("alice")["plan"]
    assert seeded.state("alice")["plan"]["places"][0]["kind"] == "museum" and blocks(plan)[0]["place"] == "oslo-art"
    usages = [dict(item_id=forbidden["id"], status="not_applicable", reason="Host says it does not apply")]
    draft = propose(seeded, plan=plan, usages=usages, key="label")
    assert draft["status"] == "rejected"
    conflicts = {check["id"] for check in draft["checks"]["conflicts"]}
    assert f"item:{forbidden['id']}:2030-06-10:Recommended" in conflicts and f"usage:{forbidden['id']}" in conflicts
    assert usage_of(draft, forbidden["id"])["status"] == "invalid"
    with pytest.raises(ValueError, match="Commit refused"):
        seeded.commit("alice", draft["draft_id"], "label", accept_unverified=True)
    blocks(plan)[0].update(kind="flex", place="oslo-park")
    respected = propose(seeded, plan=plan, usages=usages, key="respected")
    assert respected["status"] == "proposed"
    assert next(check for check in respected["checks"]["checks"] if check["id"].startswith(f"item:{forbidden['id']}:"))["status"] == "passed"


def test_s1_required_activity_cannot_be_dropped_by_a_label(seeded):
    receipt = seeded.events.receive("alice", "We must see a park.", "must-park")
    item = dict(kind="constraint", key="park", quote="We must see a park.", text="We must see a park.", subject=["*"],
                strength="must", source={"event": receipt["event_id"]}, scope={"start": "2030-06-10", "end": "2030-06-10"})
    seeded.commit("alice", propose(seeded, changes={"items": [item]}, key="must")["draft_id"], "must")
    required = seeded.ledger.current("alice")["items"][0]
    plan = seeded.state("alice")["plan"]
    usages = [dict(item_id=required["id"], status="not_applicable", reason="No time")]
    draft = propose(seeded, plan=plan, usages=usages, key="dropped")
    assert draft["status"] == "rejected"
    assert any(check["id"] == f"item:{required['id']}:activity" for check in draft["checks"]["conflicts"])
    blocks(plan)[2].update(place=None, text="Maybe a park walk, place to be decided")
    unclear = propose(seeded, plan=plan, usages=usages, key="unclear")
    assert next(check for check in unclear["checks"]["checks"] if check["id"] == f"item:{required['id']}:activity")["status"] == "unverified"
    blocks(plan)[2].update(place="oslo-park", text=None)
    planned = propose(seeded, plan=plan, usages=[dict(item_id=required["id"], status="used", block_ids=[blocks(plan)[2]["id"]])], key="planned")
    assert planned["status"] == "proposed"
    assert usage_of(planned, required["id"])["verification"] == "server_checked"


def solo_fact(event):
    return dict(key="companions", value=[], text="Today I am alone", subject=["p1"], scope={"start": "2030-06-10", "end": "2030-06-10"},
                true_from=now(), invalidated_by="Day ends", source={"event": event})


def test_s3_fact_and_preference_in_one_ledger_only_proposal_are_both_recorded(seeded):
    receipt = seeded.events.receive("alice", "Today I am alone and I want to see art.", "mixed")
    preference = dict(kind="preference", key="art", quote="I want to see art.", text="I want to see art.", subject=["p1"],
                      strength="prefer", source={"event": receipt["event_id"]}, scope={"start": "2030-06-10", "end": "2030-06-10"})
    draft = propose(seeded, changes={"facts": [solo_fact(receipt["event_id"])], "items": [preference]}, key="mixed")
    assert draft["status"] == "proposed" and draft["warnings"] and "needs_replan" in draft["notice"]
    version = seeded.commit("alice", draft["draft_id"], "mixed")
    assert version["needs_replan"] and version["plan"] == seeded.store.all("versions", "alice")[0]["plan"]
    assert [fact["key"] for fact in seeded.ledger.current("alice")["facts"]] == ["companions"]
    assert [item["key"] for item in seeded.ledger.current("alice")["items"]] == ["art"]


def test_s3_invalid_other_part_does_not_hold_back_the_facts(seeded):
    receipt = seeded.events.receive("alice", "Today I am alone.", "partly-invalid")
    preference = dict(kind="preference", key="art", quote="A quote the user never wrote", text="Art", subject=["p1"],
                      strength="prefer", source={"event": receipt["event_id"]})
    draft = propose(seeded, changes={"facts": [solo_fact(receipt["event_id"])], "items": [preference]}, key="partly-invalid")
    assert draft["status"] == "proposed"
    assert draft["not_recorded"] == [dict(part="items", records=1, reason="Quote is absent from the received message")]
    assert "not recorded: items" in draft["notice"]
    seeded.commit("alice", draft["draft_id"], "partly-invalid")
    assert len(seeded.ledger.current("alice")["facts"]) == 1 and seeded.ledger.current("alice")["items"] == []


def test_s3_ledger_only_proposal_rejects_usages_with_a_reason(seeded):
    receipt = seeded.events.receive("alice", "Today I am alone.", "usages")
    with pytest.raises(ValueError, match="omit usages"):
        propose(seeded, changes={"facts": [solo_fact(receipt["event_id"])]}, usages=[dict(item_id="x", status="used")], key="usages")


EVENING = {"start": "2030-06-10", "end": "2030-06-10", "time_from": "19:00", "time_to": "22:00"}


def evening_items(seeded, text, first, second, **declared):
    receipt = seeded.events.receive("alice", text, "evening")
    source = {"event": receipt["event_id"]}
    common = dict(subject=["p1", "p2", "p3"], strength="prefer", source=source, scope=EVENING)
    return [dict(common, kind="preference", key=first[0], quote=first[1], text=first[1]),
            dict(common, kind=second[2], key=second[0], quote=second[1], text=second[1], **declared)]


def test_s4_host_declared_exclusive_items_of_different_kinds_become_pending(seeded):
    items = evening_items(seeded, "晚饭后去渔人堡。晚饭后一定去抱石。", ("fort", "晚饭后去渔人堡"), ("climb", "晚饭后一定去抱石", "constraint"),
                          exclusive_with=["fort"])
    items[1]["strength"] = "must"
    draft = propose(seeded, changes={"items": items})
    seeded.commit("alice", draft["draft_id"], "declared")
    assert {item["status"] for item in seeded.store.all("items", "alice")} == {"pending"}
    questions = seeded.store.all("undecided", "alice")
    assert len(questions) == 1 and questions[0]["options"] == ["晚饭后去渔人堡", "晚饭后一定去抱石"] and questions[0]["default"]


def test_s4_undeclared_compatible_items_stay_active_with_a_crowding_notice(seeded):
    items = evening_items(seeded, "晚饭后去渔人堡，晚饭后吃冰淇淋。", ("fort", "晚饭后去渔人堡"), ("dessert", "晚饭后吃冰淇淋", "preference"))
    draft = propose(seeded, changes={"items": items})
    assert draft["checks"]["undecided"] == [] and [notice["texts"] for notice in draft["crowding"]] == [["晚饭后去渔人堡", "晚饭后吃冰淇淋"]]
    seeded.commit("alice", draft["draft_id"], "compatible")
    stored = seeded.store.all("items", "alice")
    assert {item["status"] for item in stored} == {"active"} and seeded.store.all("undecided", "alice") == []
    assert stored[0]["relations"]["competes_with"] == [stored[1]["id"]]
    bundle = package(seeded, seeded.turn("alice", "Plan the evening", "evening-plan", scope=EVENING))
    assert {item["key"] for item in bundle["items"]} == {"fort", "dessert"}


def test_x1_unknown_timezone_is_a_readable_validation_error(plan):
    with pytest.raises(ValidationError, match="Unknown IANA timezone: 'Europe/Viena'"):
        Trip.model_validate(dict(plan, timezone="Europe/Viena"))


def test_a_quote_that_differs_only_in_punctuation_width_says_how_to_fix_it(seeded):
    """A transcribed screenshot (2026-11-12 real-model runs): the message has 绝! and the quote re-typed it as 绝！."""
    receipt = seeded.events.receive("alice", "Fiskebar Nord 鱼汤真的绝!周一闭店。", "width")
    preference = dict(kind="preference", key="soup", quote="Fiskebar Nord 鱼汤真的绝！周一闭店。", text="Fish soup", subject=["p1"],
                      strength="prefer", source={"event": receipt["event_id"]})
    with pytest.raises(ValueError, match="^Quote is absent from the received message; .*character for character"):
        propose(seeded, changes={"items": [preference]}, key="width")


def test_an_unrelated_quote_gets_no_width_hint():
    from tripwright.ledger import quote_hint

    assert quote_hint("A quote the user never wrote", "Today I am alone.") == ""
    assert quote_hint("绝！", "绝!") and quote_hint("Ａ1", "A1")


def test_the_record_item_schema_says_subject_is_people_and_the_quote_is_exact():
    """Real-model runs put a café's name in subject and re-typed punctuation in quotes."""
    from tripwright.adapters.mcp import ITEM_SCHEMA

    assert "never a place" in ITEM_SCHEMA["properties"]["subject"]["description"]
    assert "character for character" in ITEM_SCHEMA["properties"]["quote"]["description"]


def test_a_proposal_that_names_this_as_its_source_event_says_to_use_the_turns_event_id(seeded):
    """Real-model runs (2026-11-12) sent source.event "this", which works only in turn's record, to propose."""
    preference = dict(kind="preference", key="soup", quote="Fish soup", text="Fish soup", subject=["p1"],
                      strength="prefer", source={"event": "this"})
    with pytest.raises(ValueError, match="^Unknown message 'this'.*event_id.*only in turn's record"):
        propose(seeded, changes={"items": [preference]}, key="this")
