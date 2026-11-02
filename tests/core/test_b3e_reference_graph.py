"""B3e: a user lock protects everything the locked value reaches through the trip model's references (D-35).

Two parts. The model part checks that the reference fields are read from trip.py and not listed by hand: every string
field is either marked `Ref` or declared plain here, and the marks agree with what the Trip validator treats as a
reference. The lock part generates cases from the model instead of listing them: for every object reachable from a
locked block (places, people, bookings, fares; through fares to the booking they are a line of and along `included_in`
chains), for each lock shape (whole block, the one field the path starts from), every protected field of the object
is changed to another valid value and the candidate must be refused at propose and at commit, also with
`accept_unverified=True`; every description field in `locks.DESCRIPTION` is changed and the candidate must pass.
B3f (D-36) removed the exemptions this file tested as legal (a booking's purchase step, coordinates within 100 m, the
channel, deadline and refund terms, a place's hours, price, rain flag, priority and people); the tests that assumed them
now expect a refusal, and refusals name the changed field (`booking:pass1/what`) instead of the object.
Fictional people, places and tickets only."""
from __future__ import annotations

import datetime
import typing
from copy import deepcopy
from itertools import count

import pytest
from pydantic import BaseModel, ValidationError, create_model

from tripwright import locks
from tripwright.locks import DESCRIPTION
from tripwright.trip import Block, PlaceId, Trip, field_type, reference_fields, reference_kind, trip_models

D1, D2, D3 = "2032-04-01", "2032-04-02", "2032-04-03"
GROUP = ["u1", "u2", "u3"]
EVERYONE = ["u1", "u2", "u3", "u4"]
LOCKED = "d1-climb"


def place(place_id, name, kind, coords, area="Lakeside"):
    return dict(id=place_id, name=name, name_local=f"{name} (fictional)", area=area, kind=kind, coords=coords)


def chain_pass(index, last):
    booking = dict(id=f"pass{index}", date=D1, kind="day_pass", what=f"Lakeside pass {index}", subject=GROUP, status="on_site",
                   place=f"desk-{index}")
    if index < last:
        booking["fares"] = [dict(id=f"F{index}", segment=f"pass {index}", ticket=f"Included in pass {index + 1}", subject=GROUP,
                                 included_in=f"pass{index + 1}")]
    return booking


def trip(passes=3) -> dict:
    """The locked block names a place, an option, an origin, a destination, a fare, a booking and a baggage holder. The
    fare F0 is a line of the booking `ride` and is included in pass1; pass1 is included in pass2, pass2 in pass3. Every
    booking has its own place. Day one also has an anchor, a base and rain candidates, so every model appears."""
    return dict(
        id="graph-trip", title="Fictional reference graph", start=D1, end=D3, timezone="Europe/Vienna", areas=["Lakeside", "Hilltown"],
        people=[dict(id="u1", name="Ash"), dict(id="u2", name="Bea"), dict(id="u3", name="Cy"), dict(id="u4", name="Dee")],
        places=[place("gym-a", "Boulder hall A", "climbing", [47.80, 13.04]), place("gym-b", "Boulder hall B", "climbing", [47.81, 13.05]),
                place("pier-o", "North pier", "transport", [47.82, 13.06]), place("pier-d", "South pier", "transport", [47.83, 13.07]),
                place("gym-desk", "Hall A ticket desk", "transport", [47.8002, 13.0402]),
                place("shuttle-stop", "Shuttle stop", "transport", [47.84, 13.08]),
                *[place(f"desk-{index}", f"Pass desk {index}", "transport", [47.85 + index / 100, 13.09]) for index in range(1, passes + 1)],
                place("cafe", "Cafe Ruhe", "food", [47.88, 13.12]), place("hill", "Hill top", "view", [47.90, 13.20], area="Hilltown")],
        bookings=[
            dict(id="hall-entry", date=D1, kind="ticket", what="Evening entry to hall A", subject=GROUP, status="to_buy", place="gym-desk",
                 why_now="Evening slots sell out", contact=dict(name="Desk A")),
            dict(id="ride", date=D1, kind="ticket", what="Lake shuttle", subject=EVERYONE, status="on_site", place="shuttle-stop",
                 fares=[dict(id="F0", segment="pier to hall", ticket="Shuttle single", subject=GROUP, included_in="pass1",
                             amount_per_person=0, currency="EUR")]),
            *[chain_pass(index, passes) for index in range(1, passes + 1)],
            dict(id="other-pass", date=D2, kind="day_pass", what="Hill pass", subject=["u4"], status="on_site", place="hill",
                 fares=[dict(id="F9", segment="hill", ticket="Hill single", subject=["u4"])])],
        days=[
            dict(date=D1, title="Day one", area="Lakeside", base="cafe", candidates={"rain": ["gym-b"]},
                 anchors=[dict(id="last-boat", time="22:30", text="Last boat back", subject=GROUP, place="pier-d", destination="pier-o",
                               booking="ride", line="Boat 1")],
                 plans=[dict(id="rec", name="Recommended", tracks=[
                     dict(subject=GROUP, blocks=[
                         dict(id="d1-cafe", start="10:00", end="11:00", kind="meal", place="cafe"),
                         dict(id=LOCKED, start="19:30", end="21:00", kind="visit", place="gym-a", options=["gym-b"], origin="pier-o",
                              destination="pier-d", fare="F0", booking="hall-entry", baggage_holder="u1", text="饭后抱石（用户已定）")]),
                     dict(subject=["u4"], blocks=[dict(id="d1-u4", start="10:00", end="11:00", kind="flex", place="cafe")])])]),
            dict(date=D2, title="Day two", area="Hilltown", plans=[dict(id="rec", name="Recommended", tracks=[dict(subject=["u4"], blocks=[
                dict(id="d2-hill", start="10:00", end="12:00", kind="visit", place="hill", fare="F9")])])]),
            dict(date=D3, title="Day three", area="Lakeside", plans=[dict(id="rec", name="Recommended", tracks=[dict(subject=EVERYONE, blocks=[
                dict(id="d3-cafe", start="10:00", end="11:00", kind="meal", place="cafe")])])])])


