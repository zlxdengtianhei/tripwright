"""User locks: what a user-decided plan part is, and whether a candidate still holds it.

A lock names a plan part by JSON pointer; blocks are addressed as /blocks/{id}[/field]. The invariant is that the user's
choice still holds in the plan that would be committed. The choice is the locked value and everything it depends on:
`resolve` follows every reference field of the trip model (the `Ref` markers in trip.py) from the locked value, and from
an object to the object it is a line of (a fare to its booking), until nothing new is reached; for a block it also
records where the block sits (date, plan ID, the plan's position among the day's alternatives, its track's people) and
the timezone its clock times are read in. Every field of every object reached is protected except the description
fields in FIELDS; nothing else is exempt (D-36): no distance tolerance, no status step. A protected field changes only by
a new user decision, the user's own report or a correction the user confirmed; the ledger records those as a new
decision whose locked value and snapshot carry the new value (`amend`). Display names identify nothing. `lock_changes`
is the only comparator; propose, deep checks and commit all call it, and it names each changed field.

Since D-53 only what the user marked a must locks (`binding`); the user's other decisions are records the agent may
change with a reason, and every plan change is written field by field to the version's change log (`plan_changes`).
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from itertools import count
from types import UnionType
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

from tripwright.trip import Block, Day, Trip, field_type, reference_fields, reference_kind, trip_models

# What each field of the trip model is to a user lock. A description field changes nothing the user gets (where, which
# entrance, when, with whom, what it costs, how to buy, what happens if missed, the ticket's state) and changes freely.
# External facts and the user's choices are protected alike; the class says what a correction may cite (evidence backs
# external facts only). The reason for each row is in docs/DECISIONS-impl.md B3f, and
# tests/core/test_b3f_field_table.py keeps that table, this one and the model equal: a field the model gains is
# protected, and the test fails, until it is classified here. Rows added later sit in their own section of that file
# (Anchor.deadline: A3; Anchor.where: A11).
DESCRIPTION_FIELD, EXTERNAL, CHOICE = "description", "external", "choice"
D, E, C = DESCRIPTION_FIELD, EXTERNAL, CHOICE
FIELDS = {
    "Person": dict(id=C, name=C),
    "Place": dict(id=C, name=C, name_local=C, area=C, kind=E, subject=C, priority=C, reason=D, coords=E, hours=E, price=E,
                  links=D, checked=D, notes=D, rain_ok=E, transit_id=E, opening_hours=E, hours_source=D),
    "Fare": dict(id=C, segment=C, ticket=C, subject=C, amount_per_person=E, currency=E, date=C, included_in=C,
                 alternative=C, estimated=E),
    "Booking": dict(id=C, date=C, kind=C, what=C, subject=C, status=C, place=C, due=C, why_now=D, where=C, if_missed=C,
                    contact=C, fares=C),
    "Block": dict(id=C, start=C, end=C, kind=C, place=C, options=C, text=C, certainty=E, mode=C, source=E, app=C, fare=C,
                  booking=C, note=D, transfer_minutes=E, walk_minutes=E, baggage_holder=C, equipment=C, evidence_ids=D,
                  critical_facts=E, origin=C, destination=C, line=C),
    "Track": dict(subject=C, blocks=C),
    "Plan": dict(id=C, name=D, note=D, tracks=C),
    "Anchor": dict(id=C, time=E, text=C, subject=C, place=C, booking=C, buffer_minutes=C, travel_minutes=E, evidence_ids=D,
                   consequence=E, alternative=C, app=C, destination=C, line=C, deadline=C, where=E),
    "Day": dict(date=C, title=D, area=C, pace=C, base=C, sunset=E, anchors=C, plans=C, candidates=C, rain_plan=C, notes=D),
    "Trip": dict(id=C, title=D, start=C, end=C, timezone=E, areas=C, people=C, default_pace=C, budgets=C, places=C,
                 bookings=C, days=C),
}
del D, E, C
DESCRIPTION = {model: {name for name, kind in fields.items() if kind == DESCRIPTION_FIELD} for model, fields in FIELDS.items()}
WHERE = ("date", "plan", "rank", "subject", "timezone")
# Snapshots of this shape compare every protected field; earlier ones are rebound from the last committed plan.
SHAPE = "reachable:b3f"
MODELS = {model.__name__.lower(): model for model in trip_models()}
# Kinds a reference can name inside the trip document; other kinds (an area name, an evidence record) are values.
REFERENCED = {kind for kind in reference_fields().values() if kind in MODELS}


def field_class(model_name: str, name: str) -> str | None:
    """The class of a model field in FIELDS; None for a field nobody has classified, which locks protect."""
    return FIELDS.get(model_name, {}).get(name)


def described(model_name: str, name: str) -> bool:
    return field_class(model_name, name) == DESCRIPTION_FIELD


def unescape(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


def identify(document: dict, previous: dict | None = None) -> dict:
    """Give every plan a stable ID unique within its day, in place. A plan without one inherits the ID at the same
    position of that day in the previous version unless the candidate uses that ID elsewhere: a host that omits IDs is
    taken to keep the order. Otherwise it gets an ID that neither version uses on that day. Locks also compare the
    plan's position, so inheriting by position cannot move a locked block to another alternative."""
    if previous is not None and any(plan.get("id") is None for day in previous["days"] for plan in day["plans"]):
        previous = identify(deepcopy(previous))
    earlier = {day["date"]: [plan["id"] for plan in day["plans"]] for day in (previous or {}).get("days", [])}
    for day in document["days"]:
        before = earlier.get(day["date"], [])
        used = {plan["id"] for plan in day["plans"] if plan.get("id") is not None}
        taken = used | set(before)
        for rank, plan in enumerate(day["plans"]):
            if plan.get("id") is not None:
                continue
            inherited = before[rank] if rank < len(before) else None
            plan["id"] = inherited if inherited is not None and inherited not in used else next(
                f"P{number}" for number in count(1) if f"P{number}" not in taken)
            used.add(plan["id"])
            taken.add(plan["id"])
    return document


