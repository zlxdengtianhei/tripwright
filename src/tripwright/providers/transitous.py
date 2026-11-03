"""Transitous (MOTIS) transport, adapted from the October 21 transit service."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import coverage
from .base import Itinerary, Leg, Place, Sourced
from .common import HTTPProvider, ProviderError, RollingBudget, SharedLookups, budgeted, gather_cancel, validate_limit, validate_place

MODES = ("TRAM,SUBWAY,FERRY,BUS,COACH,RAIL,HIGHSPEED_RAIL,LONG_DISTANCE,NIGHT_RAIL,"
         "REGIONAL_FAST_RAIL,REGIONAL_RAIL,SUBURBAN,FUNICULAR,AERIAL_LIFT,CABLE_CAR,OTHER")
ATTRIBUTION = ("Public transport data: Transitous, MOTIS and the transport data providers listed at https://transitous.org/sources/; "
               "map data © OpenStreetMap contributors. Non-commercial use only.")
COORD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
# Transitous's terms (https://transitous.org/api/): more than 720 of these in 24 hours needs the maintainers' agreement.
ROUTING = ("v6/plan", "v1/one-to-many", "v1/one-to-all")
ROUTING_PER_DAY = 720
RAIL_MODES = {"RAIL", "HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL", "REGIONAL_FAST_RAIL", "REGIONAL_RAIL", "SUBURBAN"}
STATION_WORDS = {"hbf": "hbf", "hauptbahnhof": "hbf", "bahnhof": "bahnhof", "bahnhst": "bahnhof", "bhf": "bahnhof",
                 "bf": "bahnhof", "station": "bahnhof"}
HUB = re.compile(r"hauptbahnhof|bahnhof|bahnhst|\bhbf\b|\bbhf\b|busterminal|busbahnhof|\bzob\b|station|terminal", re.I)


def usage_path() -> Path:
    """Where the route searches of the last 24 hours are counted across processes: TRIPWRIGHT_TRANSITOUS_USAGE, else
    the user's cache directory."""
    given = os.environ.get("TRIPWRIGHT_TRANSITOUS_USAGE")
    if given:
        return Path(given)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tripwright" / "transitous-routing.log"


def shared_folder(usage: Path) -> Path:
    """Where the answers and pacing shared across processes live: TRIPWRIGHT_TRANSITOUS_SHARED, else beside the
    route-search counter (on a server, the data volume)."""
    given = os.environ.get("TRIPWRIGHT_TRANSITOUS_SHARED")
    return Path(given) if given else usage.parent / "transitous-shared"


def headsign(value) -> str | None:
    """A vehicle's destination sign, or None when the feed's value names nothing (Postbus 541 and 546 send "0")."""
    text = str(value or "").strip()
    return text if re.search(r"[^\W\d_]", text) else None


def words(text: str) -> set[str]:
    found = {word for word in re.split(r"[\W_]+", text.casefold()) if word}
    return {STATION_WORDS.get(word, word) for word in found}


def ranked(text: str, hits: list[Place]) -> list[tuple[Place, str]]:
    """Stops for a typed name, best first, each with why (A8). Not the upstream's first match: "Bad Ischl" first finds a
    bus stop 4 km from the station. Order: most of the typed words in the stop's name; in the place the text names (a
    municipality or district of the stop); a train station or a bus or rail hub; then the upstream's order."""
    said = words(text)

    def local(place):
        return any(words(area) and words(area) <= said for area in place.areas)

    def station(place):
        return bool(RAIL_MODES & set(place.modes)) or bool(HUB.search(place.name))

    order = sorted(enumerate(hits), key=lambda pair: (-len(words(pair[1].name) & said), not local(pair[1]),
                                                       not station(pair[1]), pair[0]))
    best = order[0][1] if order else None
    output = []
    for position, place in order:
        if place is best and position == 0:
            why = "first stop match"
        elif place is best:
            why = "train station" if RAIL_MODES & set(place.modes) else ("hub" if HUB.search(place.name) else "closest name")
        else:
            why = "alternative"
        output.append((place, why))
    return output


def day_window(day: date, tz):
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start, {"timetableView": "true", "searchWindow": int(end.timestamp() - start.timestamp()), "numItineraries": 5}


_day_extra = day_window


def _local(value, tz):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Upstream timestamp has no timezone")
    return stamp.astimezone(tz)


