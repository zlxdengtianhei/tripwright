"""B3f (D-36): every field of the trip model has one class for user locks, and the lock follows that table and nothing
else. The table in docs/DECISIONS-impl.md B3f must equal `locks.FIELDS` and cover every model field, so a field the
model gains turns this file red until someone classifies it (meanwhile the lock protects it). Lock cases are generated
from the table over the fictional trip of test_b3e_reference_graph: for every field of every model, an agent changes
it on an object a lock holds; a protected field (external fact or choice) is refused at propose naming the field and at
commit with `accept_unverified=True`, a description field commits. Fictional people, places and tickets only."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError, create_model

from test_b3e_reference_graph import D1, D2, GROUP, LOCKED, agent, alternatives, lock_conflicts, say, seed
from tripwright import locks
from tripwright.locks import CHOICE, DESCRIPTION_FIELD, EXTERNAL, FIELDS
from tripwright.trip import Place, Trip, trip_models

DOC = Path(__file__).resolve().parents[2] / "docs" / "DECISIONS-impl.md"
CLASSES = (DESCRIPTION_FIELD, EXTERNAL, CHOICE)


OLD_VOLUMES = sorted((DOC.parent / "history").glob("*-DECISIONS-impl.md"))

def documented() -> list[tuple[str, str, str, str]]:
    """Table rows also arrive in later sections that appended them (A3, A7, A11); since the 11-04 tidy those
    sections live verbatim in docs/history/*-DECISIONS-impl.md, so read them there too. The 2026-1031 volume
    holds only pre-B3f sections; scanning it whole adds no rows today (its sections carry no table rows)."""
    rows: list[tuple[str, str, str, str]] = []
    for path in [DOC, *OLD_VOLUMES]:
        text = path.read_text(encoding="utf-8")
        if "# Implementation decisions — B3f" in text:
            text = text[text.index("# Implementation decisions — B3f"):]
        rows += re.findall(r"^\| (\w+)\.(\w+) \| (\w+) \| (.+?) \|$", text, re.M)
    return rows


def unclassified(models) -> set[tuple[str, str]]:
    return {(model.__name__, name) for model in models for name in model.model_fields if locks.field_class(model.__name__, name) is None}


# --- the table ---------------------------------------------------------------------------------------------------------

def test_every_model_field_has_exactly_one_class():
    models = trip_models()
    assert unclassified(models) == set()
    assert set(FIELDS) == {model.__name__ for model in models}
    for model in models:
        assert set(FIELDS[model.__name__]) == set(model.model_fields), f"stale row in locks.FIELDS for {model.__name__}"
    assert {kind for fields in FIELDS.values() for kind in fields.values()} == set(CLASSES)


def test_a_field_the_model_gains_is_reported_and_protected_until_classified():
    """What a developer adding Place.wifi would see: the table test fails, and the lock compares the new field."""
    extended = create_model("Place", __base__=Place, wifi=(str | None, None))
    assert unclassified([extended]) == {("Place", "wifi")}
    assert locks.field_class("Place", "wifi") is None and not locks.described("Place", "wifi")
    assert locks.differences(dict(id="x", wifi="free"), dict(id="x", wifi="paid"), extended, "place:x") == ["place:x/wifi"]


def test_the_documented_table_equals_the_code_with_a_reason_per_row():
    rows = documented()
    keys = [(model, name) for model, name, _, _ in rows]
    assert len(keys) == len(set(keys)), "a field has two rows"
    assert {(model, name): kind for model, name, kind, _ in rows} == {
        (model, name): kind for model, fields in FIELDS.items() for name, kind in fields.items()}
    assert all(len(reason.split()) >= 2 for *_, reason in rows), "a row without a reason"
    counts = {kind: sum(1 for _, _, row_kind, _ in rows if row_kind == kind) for kind in CLASSES}
    # A3 added Anchor.deadline (choice); A7 Place.opening_hours (external) and Place.hours_source (description); A11
    # Anchor.where (external)
    assert counts == {DESCRIPTION_FIELD: 14, EXTERNAL: 21, CHOICE: 76}, counts


def test_judge_five_exemptions_are_protected_and_description_names_no_person():
    """The fields of counterexample A (channel, refund terms, deadline, status, coordinates) and the other B3e
    exemptions are protected; no description field refers to a person, so none can hide who goes."""
    for model, name in [("Booking", "where"), ("Booking", "if_missed"), ("Booking", "due"), ("Booking", "status"),
                        ("Place", "coords"), ("Place", "hours"), ("Place", "price"), ("Place", "rain_ok"),
                        ("Place", "priority"), ("Place", "subject")]:
        assert locks.field_class(model, name) in (EXTERNAL, CHOICE), (model, name)
    assert not any(locks.reference_kind(locks.field_type(locks.MODELS[model.lower()].model_fields[name])) == "person"
                   for model, names in locks.DESCRIPTION.items() for name in names)


# --- the lock follows the table: generated cases -----------------------------------------------------------------------

def locate(plan, model: str):
    """The object of each model the generated locks hold, and the key its refusals start with."""
    day = next(entry for entry in plan["days"] if entry["date"] == D1)
    plan_one = day["plans"][0]
    block = next(block for track in plan_one["tracks"] for block in track["blocks"] if block["id"] == LOCKED)
    return {
        "Person": next(person for person in plan["people"] if person["id"] == "u1"),
        "Place": next(place for place in plan["places"] if place["id"] == "gym-a"),
        "Fare": next(fare for booking in plan["bookings"] for fare in booking["fares"] if fare["id"] == "F0"),
        "Booking": next(booking for booking in plan["bookings"] if booking["id"] == "hall-entry"),
        "Block": block,
        "Track": plan_one["tracks"][0],
        "Plan": plan_one,
        "Anchor": day["anchors"][0],
        "Day": day,
        "Trip": plan,
    }[model]


# Values the generic alternatives cannot build (validated clocks, maps of lists, containers without IDs, dates).
SPECIAL = {
    ("Block", "start"): ["19:40"], ("Block", "end"): ["21:10"], ("Anchor", "time"): ["22:45"],
    ("Day", "candidates"): [{"rain": ["gym-b", "cafe"]}], ("Trip", "budgets"): [{"EUR": 500.0}],
    ("Trip", "timezone"): ["Europe/Berlin"], ("Trip", "start"): ["2032-03-31"], ("Trip", "end"): ["2032-04-04"],
    ("Place", "opening_hours"): [["Mo-Su 06:00-22:00"]],
}
DESCRIBED = {"evidence_ids": ["EV-b3f-described"], "links": ["https://example.invalid/b3f"], "checked": "2032-03-30",
             "hours_source": {"url": "https://example.invalid/b3f-hours"}}


def mutated(plan, model: str, name: str):
    """The plan with one field of the located object changed to the first value the trip validator accepts."""
    record = locks.MODELS[model.lower()]
    current = locate(plan, model).get(name)
    if locks.described(model, name):
        options = [DESCRIBED.get(name, (current or "") + " (changed)")]
    elif (model, name) in SPECIAL:
        options = SPECIAL[(model, name)]
    elif (model, name) == ("Plan", "tracks"):
        options = [[*current, dict(subject=["u4"], blocks=[])]]
    elif (model, name) == ("Day", "plans"):
        options = [[*current, dict(id="extra", name="Extra", tracks=[])]]
    elif (model, name) == ("Trip", "days"):
        options = [current[:-1]]
    elif name == "id":
        # An ID names the object: rename it everywhere it is referred to, as a host that re-keys it would.
        old = locate(plan, model)["id"]
        candidate = json.loads(json.dumps(plan).replace(json.dumps(old), json.dumps(old + "-renamed")))
        Trip.model_validate(candidate)
        return candidate
    elif (model, name) == ("Day", "date"):
        candidate = deepcopy(plan)
        first, second = candidate["days"][0], candidate["days"][1]
        first["date"], second["date"] = second["date"], first["date"]
        return candidate
    else:
        options = alternatives(plan, record, name, current, locate(plan, "Booking") if model == "Fare" else None)
    for value in options:
        candidate = deepcopy(plan)
        locate(candidate, model)[name] = value
        try:
            Trip.model_validate(candidate)
        except ValidationError:
            continue
        return candidate
    raise AssertionError(f"no valid change of {model}.{name}")


GROUPS = {
    # The whole-block lock reaches the people of its track, its place, fare, the fare's booking and the block's booking;
    # it also holds the trip timezone. A day lock holds the day with its plans, tracks and anchors. Trip-level fields are
    # held only by locks on them.
    "block": ("Person", "Place", "Fare", "Booking", "Block"),
    "day": ("Track", "Plan", "Anchor", "Day"),
    "trip": ("Trip",),
}
CASES = [(group, model, name) for group, models in GROUPS.items() for model in models for name in FIELDS[model]]


def lock_group(service, group: str):
    plan = service.state("alice")["plan"]
    if group == "block":
        paths = [f"/blocks/{LOCKED}"]
    elif group == "day":
        paths = [f"/days/{D1}"]
    else:
        paths = [f"/blocks/{LOCKED}"] + [f"/{name}" for name, kind in FIELDS["Trip"].items() if kind != DESCRIPTION_FIELD]
    draft = say(service, "这些都定了。", changes=lambda event: dict(decisions=[dict(
        question="定了", options=["照现在"], choice="照现在", decided_by="user", must=True, subject=GROUP,
        source=dict(event=event, basis="user_explicit"), locked_paths={path: locks.pointer(plan, path) for path in paths})]))
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


def names_the_field(entry: str, model: str, name: str, group: str) -> bool:
    """A refusal names the field, or for a field that names the object (an ID, a day's date), the lock's parts it moved."""
    if entry in ("missing", name) or (name == "id" or (model, name) == ("Day", "date")) and group != "trip":
        return True
    if group == "trip" and (entry == "value" or entry.startswith("value/")):
        return True
    return name in entry.split("/")[1:]


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_field_of_a_held_object_follows_its_class(service, group):
    seed(service)
    lock_group(service, group)
    tried = {DESCRIPTION_FIELD: 0, EXTERNAL: 0, CHOICE: 0}
    for _, model, name in [case for case in CASES if case[0] == group]:
        kind = FIELDS[model][name]
        candidate = mutated(service.state("alice")["plan"], model, name)
        draft = agent(service, candidate)
        label = f"{model}.{name} ({kind})"
        conflicts = lock_conflicts(draft)
        if kind == DESCRIPTION_FIELD:
            assert conflicts == [], (label, [check["details"]["changed"] for check in conflicts])
            service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
        else:
            assert draft["status"] == "rejected" and conflicts, label
            named = [entry for check in conflicts for entry in check["details"]["changed"]]
            assert any(names_the_field(entry, model, name, group) for entry in named), (label, named)
            with pytest.raises(ValueError, match=r"Commit refused: .*locked:"):
                service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
        tried[kind] += 1
    assert tried[EXTERNAL] + tried[CHOICE] >= 4 and sum(tried.values()) == sum(len(FIELDS[model]) for model in GROUPS[group]), tried


def test_the_generated_cases_cover_every_field_of_the_model():
    assert {(model, name) for _, model, name in CASES} == {(model.__name__, name) for model in trip_models() for name in model.model_fields}


def test_a_lock_on_a_field_reached_from_the_block_names_that_field_in_the_refusal(service):
    """Counterexample A2 of judge five, generated: each formerly exempt booking field is refused with its own path."""
    seed(service)
    lock_group(service, "block")
    for name, value in [("where", ["Private reseller only"]), ("if_missed", "No refund; buy another ticket"), ("due", D2),
                        ("status", "bought")]:
        plan = service.state("alice")["plan"]
        locate(plan, "Booking")[name] = value
        draft = agent(service, plan)
        assert lock_conflicts(draft)[0]["details"]["changed"] == [f"booking:hall-entry/{name}"], name
        with pytest.raises(ValueError, match=rf"booking:hall-entry/{name} changed"):
            service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