def blocks(document: dict) -> dict:
    """Every block by ID with where it sits: date, plan ID, the plan's position among the day's alternatives (the first
    is the one to follow) and the sorted IDs of its track's people."""
    return {block["id"]: (dict(date=day["date"], plan=plan.get("id"), rank=rank, subject=sorted(track["subject"])), block)
            for day in document["days"] for rank, plan in enumerate(day["plans"])
            for track in plan["tracks"] for block in track["blocks"]}


def escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def name_of(element) -> str | None:
    """The identity a list element is addressed by: its id, or a day's date."""
    if isinstance(element, dict):
        for key in ("id", "date"):
            if isinstance(element.get(key), str):
                return element[key]
    return None


def element(values: list, part: str):
    return next((value for value in values if name_of(value) == part), None) or values[int(part)]


def pointer(document: dict, path: str):
    """Resolve a lock path. List elements are addressed by id (days by date); a number still selects a position."""
    if not path.startswith("/"):
        raise ValueError("Lock path must be a JSON pointer")
    if path.startswith("/blocks/"):
        document = dict(document, blocks={block_id: block for block_id, (_, block) in blocks(document).items()})
    value = document
    try:
        for part in path[1:].split("/"):
            part = unescape(part)
            value = element(value, part) if isinstance(value, list) else value[part]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError(f"Locked path is missing: {path}") from None
    return value


def stable_path(document: dict, path: str) -> str:
    """Rewrite a positional lock path to identities, so reordering a list cannot point the lock at another element:
    blocks become /blocks/{id}; other list elements with an id (or a day's date) are addressed by it. Elements without
    one (tracks) keep their position, which only makes a reorder look like a change. A path this document cannot
    resolve outside blocks stays as written."""
    parts = path.split("/")
    if path.startswith("/days/") and "blocks" in parts:
        position = parts.index("blocks") + 1
        block = pointer(document, "/".join(parts[:position + 1]))
        return "/".join(["", "blocks", escape(block["id"]), *parts[position + 1:]])
    if path.startswith("/blocks/"):
        return path
    value, stable = document, [""]
    try:
        for part in parts[1:]:
            if isinstance(value, list):
                value = element(value, unescape(part))
                stable.append(escape(name_of(value)) if name_of(value) is not None else part)
            else:
                value = value[unescape(part)]
                stable.append(part)
    except (KeyError, IndexError, ValueError, TypeError):
        return path
    return "/".join(stable)


