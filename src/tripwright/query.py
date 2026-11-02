from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from tripwright.providers.base import (
    GeocodeProvider, Itinerary, Leg, Place, ProviderUnavailable, Sourced,
    TransitProvider, WeatherProvider,
)
from tripwright.evidence import WEB_TYPES, locate, outdoor, request_for, snapshot, supports, web_record
from tripwright.providers import connections as views
from tripwright.providers.common import request_scope
from tripwright.providers.pages import PageReader, PageUnavailable, public_url, quote_on
from tripwright.providers.transitous import TransitousProvider
from tripwright.providers.openmeteo import OpenMeteoProvider
from datetime import date as Date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from tripwright.storage import Store, clock, identifier, now
from tripwright.trip import Model, aware, input_problems, json_type, local_instant, minutes, opening_at, zone


class Target(Model):
    block_id: str | None = None
    anchor_id: str | None = None
    date: str
    fact: Literal["timetable", "hard_node", "weather", "opening_hours"]
    place_id: str | None = None
    draft_id: str | None = None


class WeatherPlace(Model):
    name: str
    lat: float
    lon: float
    id: str | None = None


def solver_clock(value: str) -> str:
    """A clock time the day solver reads: HH:MM, or HH:MM(+1) for the next day."""
    from tripwright.solver.day import ServiceError, mins

    try:
        mins(value)
    except ServiceError as error:
        raise ValueError(error.message) from None
    return value


def solver_date(value: str) -> str:
    try:
        if 2000 <= Date.fromisoformat(value).year <= 2100:
            return value
    except ValueError:
        pass
    raise ValueError("must be an ISO date YYYY-MM-DD between 2000 and 2100")


def solver_zone(value: str) -> str:
    zone(value)
    return value


Clock = Annotated[str, AfterValidator(solver_clock)]
# Where the solver resolves a node: "lat,lon", a stop ID, or a stop name it searches.
Location = Annotated[str, Field(min_length=1)]


class DayStart(Model):
    id: str
    at: Location
    time: Clock
    name: str | None = None


class DayEnd(Model):
    id: str
    at: Location
    by: Clock
    name: str | None = None


class DayCandidate(Model):
    id: str
    at: Location
    priority: Literal["must", "必去", "最建议", "建议", "可选", "3", "2", "1", 3, 2, 1]
    stay: int = Field(ge=1, le=600, description="Minutes to stay.")
    open: Annotated[list[Clock], Field(min_length=2, max_length=2)] | None = Field(default=None, description="['open', 'close'].")
    last_entry: Clock | None = None
    before: str | None = Field(default=None, description="ID of a candidate or the end this one must precede.")
    name: str | None = None


def departure(value: list[str]) -> list[str]:
    for position in (0, 1):
        try:
            solver_clock(value[position])
        except ValueError as error:
            raise ValueError(f"item {position}: {error}") from None
    return value


class DayOverride(Model):
    # "from" is the wire name; "from_" is what a model dump without aliases writes, so both are read.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    minutes: int | None = Field(default=None, ge=0, le=1440)
    deps: list[Annotated[list[str], Field(min_length=2, max_length=3), AfterValidator(departure)]] = Field(
        default_factory=list, description="Departures [departure HH:MM, arrival HH:MM, optional label].")
    label: str | None = None


class SolverDay(Model):
    """The whole-day solver's input (query transit action=plan_day). The same model gives the MCP schema and the
    service's check, so a wrong nested shape is refused at the boundary with its path, not inside the solver."""
    day: Annotated[str, AfterValidator(solver_date)]
    timezone: Annotated[str, AfterValidator(solver_zone)]
    pace: str | None = None
    start: DayStart
    end: DayEnd
    candidates: list[DayCandidate] = Field(default_factory=list)
    overrides: list[DayOverride] = Field(default_factory=list)


class Arguments(BaseModel):
    """The shape of query arguments. Fields not named here pass through unchanged, as before."""
    model_config = ConfigDict(extra="allow")
    action: Literal["stops", "plan_day"] | None = None
    text: str | None = None
    limit: int | None = None
    origin: str | None = None
    destination: str | None = None
    when: str | None = None
    arrive_by: bool | None = None
    fresh: bool | None = None
    place: WeatherPlace | None = None
    date: str | None = None
    lat: float | None = None
    lon: float | None = None
    km: float | None = None
    target: Target | None = None
    day: SolverDay | None = None
    idempotency_key: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    timezone: Annotated[str, AfterValidator(solver_zone)] | None = Field(default=None, description=(
        "IANA zone of the local times in the answer; default: the trip's."))
    # latest_departure (A5)
    anchor_id: str | None = Field(default=None, description=(
        "latest_departure: the hard node to reach; its time, buffer_minutes and deadline (external, plan) set the target."))
    by: str | None = Field(default=None, description=(
        "latest_departure without an anchor: be AT the destination by this time — an arrival deadline at the "
        "destination, not a vehicle's boarding time at a station on the way (for that use board_at). ISO with offset, "
        "or HH:MM with date."))
    board_at: str | None = Field(default=None, description=(
        "latest_departure: catch the vehicle boarding at this time AT the destination stop (a boarding time there, not "
        "an arrival deadline). ISO with offset, or HH:MM with date."))
    deadline: Literal["external", "plan"] | None = Field(default=None, description=(
        "latest_departure with `by`: external = an operator's limit; plan = the plan's own target. Omitted: not marked."))
    external_deadline: str | None = Field(default=None, description=(
        "latest_departure for a plan target: the external limit behind it, an anchor ID or a time. Default: the "
        "trip's earliest external anchor within 12 hours after the target at the same place."))
    after: str | None = Field(default=None, description=(
        "latest_departure: search departures from this time (ISO with offset, or HH:MM with date). Default: now, "
        "and not earlier than 4 hours before the target."))
    # transit continuous window (A9)
    until: str | None = Field(default=None, description=(
        "transit: every departure from `when` up to this time (ISO with offset, or HH:MM with date; an HH:MM earlier "
        "than `when` means the next day). The service pages on from the last departure found until the results pass "
        "`until` or the data ends, one route search of the daily budget per page, and answers with the continuous "
        "coverage, one sentence per departure and a summary. For \"the last ones tonight\": until the day's end."))
    # web_evidence (A5)
    url: str | None = Field(default=None, description="web_evidence: the page's address (http or https).")
    quote: Annotated[str, Field(min_length=1, max_length=2000)] | None = Field(default=None, description=(
        "web_evidence: the sentence as written on the page, verbatim, in its own language."))
    claim: Annotated[str, Field(min_length=1, max_length=1000)] | None = Field(default=None, description=(
        "web_evidence: what the quote is to support, e.g. 'The document desk closes 40 minutes before departure'."))
    type: Literal["rule", "opening_hours", "event", "place", "price", "other"] | None = Field(default=None, description=(
        "web_evidence: what kind of fact the quote states."))
    read_from: Literal["page", "search_snippet"] | None = Field(default=None, description=(
        "web_evidence: page = read on the page itself (default); search_snippet = only a search result's snippet "
        "was read, which is recorded but never counts as checked."))
    check_quote: bool | None = Field(default=None, description=(
        "web_evidence: false skips the service's one fetch of the page to look for the quote."))
    detail: Literal["compact", "full"] | None = Field(default=None, description=(
        "latest_departure: compact (default) gives the first choice, the fallback, the first later ones, the window and "
        "the summary in about 10 KB; full adds every connection and every leg time. The evidence keeps the full result."))
    # open_places (A7)
    near: str | None = Field(default=None, description=(
        "open_places: where to measure from: a place of the trip (ID or name) or 'lat,lon'."))
    at: str | None = Field(default=None, description=(
        "open_places: open at this time (ISO with offset, or HH:MM with date); default now."))
    # check_answer (A6)
    part: Literal["first", "second"] | None = Field(default=None, description=(
        "check_answer: which part the draft in `text` is."))
    request_id: str | None = Field(default=None, description=(
        "check_answer: the request_id of this question's turn; evidence from 6 hours before it on backs the draft."))
    question: str | None = Field(default=None, description=(
        "check_answer: this question's words as the user sent them (send it when its turn was not recorded); without it "
        "and request_id, the question is the latest user message of the last 30 minutes, else unknown (no meal check)."))


REQUIRED = {("transit", None): ("origin", "destination", "when"), ("transit", "stops"): ("text",),
            ("transit", "plan_day"): ("day", "idempotency_key"), ("weather", None): ("place", "date"),
            ("nearby", None): ("lat", "lon"), ("geocode", None): ("text",),
            ("latest_departure", None): ("origin", "destination"), ("web_evidence", None): ("url", "quote", "claim", "type"),
            ("check_answer", None): ("text", "part"), ("open_places", None): ("near",)}
# A web check's time is the service's clock: a time the host writes is refused, not stored.
HOST_TIMES = ("fetched_at", "checked_at", "recorded_at", "at", "time", "valid_until")
SHAPES = {"arguments.target": "{block_id or anchor_id, date, fact, optional place_id and draft_id}",
          "arguments.place": "{name, lat, lon}", "arguments.day": "{day, timezone, start, end, candidates}",
          "arguments.day.start": "{id, at, time}", "arguments.day.end": "{id, at, by}",
          "arguments.day.candidates.*": "{id, at, priority, stay, optional open, last_entry, before, name}",
          "arguments.day.overrides.*": "{from, to, optional minutes, deps}"}


def inline(schema: dict) -> dict:
    """A model's JSON schema with its $defs inlined, to nest it inside another schema (a tool's argument)."""
    definitions = schema.get("$defs", {})

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return walk(definitions[value["$ref"].rsplit("/", 1)[1]])
            return {key: walk(entry) for key, entry in value.items() if key != "$defs"}
        if isinstance(value, list):
            return [walk(entry) for entry in value]
        return value

    return walk(schema)


def arguments_schema() -> dict:
    return inline(Arguments.model_json_schema(by_alias=True))


def checked_arguments(kind: str, arguments) -> dict:
    """Validate query arguments at the tool boundary, so a wrong nested shape is refused with the field, the expected
    type and what was sent, instead of failing later inside a provider or the solver. Returns the arguments with an
    empty target removed (an empty target always meant no target) and values in the validated types."""
    if not isinstance(arguments, dict):
        raise ValueError(f"arguments must be an object, got {json_type(arguments)}")
    arguments = {key: value for key, value in arguments.items() if not (key == "target" and value in ({}, None))}
    try:
        parsed = Arguments.model_validate(arguments)
    except ValidationError as error:
        raise ValueError("Invalid query arguments: " + "; ".join(input_problems(error, "arguments", SHAPES))) from None
    problems = []
    if parsed.target is not None and kind in ("latest_departure", "web_evidence", "check_answer", "open_places"):
        problems.append(f"arguments.target binds transit or weather evidence to a plan fact; {kind} does not take it"
                        + (" (use arguments.anchor_id)" if kind == "latest_departure" else ""))
    if parsed.target is None:
        problems += [f"arguments.{name} is required" for name in REQUIRED.get((kind, parsed.action), ()) if arguments.get(name) is None]
    elif bool(parsed.target.block_id) == bool(parsed.target.anchor_id):
        problems.append("arguments.target needs exactly one of block_id or anchor_id")
    given = [name for name in ("anchor_id", "by", "board_at") if getattr(parsed, name) is not None]
    if kind == "latest_departure" and "by" in given and "board_at" in given:
        problems.append("arguments takes one of by (an arrival deadline at the destination) or board_at (a vehicle's "
                        "boarding time at the destination stop), not both; with neither, nor anchor_id, the service "
                        "answers tonight's last connection to the destination")
    # A10: latest_departure without a deadline is tonight's last connection; `until` then ends "tonight"
    last_connection = kind == "latest_departure" and not given
    if parsed.until is not None and (kind != "transit" or parsed.action is not None) and not last_connection:
        problems.append("arguments.until goes with query(kind=\"transit\") without an action (every departure from `when` "
                        "up to `until`), or with latest_departure without anchor_id, by and board_at (tonight's last "
                        "connection, searched up to `until`)")
    if parsed.until is not None and parsed.arrive_by:
        problems.append("arguments.until and arguments.arrive_by do not go together: until pages departures forward "
                        "from `when`")
    if kind == "web_evidence":
        problems += [f"arguments.{name}: the service records the time of a web check itself; do not send it"
                     for name in HOST_TIMES if name in arguments]
        if parsed.url is not None:
            try:
                public_url(parsed.url)
            except ValueError as error:
                problems.append(f"arguments.url {error}, got {parsed.url!r}")
    if problems:
        raise ValueError("Invalid query arguments: " + "; ".join(problems))
    return parsed.model_dump(exclude_none=True, by_alias=True)


def json_value(value):
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(element) for key, element in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(element) for element in value]
    return value


def synthetic(value) -> Sourced:
    fetched = clock()
    return Sourced(value=value, source="demo:synthetic — fictional data, not for travel", fetched_at=fetched,
                   valid_until=fetched + timedelta(minutes=5), notes=["Synthetic data cannot verify a critical fact."])


class DemoTransit:
    async def stops(self, text: str, *, limit: int = 8):
        return synthetic([Place(text + " (fictional stop)", 59.91, 10.75, "demo-stop")][:limit])

    async def plan(self, origin: Place | str, destination: Place | str, when: datetime, *, arrive_by: bool = False, limit: int = 5):
        departure = when - timedelta(minutes=25) if arrive_by else when
        return synthetic([Itinerary([Leg("rail", str(origin), str(destination), departure, departure + timedelta(minutes=25), "DEMO")])][:limit])

    async def walk_minutes(self, origin: Place, destinations: list[Place]):
        return synthetic([15 for destination in destinations])


class DemoWeather:
    async def day(self, place: Place, date: str):
        return synthetic({"date": date, "place": place.name, "summary": "Fictional dry weather", "temperature_c": 14})


class DemoGeocode:
    async def search(self, text: str, *, limit: int = 5):
        return synthetic([Place(text + " (fictional result)", 59.91, 10.75, "demo-place")][:limit])


# --- latest departure (A5) ------------------------------------------------------------------------------------------

LOOKBACK = timedelta(hours=4)  # by default the search starts at most this long before the target
LINK_HOURS = 12  # a plan target's external limit is looked for this far after it
PAGES = 6  # departure searches, each from the last departure found, until the results pass the limit
DEADLINE_WORDS = {"zh": dict(external="外部截止", plan="计划目标", unmarked="没标是外部截止还是计划目标", board="上车时刻"),
                  "en": dict(external="external deadline", plan="plan target", unmarked="not marked external or plan",
                             board="boarding time")}


def moment(value: str, day: str | None, tz: ZoneInfo, name: str) -> datetime:
    """An ISO time with offset, or HH:MM on `day` in the trip's zone."""
    try:
        if len(value) == 5 and value[2] == ":":
            minutes(value)
            if not day:
                raise ValueError
            found = local_instant(day, value, tz.key)
            if found is None:
                raise ValueError
            return found
        return aware(value)
    except ValueError:
        raise ValueError(f"Invalid query arguments: arguments.{name} must be an ISO time with offset, or HH:MM with "
                         f"arguments.date, got {value!r}") from None