def normalize(payload, tz):
    output = []
    for itinerary in payload["itineraries"]:
        start = _local(itinerary["startTime"], tz)
        end = _local(itinerary["endTime"], tz)
        previous = start
        legs = []
        for item in itinerary["legs"]:
            depart = _local(item["startTime"], tz) if item.get("startTime") else previous
            arrive = (_local(item["endTime"], tz) if item.get("endTime") else
                      (depart.astimezone(timezone.utc) + timedelta(seconds=item["duration"])).astimezone(tz))
            if arrive.timestamp() < depart.timestamp():
                raise ValueError("Negative upstream leg duration")
            legs.append(Leg(
                mode=item["mode"].lower(), origin=item["from"]["name"], destination=item["to"]["name"],
                depart=depart, arrive=arrive,
                line=item.get("displayName") or item.get("routeShortName") or item.get("tripShortName"),
                platform=item["from"].get("track") or item["from"].get("scheduledTrack"),
                extra={key: item[key] for key in ("headsign", "agencyName", "realTime", "cancelled", "reservation", "alerts")
                       if key in item and (key != "headsign" or headsign(item[key]))}))
            previous = arrive
        if not legs or legs[0].depart.timestamp() != start.timestamp() or legs[-1].arrive.timestamp() != end.timestamp():
            raise ValueError("Incomplete upstream itinerary")
        output.append(Itinerary(legs))
    return output


def end_view(payload_end: dict | None, resolved: dict) -> dict:
    """One end of a search as the upstream took it: the stop's name, ID and coordinates from the plan's own `from`/`to`
    (a stop ID alone has none until then), with how it was chosen from what was typed."""
    end = payload_end or {}
    name = end.get("name")
    view = dict(said=resolved.get("said"), how=resolved.get("how"),
                name=None if name in (None, "START", "END") else name, stop_id=end.get("stopId") or None,
                lat=end.get("lat", resolved.get("lat")), lon=end.get("lon", resolved.get("lon")))
    if view["name"] is None and resolved.get("how") not in ("coordinates", "place"):
        view["name"] = resolved.get("label")
    if resolved.get("alternatives"):
        view["alternatives"] = resolved["alternatives"]
    return view