def same_object(first: str, second: str) -> bool:
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


# --- what a decision decides and what a lock holds, by object (D-51) ----------------------------------------------------
# A lock path names a part of the plan; the comparator protects that part and every object it reaches. Whether a new
# user decision replaces an old lock is judged on the same objects: each part is named the way references name objects
# ('kind:id', a day by its date, the trip's own fields under 'trip'), followed by the field path inside it, so
# '/blocks/d1/start' is 'block:d1/start' and '/bookings/entry/where' is 'booking:entry/where'.

# Kinds whose ID names one object in the whole trip: the referenced kinds and blocks (lock paths address them by ID).
IDENTIFIED = (*REFERENCED, "block")


def _named(value, record) -> str | None:
    kind = record.__name__.lower()
    if kind in IDENTIFIED and isinstance(value, dict) and isinstance(value.get("id"), str):
        return f"{kind}:{value['id']}"
    if kind == "day" and isinstance(value, dict) and isinstance(value.get("date"), str):
        return f"day:{value['date']}"
    return None


def identity(path: str) -> str:
    """The part a lock path addresses, named by object: `block:d1/start`, `booking:entry/where`, `fare:F0`,
    `day:2031-05-01/plans/rec`, `trip/timezone`."""
    parts = [unescape(part) for part in path[1:].split("/")]
    if parts[0] == "blocks" and len(parts) > 1:
        return "/".join([f"block:{parts[1]}", *parts[2:]])
    name, annotation = "trip", Trip
    for part in parts:
        record = model(annotation)
        if record is not None:
            annotation = field_type(record.model_fields[part]) if part in record.model_fields else None
            name += f"/{part}"
            continue
        element_type = inner(annotation)
        record = model(element_type)
        kind = record.__name__.lower() if record is not None else None
        name = f"{kind}:{part}" if kind in IDENTIFIED or kind == "day" else f"{name}/{part}"
        annotation = element_type
    return name


def contained(value, annotation) -> set[str]:
    """The objects with a name of their own inside a value: the blocks of a day, the fares of a booking."""
    found = set()
    record = model(annotation)
    if record is not None and isinstance(value, dict):
        if (name := _named(value, record)) is not None:
            found.add(name)
        for field_name, field in record.model_fields.items():
            found |= contained(value.get(field_name), field_type(field))
    elif (element_type := inner(annotation)) is not None and isinstance(value, (list, dict)):
        for item in value.values() if isinstance(value, dict) else value:
            found |= contained(item, element_type)
    return found


def claimed(path: str, value) -> set[str]:
    """What a decision at `path` decides: the part it addresses and every named object inside its value."""
    return {identity(path), *contained(value, annotation_at(path))}


def where(block_id: str) -> str:
    """Where a block sits (date, plan, the plan's position, its track's people): fixed by a lock on the block, or on its
    day, whose value holds the order of its plans and tracks; not by a lock on its plan alone."""
    return f"where:block:{block_id}"


def held(path: str, value, recorded: dict | None) -> set[str]:
    """What a lock protects, as the comparator does: what it decides, where its block sits (where the blocks of a
    locked day sit), every object its snapshot reached (with the objects inside them) and the timezone."""
    found = claimed(path, value)
    if path.startswith("/blocks/"):
        found.add(where(unescape(path.split("/")[2])))
    if model(annotation_at(path)) in (Day, Trip) or model(inner(annotation_at(path))) is Day:
        found |= {where(name.split(":", 1)[1]) for name in contained(value, annotation_at(path)) if name.startswith("block:")}
    for key, record in ((recorded or {}).get("references") or {}).items():
        found.add(key)
        if record is not None and key.split(":", 1)[0] in MODELS:
            found |= contained(record, MODELS[key.split(":", 1)[0]])
    if recorded and "timezone" in recorded:
        found.add("trip/timezone")
    return found