def match_place(plan: dict | None, value: str) -> dict | None:
    """The trip's place a lookup's origin or destination names (A7): its ID, or its name or local name, alone or before
    an address ("Blockfabrik, Schloßgasse 10-12"); only places with a stop or coordinates. A matched place is looked up
    by what the trip stores, so an address is never geocoded to somewhere else."""
    places = [place for place in (plan or {}).get("places", []) if place.get("transit_id") or place.get("coords")]
    exact = next((place for place in places if place["id"] == value), None)
    if exact or re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", value):
        return exact
    said = re.sub(r"\s+", " ", value.casefold()).strip()
    head = name_head(said)
    names = [(re.sub(r"\s+", " ", name.casefold()).strip(), place) for place in places
             for name in {place.get("name_local", ""), place.get("name", "")} if name]
    found = [(len(name), place) for name, place in names if name in (said, head)]
    if found:
        return max(found, key=lambda pair: pair[0])[1]
    # A11: “Kletterhalle Nordbahn” names the stored “Kletterhalle Nordbahn, Beispielgasse 1” (the replay geocoded such a
    # gym's name to a stop in another country); a head two places share names neither
    heads = {place["id"]: place for name, place in names if name_head(name) in (said, head)}
    return next(iter(heads.values())) if len(heads) == 1 else None


def name_head(name: str) -> str:
    """A name before its address or note: “kletterhalle nordbahn, beispielgasse 1” → “kletterhalle nordbahn”."""
    return re.split(r"\s*[,，(（–—]\s*|\s+-\s+", name)[0].strip()


def location_of(place: dict) -> str:
    return place["transit_id"] if place.get("transit_id") else ",".join(str(coordinate) for coordinate in place["coords"])


def place_location(plan: dict | None, value: str) -> str:
    """Where to look up a lookup's end: a trip place's stop or coordinates (by ID or name), else the text as sent."""
    place = match_place(plan, value)
    return location_of(place) if place else value


# A name the trip does not store, resolved this far from every place of the trip, is refused (A11): Wien Hbf lies 211 km
# from a Salzkammergut trip; the replay's Hannover Ahlem lies over 500 km from every place of its trip.
FAR_KM = 300


def coordinates(plan: dict | None, value: str) -> tuple[float, float] | None:
    place = match_place(plan, value)
    if place and place.get("coords"):
        return tuple(place["coords"])
    found = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", value)
    return (float(found[1]), float(found[2])) if found else None


def kilometres(first: tuple[float, float], second: tuple[float, float]) -> float:
    latitude = math.radians(second[0] - first[0])
    longitude = math.radians(second[1] - first[1])
    haversine = math.sin(latitude / 2) ** 2 + math.cos(math.radians(first[0])) * math.cos(math.radians(second[0])) * math.sin(longitude / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(haversine)))


def place_row(place: dict, here: tuple[float, float] | None, local: datetime, language: str) -> dict:
    """A place of the trip at a moment: whether its stored hours have it open at `local`, when it closes, where the
    hours were read, and how far it is from `here` (None without a point)."""
    distance = kilometres(here, tuple(place["coords"])) if here and place.get("coords") else None
    is_open, closes = opening_at(place.get("opening_hours") or [], local.weekday(), local.hour * 60 + local.minute)
    row = dict(id=place["id"], name=place["name"], name_local=place["name_local"], kind=place.get("kind"),
               distance_km=round(distance, 2) if distance is not None else None, open=is_open,
               opening_hours=place.get("opening_hours") or [], hours_source=place.get("hours_source"))
    if closes is not None:
        row["closes"] = f"{closes % 1440 // 60:02d}:{closes % 60:02d}" + (("（次日）" if language == "zh" else " (next day)") if closes > 1440 else "")
    if is_open is None and place.get("hours"):
        row["hours"] = place["hours"]
    return row


def place_rows(plan: dict | None, here: tuple[float, float], local: datetime, reach: float, words: list[str],
               language: str) -> list[dict]:
    """The trip's places within `reach` km of `here`, nearest first (place_row each): open_places (A7) and turn's
    points (A12). `words` filter by the name, kind or notes."""
    rows = []
    for place in (plan or {}).get("places", []):
        said = " ".join(str(place.get(key) or "") for key in ("name", "name_local", "kind", "notes")).casefold()
        if not place.get("coords") or any(word not in said for word in words):
            continue
        row = place_row(place, here, local, language)
        if row["distance_km"] <= reach:
            rows.append(row)
    return sorted(rows, key=lambda row: row["distance_km"])


RAIL = {"rail", "train", "regional_rail", "highspeed_rail", "long_distance", "night_rail", "regional_fast_rail", "suburban"}
STATION_KINDS = {"station", "train_station", "railway_station", "rail_station"}
NEAR_KM = 3.0  # a planned train "goes to" the lookup's destination when it ends this close to it
TRANSFER = timedelta(minutes=3)  # from the feeder's arrival at the station to the train's departure


def stations(plan: dict | None, origin: str, destination: str) -> list[dict]:
    """The trip's stations a train toward the destination leaves from (A7): the origins of the plan's rail travel
    blocks that end at or near the destination; without such a block, places of a station kind. At most two."""
    places = {place["id"]: place for place in (plan or {}).get("places", [])}
    goal = coordinates(plan, destination)
    start = match_place(plan, origin)
    found = []
    for day in (plan or {}).get("days", []):
        for option in day.get("plans", []):
            for track in option.get("tracks", []):
                blocks = track.get("blocks", [])
                for position, block in enumerate(blocks):
                    if block.get("kind") != "travel" or str(block.get("mode") or "").casefold() not in RAIL:
                        continue
                    source = block.get("origin") or (blocks[position - 1].get("place") if position else None)
                    end = places.get(block.get("destination") or block.get("place"))
                    reaches = end is not None and ((match_place(plan, destination) or {}).get("id") == end["id"] or (
                        goal is not None and end.get("coords") and kilometres(goal, tuple(end["coords"])) <= NEAR_KM))
                    if source in places and reaches:
                        found.append(places[source])
    if not found:
        found = [place for place in places.values() if str(place.get("kind", "")).casefold() in STATION_KINDS]
    usable = [place for place in found if (place.get("transit_id") or place.get("coords")) and place["id"] != (start or {}).get("id")]
    return list({place["id"]: place for place in usable}.values())[:2]


def by_rail(itinerary: Itinerary) -> bool:
    return any(leg.mode in RAIL for leg in itinerary.legs)


def arrives_by_rail(itinerary: Itinerary) -> bool:
    """The ride that reaches the destination is a train (A10 replay: a route of U6, S1, then the VAB 5 bus was called the
    last train because one ride was rail)."""
    rides = [leg for leg in itinerary.legs if leg.mode != "walk"]
    return bool(rides) and rides[-1].mode in RAIL


def train_ride(view: dict) -> dict:
    """The train a “last train” names: the ride that reaches the destination (arrives_by_rail), its own time and line,
    never the first ride's (A11 replay: “火车最后一班 00:47 18” named the tram to the station of the 00:53 REX)."""
    return [leg for leg in view["legs"] if not leg["walk"]][-1]


def line_key(line: str | None) -> tuple[str, str]:
    """A line as letters and digits apart: “RJX 19959” → ("rjx", "19959")."""
    bare = re.sub(r"\s+", "", (line or "").casefold())
    return re.sub(r"\d", "", bare), re.sub(r"\D", "", bare)


def plan_rides(plan: dict | None, destination: str, found: list[Itinerary], start: datetime, until: datetime, tz: ZoneInfo,
               language: str, app: str | None) -> list[dict]:
    """The plan's own trains to this destination (boarding nodes with a line) that leave inside the searched span, and
    whether this lookup found each (a12b replay: the 22:42 RJX was among the departures, the compact result did not show
    it, and the answer said “这次没查到，以下面的班次为准”). A ride at that minute with the same line, or the same kind of
    train under another number (“数据源写作 …”), is found; else the sentence says it is not in these results and where
    to check it, never that it is gone."""
    said, place = LAST_SAY.get(language, LAST_SAY["en"]), match_place(plan, destination)
    told = []
    for _, anchor, instant in anchors(plan):
        if not place or not anchor.get("line") or anchor.get("destination") != place["id"] or instant is None or not start <= instant <= until:
            continue
        want = line_key(anchor["line"])
        rides = [(itinerary, leg) for itinerary in found for leg in itinerary.legs if leg.mode != "walk"
                 and abs((leg.depart - instant).total_seconds()) < 60
                 and any(part and part == other for part, other in zip(want, line_key(leg.line)))]
        exact = [(itinerary, leg) for itinerary, leg in rides if line_key(leg.line) == want]
        match = max(exact or rides, key=lambda pair: pair[0].depart, default=None)
        time = views.clock(instant, tz)
        if match is None:
            named = (app or said["app"]) + (" " if language == "zh" and (app or said["app"])[-1].isascii() else "")
            sentence = said["plan_missing"].format(time=time, line=anchor["line"], app=named)
        else:
            itinerary, leg = match
            sentence = said["plan_found" if line_key(leg.line) == want else "plan_found_as"].format(
                time=time, line=anchor["line"], found=leg.line, leave=views.clock(itinerary.depart, tz))
        told.append(dict(anchor_id=anchor["id"], time=time, line=anchor["line"], found=match is not None,
                         found_line=match[1].line if match else None, sentence=sentence, itinerary=match[0] if match else None))
    return told


def cancelled(itinerary: Itinerary) -> bool:
    return any(leg.extra.get("cancelled") for leg in itinerary.legs)


def ticket(view: dict, language: str, stored: bool = False) -> dict:
    """Whether a route needs a ticket of its own, where that can be told from the timetable: a long-distance coach
    always does. Otherwise the trip's stored tickets when it has some (A12: “票价和通票没查” beside a stored 24-hour ticket
    and its €2.30 supplement made the host write “市内票可能不够、查票罚款”), else unknown."""
    coach = [leg for leg in view["legs"] if leg["mode"] == "coach" or "flix" in f"{leg.get('line')} {leg.get('operator')}".casefold()]
    if coach:
        line = coach[0].get("line") or coach[0].get("operator") or "coach"
        return dict(need="extra", why=f"长途大巴 {line} 要单独买票" if language == "zh" else f"the coach {line} needs its own ticket")
    if stored:
        return dict(need="stored", why="按这趟存的票" if language == "zh" else "as the trip's stored tickets say")
    return dict(need="unknown", why="票价和通票没查" if language == "zh" else "fares and passes were not checked")


# --- the trip's stored tickets (A12) ------------------------------------------------------------------------------------------
TICKET_KINDS = ("ticket", "pass")
TICKET_DAYS = 1  # a ticket bought the day before still counts (a 24-hour ticket bought in the afternoon)
TICKET_SAY = {"zh": dict(head="这趟存的票：", bought="已买", booked="已订", on_site="现场买", to_buy="要买", join="；", fares="，",
                         instead="买不到时：", estimated="（估的）", each="/人", gap=""),
              "en": dict(head="The trip's stored tickets: ", bought="bought", booked="booked", on_site="to buy on site", to_buy="to buy",
                         join="; ", fares=", ", instead="if that cannot be bought, ", estimated=" (estimated)", each=" per person", gap=" ")}


def price(fare: dict, said: dict) -> str:
    amount = fare.get("amount_per_person")
    if amount is None:
        return ""
    money = f"€{amount:.2f}" if (fare.get("currency") or "EUR") == "EUR" else f"{amount:.2f} {fare['currency']}"
    return said["gap"] + money + said["each"] + (said["estimated"] if fare.get("estimated") else "")


def stored_tickets(plan: dict | None, day: str, language: str) -> dict | None:
    """The tickets the trip stores for `day` (or bought up to TICKET_DAYS before): each with what it is, its status and
    its fares (ticket, the segment it covers, the price; an estimated price says so). One sentence says them once; no
    stored ticket gives None. The service does not judge which ticket a route needs: it says what is stored."""
    said = TICKET_SAY.get(language, TICKET_SAY["en"])
    first = Date.fromisoformat(day) - timedelta(days=TICKET_DAYS)
    found = [booking for booking in (plan or {}).get("bookings", []) if str(booking.get("kind", "")).casefold() in TICKET_KINDS
             and booking.get("status") in ("bought", "booked", "on_site", "to_buy")
             and first <= Date.fromisoformat(str(booking["date"])) <= Date.fromisoformat(day)]
    if not found:
        return None
    parts = []
    for booking in found:
        fares = sorted(booking.get("fares") or [], key=lambda fare: bool(fare.get("alternative")))
        told = said["fares"].join((said["instead"] if fare.get("alternative") else "") + fare["ticket"]
                                  + (f"（{fare['segment']}）" if language == "zh" else f" ({fare['segment']})") + price(fare, said)
                                  for fare in fares) if fares else booking["what"]
        parts.append(f"{said[booking['status']]}：{told}" if language == "zh" else f"{said[booking['status']]}: {told}")
    items = [dict(id=booking["id"], status=booking["status"], what=booking["what"],
                  fares=[{key: fare.get(key) for key in ("ticket", "segment", "amount_per_person", "currency", "estimated", "alternative")}
                         for fare in booking.get("fares") or []]) for booking in found]
    return dict(text=said["head"] + said["join"].join(parts), items=items)


def anchors(plan: dict | None):
    for day in (plan or {}).get("days", []):
        for anchor in day.get("anchors", []):
            yield day["date"], anchor, local_instant(day["date"], anchor["time"], plan["timezone"])


# --- what a hard node's time means (A10, D-67): read from its fields, in this one table ------------------------------------
# a9b: the anchor “22:42 RJX at Wien Hbf, toward the airport” was read as “be at the airport by 22:42”. A node with a
# vehicle (its `line`, or the `destination` it rides to) and the `place` it leaves from is the moment that vehicle boards
# at `place`; every other node is a time to be at its place, an external deadline, the plan's own target or not marked.
# A11: a node done online (`where: online`, “网上值机截止”) is no time to be anywhere: never a deadline to reach, never a
# latest departure (replay: “要 05:45 前到，最晚 05:13 出门” for the online check-in). Never decided by an anchor's ID.
ANCHOR_MEANINGS = (
    ("online_deadline", "where online: done on a phone or computer wherever the person is; no place to be at, no latest departure",
     lambda anchor: anchor.get("where") == "online"),
    ("boarding_time", "line or destination, with place: the vehicle boards at place at time",
     lambda anchor: bool(anchor.get("place")) and bool(anchor.get("line") or anchor.get("destination"))),
    ("arrival_deadline", "any other node: be at place by time (deadline external, plan or not marked)",
     lambda anchor: True),
)


def anchor_meaning(anchor: dict) -> str:
    """online_deadline, boarding_time or arrival_deadline, from the node's fields by ANCHOR_MEANINGS."""
    return next(meaning for meaning, _, applies in ANCHOR_MEANINGS if applies(anchor))


def place_label(plan: dict | None, place_id: str | None) -> str | None:
    place = next((place for place in (plan or {}).get("places", []) if place["id"] == place_id), None)
    return (place.get("name_local") or place.get("name")) if place else place_id