# --- the model: reference fields are marked, and the marks match the validator ---------------------------------------

# String fields that are not references, with why. A new string field must be added here or marked Ref in trip.py.
PLAIN = {
    "Person": {"id", "name"},
    # transit_id is a stop ID in the transit provider, outside the trip document; locks compare it as a value.
    # opening_hours are rules ('Mo-Fr 06:00-22:00'); hours_source holds a page address and an evidence record's ID (A7).
    "Place": {"id", "name", "name_local", "kind", "reason", "hours", "checked", "notes", "transit_id", "links", "opening_hours", "hours_source"},
    "Fare": {"id", "segment", "ticket", "currency"},
    "Booking": {"id", "kind", "what", "why_now", "where", "if_missed", "contact"},
    "Block": {"id", "start", "end", "text", "mode", "source", "app", "note", "equipment", "critical_facts", "line"},
    "Plan": {"id", "name", "note"},
    "Anchor": {"id", "time", "text", "consequence", "alternative", "app", "line"},
    "Day": {"title", "pace", "sunset", "rain_plan", "notes"},
    # areas defines the names that area fields refer to.
    "Trip": {"id", "title", "timezone", "areas", "default_pace"},
}
# Ref kinds that are not trip models: a name in Trip.areas, and evidence records kept outside the trip document.
LABELS = {"area", "evidence"}
# Fields whose values have a checked form (A7): how to change one to another valid value.
VALID_CHANGES = {
    ("Place", "opening_hours"): lambda value: [*(value or []), "Sa,Su 08:00-20:00"],
    ("Place", "hours_source"): lambda value: dict(value or {}, url="https://example.org/hours-changed"),
}


def leaf(annotation):
    """The element type at the bottom of Optional, Annotated, lists, tuples and dict values."""
    while True:
        bare = locks.bare(annotation)
        inner = locks.inner(bare)
        if inner is None:
            return bare
        annotation = inner


def string_fields(models) -> set[tuple[str, str]]:
    return {(model.__name__, name) for model in models for name, field in model.model_fields.items() if leaf(field_type(field)) is str}


def unclassified(models) -> set[tuple[str, str]]:
    references = reference_fields(models)
    return {key for key in string_fields(models) if key not in references and key[1] not in PLAIN.get(key[0], set())}


def test_every_string_field_is_a_marked_reference_or_declared_plain():
    models = trip_models()
    assert unclassified(models) == set()
    references = reference_fields(models)
    assert not {key for key in references if key[1] in PLAIN.get(key[0], set())}, "a field is both a reference and plain"
    assert {(name, field) for name, fields in PLAIN.items() for field in fields} <= string_fields(models), "stale PLAIN entry"
    model_kinds = {model.__name__.lower() for model in models}
    assert set(references.values()) <= model_kinds | LABELS, "a Ref kind names neither a trip model nor a known label"


