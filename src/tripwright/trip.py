from __future__ import annotations

import re
from datetime import date as Date, datetime, timezone
from functools import wraps
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ref:
    """Marks a string as the ID of another object. `kind` is the referenced model's name in lower case ("place" is a
    Place in Trip.places), "area" for a name in Trip.areas, or "evidence" for an evidence record kept outside the trip
    document. Every reference field carries one: user locks follow them to find what a choice depends on
    (locks.py), and tests/core/test_b3e_reference_graph.py fails when a string field is neither marked nor declared
    plain."""
    __slots__ = ("kind",)

    def __init__(self, kind: str):
        self.kind = kind

    def __repr__(self) -> str:
        return f"Ref({self.kind!r})"


PersonId = Annotated[str, Ref("person")]
PlaceId = Annotated[str, Ref("place")]
BookingId = Annotated[str, Ref("booking")]
FareId = Annotated[str, Ref("fare")]
AreaName = Annotated[str, Ref("area")]
EvidenceId = Annotated[str, Ref("evidence")]


def field_type(field):
    """A model field's annotation with its top-level Annotated metadata put back (pydantic moves it to `metadata`)."""
    return Annotated[(field.annotation, *field.metadata)] if field.metadata else field.annotation


def reference_kind(annotation) -> str | None:
    """The Ref kind of a string annotation, looking through Optional, lists, tuples and dict values; None if plain."""
    origin = get_origin(annotation)
    if origin is Annotated:
        base, *metadata = get_args(annotation)
        return next((marker.kind for marker in metadata if isinstance(marker, Ref)), None) or reference_kind(base)
    if origin in (Union, UnionType, list, tuple, set):
        return next(filter(None, (reference_kind(argument) for argument in get_args(annotation) if argument is not Ellipsis)), None)
    if origin is dict:
        return reference_kind(get_args(annotation)[1])
    return None


def trip_models() -> list[type[Model]]:
    """The models of the trip document (ledger and query records subclass Model elsewhere)."""
    return [model for model in Model.__subclasses__() if model.__module__ == __name__]


def reference_fields(models=None) -> dict[tuple[str, str], str]:
    """Every reference field of the trip models as {(model name, field name): kind}, read from the Ref markers."""
    return {(model.__name__, name): kind for model in models or trip_models() for name, field in model.model_fields.items()
            if (kind := reference_kind(field_type(field))) is not None}


def readable(validator):
    """Pydantic reports only ValueError and AssertionError as validation errors; a lookup or arithmetic error inside a
    validator would escape as a crash whose reason no client can read, so convert it."""
    @wraps(validator)
    def checked(self):
        try:
            return validator(self)
        except (LookupError, ArithmeticError) as error:
            raise ValueError(f"{type(self).__name__}: {error.args[0] if error.args else error}") from None
    return checked


JSON_TYPES = {dict: "object", list: "array", tuple: "array", str: "string", bool: "boolean", int: "integer", float: "number",
              type(None): "null"}
EXPECTED = {"model_type": "an object", "model_attributes_type": "an object", "dict_type": "an object", "list_type": "an array",
            "tuple_type": "an array", "string_type": "a string", "int_type": "an integer", "int_parsing": "an integer",
            "int_from_float": "an integer", "float_type": "a number", "float_parsing": "a number", "bool_type": "true or false",
            "bool_parsing": "true or false", "greater_than": "a positive number"}


def json_type(value) -> str:
    return JSON_TYPES.get(type(value), type(value).__name__)


def input_problems(error, root: str, shapes: dict[str, str] | None = None) -> list[str]:
    """Each problem of a pydantic ValidationError as one readable line with the field path from `root` (list positions
    included, e.g. `usages.0.reason`), what the field needs and what was sent. `shapes` adds the expected keys of an
    object field; its keys write list positions as `*`. Tool boundaries share it so every refusal reads the same."""
    problems = []
    for detail in error.errors():
        path = ".".join([root, *map(str, detail["loc"])])
        pattern = ".".join([root, *("*" if isinstance(part, int) else str(part) for part in detail["loc"])])
        message = detail["msg"]
        if detail["type"] == "missing":
            problems.append(f"{path} is required")
        elif detail["type"] == "extra_forbidden":
            problems.append(f"{path} is not a known field")
        elif detail["type"] == "value_error":
            problems.append(f"{path}: {message.removeprefix('Value error, ')}")
        elif detail["type"] in EXPECTED or message.startswith("Input should be "):
            expected = EXPECTED.get(detail["type"], message.removeprefix("Input should be ").rstrip("."))
            if detail["type"] in ("model_type", "dict_type") and pattern in (shapes or {}):
                expected += " " + shapes[pattern]
            problems.append(f"{path} must be {expected}, got {json_type(detail['input'])}")
        else:
            problems.append(f"{path}: {message}, got {json_type(detail['input'])}")
    return problems


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown IANA timezone: {name!r}; use a name such as 'Europe/Vienna'") from None