def covers(claims: set[str], parts: set[str]) -> bool:
    """Whether a decision deciding `claims` decides each of `parts` whole (the same part or one that contains it)."""
    return all(any(part == claim or part.startswith(claim + "/") for claim in claims) for part in parts)


def reach(claims: set[str], path: str, value, recorded: dict | None) -> str | None:
    """How a new user decision deciding `claims` meets the lock at `path`: "covers" when it decides everything the lock
    decided (the lock may be replaced; what the lock reached is decided again with it), "touches" when it decides only a
    part of what the lock protects (a field inside it, or a field of an object it reaches), None when they share
    nothing (both stand)."""
    if covers(claims, claimed(path, value)):
        return "covers"
    if any(same_object(claim, part) for claim in claims for part in held(path, value, recorded)):
        return "touches"
    return None


# --- conservation across a commit (D-52) --------------------------------------------------------------------------------
# The commit checks the plan it would store against the user locks of the version it is based on, whatever the ledger
# recorded in between: each protected part that changed needs the user's authority for it, and each one still in the plan
# stays protected by a current lock. These name parts the way `held` does.

def protects(names: set[str], part: str) -> bool:
    """Whether a set of protected names covers `part` (the same name or one that contains it)."""
    return any(part == name or part.startswith(name + "/") for name in names)


def decides(path: str, value, before: dict | None) -> set[str]:
    """What a user decision at `path` decides: what it claims, and the named objects the part held in the version it is
    based on, so deciding a day again also decides a block it no longer has."""
    found = claimed(path, value)
    if before is not None:
        try:
            found |= contained(pointer(before, path), annotation_at(path))
        except ValueError:
            pass
    return found


def required(document: dict, path: str) -> set[str]:
    """What the lock at `path` would protect in this document; empty when the part is gone."""
    try:
        recorded = snapshot(document, path)
    except ValueError:
        return set()
    return held(path, pointer(document, path), recorded)


def changed_items(path: str, changes: list[str]) -> set[str]:
    """`lock_changes` names (`value/start`, `date`, `booking:entry/where`, `timezone`, `missing`) as protected names."""
    found = set()
    for change in changes:
        if change == "value" or change in ("missing", "references", "snapshot"):
            found.add(identity(path))
        elif change.startswith("value/"):
            found.add(identity(path + change[len("value"):]))
        elif change in WHERE[:-1]:
            found.add(where(unescape(path.split("/")[2])) if path.startswith("/blocks/") else identity(path))
        elif change == "timezone":
            found.add("trip/timezone")
        else:
            key, _, rest = change.partition("/")
            kind, object_id = key.split(":", 1)
            lists = dict(place="places", booking="bookings", person="people")
            found.add(identity(f"/{lists[kind]}/{escape(object_id)}/{rest}".rstrip("/")) if kind in lists else change)
    return found


def field_name(document: dict, target: str, field: str) -> str:
    """The protected name of a report's or correction's field: `booking:entry/where`, `block:d1/start`, `trip/timezone`,
    `day:2031-05-01/area`, an anchor's inside its day."""
    kind, object_id = target.split(":", 1)
    if kind == "trip":
        return f"trip/{field}"
    if kind == "anchor":
        day = next((day["date"] for day in document.get("days", []) for anchor in day.get("anchors", []) if anchor.get("id") == object_id), None)
        return f"day:{day}/anchors/{object_id}/{field}"
    return f"{kind}:{object_id}/{field}"


def path_of(document: dict, name: str) -> str | None:
    """A lock path for a protected name, when one addresses exactly it: a trip field, a whole place, booking, fare or
    person. None for where a block sits, which only a lock on the block or its day holds."""
    if name.startswith("trip/") and name.count("/") == 1 and name[5:] in Trip.model_fields:
        return "/" + name[5:]
    kind, _, object_id = name.partition(":")
    if "/" in object_id or kind not in ("place", "booking", "person", "fare"):
        return None
    if kind == "fare":
        owner = next((booking["id"] for booking in document.get("bookings", []) for fare in booking.get("fares", []) if fare.get("id") == object_id), None)
        return f"/bookings/{escape(owner)}/fares/{escape(object_id)}" if owner else None
    return f"/{dict(place='places', booking='bookings', person='people')[kind]}/{escape(object_id)}"