def anchor_conflict(plan: dict | None, anchor: dict, arguments: dict, instant: datetime, tz: ZoneInfo) -> str | None:
    """Why the host's other arguments contradict what the anchor's fields say (ANCHOR_MEANINGS), with the fix; None
    when they agree. The node sets the target alone: `by` or `board_at` beside it is refused, saying which kind of time
    the node is. A boarding node is caught at its place: the destination is that place or the place the vehicle rides
    to."""
    name, at = anchor["id"], views.clock(instant, tz)
    if anchor_meaning(anchor) == "boarding_time":
        stop, toward = place_label(plan, anchor["place"]), place_label(plan, anchor.get("destination"))
        what = (f"hard node {name!r} is a boarding time: {anchor.get('line') or 'the vehicle'} leaves {stop} at {at}"
                + (f" toward {toward}" if toward else "") + ", not a deadline to be at the destination")
        fix = (f"Send one of anchor_id, by or board_at. To catch it: anchor_id={name!r} alone (the service searches to "
               f"{stop}). For tonight's last connection to {toward or arguments['destination']}: latest_departure with "
               f"origin and destination only.")
    else:
        what = (f"hard node {name!r} is a time to be at {place_label(plan, anchor.get('place')) or 'its place'} by {at} "
                f"({anchor.get('deadline') or 'not marked'}), not a vehicle's boarding time")
        fix = (f"Send one of anchor_id, by or board_at: anchor_id={name!r} alone for this node, by alone for another "
               f"arrival deadline, board_at alone (destination the boarding stop) to catch a vehicle.")
    for key, kind in (("by", "an arrival deadline"), ("board_at", "a boarding time")):
        if arguments.get(key):
            return f"{what}; arguments.{key} ({kind}) was sent with it. {fix}"
    if anchor_meaning(anchor) == "boarding_time":
        said = (match_place(plan, arguments["destination"]) or {}).get("id", arguments["destination"])
        if said not in {anchor["place"], anchor.get("destination")} - {None}:
            return f"{what}; arguments.destination {arguments['destination']!r} is neither {stop} nor {toward or 'its destination'}. {fix}"
    return None


def deadline_target(plan: dict | None, arguments: dict, tz: ZoneInfo) -> dict:
    """What the departure must reach: the plan's target (`plan_by`: the time less the plan's buffer) and the limit
    (`limit`: an external deadline; for a target not marked, its own time; for a plan target, the external deadline
    behind it, if one is known). An anchor's meaning comes from ANCHOR_MEANINGS: a boarding node is reached by being at
    its place (`board_place`) by its time, as with `board_at`; `meaning` says which (A10)."""
    anchor, board_place = None, None
    if arguments.get("anchor_id"):
        found = [(day, entry, instant) for day, entry, instant in anchors(plan)
                 if entry["id"] == arguments["anchor_id"] and arguments.get("date") in (None, day)]
        if not found:
            raise ValueError(f"Invalid query arguments: arguments.anchor_id: no hard node {arguments['anchor_id']!r} in the "
                             f"current plan" + (f" on {arguments['date']}" if arguments.get("date") else ""))
        if len(found) > 1:
            raise ValueError(f"Invalid query arguments: arguments.anchor_id: {arguments['anchor_id']!r} is on several days; add arguments.date")
        day, anchor, instant = found[0]
        if instant is None:
            raise ValueError("The hard node's local time is ambiguous or does not exist on that day")
        if anchor_meaning(anchor) == "online_deadline":
            raise ValueError(f"Invalid query arguments: hard node {anchor['id']!r} is done online (where: online) by "
                             f"{views.clock(instant, tz)}: nobody has to be anywhere then, so it has no latest departure. "
                             f"For tonight's last connection send origin and destination only; for a time to be there, "
                             f"that node's anchor_id or by.")
        conflict = anchor_conflict(plan, anchor, arguments, instant, tz)
        if conflict:
            raise ValueError("Invalid query arguments: " + conflict)
        if anchor_meaning(anchor) == "boarding_time":
            deadline, board_place = "board", anchor["place"]
        else:
            deadline = anchor.get("deadline") or "unmarked"
        buffer, text = anchor.get("buffer_minutes", 0), anchor.get("text")
    else:
        if arguments.get("board_at"):
            # A boarding time AT the destination stop (A9): the target is being at that stop then, not reaching a
            # final destination; it never reads as an arrival deadline.
            instant = moment(arguments["board_at"], arguments.get("date"), tz, "board_at")
            day, deadline, buffer, text = instant.astimezone(tz).date().isoformat(), "board", 0, None
        else:
            instant = moment(arguments["by"], arguments.get("date"), tz, "by")
            day, deadline, buffer, text = instant.astimezone(tz).date().isoformat(), arguments.get("deadline") or "unmarked", 0, None
    limit, limit_from = (instant, "target") if deadline != "plan" else external_after(plan, arguments, instant, anchor, tz)
    return dict(anchor_id=arguments.get("anchor_id"), date=day, text=text, deadline=deadline, buffer_minutes=buffer,
                meaning="boarding_time" if deadline == "board" else "arrival_deadline",
                at=instant, plan_by=instant - timedelta(minutes=buffer), limit=limit, limit_from=limit_from,
                board_place=board_place, line=(anchor or {}).get("line"), toward=(anchor or {}).get("destination"))


def external_after(plan, arguments, instant, anchor, tz) -> tuple[datetime | None, str | None]:
    given = arguments.get("external_deadline")
    if given:
        linked = next((found for _, entry, found in anchors(plan) if entry["id"] == given), None)
        limit = linked or moment(given, arguments.get("date"), tz, "external_deadline")
        if limit <= instant:
            raise ValueError("Invalid query arguments: arguments.external_deadline must come after the plan target")
        return limit, given if linked else "argument"
    if anchor is None:
        return None, None
    later = [(found, entry) for _, entry, found in anchors(plan) if entry.get("deadline") == "external" and found is not None
             and instant < found <= instant + timedelta(hours=LINK_HOURS) and anchor_meaning(entry) != "online_deadline"]
    place = anchor.get("place")
    later = [pair for pair in later if pair[1].get("place") == place] or ([] if place else later) or \
        [pair for pair in later if not pair[1].get("place")]
    if not later:
        return None, None
    found, entry = min(later, key=lambda pair: pair[0])
    return found, entry["id"]


def ride_text(view: dict, language: str) -> str:
    """Every ride of a connection in order, each with its own line, from and to stop and times (A8: the first ride's
    line with the last ride's stop read as a direct ride)."""
    rides = [leg for leg in view["legs"] if not leg["walk"]]
    if not rides:
        return views.leg_line(view["legs"][0], language)
    return views.words(language)["then"].join(views.leg_line(leg, language) for leg in rides)


# --- sentences a host copies (A8, D-57, D-60) ---------------------------------------------------------------------------
# A Transitous feed list (coverage.files_for) -> the regions.yaml row whose timetable app the check sentence names.
FEED_REGIONS = {"at": "at-rural", "ch": "ch", "de": "de"}
SAY = {"zh": dict(target="要 {plan_by} 前到，最晚 ", partial="要 {plan_by} 前到，可以坐：", after="误了 {board} 那班，之后还有 {count} 班赶得上 {bound}；最早的：",
                  none_after="误了 {board} 那班，之后没有赶得上 {bound} 的班次{basis}",
                  miss="要是误了 {board} 那班，就赶不上 {plan_by}{basis}", unknown_after="误了 {board} 那班之后还有没有赶得上的，没查到",
                  last="赶得上 {bound} 的最后一班是 {board} {line}（{leave} 出门）", next="；再下一班 {board} 开，{reach} 到，过了 {bound}",
                  basis="{basis}", none_reach="要 {plan_by} 前到，查到的班次都赶不上{basis}",
                  only_limit="要是 {limit} 前到也行，还能坐：", reached="查到 {reached} 为止；之后的没查",
                  empty="{start} 起没查到班次；不能说没车，也不能说“最后一班”"),
       "en": dict(target="To arrive by {plan_by}, at the latest: ", partial="To arrive by {plan_by}, you can take: ", after="If you miss the {board}, {count} more arrive by {bound}; the first: ",
                  none_after="If you miss the {board}, nothing after it arrives by {bound}{basis}",
                  miss="If you miss the {board}, you will not make {plan_by}{basis}", unknown_after="Whether anything after the {board} arrives in time was not found",
                  last="The last one that arrives by {bound} is the {board} {line} (leave {leave})", next="; the next leaves {board} and arrives {reach}, after {bound}",
                  basis="{basis}", none_reach="To arrive by {plan_by}: none of the connections found makes it{basis}",
                  only_limit="If {limit} is late enough: ", reached="Searched up to {reached}; nothing after it was searched",
                  empty="No departures found from {start}: do not say there is none, or \"the last one\""),
       }


def searched(meta: dict) -> dict:
    """The stops the transit provider searched from and to, as it took them: what was typed, the stop's name and ID,
    how it was chosen, the other matches (A8: a typed name is not taken to mean its first match unseen)."""
    return {end: {key: meta[end].get(key) for key in ("said", "name", "stop_id", "how", "alternatives") if meta[end].get(key)}
            for end in ("origin", "destination") if meta.get(end)}


def end_name(plan: dict | None, said: str, searched: dict | None) -> str | None:
    """The name to type for an end: the trip's place's local name, else the stop the provider searched, else what was
    typed unless it is coordinates or a stop ID."""
    place = match_place(plan, said)
    if place:
        return place.get("name_local") or place.get("name")
    if searched and searched.get("name"):
        return searched["name"]
    return None if re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", said) or (re.search(r"[:_]", said) and " " not in said) else said


def arrival_name(plan: dict | None, said: str, searched: dict | None, view: dict | None, language: str) -> str:
    """The destination's name in a sentence: end_name, else the stop where `view` gets off, else “终点” (a11b: a
    destination given as coordinates, with no stored place, read “夜里一直有车到 None”)."""
    return end_name(plan, said, searched) or ((view or {}).get("alights") or {}).get("stop") or \
        ("终点" if language == "zh" else "the destination")


def feed_region(files: set[str]) -> str | None:
    return next((FEED_REGIONS[name] for name in sorted(files) if name in FEED_REGIONS), None)


HOW = {"zh": dict(real="实时晚点", platform="站台", disruption="临时停运", join="、", last="和",
                  missing="{what}时刻表里没有，出发前在 {app}查。搜 {query}{when}。{steps}结果里看实时时刻、站台和停运通知。",
                  when="，出发时间填 {clock}", not_yet="这段日期的班次还没进数据，在 {app}查。搜 {query}{when}。{steps}",
                  missed="误了主选，查末班：在 {app}搜 {query}，出发时间填 {clock}。"),
       "en": dict(real="real-time delays", platform="platforms", disruption="disruptions", join=", ", last=" and ",
                  missing="The timetable does not have {what}; check them in {app} before you leave. Search {query}{when}. {steps} "
                          "The results show real-time times, platforms and disruption notices.",
                  when=", departure time {clock}", not_yet="This date's services are not in the data yet; check them in {app}. "
                                                         "Search {query}{when}. {steps}",
                  missed="If you miss the first choice, check the last one: in {app} search {query}, departure time {clock}.")}


def listed(items: list[str], said: dict) -> str:
    return items[0] if len(items) == 1 else said["join"].join(items[:-1]) + said["last"] + items[-1]


def how_to_check(view: dict | None, ends: tuple[str | None, str | None], region: str | None, notes: list[dict],
                 language: str, reports: list[dict] = ()) -> dict | None:
    """Where to get what this lookup cannot give (D-61), for the end of the answer, not each transit line: real-time
    delays where the rides are timetable only, platforms the data lacks, and disruptions always; for a date the data
    does not reach yet, that day's services. The app is the region's timetable tool (regions.yaml), with what to type,
    the steps and a link; this trip's or the user's long-term app setting (`reports`, from consider.sources) comes
    first (M1). `view` is the connection the answer takes (its first ride's stop → its last ride's stop); without one,
    `ends` are the names to search."""
    from tripwright.render import lines
    from tripwright.render.regions import choose

    said = HOW.get(language, HOW["en"])
    rides = [leg for leg in (view or {}).get("legs", []) if not leg["walk"]]
    origin, destination = (rides[0]["from"], rides[-1]["to"]) if rides else ends
    if not origin or not destination or origin == destination:
        return None
    tool = choose(region, reports=reports).timetable
    leg = lines.transit_leg("", origin, destination, mode="bus", region=region, language=language, reports=reports,
                            day=rides[0]["depart_at"][:10] if rides else None, clock=rides[0]["depart"] if rides else None)
    when = said["when"].format(clock=rides[0]["depart"]) if rides else ""
    app = leg["search"]["app"]
    named = app + (" " if language == "zh" and app[-1].isascii() else "")  # "ÖBB App 查", "Google 地图查"
    steps = tool.usage(language)
    steps = steps[0].upper() + steps[1:] if language == "en" else steps
    if not rides and any(note["kind"] in ("not_yet", "too_old") for note in notes):
        what, text = ["not_yet"], said["not_yet"].format(app=named, query=leg["search"]["query"], when=when, steps=steps)
    elif not rides:
        return None
    else:
        what = (["real_time"] if not all(ride.get("real_time") for ride in rides) else []) + \
            (["platform"] if not all(ride.get("platform") for ride in rides) else []) + ["disruption"]
        names = [said[{"real_time": "real", "platform": "platform", "disruption": "disruption"}[item]] for item in what]
        text = said["missing"].format(what=listed(names, said), app=named, query=leg["search"]["query"], when=when, steps=steps)
    return dict(what=what, app=app, search=leg["search"]["query"], clock=rides[0]["depart"] if rides else None,
                steps=tool.usage(language), link=leg["link"], text=text)


def trip_sources(store: Store, owner: str, plan: dict | None, region: str | None, language: str) -> tuple[list[dict], dict]:
    """This trip's app choices for the lookup's region (render.regions.choose reports) and the one-line offer to draft
    a question, in English and the region's languages (M1; D-61 four, six)."""
    from tripwright import consider

    known = consider.sources(store, owner, plan, at=now(), language=language, region=region)
    offer = consider.offer(known["languages"], language)
    return known["reports"], dict(say=offer["say"], languages=offer["languages"])


def head_sentences(meta: dict, notes: list[dict], language: str) -> list[str]:
    """What the user must see before the times: a stop searched under another name than typed, and coverage limits."""
    found = [views.resolved_sentence(end, meta.get(end), language) for end in ("origin", "destination")]
    return [text for text in found if text] + [note["text"] for note in notes]