def minutes(value: str) -> int:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Time must be HH:MM")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone offset")
    return parsed


def local_instant(day: str, clock: str, zone: str) -> datetime | None:
    local = datetime.fromisoformat(f"{day}T{clock}")
    timezone_value = ZoneInfo(zone)
    candidates = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=timezone_value, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(timezone_value).replace(tzinfo=None) == local:
            candidates.add(candidate)
    return next(iter(candidates)) if len(candidates) == 1 else None


class Person(Model):
    id: str
    name: str


WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
DAY = "(?:Mo|Tu|We|Th|Fr|Sa|Su)"
SPAN = r"(?:[01]\d|2[0-3]):[0-5]\d-(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)"
OPENING_RULE = re.compile(rf"({DAY}(?:-{DAY})?(?:,{DAY}(?:-{DAY})?)*) ({SPAN}(?:,{SPAN})*)")


def opening_rule(value: str) -> str:
    """One line of opening hours (A7): weekdays, a space, time spans: 'Mo-Fr 06:00-22:00', 'Sa,Su 08:00-20:00,21:00-23:00',
    'Mo-Su 00:00-24:00'. A span that ends at or before its start runs past midnight ('Fr 18:00-02:00')."""
    if not isinstance(value, str) or not OPENING_RULE.fullmatch(value.strip()):
        raise ValueError(f"must be weekdays and times like 'Mo-Fr 06:00-22:00' or 'Sa,Su 08:00-20:00', got {value!r}")
    return value.strip()


def opening_spans(rule: str) -> tuple[set[int], list[tuple[int, int]]]:
    """The weekdays (0 = Monday) and the (open, close) minutes of one opening rule; close 1440 is midnight."""
    days_text, spans_text = rule.split(" ", 1)
    days: set[int] = set()
    for part in days_text.split(","):
        first, _, last = part.partition("-")
        start, end = WEEKDAYS.index(first), WEEKDAYS.index(last or first)
        days |= {day % 7 for day in range(start, start + (end - start) % 7 + 1)}
    spans = []
    for span in spans_text.split(","):
        opens, closes = span.split("-")
        spans.append((minutes(opens), 1440 if closes == "24:00" else minutes(closes)))
    return days, spans


def opening_at(rules: list[str], weekday: int, minute: int) -> tuple[bool | None, int | None]:
    """Whether a place with these rules is open on `weekday` (0 = Monday) at `minute` past local midnight, and when it
    closes (minutes past that midnight; above 1440 the next day). None when no rule is stored."""
    if not rules:
        return None, None
    for rule in rules:
        days, spans = opening_spans(rule)
        for opens, closes in spans:
            overnight = closes <= opens
            if weekday in days and opens <= minute < (closes + 1440 if overnight else closes):
                return True, closes + 1440 if overnight else closes
            if overnight and (weekday - 1) % 7 in days and minute < closes:
                return True, closes
    return False, None


OpeningRule = Annotated[str, AfterValidator(opening_rule)]


class Place(Model):
    id: str
    name: str
    name_local: str
    area: AreaName
    kind: str = "other"
    subject: list[PersonId] = Field(default_factory=list)
    priority: Literal["must", "recommended", "optional"] = "optional"
    reason: str | None = None
    coords: tuple[float, float] | None = None
    hours: str | None = None
    price: dict[str, Any] | None = None
    links: list[str] = Field(default_factory=list)
    checked: str | None = None
    notes: str | None = None
    rain_ok: bool | None = None
    transit_id: str | None = None
    opening_hours: list[OpeningRule] = Field(default_factory=list, description=(
        "When it is open, one rule per line: weekdays (Mo Tu We Th Fr Sa Su) and local times, e.g. 'Mo-Fr 06:00-22:00', "
        "'Sa,Su 08:00-20:00', 'Mo-Su 00:00-24:00'; a span ending at or before its start runs past midnight "
        "('Fr 18:00-02:00'). Empty: not known. Copy it from the operator's page and set hours_source."))
    hours_source: dict[str, str] | None = Field(default=None, description=(
        "Where opening_hours was read: {url, evidence_id}; evidence_id is the web_evidence record of the page's sentence."))

    @model_validator(mode="after")
    @readable
    def source_keys(self):
        if self.hours_source is not None and (not self.hours_source or set(self.hours_source) - {"url", "evidence_id"}):
            raise ValueError("hours_source holds url and evidence_id")
        return self