# --- which decisions lock, and what a plan change is (D-53) -------------------------------------------------------------
# Only what the user marked a must locks: the comparator, the admission, the order, the single use and the conservation
# above apply to the paths `binding` names. The user's other decisions stay as records; the agent may change what they
# decided when it says why, and each change is written to the version's change log.

def user_part(decision: dict, path: str) -> bool:
    """Whether the user decided `path` of this decision: the whole decision, or a part of an agent's marked user."""
    return decision["decided_by"] == "user" or (decision.get("parts") or {}).get(path) == "user"


def binding(decision: dict, path: str) -> bool:
    """Whether the decision locks `path` against the agent: the user decided it and marked it a must. A decision stored
    before the mark existed has none and locks nothing."""
    return bool(decision.get("must")) and user_part(decision, path)


def _without(record: dict, *names: str) -> dict:
    return {name: value for name, value in record.items() if name not in names}


def plan_objects(document: dict) -> dict[str, tuple[str, dict]]:
    """Every object of a plan by the target a report names ('trip:<id>', 'person:u1', 'place:gym-a', 'booking:entry',
    'fare:F0', 'day:2031-03-01', 'anchor:a1', 'block:d1'), with the name its fields have in `held` and its fields. A
    booking lists its fares and a day its anchors by ID (each is an object of its own); a day lists its plans' ID, name
    and note; a block's `position` is where it sits (`blocks`)."""
    found = {f"trip:{document.get('id')}": ("trip", _without(document, "people", "places", "bookings", "days"))}
    for kind, key in (("person", "people"), ("place", "places")):
        for record in document.get(key, []):
            found[f"{kind}:{record['id']}"] = (f"{kind}:{record['id']}", dict(record))
    for booking in document.get("bookings", []):
        fares = booking.get("fares", [])
        found[f"booking:{booking['id']}"] = (f"booking:{booking['id']}", dict(_without(booking, "fares"), fares=[fare["id"] for fare in fares]))
        for fare in fares:
            found[f"fare:{fare['id']}"] = (f"fare:{fare['id']}", dict(fare))
    for day in document.get("days", []):
        anchors = day.get("anchors", [])
        found[f"day:{day['date']}"] = (f"day:{day['date']}", dict(
            _without(day, "anchors", "plans"), anchors=[anchor["id"] for anchor in anchors],
            plans=[dict(id=plan.get("id"), name=plan.get("name"), note=plan.get("note")) for plan in day.get("plans", [])]))
        for anchor in anchors:
            found[f"anchor:{anchor['id']}"] = (f"day:{day['date']}/anchors/{escape(anchor['id'])}", dict(anchor))
    for block_id, (position, block) in blocks(document).items():
        found[f"block:{block_id}"] = (f"block:{block_id}", dict(block, position=position))
    return found


def _same(field: str, old, new) -> bool:
    if field == "subject" and isinstance(old, list) and isinstance(new, list):
        return sorted(old) == sorted(new)  # people have no order
    return old == new


def plan_changes(before: dict | None, after: dict) -> list[dict]:
    """What a new plan changes, one line per field of each object: {target, field, change, old, new, name}; `change` is
    changed, added or removed, and an object only one side has is one line with field None. `name` is the field's name
    in `held` and `changed_items` (`block:d1/start`, `where:block:d1` for a block's position, `trip/timezone`), for the
    caller to match against decisions; it is not shown. A first plan is one line for the trip."""
    if before is None:
        return [dict(target=f"trip:{after.get('id')}", field=None, change="added", old=None, new=None, name="trip")]
    old_objects, new_objects = plan_objects(before), plan_objects(after)
    lines = []
    for target in sorted(old_objects.keys() | new_objects.keys()):
        if target not in new_objects or target not in old_objects:
            name = (old_objects.get(target) or new_objects[target])[0]
            lines.append(dict(target=target, field=None, change="removed" if target not in new_objects else "added",
                              old=None, new=None, name=name))
            continue
        (name, old), (_, new) = old_objects[target], new_objects[target]
        for field in sorted(old.keys() | new.keys()):
            if not _same(field, old.get(field), new.get(field)):
                shown = where(target.split(":", 1)[1]) if target.startswith("block:") and field == "position" else (
                    f"trip/{field}" if name == "trip" else f"{name}/{field}")
                lines.append(dict(target=target, field=field, change="changed", old=old.get(field), new=new.get(field), name=shown))
    return lines