def latest_sentences(result: dict, target: dict, connections: list[Itinerary], start: datetime, tz: ZoneInfo, language: str,
                     stop: str | None = None) -> list[str]:
    """latest_departure as sentences a host copies: the first choice, what is left after it (how many, the first),
    the last one that still arrives in time with the next one, or how far the search reached. A boarding target says
    where the vehicle is caught (`stop`; A10: “要 22:42 前到” read as reaching the airport by then)."""
    said = SAY.get(language, SAY["en"])
    if target["meaning"] == "boarding_time":
        line = target.get("line")
        caught = (f"赶 {{plan_by}} 在 {stop or '上车站'} 开的" + (f" {line}，" if line else "车，") if language == "zh"
                  else f"To catch the {{plan_by}} {line or 'service'} at {stop or 'the boarding stop'}, ")
        said = dict(said, target=caught + ("最晚 " if language == "zh" else "at the latest: "),
                    partial=caught + ("可以坐：" if language == "zh" else "you can take: "),
                    none_reach=caught + ("查到的班次都赶不上{basis}" if language == "zh" else "none of the connections found makes it{basis}"))
    plan_by, limit = views.clock(target["plan_by"], tz), views.clock(target["limit"], tz) if target["limit"] else None
    bound_at = target["limit"] or target["plan_by"]
    bound = views.clock(bound_at, tz)
    covered, primary, fallback = result["window"]["covers_target"], result["primary"], result["fallback"]

    def told(view):
        return views.sentence(view, language)

    def kind(view):
        """The per-connection real-time suffix: empty when every ride is timetable-only (the result's `note` says that
        once, A9) so the answer never carries “按时刻表，不是实时” on each line."""
        return views.basis_suffix([leg for leg in view["legs"] if not leg["walk"]], language)

    out = []
    usable = sorted((itinerary for itinerary in connections if not cancelled(itinerary)), key=lambda itinerary: itinerary.depart)
    if primary:
        # "at the latest" only when the search went on until connections miss the target; else just one that makes it
        out.append(said["target" if covered else "partial"].format(plan_by=plan_by) + told(primary))
        board = (primary["boards"] or {}).get("time") or primary["leave"]
        after = [itinerary for itinerary in usable if itinerary.depart > aware(primary["leave_at"]) and itinerary.arrive <= bound_at]
        if fallback:
            out.append(said["after"].format(board=board, count=len(after), bound=bound) + told(fallback))
        elif covered and target["limit"] is not None:
            out.append(said["none_after"].format(board=board, bound=bound, basis=kind(primary)))
        elif covered:
            out.append(said["miss"].format(board=board, plan_by=plan_by, basis=kind(primary)))
        else:
            out.append(said["unknown_after"].format(board=board))
    elif result["later"]:
        out.append(said["only_limit"].format(limit=limit) + told(result["later"][0]))
    elif covered and connections:
        out.append(said["none_reach"].format(plan_by=plan_by, basis=""))
    in_time = [itinerary for itinerary in usable if itinerary.arrive <= bound_at]
    if covered and in_time:
        last = views.connection_view(max(in_time, key=lambda itinerary: itinerary.depart), tz, language)
        following = next((itinerary for itinerary in usable if itinerary.depart > aware(last["leave_at"])), None)
        text = said["last"].format(bound=bound, board=(last["boards"] or {}).get("time") or last["leave"],
                                   line=(last["boards"] or {}).get("line") or "", leave=last["leave"]).replace("  ", " ").replace(" （", "（")
        if following is not None:
            nxt = views.connection_view(following, tz, language)
            text += said["next"].format(board=(nxt["boards"] or {}).get("time") or nxt["leave"], reach=nxt["reach"], bound=bound)
        out.append(text + kind(last))
    elif not connections:
        out.append(said["empty"].format(start=views.clock(start, tz)))
    elif not covered:
        out.append(said["reached"].format(reached=views.clock(max(itinerary.depart for itinerary in connections), tz)))
    return out


def departure_rows(itineraries: list[Itinerary], tz: ZoneInfo, bound: datetime) -> list[dict]:
    """Every connection a latest_departure search found, slim, for the answer check: when it leaves, its rides, when it
    arrives, whether it is in time (check_answer reads "the last one" and "nothing later" against these)."""
    rows = []
    for itinerary in sorted(itineraries, key=lambda itinerary: itinerary.depart):
        rides = [leg for leg in itinerary.legs if leg.mode != "walk"]
        rows.append(dict(leave=views.clock(itinerary.depart, tz), reach=views.clock(itinerary.arrive, tz),
                         rides=[dict(line=leg.line, depart=views.clock(leg.depart, tz), arrive=views.clock(leg.arrive, tz),
                                     **{"from": leg.origin, "to": leg.destination}) for leg in rides],
                         cancelled=cancelled(itinerary), in_time=itinerary.arrive <= bound))
    return rows


def latest_summary(result: dict, tz: ZoneInfo, language: str) -> str:
    target, said = result["target"], DEADLINE_WORDS.get(language, DEADLINE_WORDS["en"])
    plan_by, limit, buffer = target["plan_by"], target["limit"], target["buffer_minutes"]
    if language == "zh":
        named = (f"{target['text'] or '目标'} {target['time']}（{said[target['deadline']]}"
                 + (f"，计划留 {buffer} 分钟，按 {plan_by} 到算" if buffer else "") + "）")
    else:
        named = (f"{target['text'] or 'target'} {target['time']} ({said[target['deadline']]}"
                 + (f", the plan keeps {buffer} min, so by {plan_by}" if buffer else "") + ")")
    primary, later, fallback = result["primary"], result["later"], result["fallback"]
    if language == "zh":
        parts = [f"{named}：最晚 {primary['leave']} 出门，{ride_text(primary, language)}" if primary
                 else f"{named}：窗口里没有赶得上的班次"]
        if later:
            parts.append(f"只按外部截止 {limit} 还能坐 " + "、".join(ride_text(view, language) for view in later) + "（过了计划目标）")
        parts.append(f"误了主选的后备：{ride_text(fallback, language)}" if fallback else result["fallback_note"])
        others = [entry for entry in result.get("candidates", []) if "first_choice" not in entry["route"]]
        if others:
            parts.append("别的走法：" + "、".join(
                f"{entry['label']}最晚 {entry['leave']} 出门，{entry['reach']} 到" + ("（过了计划目标）" if entry["past_plan_target"] else "")
                + ("，" + entry["ticket"]["why"] if entry["ticket"]["need"] == "extra" else "") for entry in others))
        if not result["window"]["covers_target"]:
            parts.append(result["window"]["text"])
        return "；".join(part for part in parts if part)
    parts = [f"{named}: leave by {primary['leave']}, {ride_text(primary, language)}" if primary
             else f"{named}: no connection in the window"]
    if later:
        parts.append(f"by the external deadline {limit} only: " + ", ".join(ride_text(view, language) for view in later) + " (past the plan target)")
    parts.append(f"if the first choice is missed: {ride_text(fallback, language)}" if fallback else result["fallback_note"])
    others = [entry for entry in result.get("candidates", []) if "first_choice" not in entry["route"]]
    if others:
        parts.append("other routes: " + ", ".join(f"{entry['label']}: leave by {entry['leave']}, arrive {entry['reach']}"
                                                  + (" (past the plan target)" if entry["past_plan_target"] else "") for entry in others))
    if not result["window"]["covers_target"]:
        parts.append(result["window"]["text"])
    return "; ".join(part for part in parts if part)


def covers(itineraries: list[Itinerary], bound: datetime, start: datetime) -> bool:
    """Whether the results reach past the limit: a connection leaving after the last one that arrives in time arrives
    too late, or one leaves at the limit or later. Until then a later departure may still arrive in time."""
    usable = [itinerary for itinerary in itineraries if not any(leg.extra.get("cancelled") for leg in itinerary.legs)]
    last_ok = max((itinerary.depart for itinerary in usable if itinerary.arrive <= bound), default=start)
    return any(itinerary.depart > last_ok and itinerary.arrive > bound for itinerary in usable) or \
        any(itinerary.depart >= bound for itinerary in usable)


def latest_choice(itineraries: list[Itinerary], target: dict, start: datetime, tz: ZoneInfo, language: str) -> dict:
    """The latest connection that reaches the plan's target, the later ones only the external limit allows (marked
    past the plan target), the fallback when the first choice is missed (later than it, within the limit, or none
    with the reason) and whether the search window reaches the limit."""
    plan_by, limit = target["plan_by"], target["limit"]
    bound = limit or plan_by
    usable = sorted((itinerary for itinerary in itineraries if not any(leg.extra.get("cancelled") for leg in itinerary.legs)),
                    key=lambda itinerary: (itinerary.depart, itinerary.arrive))
    primary = max((itinerary for itinerary in usable if itinerary.arrive <= plan_by),
                  key=lambda itinerary: (itinerary.depart, -itinerary.arrive.timestamp()), default=None)
    later = [itinerary for itinerary in usable if limit is not None and plan_by < itinerary.arrive <= limit]
    fallback = next((itinerary for itinerary in usable if primary is not None and itinerary.depart > primary.depart
                     and itinerary.arrive <= bound), None)
    covered = covers(itineraries, bound, start)
    span = views.window(itineraries, tz, start=start, language=language)
    if covered:
        text = (f"查了 {views.clock(start, tz)} 起出发的班次，查到赶不上 {views.clock(bound, tz)} 的为止" if language == "zh"
                else f"Searched departures from {views.clock(start, tz)} until connections miss {views.clock(bound, tz)}")
    elif not itineraries:
        text = (f"{views.clock(start, tz)} 起没查到班次：不能说没有车，也不能说“最后一班”" if language == "zh"
                else f"No departures found from {views.clock(start, tz)}: do not say there is none or \"the last one\"")
    else:
        reached = views.clock(max(itinerary.depart for itinerary in itineraries), tz) if itineraries else views.clock(start, tz)
        text = (f"窗口只到 {reached}：之后出发的班次没查到，不能说“最后一班”" if language == "zh"
                else f"The window only reaches {reached}: later departures were not found, so do not say \"the last one\"")
    if fallback is not None:
        note = None
    elif primary is None:
        note = "没有主选，也就没有后备" if language == "zh" else "No first choice, so no fallback"
    elif limit is None:
        note = ("计划目标后面没标外部截止：误了主选就赶不上计划目标，更晚的班次没列" if language == "zh"
                else "No external deadline is marked after the plan target: missing the first choice misses the target")
    elif not covered:
        note = "主选之后还有没有班次不知道（见窗口）" if language == "zh" else "Whether anything runs after the first choice is unknown (see the window)"
    else:
        note = (f"主选之后到 {views.clock(bound, tz)} 前没有别的班次赶得上" if language == "zh"
                else f"Nothing after the first choice arrives by {views.clock(bound, tz)}")

    def view(itinerary):
        shown = views.connection_view(itinerary, tz, language)
        shown.update(reaches_plan_target=itinerary.arrive <= plan_by, past_plan_target=itinerary.arrive > plan_by,
                     within_limit=itinerary.arrive <= bound)
        return shown

    return dict(primary=view(primary) if primary else None, later=[view(itinerary) for itinerary in later],
                fallback=view(fallback) if fallback else None, fallback_note=note,
                window=dict(span, by="departure", covers_target=covered, target=views.stamp(bound, tz), text=text),
                connections=[view(itinerary) for itinerary in sorted(itineraries, key=lambda itinerary: itinerary.depart)])


TOLD = 3  # connections a transit result writes as sentences
LEG_KEYS = ("mode", "walk", "line", "from", "to", "depart", "arrive", "minutes")
CONNECTION_KEYS = ("leave", "reach", "minutes", "rides", "walk_minutes", "boards", "alights", "cancelled", "past_plan_target")
LATER_SHOWN = 3


def compact_connection(view: dict | None) -> dict | None:
    """A connection without its ISO times, its one-line summary (the legs say the same) and empty leg fields."""
    if view is None:
        return None
    shown = {key: view[key] for key in CONNECTION_KEYS if key in view}
    for end in ("boards", "alights"):
        if shown.get(end):
            shown[end] = {key: shown[end][key] for key in ("stop", "time", "line")}
    shown["legs"] = [{key: leg[key] for key in LEG_KEYS if leg.get(key) not in (None, "")} | ({"cancelled": True} if leg.get("cancelled") else {})
                     for leg in view["legs"]]
    return shown


def compact_candidate(entry: dict) -> dict:
    """A route to compare or a later departure, compact: when to leave, arrive, the ticket and its rides (the first
    choice's walking legs are in `primary`; `detail="full"` has every leg of every route)."""
    shown = {key: entry[key] for key in ("route", "label", "via", "leave", "reach", "minutes", "past_plan_target", "ticket", "boards",
                                         "alights") if key in entry}
    for end in ("boards", "alights"):
        if shown.get(end):
            shown[end] = {key: shown[end][key] for key in ("stop", "time", "line")}
    shown["rides"] = [{key: leg[key] for key in ("mode", "line", "from", "to", "depart", "arrive") if leg.get(key) not in (None, "")}
                      for leg in entry["legs"] if not leg["walk"]]
    shown["walk_minutes"] = entry.get("walk_minutes")
    return shown


def compact_latest(result: dict) -> dict:
    """What an answer needs from latest_departure (A6): v2d measured 85—87 KB from a street address, above what a host
    takes inline. The first choice, the fallback, the first later ones, the window and the summary; `detail="full"` and
    the evidence record keep the rest."""
    evidence = result["evidence"]
    shown = {key: result[key] for key in ("status", "kind", "timezone", "target", "fallback_note", "window", "summary", "reason",
                                          "sentences", "coverage", "offer", "note", "tickets") if key in result}
    # where to check what the timetable lacks: its text already names the app, the search and the steps
    if result.get("how_to_check"):
        shown["how_to_check"] = {key: result["how_to_check"][key] for key in ("what", "text", "link")}
    shown.update(candidates=[compact_candidate(entry) for entry in result.get("candidates", [])], resolved=result.get("resolved"))
    shown.update(primary=compact_connection(result["primary"]), fallback=compact_connection(result["fallback"]),
                 later=[compact_candidate(view) for view in result["later"][:LATER_SHOWN]], later_count=len(result["later"]),
                 connections_count=len(result["connections"]), detail="compact",
                 evidence={key: evidence[key] for key in ("id", "kind", "source", "fetched_at", "valid_until", "stale", "synthetic")})
    return shown


def candidate_views(routes, primary: dict | None, target: dict, tz: ZoneInfo, language: str, stored: bool = False) -> list[dict]:
    """The first choice and the routes to compare, one entry per distinct connection: route (first_choice, via_station,
    fastest; several when they coincide), label, via (the station), latest time to leave, arrival, ticket."""
    labels = {"first_choice": "主选" if language == "zh" else "first choice",
              "fastest": "最快" if language == "zh" else "fastest"}
    shown: dict[tuple, dict] = {}

    def add(route: str, station: dict, view: dict):
        key = (view["leave"], view["reach"], tuple((leg["line"], leg["depart"]) for leg in view["legs"] if not leg["walk"]))
        label = (f"经 {station.get('name_local') or station.get('name')} 坐火车" if language == "zh"
                 else f"by train from {station.get('name_local') or station.get('name')}") if route == "via_station" else labels[route]
        if key in shown:
            shown[key]["route"].append(route)
            shown[key]["label"] += "、" + label if language == "zh" else ", " + label
            return
        entry = dict(view, route=[route], label=label, past_plan_target=view.get("past_plan_target", False))
        if route == "via_station":
            entry["via"] = station.get("name_local") or station.get("name")
        entry["ticket"] = ticket(view, language, stored)
        shown[key] = entry

    if primary is not None:
        add("first_choice", {}, primary)
    for route, station, itinerary in routes:
        view = views.connection_view(itinerary, tz, language)
        view.update(reaches_plan_target=itinerary.arrive <= target["plan_by"], past_plan_target=itinerary.arrive > target["plan_by"],
                    within_limit=itinerary.arrive <= (target["limit"] or target["plan_by"]))
        add(route, station, view)
    return list(shown.values())


def transit_summary(shown: list[dict], window: dict, language: str) -> str:
    rides = [f"{ride_text(view, language)}" + (f"（{view['leave']} 出门，{view['reach']} 到）" if language == "zh"
                                                 else f" (leave {view['leave']}, arrive {view['reach']})") for view in shown]
    return window["text"] + ("：" if language == "zh" else ": ") + ("、" if language == "zh" else ", ").join(rides) if rides else window["text"]


# --- transit until (A9, D-64): one continuous window, every departure in it --------------------------------------------------

