"""A7 return-for-fix: a lock recorded before a field existed must still hold a commit that leaves the locked objects
alone. The first upgrade to show it was Place.opening_hours: a lock snapshot from before A7 has no such field, the
new draft has its default `[]`, and every commit of a repository with a lock reaching a place was refused. The
comparator reads a field a record lacks as its default; a real change of a locked place's hours is still refused, and
writing hours there goes the correction's way. Swept over every field of every model a lock records, and of the locked
block itself. Fictional people and places only."""
from __future__ import annotations

import asyncio

import pytest

from test_b3e_reference_graph import LOCKED, agent, lock, lock_conflicts, say, seed, trip
from tripwright import locks
from tripwright.locks import field_type, normalized
from tripwright.trip import Block, Trip, trip_models

PATH = f"/blocks/{LOCKED}"
HALL = "place:gym-a"


def default(model, name):
    field = model.model_fields[name]
    return normalized(field.get_default(call_default_factory=True), field_type(field))


def at_default(record: dict, model, name) -> bool:
    """A field an upgrade could have added to this record: one whose value is the field's default, as old data reads."""
    return not model.model_fields[name].is_required() and record.get(name) == default(model, name)


# The objects the lock on the fictional trip records (fares also as lines of their bookings), and the fields of each that
# hold their default: each could have come with a later upgrade (old data cannot hold another value for a field it did
# not have). Description fields are not in a snapshot (`kept` leaves them out), so they cannot hold a lock.
DOCUMENT = Trip.model_validate(trip()).model_dump(mode="json")
SNAPSHOT = [(key.split(":", 1)[0], record) for key, record in locks.snapshot(DOCUMENT, PATH)["references"].items() if record is not None]
SNAPSHOT += [("fare", fare) for kind, record in list(SNAPSHOT) if kind == "booking" for fare in record.get("fares") or []]
REFERENCED = sorted({(kind, name) for kind, record in SNAPSHOT for name in locks.MODELS[kind].model_fields
                     if name in record and at_default(record, locks.MODELS[kind], name)})
THE_BLOCK = locks.pointer(DOCUMENT, PATH)
IN_THE_BLOCK = sorted(name for name in Block.model_fields if at_default(THE_BLOCK, Block, name) and not locks.described("Block", name))


def decision(service) -> dict:
    return next(entry for entry in service.store.all("decisions", "alice") if entry["status"] == "active" and PATH in entry["locked_paths"])


def before_upgrade(service, kind: str | None, names: set[str], *, in_value: set[str] = frozenset()):
    """Rewrite the stored lock, and the committed plan, as they were before these fields existed: the snapshot's
    recorded objects of `kind` (and the locked block, for `in_value`) do not have them."""
    stored = decision(service)
    dropped = 0
    recorded = [record for key, record in stored["placement"][PATH]["references"].items() if record is not None and key.split(":", 1)[0] == kind]
    if kind == "fare":  # a fare is also recorded as a line of its booking
        recorded += [fare for key, record in stored["placement"][PATH]["references"].items() if record is not None and key.startswith("booking:")
                     for fare in record.get("fares") or []]
    for record in recorded:
        for name in names:
            if name in record and at_default(record, locks.MODELS[kind], name):
                record.pop(name)
                dropped += 1
    for name in in_value:
        if at_default(stored["locked_paths"][PATH], Block, name):
            stored["locked_paths"][PATH].pop(name)
            dropped += 1
    version = service.store.all("versions", "alice")[-1]
    lists = dict(place=version["plan"]["places"], booking=version["plan"]["bookings"],
                 fare=[fare for booking in version["plan"]["bookings"] for fare in booking.get("fares", [])], person=version["plan"]["people"])
    for record in lists.get(kind, []):
        for name in names:
            if name in record and at_default(record, locks.MODELS[kind], name):
                record.pop(name)
    with service.store.transaction():
        service.store.put("decisions", "alice", stored)
        service.store.put("versions", "alice", version)
    assert dropped, "nothing to rewrite: the case would not test an upgrade"


def locked_trip(service):
    lock(seed(service), PATH)
    return service


def hall(plan) -> dict:
    return next(place for place in plan["places"] if place["id"] == HALL.split(":")[1])


def test_a_lock_from_before_opening_hours_holds_a_commit_that_leaves_the_place_alone(service):
    before_upgrade(locked_trip(service), "place", {"opening_hours", "hours_source"})
    plan = service.state("alice")["plan"]
    assert "opening_hours" not in hall(plan)  # the committed plan as an old repository stores it
    draft = agent(service, plan)
    assert lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


def test_the_same_lock_still_refuses_a_real_change_of_the_locked_places_hours(service):
    before_upgrade(locked_trip(service), "place", {"opening_hours", "hours_source"})
    plan = service.state("alice")["plan"]
    hall(plan)["opening_hours"] = ["Mo-Su 06:00-22:00"]
    draft = agent(service, plan)
    assert [check["details"]["changed"] for check in lock_conflicts(draft)] == [[f"{HALL}/opening_hours"]]
    with pytest.raises(ValueError, match=r"Commit refused: .*locked:"):
        service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
    # a description field of the same place, hours_source, changes freely
    plan = service.state("alice")["plan"]
    hall(plan)["hours_source"] = dict(url="https://hall.example.org/hours")
    assert lock_conflicts(agent(service, plan)) == []