# --- the trip model as types ------------------------------------------------------------------------------------------

def bare(annotation):
    """The annotation without Annotated and Optional."""
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, UnionType) and len(arguments := [entry for entry in get_args(annotation) if entry is not type(None)]) == 1:
            annotation = arguments[0]
        else:
            return annotation


def model(annotation) -> type[BaseModel] | None:
    annotation = bare(annotation)
    return annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None


def inner(annotation):
    """The annotation of the elements of a list, tuple or dict value; None for anything else."""
    annotation = bare(annotation)
    if get_origin(annotation) in (list, tuple, set):
        return get_args(annotation)[0]
    if get_origin(annotation) is dict:
        return get_args(annotation)[1]
    return None


def annotation_at(path: str):
    """The model's annotation for the value a lock path addresses; None when the model has no such part."""
    parts = [unescape(part) for part in path[1:].split("/")]
    annotation, parts = (Block, parts[2:]) if parts[0] == "blocks" else (Trip, parts)
    for part in parts:
        record = model(annotation)
        if record is not None:
            if part not in record.model_fields:
                return None
            annotation = field_type(record.model_fields[part])
        elif (annotation := inner(annotation)) is None:
            return None
    return annotation


def targets(value, annotation):
    """'kind:id' of every trip object the value refers to, through nested models, lists and dict values. A description
    field is not followed: changing it does not change the choice, so neither does what it names."""
    if value is None:
        return
    kind = reference_kind(annotation) if model(annotation) is None and inner(annotation) is None else None
    if kind is not None:
        if kind in REFERENCED and isinstance(value, str):
            yield f"{kind}:{value}"
        return
    record = model(annotation)
    if record is not None and isinstance(value, dict):
        for name, field in record.model_fields.items():
            if not described(record.__name__, name):
                yield from targets(value.get(name), field_type(field))
    elif (element_type := inner(annotation)) is not None:
        for item in value.values() if isinstance(value, dict) else value if isinstance(value, list) else ():
            yield from targets(item, element_type)


def objects(document: dict) -> dict[str, tuple[type[BaseModel], dict, str | None]]:
    """Every object a reference can name, by 'kind:id': its model, its record, and the key of the object it is a line of
    (a fare's booking), found by walking the trip model rather than by naming its lists."""
    found = {}

    def visit(value, annotation, owner):
        record = model(annotation)
        if record is not None and isinstance(value, dict):
            kind = record.__name__.lower()
            key = f"{kind}:{value['id']}" if kind in REFERENCED and isinstance(value.get("id"), str) else None
            if key is not None:
                found[key] = (record, value, owner)
            for name, field in record.model_fields.items():
                visit(value.get(name), field_type(field), key or owner)
        elif (element_type := inner(annotation)) is not None and isinstance(value, (list, dict)):
            for item in value.values() if isinstance(value, dict) else value:
                visit(item, element_type, owner)

    visit(document, Trip, None)
    return found


def kept(value, annotation):
    """The protected part of a record: description fields removed, at every nesting level."""
    record = model(annotation)
    if record is not None and isinstance(value, dict):
        return {name: kept(value.get(name), field_type(field)) for name, field in record.model_fields.items()
                if not described(record.__name__, name)}
    if (element_type := inner(annotation)) is not None and isinstance(value, (list, dict)):
        return {key: kept(item, element_type) for key, item in value.items()} if isinstance(value, dict) else [kept(item, element_type) for item in value]
    return value


def accepts_none(annotation) -> bool:
    try:
        adapter(annotation).validate_python(None)
        return True
    except (ValidationError, TypeError):
        return False