class Fare(Model):
    id: str
    segment: str
    ticket: str
    subject: list[PersonId]
    amount_per_person: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    date: Date | None = None
    included_in: BookingId | None = None
    alternative: bool = False
    estimated: bool = False

    @model_validator(mode="after")
    @readable
    def money(self):
        if self.amount_per_person is not None and not self.currency:
            raise ValueError("A known fare needs its currency")
        return self


class Booking(Model):
    id: str
    date: Date
    kind: str
    what: str
    subject: list[PersonId]
    status: Literal["to_buy", "to_book", "to_contact", "bought", "booked", "contacted", "on_site", "cancelled"]
    place: PlaceId | None = None
    due: Date | None = None
    why_now: str | None = None
    where: list[str] = Field(default_factory=list)
    if_missed: str | None = None
    contact: dict[str, str] | None = None
    fares: list[Fare] = Field(default_factory=list)


class Block(Model):
    id: str
    start: str
    end: str | None = None
    kind: Literal["visit", "travel", "meal", "flex", "anchor", "reserve"]
    place: PlaceId | None = None
    options: list[PlaceId] = Field(default_factory=list)
    text: str | None = None
    certainty: Literal["fixed", "estimated", "unknown"] = "estimated"
    mode: str | None = None
    source: str | None = None
    app: str | None = None
    fare: FareId | None = None
    booking: BookingId | None = None
    note: str | None = None
    transfer_minutes: int | None = Field(default=None, ge=0)
    walk_minutes: int | None = Field(default=None, ge=0)
    baggage_holder: PersonId | None = Field(default=None, description=(
        "Person ID who must do this per-person task, such as collecting luggage or picking up tickets; the block must sit "
        "in a track containing that person. Set it whenever the task belongs to one person: free text is not checked."))
    equipment: list[str] = Field(default_factory=list)
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
    critical_facts: list[str] = Field(default_factory=list)
    origin: PlaceId | None = None
    destination: PlaceId | None = None
    line: str | None = None

    @model_validator(mode="after")
    @readable
    def times(self):
        start = minutes(self.start)
        if self.end is None and self.kind != "anchor":
            raise ValueError("Only an anchor block may omit end")
        if self.end is not None and minutes(self.end) <= start:
            raise ValueError("Block end must be after start; split overnight blocks across dates")
        if not (self.place or self.options or self.text):
            raise ValueError("Block requires a place, options, or text")
        return self


class Track(Model):
    subject: list[PersonId] = Field(min_length=1)
    blocks: list[Block] = Field(default_factory=list)


class Plan(Model):
    id: str | None = Field(default=None, description=(
        "Stable plan ID, unique within its day. Keep it when editing a plan; omit it for a new plan and the service "
        "assigns one. User locks follow this ID and the plan's position among the day's alternatives, never its name."))
    name: str
    note: str | None = None
    tracks: list[Track] = Field(default_factory=list)


class Anchor(Model):
    id: str
    time: str
    text: str
    subject: list[PersonId] = Field(min_length=1)
    place: PlaceId | None = None
    booking: BookingId | None = None
    buffer_minutes: int = Field(default=0, ge=0)
    travel_minutes: int | None = Field(default=None, ge=0)
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
    consequence: str = "Missing this node disrupts the onward plan."
    alternative: str = "Confirm locally and retain an earlier departure option."
    app: str | None = None
    destination: PlaceId | None = None
    line: str | None = None
    deadline: Literal["external", "plan"] | None = Field(default=None, description=(
        "What kind of time this is: external = set by an operator or outside rule (a counter closes, the last train "
        "leaves); plan = the user's or the plan's own target that keeps a margin before an external deadline (be at the "
        "counter by then). Answers treat only external times as the limit. Omitted means not marked."))
    where: Literal["on_site", "online"] | None = Field(default=None, description=(
        "Where the node is done: online = on a phone or computer wherever the person is (online check-in closes, an app "
        "deadline); nobody has to be at place by then, so it is never a time to arrive or a latest departure. on_site or "
        "omitted = be at place by time."))

    @model_validator(mode="after")
    @readable
    def time_format(self):
        minutes(self.time)
        if self.where == "online" and self.travel_minutes is not None:
            raise ValueError("an online hard node (where: online) needs no travel: drop travel_minutes")
        return self