def test_a_new_unmarked_reference_field_is_reported():
    """What a developer adding Block.parking without a Ref mark would see; marked, it is derived and followed."""
    unmarked = create_model("Block", __base__=Block, parking=(str | None, None))
    assert unclassified([unmarked]) == {("Block", "parking")}
    marked = create_model("Block", __base__=Block, parking=(PlaceId | None, None))
    assert unclassified([marked]) == set() and reference_fields([marked])[("Block", "parking")] == "place"
    assert list(locks.targets(dict(parking="gym-b"), marked)) == ["place:gym-b"]


def instances(value, annotation, found):
    """Every model instance in a document, as (model, record) pairs, found through the model's own types."""
    record = locks.model(annotation)
    if record is not None and isinstance(value, dict):
        found.append((record, value))
        for name, field in record.model_fields.items():
            instances(value.get(name), field_type(field), found)
    elif locks.inner(annotation) is not None and isinstance(value, (list, dict)):
        for item in value.values() if isinstance(value, dict) else value:
            instances(item, locks.inner(annotation), found)
    return found


def probe(document, record_index, name, annotation, candidate) -> bool:
    """Whether the trip validates with the field set to candidate: appended to a list, put in a dict's first entry."""
    changed = deepcopy(document)
    record = instances(changed, Trip, [])[record_index][1]
    current, bare = record.get(name), locks.bare(annotation)
    if typing.get_origin(bare) is dict:
        shaped = [candidate] if typing.get_origin(locks.bare(locks.inner(bare))) is list else candidate
        record[name] = dict(current or {}, **{next(iter(current or {}), "k"): shaped})
    elif locks.inner(bare) is not None:
        record[name] = [*(current or []), candidate]
    else:
        record[name] = candidate
    try:
        Trip.model_validate(changed)
        return True
    except ValidationError:
        return False


def test_reference_marks_match_what_the_validator_enforces():
    """A string field is a reference for the validator when an unknown ID is refused but some existing ID is accepted.
    Over a trip where every model appears: such fields are exactly the marked in-document references."""
    document = Trip.model_validate(trip()).model_dump(mode="json")
    found = instances(document, Trip, [])
    assert {record.__name__ for record, _ in found} == {model.__name__ for model in trip_models()}
    ids = sorted({record["id"] for _, record in found if isinstance(record.get("id"), str)} | set(document["areas"]))
    enforced = set()
    for index, (record, value) in enumerate(found):
        for name, field in record.model_fields.items():
            annotation = field_type(field)
            if leaf(annotation) is not str or (record.__name__, name) in enforced:
                continue
            others = [candidate for candidate in ids if candidate != value.get(name)]
            if not probe(document, index, name, annotation, "zz-unknown") and any(probe(document, index, name, annotation, candidate) for candidate in others):
                enforced.add((record.__name__, name))
    marked = {key for key, kind in reference_fields().items() if kind != "evidence"}
    assert enforced == marked


# --- locks: cases generated from the model ---------------------------------------------------------------------------

KEYS = count()


def seed(service, plan=None):
    turn = service.turn("alice", "Create the fictional trip.", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan or trip(), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def say(service, text, *, plan=None, changes=None, nature="user_verbatim", speaker="u1"):
    """A message and a proposal answering it. The user's messages carry their writer, u1 (a user decision is admitted
    only from them, D-49); an agent's carries none."""
    key = f"k{next(KEYS)}"
    turn = service.turn("alice", text, key, nature=nature, native_ref=f"fixture:{key}" if nature == "user_verbatim" else None,
                        speaker=speaker if nature in ("user_verbatim", "host_restatement") else None)
    if callable(changes):
        changes = changes(turn["receipt"]["event_id"])
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, changes=changes, idempotency_key=key)


def lock(service, path, *, plan=None):
    current = plan or service.state("alice")["plan"]
    draft = say(service, "第一天饭后去岩馆 A，整个块和所用的票都定了。", plan=plan, changes=lambda event: dict(decisions=[dict(
        question="饭后去哪、用哪张票", options=["岩馆 A 与所选票"], choice="岩馆 A 与所选票", decided_by="user", must=True, subject=GROUP,
        source=dict(event=event, basis="user_explicit"), locked_paths={path: locks.pointer(current, path)})]))
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
    return service


def agent(service, plan):
    return say(service, "agent updates referenced objects", plan=plan, nature="agent_inference")