class TransitousProvider(HTTPProvider):
    def __init__(self, base_url=None, *, timezone_name=None, routing_per_day=None, usage_file=None, **kwargs):
        super().__init__("transitous", base_url or os.environ.get("TRIPWRIGHT_TRANSITOUS_API", "https://api.transitous.org/api"),
                         attribution=ATTRIBUTION, **kwargs)
        self.timezone_name = timezone_name
        limit = routing_per_day or int(os.environ.get("TRIPWRIGHT_TRANSITOUS_ROUTING_PER_DAY") or ROUTING_PER_DAY)
        # Shared across processes when this instance talks to the real network (it made its own client) or a file is named.
        shared = usage_file or (usage_path() if self.owns_client or os.environ.get("TRIPWRIGHT_TRANSITOUS_USAGE") else None)
        self.routing = RollingBudget(limit, path=shared, source=self.source)
        if self.shared is None and shared:
            # Answers and pacing shared across processes, beside the shared counter (s4b): see SharedLookups.
            self.shared = SharedLookups(shared_folder(Path(shared)))
        reason = (f"At most {limit} route searches in 24 hours (Transitous's terms for one user); try again later "
                  "or check the operator's timetable by hand")
        self.budgets = {path: (self.routing, reason) for path in ROUTING}

    def coverage(self, meta: dict, language: str = "zh") -> list[dict]:
        """Whether this data could answer the search `meta` describes (its ends, days and the stops its results
        passed); see coverage.py."""
        days = [date.fromisoformat(day) for day in meta.get("days", [])]
        ends = [meta[key] for key in ("origin", "destination") if meta.get(key)]
        return coverage.assess(ends, days, language, stops=meta.get("stops", []))

    def feed_lists(self, meta: dict) -> set[str]:
        """The Transitous feed lists (regions: "at", "de", "au-nsw") the search `meta` describes touched."""
        ends = [meta[key] for key in ("origin", "destination") if meta.get(key)]
        return coverage.files_for(ends, meta.get("stops", []), coverage.load())

    @budgeted
    async def stops(self, text: str, *, limit=8, fresh=False):
        validate_limit(limit)
        text = str(text).strip()
        if not text:
            raise ValueError("query must not be empty")
        params = {"text": text, "type": "STOP", "numResults": max(limit, 5)}

        async def fetch():
            payload = await self.get_json("v1/geocode", params)
            hits = []
            for item in payload:
                if item.get("type") != "STOP" or item.get("lat", -1) == -1:
                    continue
                areas = tuple(area["name"] for area in item.get("areas") or [] if area.get("name") and (area.get("adminLevel") or 0) > 2)
                place = Place(item["name"], float(item["lat"]), float(item["lon"]), item["id"],
                              modes=tuple(item.get("modes") or ()), areas=areas)
                validate_place(place)
                hits.append(place)
            return hits

        result = await self.cached("stops", params, fetch, ttl=timedelta(days=7), retention=timedelta(days=30), fresh=fresh)
        return replace(result, value=result.value[:limit])

    @budgeted
    async def resolve(self, location: Place | str, *, fresh=False):
        """Resolve solver locations; raw stop IDs intentionally have unknown coordinates."""
        stamp = self.clock()
        if isinstance(location, Place):
            validate_place(location)
            value = {"place": location.id or f"{location.lat},{location.lon}", "label": location.name,
                     "lat": location.lat, "lon": location.lon, "how": "place"}
        else:
            text = str(location).strip()
            match = COORD.fullmatch(text)
            if match:
                lat, lon = float(match[1]), float(match[2])
                validate_place(Place(text, lat, lon))
                value = {"place": f"{lat},{lon}", "label": f"{lat:.4f},{lon:.4f}", "lat": lat, "lon": lon, "how": "coordinates"}
            elif re.search(r"[:_]", text) and " " not in text:
                value = {"place": text, "label": text, "lat": None, "lon": None, "how": "stop id"}
            else:
                hits = await self.stops(text, limit=5, fresh=fresh)
                if not hits.value:
                    raise ProviderError(self.source, "PLACE_NOT_FOUND", f"No stop found for {text!r}", retryable=False)
                order = ranked(text, hits.value)
                chosen, why = order[0]
                return replace(hits, value={"place": chosen.id, "label": chosen.name, "lat": chosen.lat, "lon": chosen.lon,
                                            "how": why, "said": text, "alternatives": [place.name for place, _ in order[1:]]})
        value.setdefault("said", str(location.name if isinstance(location, Place) else location).strip())
        return Sourced(value, self.source, stamp, None, notes=[ATTRIBUTION])

    @budgeted
    async def walk_minutes(self, origin: Place, destinations: list[Place], *, fresh=False, max_min=60):
        for place in [origin, *destinations]:
            validate_place(place)
        if not 1 <= max_min <= 1440:
            raise ValueError("max_min must be between 1 and 1440")
        params = {"one": f"{origin.lat};{origin.lon}", "many": ",".join(f"{place.lat};{place.lon}" for place in destinations),
                  "mode": "WALK", "max": max_min * 60, "maxMatchingDistance": 250, "arriveBy": "false"}

        async def fetch():
            payload = await self.get_json("v1/one-to-many", params)
            if len(payload) != len(destinations):
                raise ValueError("Wrong walking matrix size")
            values = []
            for item in payload:
                duration = item.get("duration")
                if duration is not None and duration < 0:
                    raise ValueError("Negative walking duration")
                values.append(round(duration / 60) if duration is not None else None)
            return values

        if not destinations:
            stamp = self.clock()
            return Sourced([], self.source, stamp, None, notes=[ATTRIBUTION])
        return await self.cached("walk", params, fetch, ttl=timedelta(days=30), retention=timedelta(days=60), fresh=fresh)

    async def _search(self, kind, origin, destination, when, *, arrive_by, limit, fresh, walk_min, extra, tz):
        tries = [walk_min] if walk_min is not None else [20, 60]
        if any(not 1 <= minutes <= 90 for minutes in tries):
            raise ValueError("walk_min must be between 1 and 90")
        locations = await gather_cancel(self.resolve(origin, fresh=fresh), self.resolve(destination, fresh=fresh))
        provenance = []
        for minutes in tries:
            params = {"fromPlace": locations[0].value["place"], "toPlace": locations[1].value["place"],
                      "time": when.isoformat(timespec="seconds"), "arriveBy": str(arrive_by).lower(),
                      "transitModes": MODES, "maxPreTransitTime": minutes * 60, "maxPostTransitTime": minutes * 60,
                      "detailedLegs": "false", "detailedTransfers": "false", "numItineraries": limit, **extra}

            async def fetch():
                payload = await self.get_json("v6/plan", params)
                direct = [round(item["duration"] / 60) for item in payload.get("direct", [])
                          if item["legs"] and all(leg["mode"] == "WALK" for leg in item["legs"])]
                ends = {key: {name: payload[key][name] for name in ("name", "stopId", "lat", "lon") if name in payload[key]}
                        for key in ("from", "to") if isinstance(payload.get(key), dict)}
                stops = sorted({leg[end]["stopId"] for item in payload["itineraries"] for leg in item["legs"]
                                if leg.get("mode") != "WALK" for end in ("from", "to") if leg[end].get("stopId")})
                return {"itineraries": normalize(payload, tz), "walk_direct_min": min(direct) if direct else None,
                        "ends": ends, "stops": stops}

            result = await self.cached(kind, {**params, "_tz": str(tz)}, fetch,
                                       ttl=timedelta(minutes=30) if kind == "plan" else timedelta(hours=6),
                                       retention=timedelta(hours=24 if kind == "plan" else 48), fresh=fresh)
            direct, ends = result.value["walk_direct_min"], result.value.get("ends", {})
            result = replace(result, value=result.value["itineraries"],
                             notes=[*result.notes, *([f"Direct walking: {direct} minutes"] if direct is not None else [])],
                             meta=dict(origin=end_view(ends.get("from"), locations[0].value),
                                       destination=end_view(ends.get("to"), locations[1].value),
                                       days=[when.astimezone(tz).date().isoformat()], stops=result.value.get("stops", [])))
            provenance.append(result)
            if result.value:
                break
        if minutes != tries[0]:
            result = replace(result, notes=[*result.notes, f"No itineraries with {tries[0]} min walking; widened to {minutes} min"])
        inputs = [*(location for location in locations if location.valid_until is not None), *provenance]
        expiring = [item.valid_until for item in inputs if item.valid_until is not None]
        return replace(result, stale=any(item.stale for item in inputs), meta=result.meta,
                       fetched_at=min(item.fetched_at for item in inputs),
                       valid_until=min(expiring) if expiring else None,
                       notes=list(dict.fromkeys(note for item in inputs for note in item.notes)) + [note for note in result.notes if note not in inputs[-1].notes])

    @budgeted
    async def plan(self, origin, destination, when: datetime, *, arrive_by=False, limit=5,
                   fresh=False, timezone_name=None, walk_min=None):
        validate_limit(limit)
        if not isinstance(when, datetime):
            raise ValueError("when must be a datetime")
        tz_name = timezone_name or self.timezone_name
        tz = ZoneInfo(tz_name) if tz_name else when.tzinfo
        if tz is None:
            raise ValueError("Provide a timezone-aware datetime or timezone_name")
        when = when.replace(tzinfo=tz) if when.tzinfo is None else when.astimezone(tz)
        result = await self._search("plan", origin, destination, when, arrive_by=arrive_by, limit=limit,
                                    fresh=fresh, walk_min=walk_min, extra={}, tz=tz)
        ordered = sorted(result.value, key=lambda itinerary: (itinerary.arrive if arrive_by else itinerary.depart).timestamp(), reverse=arrive_by)[:limit]
        return replace(result, value=sorted(ordered, key=lambda itinerary: itinerary.depart.timestamp()))

    @budgeted
    async def day(self, origin, destination, day: str, *, timezone_name=None, fresh=False, walk_min=None):
        tz_name = timezone_name or self.timezone_name
        if not tz_name:
            raise ValueError("timezone_name is required for a whole-day timetable")
        tz = ZoneInfo(tz_name)
        parsed = date.fromisoformat(day)
        start, extra = day_window(parsed, tz)
        result = await self._search("day", origin, destination, start, arrive_by=False, limit=5,
                                    fresh=fresh, walk_min=walk_min, extra=extra, tz=tz)
        seen, output = set(), []
        for itinerary in sorted(result.value, key=lambda item: (item.depart.timestamp(), item.arrive.timestamp())):
            key = (itinerary.depart.timestamp(), itinerary.arrive.timestamp(), tuple(leg.line for leg in itinerary.legs))
            if itinerary.depart.date() == parsed and key not in seen:
                seen.add(key)
                output.append(itinerary)
        return replace(result, value=output)