def field_value(value: dict, name: str, field):
    """A record's field as the model reads it. A field the record does not have (it was written before the model had
    the field, as a lock snapshot taken before an upgrade is), or None where the model does not take None (what `kept`
    records for such a field), is the field's default in the form the plan stores it, as validating the record would
    give. A value the record does have is itself, so a real change still differs."""
    if name in value and not (value[name] is None and not accepts_none(field_type(field))):
        return value[name]
    if field.is_required():
        return value.get(name)
    return normalized(field.get_default(call_default_factory=True), field_type(field))


def differences(before, after, annotation, at: str) -> list[str]:
    """Where the protected parts of two values differ, as paths below `at` (`booking:entry/status`, `value/start`).
    Description fields are skipped; lists of people compare as sets (a track's or booking's people have no order).
    Lists of records compare in order, each element named by its ID where both sides have the same one; any other list,
    map or tuple (coordinates), and a list of records of another length, is one value. A field a record lacks reads as
    its default (`field_value`: records and snapshots from before a field existed). Nothing else is exempt."""
    record = model(annotation)
    if record is not None and isinstance(before, dict) and isinstance(after, dict):
        found = [path for name, field in record.model_fields.items() if not described(record.__name__, name)
                 for path in differences(field_value(before, name, field), field_value(after, name, field), field_type(field), f"{at}/{name}")]
        unknown = set(before) - set(record.model_fields), set(after) - set(record.model_fields)
        if unknown[0] != unknown[1] or any(before[key] != after[key] for key in unknown[0]):
            found.append(at)
        return found
    element_type = inner(annotation)
    if element_type is not None and isinstance(before, list) and isinstance(after, list):
        if model(element_type) is not None and len(before) == len(after):
            return [path for index, (old, new) in enumerate(zip(before, after))
                    for path in differences(old, new, element_type, f"{at}/{escape(name_of(old)) if name_of(old) is not None and name_of(old) == name_of(new) else index}")]
        if reference_kind(element_type) == "person" and model(element_type) is None:
            return [] if sorted(before) == sorted(after) else [at]
    return [] if before == after else [at]


@lru_cache(maxsize=None)
def adapter(annotation) -> TypeAdapter:
    return TypeAdapter(annotation)


def normalized(value, annotation):
    """A locked value as the plan stores it (defaults filled in), so the host may send the block it was shown."""
    if annotation is None:
        return value
    try:
        typed = adapter(annotation)
        return typed.dump_python(typed.validate_python(value), mode="json")
    except (ValidationError, TypeError):
        return value


# --- what a lock holds ------------------------------------------------------------------------------------------------

def resolve(document: dict, path: str) -> dict:
    """The selection a lock path makes in this document: the locked value and its annotation, where a block sits
    (block paths only), and `references`, the protected part of every object reached from the value, keyed 'kind:id'
    (None for a key that names nothing). Raises ValueError when the path is missing."""
    value = pointer(document, path)
    annotation = annotation_at(path)
    index = objects(document)
    found = dict(value=value, annotation=annotation)
    waiting = list(targets(value, annotation))
    if path.startswith("/blocks/"):
        where, _ = blocks(document)[unescape(path.split("/")[2])]
        found.update(where, timezone=document["timezone"])
        waiting += [f"person:{person_id}" for person_id in where["subject"]]
    record = model(annotation)
    own = index.get(f"{record.__name__.lower()}:{value.get('id')}") if record is not None and isinstance(value, dict) else None
    if own is not None and own[2] is not None:
        waiting.append(own[2])
    references = {}
    while waiting:
        key = waiting.pop(0)
        if key in references:
            continue
        kind, record_value, owner = index.get(key, (None, None, None))
        references[key] = None if kind is None else kept(record_value, kind)
        if kind is not None:
            waiting += [*targets(record_value, kind), *([owner] if owner else [])]
    found["references"] = references
    return found


def snapshot(document: dict, path: str) -> dict:
    """What a lock depends on, resolved in this document, in the form stored in Decision.placement."""
    selection = resolve(document, path)
    return dict({key: selection[key] for key in WHERE if key in selection}, references=selection["references"], graph=SHAPE)