def window_until(itineraries: list[Itinerary], start: datetime, until: datetime, limited: str | None, pages: int,
                 tz: ZoneInfo, language: str) -> dict:
    """The span an until search covered: from `start`, chained page by page without gaps (each page from the last
    departure found), to `until` when the results pass it (`covered`), else to the last departure found — with the
    reason it stopped (the daily budget, or no more departures in the data). Nothing after `to` was searched."""
    covered = bool(itineraries) and max(itinerary.depart for itinerary in itineraries) >= until
    last = max((itinerary.depart for itinerary in itineraries), default=None)
    to = until if covered else last
    begin, end = views.clock(start, tz), views.clock(until, tz)
    if language == "zh":
        if not itineraries:
            text = f"{begin} 起没查到班次；不能说没车，也不能说“最后一班”"
        elif covered:
            text = f"从 {begin} 到 {end} 连续查全，{len(itineraries)} 班；{end} 之后出发的没查"
        elif limited:
            text = f"从 {begin} 连续查到 {views.clock(last, tz)} 为止（查询额度到限：{limited}）；之后出发的没查"
        else:
            text = f"从 {begin} 连续查到 {views.clock(last, tz)} 为止，之后没再查到；不能说“没有更晚的”"
    else:
        if not itineraries:
            text = f"No departures found from {begin}: do not say there is none or \"the last one\""
        elif covered:
            text = f"Searched continuously from {begin} to {end}, {len(itineraries)} found; nothing searched after {end}"
        elif limited:
            text = (f"Searched continuously from {begin} up to {views.clock(last, tz)} (the daily search budget said stop: "
                    f"{limited}); nothing searched after that")
        else:
            text = (f"Searched continuously from {begin} up to {views.clock(last, tz)}, then nothing more; do not say "
                    f"\"nothing later\"")
    return {"from": views.stamp(start, tz), "to": views.stamp(to, tz) if to else None, "by": "departure",
            "count": len(itineraries), "text": text, "continuous": bool(itineraries) and not limited, "covered": covered,
            "until": views.stamp(until, tz), "pages": pages, **({"limited": limited} if limited else {})}


def window_summary(shown: list[dict], window: dict, language: str) -> str:
    """An until window as one sentence a host copies: every departure up to `until` (boarding time and line), the next
    one after it when the search found one, and how far the search went — “Wien Hauptbahnhof → Flughafen Wien
    Bahnhof：22:53 REX 7、…、00:53 REX 7；再下一班 04:22 开；从 21:00 到 01:00 连续查全…”。"""
    until = aware(window["until"]) if window.get("until") else None
    rides = [view for view in shown if not view["cancelled"]]
    within = sorted((view for view in rides if until is None or aware(view["leave_at"]) <= until),
                    key=lambda view: (view["boards"] or {}).get("at") or view["leave_at"])
    after = next((view for view in rides if view not in within), None)

    def board(view):
        return f"{(view['boards'] or {}).get('time') or view['leave']} {(view['boards'] or {}).get('line') or ''}".strip()

    first = (within or rides or [None])[0]
    named = f"{(first['boards'] or {}).get('stop')} → {(first['alights'] or {}).get('stop')}" if first else ""
    listed = ("、" if language == "zh" else ", ").join(board(view) for view in within)
    if after is not None:
        listed += (f"；再下一班 {board(after)} 开" if language == "zh" else f"; the next leaves {board(after)}")
    if not listed:
        return window["text"]
    return (f"{named}：{listed}；{window['text']}" if language == "zh" else f"{named}: {listed}; {window['text']}")


# --- demo data and frames (A10, D-67) ---------------------------------------------------------------------------------------

DEMO = {"zh": "（虚构演示数据，不能用于出行）", "en": "(Fictional demo data, not for travel) "}


def demo_marked(result: dict, language: str) -> dict:
    """Synthetic data says so at the head of every sentence, the first line and the summary, whatever copies them."""
    mark = DEMO.get(language, DEMO["en"])
    result["sentences"] = [mark + sentence for sentence in result.get("sentences") or []]
    for key in ("summary", "first_line"):
        if result.get(key):
            result[key] = mark + result[key]
    return result


def frame_of(kind: str, shown: list[dict | None], first_line: str | None, arguments: dict, *, targets=(), last=()) -> dict:
    """What check_answer holds a second part's first line to (A10): the frame (arrival_deadline, boarding_time or
    last_connection), the times a “最晚/末班” there may name (each connection's leaving and boarding time and its rides'
    departures), the target times a “HH:MM 前” may name, the times of the last connections (last_connection) and the
    server's first line, which is the fix."""
    shown = [view for view in shown if view]
    times = {time for view in shown for time in [view.get("leave"), (view.get("boards") or {}).get("time"),
                                                  *(leg["depart"] for leg in view.get("legs", []) if not leg.get("walk"))] if time}
    return dict(kind=kind, origin=arguments.get("origin"), destination=arguments.get("destination"), times=sorted(times),
                targets=sorted({time for time in targets if time}), last=sorted(set(last)), first_line=first_line)


# --- tonight's last connection (A10, D-67): latest_departure without a deadline --------------------------------------------

SERVICE_END = "03:00"  # "tonight" ends at this local time; the night's last departures run before it
LAST_SPAN = timedelta(hours=6)  # the continuous search starts at most this long before tonight's end
LAST_PAGES = 8  # route searches (of the daily budget) one last-connection answer may use
NIGHT_GAP = timedelta(hours=2)  # "the last one tonight" only when the next departure is at least this much later
NODE_HOURS = 36  # the destination's hard node that still binds lies within this after now
LAST_SAY = {"zh": dict(last="今晚最后一班：最晚 {sentence}", regular="不用另买票的最后一班：最晚 {sentence}",
                       rail="火车最后一班 {board}：最晚 {sentence}", rail_how="这班火车怎么坐：{sentence}",
                       through="{until} 前最后开的是：{sentence}；{until} 之后也有车，下一班 {next} 开",
                       all_night="今晚不用赶：夜里一直有车到 {destination}", all_night_rail="；火车最后一班 {board}（最晚 {leave} 出门）",
                       all_night_node="；之后要守的时间：{node}", node="之后要守的时间：{node}", node_more="另外要守：{node}",
                       partial="查到的班次里最后开的是：{sentence}", next="；下一班 {next} 开",
                       node_plan="你计划{when}{time} 前到（留的余量）：{text}", node_external="{when}{time} {text}",
                       node_unmarked="{when}{time} {text}", external="；{when}{time} {text}", unchecked="（存档，没核）",
                       failed="这次没查到班次（{why}）：还有没有车、几点走都没核，出发前在交通 App 里查",
                       timeout="查询超时", unavailable="数据源没回应",
                       node_latest="{when}直接从 {origin} 去：{sentence}（今晚就到 {destination} 的话用不上）",
                       no_node="计划里没有到 {destination} 之后要守的时间", night="凌晨 ", morning="明早 ", tomorrow="明天 ",
                       plan_found="计划里的 {time} {line} 在这次的查询结果里（{leave} 出门）",
                       plan_found_as="计划里的 {time} {line} 在这次的查询结果里（数据源写作 {found}，{leave} 出门）",
                       plan_missing="计划里的 {time} {line} 不在这次的查询结果里（可能数据源没收，或车次号不同）：要坐它，先在 {app}核一下",
                       app="交通 App"),
            "en": dict(last="Tonight's last connection: {sentence}", regular="The last one without an extra ticket: {sentence}",
                       rail="The last train, {board}: {sentence}", rail_how="How to take that train: {sentence}",
                       through="The last one before {until} is — {sentence}; services go on after {until}, the next leaves {next}",
                       all_night="No need to hurry tonight: services run all night to {destination}",
                       all_night_rail="; the last train {board} (leave by {leave})", all_night_node="; then keep: {node}", node="Then keep: {node}",
                       node_more="Also keep: {node}",
                       partial="The latest found: {sentence}", next="; the next leaves {next}",
                       node_plan="by {when}{time} (your plan's margin) — {text}", node_external="{when}{time} {text}",
                       node_unmarked="{when}{time} {text}", external="; {when}{time} {text}", unchecked=" (archived, not checked)",
                       failed="The departures could not be looked up ({why}): whether anything runs and when to leave are not "
                              "checked; check the transit app before leaving",
                       timeout="the lookup timed out", unavailable="the data source did not answer",
                       node_latest="Straight from {origin} {when}: {sentence} (not needed if you reach {destination} tonight)",
                       no_node="The plan keeps no time after reaching {destination}", night="tonight ", morning="tomorrow morning ",
                       tomorrow="tomorrow ", plan_found="The plan's {time} {line} is in this lookup's results (leave at {leave})",
                       plan_found_as="The plan's {time} {line} is in this lookup's results (the data calls it {found}; leave at {leave})",
                       plan_missing="The plan's {time} {line} is not in this lookup's results (the data may lack it, or its number "
                                    "differs): to take it, check it in {app} first", app="the transit app")}


def tonight_end(arguments: dict, start: datetime, tz: ZoneInfo) -> datetime:
    """Where "tonight" ends: `until` (an HH:MM earlier than `start` is the next day), else the next SERVICE_END."""
    if arguments.get("until"):
        until = moment(arguments["until"], arguments.get("date") or start.astimezone(tz).date().isoformat(), tz, "until")
        if until <= start:
            if len(arguments["until"]) == 5 and not arguments.get("date"):
                return until + timedelta(days=1)
            raise ValueError(f"Invalid query arguments: arguments.until must come after the search start, got {arguments['until']!r}")
        return until
    day = start.astimezone(tz).date()
    for offset in (0, 1, 2):
        found = local_instant((day + timedelta(days=offset)).isoformat(), SERVICE_END, tz.key)
        if found is not None and found > start:
            return found
    return start + LAST_SPAN


def node_after(plan: dict | None, destination: str, after: datetime) -> tuple[str, dict, datetime] | None:
    """The plan's next time to be at the destination after `after` (within NODE_HOURS): a node at that place that is
    not a boarding time (ANCHOR_MEANINGS)."""
    place = match_place(plan, destination)
    if place is None:
        return None
    found = [(day, anchor, instant) for day, anchor, instant in anchors(plan) if instant is not None
             and after < instant <= after + timedelta(hours=NODE_HOURS) and anchor.get("place") == place["id"]
             and anchor_meaning(anchor) == "arrival_deadline"]
    return min(found, key=lambda entry: entry[2], default=None)


def own_words(text: str | None, language: str) -> str:
    """An anchor's own words without any stored last/only/late claim (A11: after a lookup that timed out, the quoted
    archive “计划里写过‘只剩 23:53 末班’” became the answer); the claims stay in turn's hard nodes, marked as archive."""
    from tripwright.render.labels import claim_parts, split_claims

    marked = claim_parts(text or "")
    kept = re.sub(r"（\s*）|\(\s*\)", "", "".join(part for part in split_claims(text or "") if part not in marked)).strip(" ；;，,")
    return kept or ("计划里存的时刻" if language == "zh" else "a time stored in the plan")


def lookup_failed(why: str, language: str) -> dict:
    """What a latest_departure or transit lookup that failed or timed out says (A11): that nothing was checked, never a
    stored “末班、只剩” in place of the lookup (replay: the timed-out lookup was answered with the plan's “23:53 末班”)."""
    said = LAST_SAY.get(language, LAST_SAY["en"])
    line = said["failed"].format(why=said[why])
    return dict(first_line=line, sentences=[line],
                alternative="Say these departures are not checked and where to check them; a last/only claim the plan stores is no answer.")


def day_word(moment_at: datetime, now_at: datetime, tz: ZoneInfo, language: str) -> str:
    """"" for the day of `now_at`, “凌晨 ”/“明早 ”/“明天 ” for the next, else “MM-DD ”."""
    said, local, today = LAST_SAY.get(language, LAST_SAY["en"]), moment_at.astimezone(tz), now_at.astimezone(tz).date()
    if local.date() == today:
        return ""
    if local.date() == today + timedelta(days=1):
        return said["night"] if local.hour < 4 else said["morning"] if local.hour < 12 else said["tomorrow"]
    return local.strftime("%m-%d ")