def lock_conflicts(draft):
    return [check for check in draft["checks"]["checks"] if check["id"].startswith("locked:") and check["status"] == "conflict"]


def owners(document) -> dict[str, str]:
    """A fare is a line of the booking that holds it (test-side knowledge; the implementation reads the model)."""
    return {f"fare:{fare['id']}": f"booking:{booking['id']}" for booking in document["bookings"] for fare in booking["fares"]}


def follow(document, value, annotation):
    """Test-side reference walk: 'kind:id' targets of a value through the Ref marks, skipping description fields."""
    record = locks.model(annotation)
    if value is None:
        return []
    if record is None and locks.inner(annotation) is None:
        kind = reference_kind(annotation)
        return [f"{kind}:{value}"] if kind in locks.REFERENCED else []
    if record is not None:
        return [target for name, field in record.model_fields.items() if name not in DESCRIPTION.get(record.__name__, ())
                for target in follow(document, value.get(name), field_type(field))]
    return [target for item in (value.values() if isinstance(value, dict) else value) for target in follow(document, item, locks.inner(annotation))]


def reached(document, start: list[str]) -> dict[str, int]:
    """Every object reachable from start with its depth (start objects are at depth 1)."""
    records = {f"{record.__name__.lower()}:{value['id']}": (record, value) for record, value in instances(document, Trip, [])
               if record.__name__.lower() in locks.REFERENCED}
    line_of = owners(document)
    depth, frontier = {}, [(key, 1) for key in start]
    while frontier:
        key, level = frontier.pop(0)
        if key in depth:
            continue
        depth[key] = level
        record, value = records[key]
        frontier += [(target, level + 1) for target in follow(document, value, record)] + ([(line_of[key], level + 1)] if key in line_of else [])
    return depth


def node(plan, key) -> dict:
    kind, object_id = key.split(":", 1)
    return next(value for record, value in instances(plan, Trip, []) if record.__name__.lower() == kind and value.get("id") == object_id)


def alternatives(plan, record: type[BaseModel], name: str, value, owner: dict | None):
    """Other values a field could take, most specific first; the caller keeps the first one the validator accepts."""
    annotation = field_type(record.model_fields[name])
    bare = locks.bare(annotation)
    kind = reference_kind(annotation)
    ids = {kind_name: [entry["id"] for entry_record, entry in instances(plan, Trip, []) if entry_record.__name__.lower() == kind_name]
           for kind_name in locks.REFERENCED}
    ids["area"] = plan["areas"]
    if (record.__name__, name) in VALID_CHANGES:  # values a validator checks: a different valid one
        return [VALID_CHANGES[record.__name__, name](value)]
    if typing.get_origin(bare) is typing.Literal:
        return [option for option in typing.get_args(bare) if option != value]
    if locks.inner(bare) is not None and typing.get_origin(bare) is list:
        element = locks.model(locks.inner(bare))
        if element is not None:
            copies = [entry for entry_record, entry in instances(plan, Trip, []) if entry_record is element]
            return [[*(value or []), dict(deepcopy(copy), id=f"{copy['id']}-added", **extra)] for copy in copies
                    for extra in ({}, dict(subject=(owner or {}).get("subject", ["u1"])[:1]))]
        if kind is not None:
            unused = [entry for entry in ids[kind] if entry not in (value or [])]
            return ([value[:-1]] if value and len(value) > 1 else []) + [[*(value or []), entry] for entry in unused]
        return [[*(value or []), "changed"]]
    if kind is not None:
        return [entry for entry in ids[kind] if entry != value]
    if bare is str:
        return ["changed"] if value is None else [value + " (changed)"]
    if bare is bool:
        return [not value]
    if bare in (int, float):
        return [1] if value is None else [value + 1]
    if bare is datetime.date:
        return [D2] if value in (None, D1) else [D1]
    if typing.get_origin(bare) is tuple:
        return [[47.0, 13.0]] if value is None else [[value[0] + 0.05, value[1]]]
    if typing.get_origin(bare) is dict:
        return [dict(value or {}, name=(value or {}).get("name", "x") + " (changed)", extra="changed")]
    raise AssertionError(f"no alternative for {record.__name__}.{name}: {annotation}")