def test_writing_hours_on_the_locked_place_goes_the_corrections_way(service):
    before_upgrade(locked_trip(service), "place", {"opening_hours", "hours_source"})
    page = asyncio.run(service.query("alice", "web_evidence", dict(url="https://hall.example.org/hours", quote="Open daily 06:00-22:00",
                                                                    claim="The hall is open 06:00–22:00", type="opening_hours")))
    hours = ["Mo-Su 06:00-22:00"]
    draft = say(service, "The hall's page says 06:00 to 22:00.", nature="agent_inference", changes=lambda event: dict(corrections=[dict(
        target=HALL, field="opening_hours", old=[], new=hours, basis="The hall's own page", ask="Store 06:00–22:00 as the hall's hours?",
        evidence_ids=[page["evidence"]["id"]], source=dict(event=event, basis="agent_inference"))]))
    assert draft["status"] == "proposed" and draft["lock_changes"], draft
    opened = service.commit("alice", draft["draft_id"], draft["draft_id"])["lock_changes"]["corrections"][0]
    plan = service.state("alice")["plan"]
    assert hall(plan).get("opening_hours", []) == []  # nothing changes until the user confirms
    hall(plan).update(opening_hours=hours, hours_source=dict(url="https://hall.example.org/hours", evidence_id=page["evidence"]["id"]))
    answer = service.turn("alice", "对，按官网的 6 点到 22 点。", "yes", nature="user_verbatim", native_ref="fixture:yes", speaker="u1")
    confirmed = service.propose("alice", ctx_id=answer["ctx_id"], base_version=answer["base_version"], plan=plan, idempotency_key="yes",
                                changes=dict(confirm_corrections=[dict(id=opened["id"], answer="confirmed", quote="按官网的 6 点到 22 点",
                                                                       source=dict(event=answer["receipt"]["event_id"]))]))
    assert lock_conflicts(confirmed) == [], [check["details"] for check in lock_conflicts(confirmed)]
    version = service.commit("alice", confirmed["draft_id"], "yes", accept_unverified=True)
    assert version["lock_changes"]["amended"][0]["via"] == "confirmed_correction"
    assert hall(service.state("alice")["plan"])["opening_hours"] == hours


@pytest.mark.parametrize("kind, name", REFERENCED, ids=[f"{kind}.{name}" for kind, name in REFERENCED])
def test_every_field_a_lock_records_may_be_one_it_did_not_know(service, kind, name):
    """Each field with a default, of each model a lock snapshot records, as if it had come with a later upgrade."""
    before_upgrade(locked_trip(service), kind, {name})
    draft = agent(service, service.state("alice")["plan"])
    assert lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]


@pytest.mark.parametrize("name", IN_THE_BLOCK)
def test_every_field_of_the_locked_block_may_be_one_it_did_not_know(service, name):
    before_upgrade(locked_trip(service), None, set(), in_value={name})
    draft = agent(service, service.state("alice")["plan"])
    assert lock_conflicts(draft) == [], [check["details"] for check in lock_conflicts(draft)]


CLASSIFIED = sorted((model.__name__, name) for model in trip_models() for name, field in model.model_fields.items() if not field.is_required())


@pytest.mark.parametrize("model_name, name", CLASSIFIED, ids=[f"{model}.{name}" for model, name in CLASSIFIED])
def test_the_comparator_takes_any_missing_field_of_any_model_for_its_default(model_name, name):
    """Every field with a default in the field table: absent, or None where the model does not take None, equals the
    default; a recorded object or locked value from before the field existed is unchanged by it."""
    model = locks.MODELS[model_name.lower()]
    assert locks.differences({}, {name: default(model, name)}, model, "x") == []
    assert locks.differences({name: default(model, name)}, {}, model, "x") == []
    if not locks.accepts_none(field_type(model.model_fields[name])):
        assert locks.differences({name: None}, {name: default(model, name)}, model, "x") == []


def test_the_comparator_reads_a_missing_field_as_its_default_and_a_real_value_as_itself():
    from tripwright.trip import Place

    old = dict(id="gym-a", name="Hall", name_local="Hall", area="Lakeside", kind="climbing", coords=[47.0, 13.0])
    new = Place.model_validate(old).model_dump(mode="json")
    assert locks.differences(old, new, Place, HALL) == [] and locks.differences(dict(old, opening_hours=None), new, Place, HALL) == []
    assert locks.differences(dict(old, opening_hours=["Mo-Su 06:00-22:00"]), new, Place, HALL) == [f"{HALL}/opening_hours"]
    assert locks.differences(dict(old, priority="must"), new, Place, HALL) == [f"{HALL}/priority"]
    assert locks.differences(old, dict(new, rain_ok=False), Place, HALL) == [f"{HALL}/rain_ok"]  # None is a value rain_ok takes