def recorded_snapshot(recorded: dict | None) -> bool:
    """Whether a stored lock snapshot has the current shape. B3c stored date, plan name and people; B3d stored the
    identity fields of the objects one reference away; B3e (`graph: reachable`) left out fields it exempted, such as a
    booking's channel, deadline and refund terms, which are protected now."""
    return recorded is not None and recorded.get("graph") == SHAPE


def lock_changes(document: dict, path: str, expected, recorded: dict | None) -> list[str]:
    """What a candidate changed in one user lock; empty when the user's choice still holds. Names each changed part:
    `value` or a field path inside the locked value (`value/start`), date, plan, rank (the plan's position), subject
    (track people), timezone, a field path of a recorded object (`place:gym-a/coords`), `references` when the set of
    objects reached changed without any of these, `missing` when the path is gone, or `snapshot` for a stored snapshot
    of an earlier shape that could not be rebound (it fails closed)."""
    try:
        actual = resolve(document, path)
    except ValueError:
        return ["missing"]
    changes = differences(normalized(expected, actual["annotation"]), actual["value"], actual["annotation"], "value")
    if recorded is None:
        return changes
    changes += [key for key in WHERE if (key in actual or key in recorded) and actual.get(key) != recorded.get(key)]
    if not recorded_snapshot(recorded):
        return changes or ["snapshot"]
    before, after = recorded["references"], actual["references"]
    for key, record in before.items():
        if key in after:
            changes += differences(record, after[key], MODELS[key.split(":", 1)[0]], key)
    if not changes and before.keys() != after.keys():
        changes.append("references")
    return changes


# --- changing a protected field through the ledger ---------------------------------------------------------------------

PARTIAL = object()


def amend(path: str, expected, recorded: dict | None, target: str, field: str, new) -> tuple[list, object, dict | None]:
    """Set `field` of the object `target` ('kind:id', a day by its date, the trip by its ID) to `new` wherever the lock
    at `path` holds it: in the locked value, in the recorded objects (a fare also inside its booking), the path itself
    when it addresses that field, and the snapshot's timezone for the trip's. Returns the values held before (empty when
    the lock does not hold the field; PARTIAL when the path addresses only a part of it, which the caller refuses) and
    the new locked value and snapshot. The caller checks the held values and records the change as a new decision."""
    kind, object_id = target.split(":", 1)
    annotation = annotation_at(path)
    expected, recorded = deepcopy(normalized(expected, annotation)), deepcopy(recorded)
    held = []

    def visit(value, annotation):
        record = model(annotation)
        if record is not None and isinstance(value, dict):
            if record.__name__.lower() == kind and name_of(value) == object_id and field in record.model_fields:
                held.append(field_value(value, field, record.model_fields[field]))  # a field from after the lock: its default
                value[field] = deepcopy(new)
            for name, entry in record.model_fields.items():
                visit(value.get(name), field_type(entry))
        elif (element_type := inner(annotation)) is not None and isinstance(value, (list, dict)):
            for item in value.values() if isinstance(value, dict) else value:
                visit(item, element_type)

    parts = [unescape(part) for part in path[1:].split("/")]
    for position, part in enumerate(parts):
        # The prefix before `part` is an object when the model has one there; list elements in lock paths are named by
        # ID (stable_path), so the prefix's last part is that object's ID. "/blocks" alone is not an object.
        if part != field or (parts[0] == "blocks" and position == 1):
            continue
        owner = Trip if position == 0 else model(annotation_at("/" + "/".join(escape(entry) for entry in parts[:position])))
        if owner is None or owner.__name__.lower() != kind or (position and parts[position - 1] != object_id):
            continue
        held.append(expected if position == len(parts) - 1 else PARTIAL)
        if position == len(parts) - 1:
            expected = deepcopy(new)
    visit(expected, annotation)
    if recorded is not None:
        for key, record in (recorded.get("references") or {}).items():
            if record is not None:
                visit(record, MODELS[key.split(":", 1)[0]])
        if kind == "trip" and field == "timezone" and "timezone" in recorded:
            held.append(recorded["timezone"])
            recorded["timezone"] = new
    return held, expected, recorded