def changed_plan(plan, key, name):
    """The plan with one field of one object changed to the first alternative that keeps the trip valid."""
    record = locks.MODELS[key.split(":", 1)[0]]
    line_of = owners(plan)
    owner = node(plan, line_of[key]) if key in line_of else node(plan, key) if record.__name__ == "Booking" else None
    for value in alternatives(plan, record, name, node(plan, key).get(name), owner):
        candidate = deepcopy(plan)
        node(candidate, key)[name] = value
        try:
            Trip.model_validate(candidate)
        except ValidationError:
            continue
        return candidate
    raise AssertionError(f"no valid change of {key}.{name}")


BLOCK_REFERENCES = sorted(name for (model_name, name), kind in reference_fields().items()
                          if model_name == "Block" and kind in locks.REFERENCED and name not in DESCRIPTION["Block"])


def cases(plan):
    """(lock path, object key, depth) for every object a lock reaches, per lock shape: the whole block, and each reference
    field of the block (a field lock still holds the block's track people)."""
    block = locks.pointer(plan, f"/blocks/{LOCKED}")
    people = [f"person:{person}" for person in GROUP]
    shapes = {f"/blocks/{LOCKED}": follow(plan, block, Block) + people}
    shapes.update({f"/blocks/{LOCKED}/{name}": follow(plan, block[name], field_type(Block.model_fields[name])) + people for name in BLOCK_REFERENCES})
    return [(path, key, depth) for path, start in shapes.items() for key, depth in reached(plan, start).items()]


def test_the_generated_cases_cover_every_reference_kind_and_depths_one_to_three():
    plan = Trip.model_validate(trip()).model_dump(mode="json")
    generated = cases(plan)
    whole = {key: depth for path, key, depth in generated if path == f"/blocks/{LOCKED}"}
    assert {key.split(":")[0] for key in whole} == locks.REFERENCED
    assert {depth for depth in whole.values()} >= {1, 2, 3, 4}
    # The chain of the judge's counterexample A: fare → booking it is a line of, and included_in three levels deep.
    assert whole["fare:F0"] == 1 and whole["booking:ride"] == 2 and whole["booking:pass1"] == 2 and whole["booking:pass3"] == 4
    # Not reached: another pass, its place and fare, and the blocks of other days. Dee (u4) is reached: she holds `ride`.
    assert not {"booking:other-pass", "place:hill", "fare:F9"} & set(whole) and whole["person:u4"] == 3
    assert {path.rsplit("/", 1)[1] for path, _, _ in generated if path != f"/blocks/{LOCKED}"} == {
        "baggage_holder", "booking", "destination", "fare", "options", "origin", "place"}


@pytest.fixture(params=["block", "field"])
def shape(request):
    return request.param


GENERATED = cases(Trip.model_validate(trip()).model_dump(mode="json"))
LOCK_PATHS = sorted({path for path, _, _ in GENERATED})


@pytest.mark.parametrize("path", LOCK_PATHS)
def test_changing_any_protected_field_of_any_reached_object_is_refused(service, path):
    """Each object the lock reaches, each protected field (every field but `id` and DESCRIPTION)."""
    lock(seed(service), path)
    tried = 0
    for _, key, depth in [row for row in GENERATED if row[0] == path]:
        record = locks.MODELS[key.split(":", 1)[0]]
        for name in record.model_fields:
            if name == "id" or name in DESCRIPTION.get(record.__name__, ()):
                continue
            candidate = changed_plan(service.state("alice")["plan"], key, name)
            draft = agent(service, candidate)
            label = f"{path} → {key} (depth {depth}).{name}"
            conflicts = lock_conflicts(draft)
            assert draft["status"] == "rejected" and conflicts, label
            named = {entry for check in conflicts for entry in check["details"]["changed"]}
            assert f"{key}/{name}" in named or any(entry.startswith(f"{owners(candidate).get(key)}/fares/") for entry in named), (label, named)
            with pytest.raises(ValueError, match=r"Commit refused: .*locked:"):
                service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
            tried += 1
    assert tried >= 3, (path, tried)


def test_changing_a_nested_line_of_a_reached_booking_is_refused(service):
    lock(seed(service), f"/blocks/{LOCKED}")
    candidate = service.state("alice")["plan"]
    node(candidate, "booking:pass2")["fares"][0]["ticket"] = "Museum-only entry"
    assert lock_conflicts(agent(service, candidate))[0]["details"]["changed"] == ["booking:pass2/fares/F2/ticket"]