class Day(Model):
    date: Date
    title: str
    area: AreaName
    pace: str = "relaxed"
    base: PlaceId | None = None
    sunset: str | None = None
    anchors: list[Anchor] = Field(default_factory=list)
    plans: list[Plan] = Field(default_factory=list, max_length=3)
    candidates: dict[str, list[PlaceId]] = Field(default_factory=dict)
    rain_plan: str | None = None
    notes: str | None = None


class Trip(Model):
    id: str
    title: str
    start: Date
    end: Date
    timezone: str
    areas: list[str]
    people: list[Person] = Field(min_length=1)
    default_pace: str = "relaxed"
    budgets: dict[str, float] = Field(default_factory=dict)
    places: list[Place] = Field(default_factory=list)
    bookings: list[Booking] = Field(default_factory=list)
    days: list[Day] = Field(default_factory=list)

    @model_validator(mode="after")
    @readable
    def references(self):
        zone(self.timezone)
        if self.end < self.start:
            raise ValueError("Trip end precedes start")
        people = {person.id for person in self.people}
        places = {place.id for place in self.places}
        if len(people) != len(self.people) or len(places) != len(self.places):
            raise ValueError("Duplicate person or place id")
        if len({day.date for day in self.days}) != len(self.days):
            raise ValueError("Duplicate day")
        booking_ids = {booking.id for booking in self.bookings}
        fares = [fare for booking in self.bookings for fare in booking.fares]
        fare_ids = {fare.id for fare in fares}
        if len(booking_ids) != len(self.bookings) or len(fare_ids) != len(fares):
            raise ValueError("Duplicate booking or fare id")
        block_ids = set()
        anchor_ids = set()

        def subjects(subject):
            if not subject or not set(subject) <= people or len(set(subject)) != len(subject):
                raise ValueError("Unknown, empty, or repeated subject")

        def place_reference(place):
            if place is not None and place not in places:
                raise ValueError(f"Unknown place: {place}")

        for place in self.places:
            if place.area not in self.areas:
                raise ValueError("Place area is outside trip areas")
            if place.subject:
                subjects(place.subject)
            if place.coords and not (-90 <= place.coords[0] <= 90 and -180 <= place.coords[1] <= 180):
                raise ValueError("Invalid coordinates")
        for booking in self.bookings:
            subjects(booking.subject)
            place_reference(booking.place)
            if not self.start <= booking.date <= self.end:
                raise ValueError("Booking outside trip dates")
            if booking.kind in ("reservation", "contact") and not (booking.contact or {}).get("name"):
                raise ValueError("Contact or reservation requires a contact name")
            if booking.status in ("to_buy", "to_book", "to_contact") and not booking.why_now:
                raise ValueError("Unfinished booking requires why_now")
            for fare in booking.fares:
                subjects(fare.subject)
                if not set(fare.subject) <= set(booking.subject):
                    raise ValueError("Fare holder is outside booking holders")
                if fare.included_in and fare.included_in not in booking_ids:
                    raise ValueError("Unknown included fare booking")
        for day in self.days:
            if not self.start <= day.date <= self.end or day.area not in self.areas:
                raise ValueError("Day outside configured dates or areas")
            place_reference(day.base)
            for candidates in day.candidates.values():
                for candidate in candidates:
                    place_reference(candidate)
            for anchor in day.anchors:
                if anchor.id in anchor_ids:
                    raise ValueError("Duplicate hard-node id")
                anchor_ids.add(anchor.id)
                subjects(anchor.subject)
                place_reference(anchor.place)
                place_reference(anchor.destination)
                if anchor.booking and anchor.booking not in booking_ids:
                    raise ValueError("Unknown hard-node booking")
            plan_ids = [plan.id for plan in day.plans if plan.id is not None]
            if len(set(plan_ids)) != len(plan_ids):
                raise ValueError(f"Duplicate plan id on {day.date}")
            for plan in day.plans:
                for track in plan.tracks:
                    subjects(track.subject)
                    for block in track.blocks:
                        if block.id in block_ids:
                            raise ValueError("Block ids must be unique across all plans")
                        block_ids.add(block.id)
                        place_reference(block.place)
                        place_reference(block.origin)
                        place_reference(block.destination)
                        for option in block.options:
                            place_reference(option)
                        if block.fare and block.fare not in fare_ids:
                            raise ValueError("Unknown fare reference")
                        if block.booking and block.booking not in booking_ids:
                            raise ValueError("Unknown block booking")
                        if block.baggage_holder and block.baggage_holder not in people:
                            raise ValueError("Unknown baggage holder")
        return self