class QueryRouter:
    def __init__(self, store: Store, *, transit: TransitProvider | None = None,
                 weather: WeatherProvider | None = None, geocode: GeocodeProvider | None = None, mode: str | None = None,
                 pages: PageReader | None = None):
        self.store = store
        self.mode = mode or os.environ.get("TRIPWRIGHT_PROVIDERS", "synthetic")
        if self.mode not in ("synthetic", "live"):
            raise ValueError("TRIPWRIGHT_PROVIDERS must be synthetic or live")
        if self.mode == "live":
            from tripwright.providers.nominatim import NominatimProvider

            self.transit = transit or TransitousProvider()
            self.weather = weather or OpenMeteoProvider()
            self.geocode = geocode or NominatimProvider()
            self.pages = pages or PageReader()
        else:
            self.transit = transit or DemoTransit()
            self.weather = weather or DemoWeather()
            self.geocode = geocode or DemoGeocode()
            self.pages = pages  # synthetic: no page is fetched unless a reader is given

    async def query(self, owner: str, kind: str, arguments: dict, plan: dict | None, *, zone: str | None = None,
                    at: str | None = None, language: str = "zh") -> dict:
        """`zone` is the trip's time zone (the service's; `arguments.timezone` overrides it); `at` is now."""
        with request_scope(client=owner):
            if kind == "latest_departure":
                return await self._latest(owner, arguments, plan, zone, at, language)
            if kind == "web_evidence":
                return await self._web(owner, arguments, language)
            if kind == "open_places":
                return dict(self._open_places(arguments, plan, zone, at, language), offer=trip_sources(self.store, owner, plan, None, language)[1])
            return await self._query(owner, kind, arguments, plan, zone=zone, language=language)

    def _zone(self, arguments: dict, plan: dict | None, zone: str | None) -> str | None:
        return arguments.get("timezone") or (plan or {}).get("timezone") or zone

    async def _latest(self, owner: str, arguments: dict, plan: dict | None, zone: str | None, at: str | None, language: str,
                      *, compare: bool = True) -> dict:
        """The latest departure for a deadline: a hard node (`anchor_id`; its fields say whether it is a boarding time or
        a time to be there), `by` or `board_at`. Without any of them, tonight's last connection (A10). `compare=False`
        skips the routes to compare (a hard node behind a last-connection answer)."""
        name = self._zone(arguments, plan, zone)
        if not name:
            raise ValueError("latest_departure needs the trip's time zone: commit a plan or pass arguments.timezone")
        tz = ZoneInfo(name)
        if not any(arguments.get(key) for key in ("anchor_id", "by", "board_at")):
            return await self._last_connection(owner, arguments, plan, tz, at, language)
        target = deadline_target(plan, arguments, tz)
        asked = arguments["destination"]
        if target["board_place"]:
            # a boarding node is caught at its place (A10): search there, as board_at does, whatever destination was named
            arguments = dict(arguments, destination=target["board_place"])
        now_at = aware(at) if at else clock()
        start = moment(arguments["after"], arguments.get("date"), tz, "after") if arguments.get("after") else \
            max(now_at, target["plan_by"] - LOOKBACK)
        bound = target["limit"] or target["plan_by"]
        shown_target = dict(anchor_id=target["anchor_id"], date=target["date"], text=target["text"], deadline=target["deadline"],
                            meaning=target["meaning"],
                            time=views.clock(target["at"], tz), at=views.stamp(target["at"], tz), buffer_minutes=target["buffer_minutes"],
                            plan_by=views.clock(target["plan_by"], tz), plan_by_at=views.stamp(target["plan_by"], tz),
                            limit=views.clock(target["limit"], tz) if target["limit"] else None,
                            limit_at=views.stamp(target["limit"], tz) if target["limit"] else None, limit_from=target["limit_from"],
                            **(dict(board_place=place_label(plan, target["board_place"]), line=target["line"],
                                    toward=place_label(plan, target["toward"])) if target["board_place"] else {}))
        if start >= bound:
            return dict(status="unverified", kind="latest_departure", timezone=name, target=shown_target,
                        reason=("目标时刻已过，或 after 晚于目标" if language == "zh" else "The target time has passed, or after is later than the target"),
                        consequence="No departure can reach this target.", alternative="Ask what the user does now; check the next option by hand.")
        origin, destination = await self._where(plan, arguments, "origin"), await self._where(plan, arguments, "destination")
        resolved = {end: dict(said=arguments[end], place=(match_place(plan, arguments[end]) or {}).get("id"), looked_up=where)
                    for end, where in (("origin", origin), ("destination", destination))}
        if asked != arguments["destination"]:
            resolved["destination"].update(said=asked, boarding=True)
        extra = {"timezone_name": name} if isinstance(self.transit, TransitousProvider) else {}
        try:
            found, sources = await self._pages(origin, destination, start, bound, extra)
            self._boarding_or_deadline(arguments, target, found, tz)
            candidates = await self._candidates(plan, arguments, found, target, start, bound, extra, sources) if compare else []
        except ProviderUnavailable as error:
            return dict(status="unverified", kind="latest_departure", timezone=name, target=shown_target, provider=error.provider,
                        reason=error.reason, retryable=error.retryable, consequence="The latest departure could not be computed.",
                        **lookup_failed("unavailable", language))
        result = dict(kind="latest_departure", timezone=name, target=shown_target, resolved=resolved,
                      **latest_choice(found, target, start, tz, language))
        tickets = stored_tickets(plan, target["date"], language)
        result["candidates"] = candidate_views(candidates, result["primary"], target, tz, language, bool(tickets))
        meta = self._meta(sources[:1] and sources[0], sources)
        notes = self._coverage(meta, language)
        region = feed_region(self._feeds(meta))
        result["searched"] = searched(meta)
        if not found and any(note["kind"] in ("not_yet", "too_old") for note in notes):
            result["window"]["text"] = next(note["text"] for note in notes if note["kind"] in ("not_yet", "too_old"))
        result["coverage"] = notes
        stop = shown_target.get("board_place") or (end_name(plan, arguments["destination"], meta.get("destination"))
                                                   if target["meaning"] == "boarding_time" else None)
        told = latest_sentences(result, target, found, start, tz, language, stop=stop)
        result["sentences"] = head_sentences(meta, notes, language) + told + ([tickets["text"]] if tickets else [])
        result["tickets"] = tickets
        result["first_line"] = told[0] if told else None
        reports, result["offer"] = trip_sources(self.store, owner, plan, region, language)
        result["how_to_check"] = how_to_check(result["primary"] or (result["later"] or [None])[0],
                                              (end_name(plan, arguments["origin"], meta.get("origin")),
                                               end_name(plan, arguments["destination"], meta.get("destination"))), region, notes, language,
                                              reports)
        result["note"] = views.result_note(result["connections"], language)
        source = sources[0].source if sources else "unknown"
        expiring = [sourced.valid_until for sourced in sources if sourced.valid_until is not None]
        synthetic_data = source.startswith("demo:")
        stale = any(sourced.stale for sourced in sources)
        result["summary"] = latest_summary(result, tz, language)
        if synthetic_data:
            demo_marked(result, language)
        frame = frame_of(target["meaning"], [result["primary"], *result["later"], *result["candidates"]], result["first_line"],
                         arguments, targets=[shown_target["time"], shown_target["plan_by"]])
        evidence = dict(id=identifier("EV"), kind="latest_departure", source=source,
                        fetched_at=min(sourced.fetched_at for sourced in sources).isoformat(),
                        valid_until=min(expiring).isoformat() if expiring else None, stale=stale,
                        notes=list(dict.fromkeys(note for sourced in sources for note in sourced.notes)),
                        date=target["date"], place_id=None, facts=[], synthetic=synthetic_data,
                        question=dict(origin=arguments["origin"], destination=asked,
                                      **{key: arguments[key] for key in ("anchor_id", "by", "board_at", "deadline", "external_deadline", "after") if key in arguments}),
                        value={**{key: result[key] for key in ("target", "primary", "later", "fallback", "fallback_note", "window", "candidates")},
                               "departures": departure_rows(found, tz, bound), "frame": frame},
                        coverage=notes, sentences=result["sentences"])
        self.store.put("evidence", owner, evidence, insert=True)
        uncovered = not found and any(note["kind"] in ("not_yet", "too_old") for note in notes)
        result.update(status="unverified" if synthetic_data or stale or uncovered else "sourced", evidence=evidence)
        if synthetic_data or stale:
            result.update(reason="Synthetic or stale timetable data; it cannot be called checked.")
        elif uncovered:
            result.update(reason=result["window"]["text"])
        return result if arguments.get("detail") == "full" else compact_latest(result)

    async def _last_connection(self, owner: str, arguments: dict, plan: dict | None, tz: ZoneInfo, at: str | None,
                               language: str) -> dict:
        """latest_departure without a deadline (A10, D-67): the frame of “最晚几点出发去 X” is the server's, not the
        host's. Every departure from now (at most LAST_SPAN before tonight's end) up to tonight's end (`until`, default
        the next SERVICE_END) is searched continuously, paged as transit's until (A9) within the daily budget. Returns
        tonight's last connection with when to leave the origin (changes included), how far the search went, and the
        plan's next hard node at the destination with its own latest departure; with none, no deadline is made up."""
        said, name = LAST_SAY.get(language, LAST_SAY["en"]), tz.key
        now_at = aware(at) if at else clock()
        after = moment(arguments["after"], arguments.get("date"), tz, "after") if arguments.get("after") else None
        until = tonight_end(arguments, after or now_at, tz)
        start = after or max(now_at, until - LAST_SPAN)
        shown_target = dict(meaning="last_connection", deadline=None, anchor_id=None, date=until.astimezone(tz).date().isoformat(),
                            time=views.clock(until, tz), until=views.clock(until, tz), until_at=views.stamp(until, tz),
                            plan_by=None, limit=None)
        origin, destination = await self._where(plan, arguments, "origin"), await self._where(plan, arguments, "destination")
        resolved = {end: dict(said=arguments[end], place=(match_place(plan, arguments[end]) or {}).get("id"), looked_up=where)
                    for end, where in (("origin", origin), ("destination", destination))}
        extra = {"timezone_name": name} if isinstance(self.transit, TransitousProvider) else {}
        try:
            found, sources, limited = await self._window(origin, destination, start, until, extra, pages=LAST_PAGES)
        except ProviderUnavailable as error:
            return dict(status="unverified", kind="latest_departure", timezone=name, target=shown_target,
                        provider=error.provider, reason=error.reason, retryable=error.retryable,
                        consequence="Tonight's last connection could not be looked up.", **lookup_failed("unavailable", language))
        span = window_until(found, start, until, limited, len(sources), tz, language)
        if not span["covered"] and not limited and found and len(sources) >= LAST_PAGES:
            reached = views.clock(max(itinerary.depart for itinerary in found), tz)
            span["text"] = (f"从 {views.clock(start, tz)} 连续查到 {reached} 为止；{reached} 之后出发的没查"
                            if language == "zh" else f"Searched continuously from {views.clock(start, tz)} up to {reached}; "
                                                    f"nothing searched after {reached}")
        usable = sorted((itinerary for itinerary in found if not cancelled(itinerary)), key=lambda itinerary: itinerary.depart)
        tonight = [itinerary for itinerary in usable if itinerary.depart <= until]
        latest = max(tonight, key=lambda itinerary: (itinerary.depart, -itinerary.arrive.timestamp()), default=None)
        following = next((itinerary for itinerary in usable if itinerary.depart > until), None)
        night_break = bool(span["covered"] and latest and following and following.depart - latest.depart >= NIGHT_GAP)
        all_night = bool(latest and span["covered"] and following and not night_break)
        tickets = stored_tickets(plan, start.astimezone(tz).date().isoformat(), language)

        def view(itinerary):
            shown = views.connection_view(itinerary, tz, language)
            return dict(shown, ticket=ticket(shown, language, bool(tickets)))

        last = view(latest) if latest else None
        regular = None
        if night_break and last["ticket"]["need"] == "extra":
            plain = [itinerary for itinerary in tonight if ticket(views.connection_view(itinerary, tz, language), language)["need"] != "extra"]
            regular = view(max(plain, key=lambda itinerary: itinerary.depart)) if plain else None
        # services run through the night (night buses), or the last one is no train: also the last train (A10, real data:
        # Wien Hbf → airport, REX until 00:53, airport buses all night)
        trains = [itinerary for itinerary in tonight if arrives_by_rail(itinerary)]
        rail_last = max(trains, key=lambda itinerary: itinerary.depart) if trains and span["covered"] else None
        rail = view(rail_last) if rail_last is not None and rail_last is not latest and (regular is None or
                                                                                       rail_last.depart != aware(regular["leave_at"])) else None
        nxt = view(following) if following else None
        # the connection the first line recommends (A11 D): the last train it names while services run all night, the
        # next one when it names none; else the last one (without an extra ticket). how_to_check points there, never at
        # the night's last bus (replay: the link opened 03:03 while the answer took the 00:53 REX)
        if last and span["covered"] and nxt and not night_break:
            main = rail or (view(tonight[0]) if tonight else last)
        else:
            main = regular or last
        next_text = (f"{day_word(aware((nxt['boards'] or {}).get('at') or nxt['leave_at']), start, tz, language)}{(nxt['boards'] or {}).get('time') or nxt['leave']} "
                     f"{(nxt['boards'] or {}).get('line') or ''}").strip() if nxt else None

        def told(entry):
            text = views.sentence(entry, language)
            return text + (f"（{entry['ticket']['why']}）" if language == "zh" else f" ({entry['ticket']['why']})") \
                if entry["ticket"]["need"] == "extra" else text

        meta = self._meta(sources[:1] and sources[0], sources)
        notes = self._coverage(meta, language)
        region = feed_region(self._feeds(meta))
        if not found and any(note["kind"] in ("not_yet", "too_old") for note in notes):
            span["text"] = next(note["text"] for note in notes if note["kind"] in ("not_yet", "too_old"))
        hard = await self._node_latest(owner, arguments, plan, node_after(plan, arguments["destination"], start), tz, start, at, language)
        rail_board = f"{train_ride(rail)['depart']} {train_ride(rail)['line'] or ''}".strip() if rail else None
        reports, offer = trip_sources(self.store, owner, plan, region, language)
        ends = (end_name(plan, arguments["origin"], meta.get("origin")), end_name(plan, arguments["destination"], meta.get("destination")))
        checking = how_to_check(main, ends, region, notes, language, reports)
        rides = plan_rides(plan, arguments["destination"], found, start, until, tz, language, (checking or {}).get("app"))
        # A14 (quality judge 7, 3 of 3 runs): “怎么查” searched the night's last train while the answer's first choice was the
        # plan's 22:42. The plan's own train, when this lookup has it, is the one to check; the last one gets its own line.
        planned = next((ride["itinerary"] for ride in rides if ride["itinerary"] is not None and ride["itinerary"].depart >= start), None)
        if planned is not None:
            main = view(planned)
            checking = how_to_check(main, ends, region, notes, language, reports)
            fallback = rail or last
            missed = how_to_check(fallback, ends, region, notes, language, reports) if fallback and fallback["leave"] != main["leave"] else None
            if checking and missed:
                checking = dict(checking, text=checking["text"] + "\n" + HOW.get(language, HOW["en"])["missed"].format(
                    app=missed["app"] + (" " if language == "zh" and missed["app"][-1].isascii() else ""), query=missed["search"],
                    clock=missed["clock"]), last=dict(search=missed["search"], clock=missed["clock"], link=missed["link"]))
        rides = [{key: value for key, value in ride.items() if key != "itinerary"} for ride in rides]
        lines = []
        if last and night_break:
            lines.append(said["last"].format(sentence=told(last)) + said["next"].format(next=next_text))
            if regular:
                lines.append(said["regular"].format(sentence=told(regular)))
        elif last and span["covered"] and nxt:
            # services run on past tonight's end (A10 replay): no hurry is the answer; then the last train and the next
            # time to keep, never “03:00 前最后开的是 02:43” as the first line
            lines.append(said["all_night"].format(destination=arrival_name(plan, arguments["destination"], meta.get("destination"), last, language))
                         + (said["all_night_rail"].format(board=rail_board, leave=rail["leave"]) if rail else "")
                         + (said["all_night_node"].format(node=hard["core"]) if hard else ""))
        if rail and span["covered"]:
            # all night, the first line named the last train already: this line says how to take it (A12, said once)
            lines.append(said["rail_how" if all_night else "rail"].format(board=rail_board, sentence=told(rail)))
        if last and span["covered"] and nxt and not night_break:
            lines.append(said["through"].format(until=views.clock(until, tz), sentence=told(last), next=next_text))
        if last and not span["covered"]:
            lines.append(said["partial"].format(sentence=told(last)))
        lines.append(span["text"])
        lines += [ride["sentence"] for ride in rides]  # the plan's own train: in these results or not (a12b)
        node_line = []
        if hard:
            node_lines, more = hard.pop("lines"), hard.pop("more")
            if all_night:  # the first line said the next time to keep: only what it left out (A12, said once)
                node_lines = ([said["node_more"].format(node=more)] if more else []) + node_lines[1:]
            node_line = node_lines[:1] if not all_night or more else []
            lines += node_lines
            hard.pop("core")
        elif match_place(plan, arguments["destination"]):
            lines.append(said["no_node"].format(destination=end_name(plan, arguments["destination"], None)))
        connections = [view(itinerary) for itinerary in sorted(found, key=lambda itinerary: itinerary.depart)]
        result = dict(kind="latest_departure", timezone=name, target=shown_target, resolved=resolved,
                      first_line=lines[0], sentences=head_sentences(meta, notes, language) + lines + ([tickets["text"]] if tickets else []),
                      summary=("；" if language == "zh" else "; ").join(dict.fromkeys(lines[:1] + [span["text"]] + node_line)),
                      last=last, last_without_extra_ticket=regular, last_train=rail, next_after=nxt, tonight_ends_after_last=night_break,
                      window=dict(span, covers_target=span["covered"], target=views.stamp(until, tz)), hard_node=hard,
                      coverage=notes, offer=offer, searched=searched(meta), note=views.result_note(connections, language),
                      tickets=tickets, plan_rides=rides, how_to_check=checking, connections=connections)
        source = sources[0].source if sources else "unknown"
        synthetic_data, stale = source.startswith("demo:"), any(sourced.stale for sourced in sources)
        if synthetic_data:
            demo_marked(result, language)
        backed = [entry for entry in (last, regular) if entry] if night_break else []
        backed += [rail] if rail else []
        frame = frame_of("last_connection", [last, regular, rail], result["first_line"], arguments,
                         last=[time for entry in backed for time in (entry["leave"], (entry["boards"] or {}).get("time")) if time]
                         + ([train_ride(rail)["depart"]] if rail else []))
        # what a later turn may quote from this lookup (A12: the first part of the next question, marked “查过”)
        # the connection “怎么查” searches (A14): check_answer holds the answer's search to it
        frame["main"] = sorted({time for time in [main["leave"], *(leg["depart"] for leg in main["legs"] if not leg["walk"])] if time}) if main else []
        frame["told"] = dict(all_night=all_night, night_break=night_break, until=views.stamp(until, tz),
                             origin=resolved["origin"]["place"], destination=resolved["destination"]["place"],
                             rail=dict(train_ride(rail), leave=rail["leave"]) if rail else None,
                             last=dict(last["boards"], leave=last["leave"]) if last and night_break and last.get("boards") else None)
        expiring = [sourced.valid_until for sourced in sources if sourced.valid_until is not None]
        rows = departure_rows(found, tz, until)
        for row, itinerary in zip(rows, sorted(found, key=lambda itinerary: itinerary.depart)):
            row["in_time"] = itinerary.depart <= until  # tonight: leaving by tonight's end
        evidence = dict(id=identifier("EV"), kind="latest_departure", source=source,
                        fetched_at=min(sourced.fetched_at for sourced in sources).isoformat(),
                        valid_until=min(expiring).isoformat() if expiring else None, stale=stale,
                        notes=list(dict.fromkeys(note for sourced in sources for note in sourced.notes)),
                        date=shown_target["date"], place_id=None, facts=[], synthetic=synthetic_data,
                        question=dict(origin=arguments["origin"], destination=arguments["destination"], frame="last_connection",
                                      until=views.stamp(until, tz)),
                        value=dict(target=shown_target, primary=last, later=[], fallback=None, fallback_note=None,
                                   window=result["window"], departures=rows,
                                   candidates=[entry for position, entry in enumerate((regular, rail, main))
                                               if entry and entry is not last and entry not in (regular, rail, main)[:position]],
                                   frame=frame),
                        coverage=notes, sentences=result["sentences"])
        self.store.put("evidence", owner, evidence, insert=True)
        uncovered = not found and any(note["kind"] in ("not_yet", "too_old") for note in notes)
        result.update(status="unverified" if synthetic_data or stale or uncovered else "sourced", evidence=evidence)
        if synthetic_data or stale:
            result.update(reason="Synthetic or stale timetable data; it cannot be called checked.")
        elif uncovered or not span["covered"]:
            result.update(reason=span["text"])
        if arguments.get("detail") == "full":
            return result
        shown = {key: value for key, value in result.items() if key not in ("connections", "evidence")}
        shown.update(last=compact_connection(last) and dict(compact_connection(last), ticket=last["ticket"]),
                     last_without_extra_ticket=regular and dict(compact_connection(regular), ticket=regular["ticket"]),
                     last_train=rail and dict(compact_connection(rail), ticket=rail["ticket"]),
                     next_after=compact_connection(nxt), connections_count=len(connections), detail="compact",
                     evidence={key: evidence[key] for key in ("id", "kind", "source", "fetched_at", "valid_until", "stale", "synthetic")})
        if result.get("how_to_check"):
            shown["how_to_check"] = {key: result["how_to_check"][key] for key in ("what", "text", "link", "last") if key in result["how_to_check"]}
        return shown

    async def _node_latest(self, owner: str, arguments: dict, plan: dict | None, node, tz: ZoneInfo, now_at: datetime,
                           at: str | None, language: str) -> dict | None:
        """The destination's next hard node (node_after) behind a last-connection answer: what it is, the external
        deadline behind a plan target, its own latest departure from the origin (a latest_departure lookup of its own,
        without routes to compare) and the sentences that say them."""
        if node is None:
            return None
        from tripwright.packs import last_check

        said = LAST_SAY.get(language, LAST_SAY["en"])
        day, anchor, instant = node
        # A14 (quality judge 7): the plan's own margin (“05:45 前到证件柜台排队最稳”) got its own “要 05:45 前到，最晚 05:12
        # 出门”, read as having to be at the airport by then. A plan target says it is the plan's margin; the latest time to
        # leave is the one for the operator's deadline behind it, the time one must be there; none without one.
        _, behind = external_after(plan, {}, instant, anchor, tz) if anchor.get("deadline") == "plan" else (None, None)
        linked = next(((day_of, entry, moment_at) for day_of, entry, moment_at in anchors(plan) if entry["id"] == behind), None)
        external = linked[1:] if linked else None
        keep = (behind, linked[0]) if linked else (anchor["id"], day) if anchor.get("deadline") != "plan" else None
        try:
            found = await self._latest(owner, dict(origin=arguments["origin"], destination=arguments["destination"],
                                                   anchor_id=keep[0], date=keep[1], detail="full"), plan, tz.key, at, language,
                                       compare=False) if keep else {}
        except ValueError:
            found = {}
        target = found.get("target") or {}
        # a time no source checked is quoted as archive (A11: “07:05 证件柜台关” written as a fact), the plan's own target not
        checked = last_check(self.store, owner, anchor.get("evidence_ids", []))
        deadline = anchor.get("deadline") or ("external" if checked else "unmarked")
        when, time, text = day_word(instant, now_at, tz, language), views.clock(instant, tz), own_words(anchor.get("text"), language)
        core = said[{"plan": "node_plan", "external": "node_external"}.get(deadline, "node_unmarked")].format(when=when, time=time, text=text) \
            + (said["unchecked"] if deadline != "plan" and not checked else "")
        line = core
        if external:
            entry, moment_at = external
            line += said["external"].format(when=day_word(moment_at, now_at, tz, language), time=views.clock(moment_at, tz),
                                            text=own_words(entry.get("text"), language)) \
                + ("" if last_check(self.store, owner, entry.get("evidence_ids", [])) else said["unchecked"])
        lines = [said["node"].format(node=line)]
        more = line[len(core):].lstrip("；; ") or None  # the external deadline behind a plan target, said apart when the core was
        first = found.get("first_line")
        if first and found.get("primary"):
            origin = end_name(plan, arguments["origin"], None) or ("起点" if language == "zh" else "the start")
            when = day_word(external[1], now_at, tz, language) if external else when
            lines.append(said["node_latest"].format(when=when.strip() if language == "zh" else when, origin=origin, sentence=first,
                                                    destination=end_name(plan, arguments["destination"], None) or arguments["destination"]))
        primary = found.get("primary") or {}
        return dict(anchor_id=anchor["id"], date=day, time=time, text=text, deadline=deadline,
                    external=dict(anchor_id=external[0]["id"], time=views.clock(external[1], tz), text=own_words(external[0].get("text"), language))
                    if external else None,
                    latest=dict(leave=primary.get("leave"), board=(primary.get("boards") or {}).get("time"), first_line=first,
                                status=found.get("status"), window=(found.get("window") or {}).get("text"),
                                # what the node's own evidence holds, for a check that sees only this result (a replay)
                                covers_target=bool((found.get("window") or {}).get("covers_target")),
                                target={key: target.get(key) for key in ("time", "plan_by", "limit")},
                                connection=compact_connection(found["primary"]) if found.get("primary") else None) if found else None,
                    core=core, more=more, lines=lines)

    async def _where(self, plan: dict | None, arguments: dict, end: str) -> str:
        """Where to look up a lookup's end: a trip place by its stored stop or coordinates (place_location), never by its
        name. A name the trip does not store is resolved by the provider first, and refused when it lands more than
        FAR_KM from every place of this trip (A11 replay: a climbing gym's name → Hannover Ahlem, a route via
        Cologne and Munich), never used silently."""
        value = arguments[end]
        where = place_location(plan, value)
        resolve = getattr(self.transit, "resolve", None)
        region = [tuple(place["coords"]) for place in (plan or {}).get("places", []) if place.get("coords")]
        if where != value or coordinates(plan, value) is not None or (re.search(r"[:_]", value) and " " not in value) \
                or not region or resolve is None:
            return where
        try:
            found = (await resolve(value)).value
        except ProviderUnavailable:
            return where  # the search says why
        if found.get("lat") is None:
            return where
        away = min(kilometres((found["lat"], found["lon"]), place) for place in region)
        if away > FAR_KM:
            raise ValueError(f"Invalid query arguments: arguments.{end} {value!r} is no place of this trip and was looked up by "
                             f"name as {found.get('label')!r} ({found['lat']:.4f},{found['lon']:.4f}), {away:.0f} km from the "
                             f"nearest place of this trip, so it is not used. Send the trip's place ID, or the coordinates "
                             f"\"lat,lon\" or stop ID of where you mean.")
        return where

    def _meta(self, first, sources: list[Sourced]) -> dict:
        """The question as the transit provider answered it (its first lookup's ends), with every day searched and every
        stop passed (A8). {} for a provider that does not say."""
        meta = dict(getattr(first, "meta", None) or {}) if first else {}
        if meta:
            meta["days"] = sorted({day for sourced in sources for day in (getattr(sourced, "meta", None) or {}).get("days", [])})
            meta["stops"] = sorted({stop for sourced in sources for stop in (getattr(sourced, "meta", None) or {}).get("stops", [])})
        return meta

    def _coverage(self, meta: dict, language: str) -> list[dict]:
        assess = getattr(self.transit, "coverage", None)
        return assess(meta, language) if meta and assess else []

    def _feeds(self, meta: dict) -> set[str]:
        files = getattr(self.transit, "feed_lists", None)
        return files(meta) if meta and files else set()

    async def _pages(self, origin: str, destination: str, start: datetime, bound: datetime, extra: dict) -> tuple[list[Itinerary], list[Sourced]]:
        """Departures from `start`, searched on from the last one found until the results pass `bound` (A5)."""
        found, seen, sources, when = [], set(), [], start
        for _ in range(PAGES):
            sourced = await self.transit.plan(origin, destination, when, limit=8, **extra)
            sources.append(sourced)
            new = []
            for itinerary in sourced.value:
                key = (itinerary.depart.timestamp(), itinerary.arrive.timestamp(), tuple(leg.line for leg in itinerary.legs))
                if itinerary.depart >= start and key not in seen:
                    seen.add(key)
                    new.append(itinerary)
            found += new
            if not new or covers(found, bound, start):
                break
            when = max(itinerary.depart for itinerary in found) + timedelta(minutes=1)
        return found, sources

    def _boarding_or_deadline(self, arguments: dict, target: dict, found: list[Itinerary], tz: ZoneInfo) -> None:
        """A9, D-64: `by` is an arrival deadline at the destination, never a vehicle's boarding time on the way. When
        nothing arrives by it but a found connection boards within two minutes of it, the host took a boarding time
        for the deadline: refuse with the fix (board_at, or the real deadline)."""
        if not arguments.get("by") or not found:
            return
        if any(itinerary.arrive <= target["plan_by"] and not cancelled(itinerary) for itinerary in found):
            return
        for itinerary in found:
            for leg in itinerary.legs:
                if leg.mode == "walk" or abs((leg.depart - target["plan_by"]).total_seconds()) > 120:
                    continue
                clock = views.clock(leg.depart, tz)
                raise ValueError(
                    f"Invalid query arguments: arguments.by is the deadline to BE AT {arguments['destination']}, not a "
                    f"boarding time on the way: nothing reaches it by {views.clock(target['plan_by'], tz)}, but a "
                    f"vehicle boards at {leg.origin} at {clock}. To catch that one: board_at=\"{clock}\" with "
                    f"destination=\"{leg.origin}\". To keep an arrival deadline: pass the time to be at "
                    f"{arguments['destination']} (or answer honestly that nothing makes it).")

    async def _window(self, origin: str, destination: str, start: datetime, until: datetime, extra: dict,
                      pages: int = PAGES) -> tuple[list[Itinerary], list[Sourced], str | None]:
        """Every departure from `start` up to `until` (A9, D-64): page on from the last one found (each page is one
        route search of the daily budget) until the results pass `until`, a page finds nothing new, or the budget says
        stop — then `limited` holds the reason and the answer says how far it reached. At most `pages` searches."""
        found, seen, sources, when, limited = [], set(), [], start, None
        for _ in range(pages):
            try:
                sourced = await self.transit.plan(origin, destination, when, limit=8, **extra)
            except ProviderUnavailable as error:
                if not found:
                    raise
                limited = error.reason
                break
            sources.append(sourced)
            new = []
            for itinerary in sourced.value:
                key = (itinerary.depart.timestamp(), itinerary.arrive.timestamp(), tuple(leg.line for leg in itinerary.legs))
                if itinerary.depart >= start and key not in seen:
                    seen.add(key)
                    new.append(itinerary)
            found += new
            if (found and max(itinerary.depart for itinerary in found) >= until) or not new:
                break
            when = max(itinerary.depart for itinerary in found) + timedelta(minutes=1)
        return found, sources, limited

    async def _candidates(self, plan, arguments, found, target, start, bound, extra, sources) -> list[tuple[str, dict, Itinerary]]:
        """Routes to compare with the first choice (A7): by train from each of the trip's stations toward the
        destination (the latest train that reaches the plan target, else the limit, and the latest connection to the
        station that catches it), and the fastest connection that reaches the plan target (else the limit; of equal
        ones the latest). `sources` gains their lookups."""
        routes = []
        usable = [itinerary for itinerary in found if not cancelled(itinerary)]
        usable = [itinerary for itinerary in usable if itinerary.arrive <= target["plan_by"]] or \
            [itinerary for itinerary in usable if itinerary.arrive <= bound]
        if usable:
            routes.append(("fastest", {}, min(usable, key=lambda itinerary: (itinerary.arrive - itinerary.depart, -itinerary.depart.timestamp()))))
        origin, destination = arguments["origin"], arguments["destination"]
        for station in stations(plan, origin, destination):
            where = location_of(station)
            trains, more = await self._pages(where, place_location(plan, destination), start, bound, extra)
            sources += more
            trains = [itinerary for itinerary in trains if by_rail(itinerary) and not cancelled(itinerary)]
            ordered = sorted([itinerary for itinerary in trains if itinerary.arrive <= target["plan_by"]] or
                             [itinerary for itinerary in trains if itinerary.arrive <= bound], key=lambda itinerary: itinerary.depart, reverse=True)
            if not ordered:
                continue
            here, there = coordinates(plan, origin), tuple(station["coords"]) if station.get("coords") else None
            if here is not None and there is not None and kilometres(here, there) < 0.3:
                routes.append(("via_station", station, ordered[0]))
                continue
            feeders, more = await self._pages(place_location(plan, origin), where, start, ordered[0].depart - TRANSFER, extra)
            sources += more
            feeders = [itinerary for itinerary in feeders if not cancelled(itinerary)]
            for train in ordered[:3]:
                catching = [itinerary for itinerary in feeders if itinerary.arrive <= train.depart - TRANSFER]
                if catching:
                    feeder = max(catching, key=lambda itinerary: (itinerary.depart, -itinerary.arrive.timestamp()))
                    routes.append(("via_station", station, Itinerary(feeder.legs + train.legs)))
                    break
        return routes

    def _open_places(self, arguments: dict, plan: dict | None, zone: str | None, at: str | None, language: str) -> dict:
        """The trip's places open at a moment (default now) within `km` (default 2) of `near`, nearest first, with when
        each closes and where its hours were read (A7); places whose hours are not stored are listed apart. `text`
        filters by words of the name, kind or notes (e.g. "restaurant")."""
        name = self._zone(arguments, plan, zone) or "UTC"
        tz = ZoneInfo(name)
        when = moment(arguments["at"], arguments.get("date"), tz, "at") if arguments.get("at") else (aware(at) if at else clock())
        here = coordinates(plan, arguments["near"])
        if here is None:
            raise ValueError(f"Invalid query arguments: arguments.near: not a place of this trip with coordinates, nor 'lat,lon', "
                             f"got {arguments['near']!r}")
        local = when.astimezone(tz)
        reach = arguments.get("km", 2)
        rows = place_rows(plan, here, local, reach, str(arguments.get("text") or "").casefold().split(), language)
        opened = [row for row in rows if row["open"] is True]
        unknown = [row for row in rows if row["open"] is None]
        moment_text = views.clock(when, tz)
        if language == "zh":
            summary = (f"{moment_text} 开着、离 {arguments['near']} {reach:g} 公里内：" + ("、".join(
                f"{row['name_local']}（{row['distance_km']:g} 公里，开到 {row['closes']}）" for row in opened) or "没有存着营业时间又开着的")
                + (f"；营业时间没存：{'、'.join(row['name_local'] for row in unknown)}" if unknown else ""))
        else:
            summary = (f"Open at {moment_text} within {reach:g} km of {arguments['near']}: " + (", ".join(
                f"{row['name_local']} ({row['distance_km']:g} km, until {row['closes']})" for row in opened) or "none with stored hours")
                + (f"; hours not stored: {', '.join(row['name_local'] for row in unknown)}" if unknown else ""))
        return dict(status="stored", kind="open_places", timezone=name, at=views.stamp(when, tz), weekday=local.isoweekday(),
                    near=dict(said=arguments["near"], place=(match_place(plan, arguments["near"]) or {}).get("id")), km=reach,
                    open=opened, closed=[{key: row[key] for key in ("id", "name_local", "distance_km", "opening_hours")} for row in rows if row["open"] is False],
                    unknown=unknown, summary=summary,
                    notice="Opening hours as the trip stores them, with where they were read; a place without stored hours is not known to be open.")

    async def _web(self, owner: str, arguments: dict, language: str) -> dict:
        read_from = arguments.get("read_from", "page")
        found, check = "unchecked", None
        if read_from == "search_snippet":
            check = "A search snippet is not the page; the quote was not checked."
        elif arguments.get("check_quote") is False:
            check = "The quote check was not asked for."
        elif self.pages is None:
            check = "No page reader here (synthetic providers); the quote was not checked."
        else:
            try:
                found = quote_on(arguments["quote"], await self.pages.text(arguments["url"]))
                check = None if found else "The quote is not in the fetched page's text (a page built by scripts may hide it)."
            except PageUnavailable as error:
                check = f"The page could not be checked: {error.reason}"
        evidence = web_record(arguments, quote_found=found, check=check)
        self.store.put("evidence", owner, evidence, insert=True)
        counted = evidence["counts_as_checked"]
        quote_word = {True: "原句在页面上", False: "原句不在抓到的页面上", "unchecked": "原句没核"}[found] if language == "zh" else \
            {True: "quote found on the page", False: "quote not on the fetched page", "unchecked": "quote not checked"}[found]
        why = {"it is a search snippet, not the page itself": "是搜索摘要，不是页面本身",
               "its quote was not found on the page": "原句不在页面上"}.get(evidence.get("not_counted"), evidence.get("not_counted"))
        summary = (f"记成证据 {evidence['id']}：{evidence['claim']}（{evidence['source']}，{quote_word}；"
                   f"{'可以写核过，要带原句' if counted else '不算核过：' + why}）" if language == "zh" else
                   f"Recorded {evidence['id']}: {evidence['claim']} ({evidence['source']}, {quote_word}; "
                   f"{'may be called checked, with the quote' if counted else 'not checked: ' + evidence['not_counted']})")
        response = dict(status="sourced" if counted else "unverified", kind="web_evidence", evidence=evidence, quote_found=found,
                        counts_as_checked=counted, summary=summary,
                        notice="Recorded only: no plan or locked field changes. To change a protected value, cite this evidence in changes.corrections.")
        if not counted:
            response["reason"] = evidence["not_counted"]
        return response

    async def _query(self, owner: str, kind: str, arguments: dict, plan: dict | None, *, zone: str | None = None,
                     language: str = "zh") -> dict:
        target = arguments.get("target")
        if target:
            if plan is None:
                raise ValueError("A fact target needs a current plan or draft")
            if kind == "opening" and target.get("fact") == "opening_hours":
                day, subject, node, origin, destination = locate(plan, target)
                if target.get("place_id", node.get("place")) != node.get("place"):
                    raise ValueError("Fact target branch does not match the planned branch")
                return dict(status="unverified", reason="No opening-hours provider is configured for the planned branch.",
                            consequence="The branch may be closed.", alternative="Check the exact branch or choose a confirmed alternative.")
            expected_kind, derived = request_for(plan, target)
            if kind != expected_kind or any(key in arguments and arguments[key] != value for key, value in derived.items()):
                raise ValueError("Query arguments do not match the targeted plan fact")
            arguments = dict(derived, target=target, fresh=arguments.get("fresh", False))
        try:
            until_info = None
            if kind == "transit":
                if arguments.get("action") == "stops":
                    sourced = await self.transit.stops(arguments["text"], limit=min(20, max(1, arguments.get("limit", 8))))
                else:
                    name = self._zone(arguments, plan, zone)
                    live = isinstance(self.transit, TransitousProvider)
                    extra = {"timezone_name": name} if name and live else {}
                    if arguments.get("until") is not None:
                        when = aware(arguments["when"])
                        tz_name = name or str(when.tzinfo)
                        tz_until = ZoneInfo(tz_name) if "/" in tz_name or tz_name == "UTC" else when.tzinfo
                        until_at = moment(arguments["until"], arguments.get("date") or when.astimezone(tz_until).date().isoformat(),
                                          tz_until, "until")
                        if until_at <= when:
                            if len(arguments["until"]) == 5 and not arguments.get("date"):
                                until_at += timedelta(days=1)  # an HH:MM earlier than `when` means the next day
                            else:
                                raise ValueError(f"Invalid query arguments: arguments.until must come after arguments.when, got "
                                                 f"{arguments['until']!r} (when is {views.clock(when, tz_until)})")
                        found, sources, limited = await self._window(await self._where(plan, arguments, "origin"),
                                                                     await self._where(plan, arguments, "destination"),
                                                                     when, until_at, extra)
                        expiring = [item.valid_until for item in sources if item.valid_until is not None]
                        sourced = Sourced(found, sources[0].source, min(item.fetched_at for item in sources),
                                          min(expiring) if expiring else None, stale=any(item.stale for item in sources),
                                          notes=list(dict.fromkeys(note for item in sources for note in item.notes)),
                                          meta=self._meta(sources[0], sources))
                        until_info = dict(at=until_at, limited=limited, pages=len(sources))
                    else:
                        sourced = await self.transit.plan(await self._where(plan, arguments, "origin"),
                                                          await self._where(plan, arguments, "destination"),
                                                          aware(arguments["when"]), arrive_by=arguments.get("arrive_by", False),
                                                          limit=min(10, max(1, arguments.get("limit", 5))),
                                                          **({"fresh": True} if arguments.get("fresh") and live else {}),
                                                          **extra)
            elif kind == "weather":
                place = Place(**arguments["place"])
                sourced = await self.weather.day(place, arguments["date"],
                                                **({"fresh": True} if arguments.get("fresh") and isinstance(self.weather, OpenMeteoProvider) else {}))
            elif kind == "geocode":
                sourced = await self.geocode.search(arguments["text"], limit=min(20, max(1, arguments.get("limit", 5))))
            elif kind in ("nearby", "saved"):
                places = (plan or {}).get("places", [])
                if kind == "saved":
                    words = arguments.get("text", "").casefold().split()
                    values = [place for place in places if all(word in str(place).casefold() for word in words)]
                else:
                    latitude, longitude = arguments["lat"], arguments["lon"]
                    values = []
                    for place in places:
                        if not place.get("coords"):
                            continue
                        place_latitude, place_longitude = place["coords"]
                        delta_latitude = math.radians(place_latitude - latitude)
                        delta_longitude = math.radians(place_longitude - longitude)
                        haversine = math.sin(delta_latitude / 2) ** 2 + math.cos(math.radians(latitude)) * math.cos(math.radians(place_latitude)) * math.sin(delta_longitude / 2) ** 2
                        distance = 6371 * 2 * math.asin(min(1, math.sqrt(haversine)))
                        if distance <= arguments.get("km", 5):
                            values.append(dict(place=place, distance_km=round(distance, 3)))
                    values.sort(key=lambda place: place["distance_km"])
                fetched = clock()
                sourced = Sourced(values, "user-saved places; not live opening or ticket data", fetched, None)
            elif kind == "opening":
                return dict(status="unverified", reason="No opening-hours provider is configured; consult the exact branch's first-hand source.",
                            consequence="The branch may be closed.", alternative="Choose a confirmed alternative or check at the branch.")
            else:
                raise ValueError("Unknown query kind")
        except ProviderUnavailable as error:
            return {**dict(status="unverified", provider=error.provider, reason=error.reason, retryable=error.retryable,
                           consequence="The relevant fact could not be verified.", alternative="Confirm locally or retain the previous safe plan."),
                    **(lookup_failed("unavailable", language) if kind == "transit" else {})}
        fetched = sourced.fetched_at.isoformat()
        valid_until = sourced.valid_until.isoformat() if sourced.valid_until else None
        aware(fetched)
        if valid_until:
            aware(valid_until)
        evidence = dict(id=identifier("EV"), value=json_value(sourced.value), source=sourced.source,
                        fetched_at=fetched, valid_until=valid_until, stale=sourced.stale, notes=sourced.notes,
                        kind=kind, date=arguments.get("date") or (arguments.get("when", "")[:10] or None),
                        place_id=arguments.get("place_id"), facts=[])
        if target:
            day, subject, node, origin, destination = locate(plan, target)
            evidence.update(date=day["date"], place_id=node.get("place"), binding=snapshot(plan, target),
                            target=dict(target), synthetic=sourced.source.startswith("demo:"))
            if (not evidence["synthetic"] and not sourced.stale and sourced.valid_until is not None
                    and supports(plan, target, kind, sourced.value)):
                evidence["facts"] = [target["fact"]]
        shown = None
        if kind == "transit" and arguments.get("action") != "stops":
            # Per leg, in the trip's zone, with the span searched (A5): an answer never reads a UTC clock time or a
            # door-to-door start as a vehicle's departure, and says "the only" or "the last" only inside the window.
            name = self._zone(arguments, plan, zone) or str(aware(arguments["when"]).tzinfo)
            tz = ZoneInfo(name) if "/" in name or name == "UTC" else aware(arguments["when"]).tzinfo
            when = aware(arguments["when"])
            arrive_by = bool(arguments.get("arrive_by"))
            itineraries = sorted(sourced.value, key=lambda itinerary: itinerary.depart)
            span = views.window(itineraries, tz, start=None if arrive_by else when, end=when if arrive_by else None,
                                arrive_by=arrive_by, language=language)
            if until_info is not None:
                span = window_until(itineraries, when, until_info["at"], until_info["limited"], until_info["pages"], tz, language)
            meta = self._meta(sourced, [sourced])
            notes = self._coverage(meta, language)
            if not itineraries and any(note["kind"] in ("not_yet", "too_old") for note in notes):
                span = dict(span, text=next(note["text"] for note in notes if note["kind"] in ("not_yet", "too_old")))
            region = feed_region(self._feeds(meta))
            connections = [views.connection_view(itinerary, tz, language) for itinerary in itineraries]
            # A9: an until window tells every departure it found; a plain lookup tells the first TOLD.
            told = [views.sentence(view, language) for view in (connections if until_info is not None else connections[:TOLD])]
            ends = (end_name(plan, arguments["origin"], meta.get("origin")), end_name(plan, arguments["destination"], meta.get("destination")))
            reports, offer = trip_sources(self.store, owner, plan, region, language)
            shown = dict(timezone=name, connections=connections, window=span, coverage=notes, offer=offer,
                         how_to_check=how_to_check(connections[0] if connections else None, ends, region, notes, language, reports),
                         searched=searched(meta), note=views.result_note(connections, language),
                         sentences=head_sentences(meta, notes, language) + told + [span["text"]])
            shown["summary"] = (window_summary(shown["connections"], span, language) if until_info is not None
                                else transit_summary(shown["connections"], span, language))
            if until_info is not None:
                shown["sentences"].append(shown["summary"])
            if sourced.source.startswith("demo:"):
                demo_marked(shown, language)
            evidence.update(value=json_value(views.localized(sourced.value, tz)), window=span, coverage=notes, sentences=shown["sentences"])
            if not itineraries and any(note["kind"] in ("not_yet", "too_old") for note in notes):
                shown.update(reason=span["text"])
        self.store.put("evidence", owner, evidence, insert=True)
        uncovered = bool(shown and shown.get("reason"))
        response = dict(status="unverified" if uncovered or sourced.stale or sourced.source.startswith("demo:") or target and not evidence["facts"] else "sourced",
                        evidence=evidence, notice="Only facts explicitly bound to the matching plan object can pass; weather evidence establishes forecast availability, not outdoor safety.")
        if shown:
            response.update(shown)
        if target and not evidence["facts"]:
            response.update(reason="The provider result does not establish this exact planned fact, or is synthetic/stale.",
                            consequence="The planned connection or external condition remains unverified.",
                            alternative="Confirm against the exact first-hand source or retain a safe fallback.")
        return response

    async def recheck(self, owner: str, plan: dict) -> tuple[dict, list[dict]]:
        candidate = deepcopy(plan)
        places = {place["id"]: place for place in candidate["places"]}
        requests = []
        notes = []
        for day in candidate["days"]:
            for anchor in day["anchors"]:
                requests.append(("transit", anchor, dict(anchor_id=anchor["id"], date=day["date"], fact="hard_node")))
            for option in day["plans"]:
                for track in option["tracks"]:
                    for block in track["blocks"]:
                        target = dict(block_id=block["id"], date=day["date"])
                        if block["kind"] == "travel" and block.get("mode") != "walk":
                            requests.append(("transit", block, dict(target, fact="timetable")))
                        if outdoor(block, places.get(block.get("place"))):
                            requests.append(("weather", block, dict(target, fact="weather")))
                        if block["kind"] in ("visit", "meal"):
                            notes.append(dict(target=target, fact="opening_hours", status="unverified",
                                              reason="No opening-hours provider is configured for this exact branch."))
        for kind, node, target in requests:
            retained = []
            for evidence_id in node.get("evidence_ids", []):
                try:
                    old = self.store.get("evidence", owner, evidence_id)
                except ValueError:
                    continue
                if target["fact"] not in old.get("facts", []):
                    retained.append(evidence_id)
            node["evidence_ids"] = retained
            try:
                result = await self.query(owner, kind, {"target": target, "fresh": True}, candidate)
            except ValueError as error:
                result = dict(status="unverified", reason=str(error))
            evidence = result.get("evidence")
            if evidence:
                node["evidence_ids"] = list(dict.fromkeys([*node.get("evidence_ids", []), evidence["id"]]))
            notes.append(dict(target=target, status="sourced" if evidence and evidence["facts"] else "unverified",
                              reason=result.get("reason") or ("Matching fact bound to provider evidence" if evidence and evidence["facts"] else "Provider result does not verify the planned fact")))
        return candidate, notes

    async def aclose(self):
        for provider in (self.transit, self.weather, self.geocode, self.pages):
            close = getattr(provider, "aclose", None)
            if close:
                await close()