def test_objects_the_lock_does_not_reach_may_change(service, shape):
    lock(seed(service), f"/blocks/{LOCKED}" if shape == "block" else f"/blocks/{LOCKED}/fare")
    candidate = service.state("alice")["plan"]
    node(candidate, "booking:other-pass").update(what="Museum pass", kind="museum_ticket", place="cafe")
    node(candidate, "place:hill").update(name="Other hill", kind="park", coords=[47.0, 13.0])
    node(candidate, "fare:F9")["ticket"] = "Museum single"
    draft = agent(service, candidate)
    assert lock_conflicts(draft) == []
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


def description_change(plan, key, name):
    candidate = deepcopy(plan)
    record = locks.MODELS[key.split(":", 1)[0]]
    annotation = locks.bare(field_type(record.model_fields[name]))
    current = node(candidate, key).get(name)
    if (key.split(":", 1)[0].capitalize(), name) in VALID_CHANGES:
        value = VALID_CHANGES[key.split(":", 1)[0].capitalize(), name](current)
    elif reference_kind(annotation) == "person":
        value = [*(current or []), "u4"]
    elif typing.get_origin(annotation) is typing.Literal:
        value = next(option for option in typing.get_args(annotation) if option != current)
    elif typing.get_origin(annotation) is list:
        value = [*(current or []), "https://example.invalid/changed"]
    elif typing.get_origin(annotation) is dict:
        value = dict(current or {}, EUR=21)
    elif annotation is bool:
        value = not current
    elif annotation is datetime.date:
        value = D1
    else:
        value = (current or "") + " changed"
    node(candidate, key)[name] = value
    Trip.model_validate(candidate)
    return candidate


@pytest.mark.parametrize("model_name,name", sorted((model_name, name) for model_name in ("Place", "Booking") for name in DESCRIPTION[model_name]))
def test_each_description_field_of_a_reached_object_may_change(service, shape, model_name, name):
    """Every DESCRIPTION field of a place and a booking, on the deepest reached object of that kind."""
    seed(service)
    path = f"/blocks/{LOCKED}" if shape == "block" else f"/blocks/{LOCKED}/fare"
    lock(service, path)
    plan = service.state("alice")["plan"]
    reached_here = reached(plan, follow(plan, locks.pointer(plan, path), locks.annotation_at(path)) + [f"person:{person}" for person in GROUP])
    key = max((key for key in reached_here if key.startswith(model_name.lower() + ":")), key=lambda key: (reached_here[key], key))
    draft = agent(service, description_change(plan, key, name))
    assert lock_conflicts(draft) == [], (key, name, [check["details"] for check in lock_conflicts(draft)])
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


def test_people_named_only_in_a_description_field_do_not_join_the_lock(service):
    """B3f: Place.subject (whom the place is meant for) is the user's choice now, so changing it is refused; people named
    only in description fields still do not join the lock (no description field names a person any more)."""
    lock(seed(service), f"/blocks/{LOCKED}/place")
    candidate = service.state("alice")["plan"]
    node(candidate, "place:gym-a")["subject"] = ["u4"]
    assert lock_conflicts(agent(service, candidate))[0]["details"]["changed"] == ["place:gym-a/subject"]
    recorded = next(iter(service.ledger.current("alice")["decisions"][0]["placement"].values()))
    assert sorted(recorded["references"]) == ["person:u1", "person:u2", "person:u3", "place:gym-a"]
    assert not any(reference_kind(field_type(locks.MODELS[model_name.lower()].model_fields[name])) == "person"
                   for model_name, names in DESCRIPTION.items() for name in names)


@pytest.mark.parametrize("name,value", [("note", "Bring chalk"), ("evidence_ids", ["EV-b3e-hall"])])
def test_each_description_field_of_the_locked_block_may_change(service, shape, name, value):
    seed(service)
    lock(service, f"/blocks/{LOCKED}" if shape == "block" else f"/blocks/{LOCKED}/start")
    plan = service.state("alice")["plan"]
    locks.pointer(plan, f"/blocks/{LOCKED}")[name] = value
    draft = agent(service, plan)
    assert lock_conflicts(draft) == []
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


def test_the_description_whitelist_is_the_documented_table():
    """Widening this table weakens every lock; it changes only together with this test and docs/DECISIONS-impl.md B3f
    (which replaced the B3e table: no channel, deadline, refund terms, hours, price, rain flag, priority or people)."""
    assert {model_name: names for model_name, names in DESCRIPTION.items() if names} == {
        "Place": {"notes", "reason", "links", "checked", "hours_source"},  # A7: where opening hours were read
        "Booking": {"why_now"},
        "Block": {"note", "evidence_ids"},
        "Plan": {"name", "note"},
        "Anchor": {"evidence_ids"},
        "Day": {"title", "notes"},
        "Trip": {"title"},
    }
    assert not hasattr(locks, "PURCHASED") and not hasattr(locks, "SAME_PLACE_METRES")
    for model_name, names in DESCRIPTION.items():
        assert names <= set(locks.MODELS[model_name.lower()].model_fields), model_name


# --- the rest of the class: status steps, cycles, other lock roots, earlier snapshots, the user's own decision ---------

@pytest.mark.parametrize("booking_id,step", [
    ("hall-entry", "bought"), ("hall-entry", "cancelled"), ("hall-entry", "on_site"),
    ("ride", "bought"), ("ride", "cancelled"), ("pass3", "cancelled"), ("pass3", "booked")])
def test_an_agent_cannot_move_a_reached_booking_status(service, booking_id, step):
    """B3f: no status step is exempt; the purchase steps B3e let through (to_buy→bought, on_site→bought) are refused as
    well. The user's report changes a status (test_b3f_three_paths.py)."""
    seed(service)
    lock(service, f"/blocks/{LOCKED}")
    plan = service.state("alice")["plan"]
    node(plan, f"booking:{booking_id}")["status"] = step
    draft = agent(service, plan)
    assert f"booking:{booking_id}/status" in lock_conflicts(draft)[0]["details"]["changed"]
    with pytest.raises(ValueError, match=f"booking:{booking_id}/status"):
        service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


@pytest.mark.parametrize("cycle", ["two bookings", "a booking in itself"])
def test_included_in_cycles_end_and_stay_protected(service, cycle):
    plan = trip(passes=2)
    second = next(booking for booking in plan["bookings"] if booking["id"] == "pass2")
    if cycle == "two bookings":
        second["fares"] = [dict(id="F2", segment="pass 2", ticket="Included in pass 1", subject=GROUP, included_in="pass1")]
    else:
        second["fares"] = [dict(id="F2", segment="pass 2", ticket="Included in itself", subject=GROUP, included_in="pass2")]
    seed(service, plan)
    lock(service, f"/blocks/{LOCKED}/fare")
    recorded = next(iter(service.store.all("decisions", "alice")[0]["placement"].values()))
    assert {"booking:ride", "booking:pass1", "booking:pass2"} <= set(recorded["references"])
    candidate = service.state("alice")["plan"]
    node(candidate, "booking:pass2")["what"] = "Museum-only entry"
    assert "booking:pass2/what" in lock_conflicts(agent(service, candidate))[0]["details"]["changed"]
    candidate = service.state("alice")["plan"]
    node(candidate, "booking:pass2")["why_now"] = "Collect at the counter"
    assert lock_conflicts(agent(service, candidate)) == []


def test_counterexample_a_is_refused_and_the_user_can_decide_again(service):
    """The judge's shape: the block and fare stay identical, the pass the fare is included in becomes a museum ticket."""
    seed(service)
    lock(service, f"/blocks/{LOCKED}")
    candidate = service.state("alice")["plan"]
    node(candidate, "booking:pass1").update(what="Museum-only entry", kind="museum_ticket")
    draft = agent(service, candidate)
    assert lock_conflicts(draft)[0]["details"]["changed"] == ["booking:pass1/kind", "booking:pass1/what"]
    with pytest.raises(ValueError, match=r"booking:pass1/kind, booking:pass1/what changed"):
        service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
    block = locks.pointer(candidate, f"/blocks/{LOCKED}")
    draft = say(service, "改成博物馆票，我们定了。", plan=candidate, changes=lambda event: dict(decisions=[dict(
        question="用哪张票", options=["博物馆票"], choice="博物馆票", decided_by="user", must=True, subject=GROUP,
        source=dict(event=event, basis="user_explicit"), locked_paths={f"/blocks/{LOCKED}": block})]))
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []
    service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
    assert [decision["status"] for decision in service.store.all("decisions", "alice")] == ["superseded", "active"]
    again = service.state("alice")["plan"]
    node(again, "booking:pass1")["what"] = "Lakeside pass 1"
    assert lock_conflicts(agent(service, again))[0]["details"]["changed"] == ["booking:pass1/what"]


@pytest.mark.parametrize("path,key,changed", [
    ("/bookings/ride", "booking:pass1", "booking:pass1"),
    ("/bookings/ride/fares/F0", "booking:ride", "booking:ride"),
    ("/bookings/ride/fares/F0", "booking:pass3", "booking:pass3"),
    ("/days/2032-04-01", "booking:ride", "booking:ride"),
    ("/days/2032-04-01", "place:desk-3", "place:desk-3")])
def test_locks_on_other_plan_parts_protect_what_they_reach(service, path, key, changed):
    seed(service)
    lock(service, path)
    candidate = service.state("alice")["plan"]
    node(candidate, key)["kind"] = node(candidate, key)["kind"] + " (changed)"
    assert f"{changed}/kind" in lock_conflicts(agent(service, candidate))[0]["details"]["changed"]
    candidate = service.state("alice")["plan"]
    target = node(candidate, key)
    target.update(why_now="Collect at the counter") if key.startswith("booking:") else target.update(notes="Opens at nine")
    assert lock_conflicts(agent(service, candidate)) == []


def test_a_day_lock_lets_anchor_evidence_and_block_notes_change(service):
    seed(service)
    lock(service, f"/days/{D1}")
    candidate = service.state("alice")["plan"]
    candidate["days"][0]["anchors"][0]["evidence_ids"] = ["EV-b3e-boat"]
    locks.pointer(candidate, f"/blocks/{LOCKED}")["note"] = "Bring chalk"
    assert lock_conflicts(agent(service, candidate)) == []
    candidate = service.state("alice")["plan"]
    candidate["days"][0]["anchors"][0]["line"] = "Boat 2"
    assert lock_conflicts(agent(service, candidate))[0]["details"]["changed"] == ["value/anchors/last-boat/line"]


def b3d_placement(plan):
    """The B3d snapshot shape: identity fields of the objects one reference away, no `graph` key."""
    return {f"/blocks/{LOCKED}": dict(date=D1, plan="rec", rank=0, subject=GROUP, timezone="Europe/Vienna", references={
        "place:gym-a": dict(name="Boulder hall A"), "fare:F0": dict(segment="pier to hall")})}


def test_a_b3d_snapshot_is_rebound_to_the_reachable_graph(service):
    seed(service)
    lock(service, f"/blocks/{LOCKED}")
    stored = service.store.all("decisions", "alice")[0]
    stored["placement"] = b3d_placement(service.state("alice")["plan"])
    service.store.put("decisions", "alice", stored)
    candidate = service.state("alice")["plan"]
    node(candidate, "booking:pass3")["place"] = "hill"
    assert lock_conflicts(agent(service, candidate))[0]["details"]["changed"] == ["booking:pass3/place"]
    assert lock_conflicts(agent(service, service.state("alice")["plan"])) == []


def test_a_b3d_snapshot_that_cannot_be_rebound_fails_closed(service):
    seed(service)
    lock(service, f"/blocks/{LOCKED}")
    without = service.state("alice")["plan"]
    track = without["days"][0]["plans"][0]["tracks"][0]
    track["blocks"] = [block for block in track["blocks"] if block["id"] != LOCKED]
    service.store.put("versions", "alice", dict(service.versions.current("alice"), plan=without))
    stored = service.store.all("decisions", "alice")[0]
    stored["placement"] = b3d_placement(without)
    service.store.put("decisions", "alice", stored)
    assert lock_conflicts(agent(service, Trip.model_validate(trip()).model_dump(mode="json")))[0]["details"]["changed"] == ["snapshot"]


def test_deep_check_and_external_edits_use_the_same_comparator(service):
    seed(service)
    lock(service, f"/blocks/{LOCKED}/fare")
    candidate = service.state("alice")["plan"]
    node(candidate, "place:desk-2")["name"] = "Museum desk"
    draft = agent(service, candidate)
    assert lock_conflicts(draft)[0]["details"]["changed"] == ["place:desk-2/name"]
    while service.result("alice", draft["job_id"])["status"] not in ("completed", "failed", "stale"):
        assert service.jobs.run_once("alice")
    deep = service.result("alice", draft["job_id"])
    assert deep["status"] == "completed" and deep["result"]["verdict"] == "conflict", deep
    assert [check["details"]["changed"] for check in deep["result"]["value"]["checks"]["conflicts"]
            if check["id"].startswith("locked:")] == [["place:desk-2/name"]]
    adopted = service.versions.adopt("alice", candidate, author="editor")
    assert not adopted["adopted"] and any("locked:" in conflict and "place:desk-2/name changed" in conflict for conflict in adopted["conflicts"])
