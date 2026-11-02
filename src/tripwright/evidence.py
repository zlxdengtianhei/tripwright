from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from tripwright import storage
from tripwright.storage import identifier
from tripwright.trip import local_instant

# Web evidence (A5): what the host read on a page, recorded by the service with the service's own clock. How long each
# kind stays current, in hours: opening hours as in A2's refresh time; an event for a day; rules, places and prices
# for a month.
WEB_TYPES = ("rule", "opening_hours", "event", "place", "price", "other")
WEB_VALID_HOURS = dict(rule=720, opening_hours=168, event=24, place=720, price=720, other=168)
READ_FROM = ("page", "search_snippet")


def uncounted(evidence: dict) -> str | None:
    """Why a record does not count as a check, or None. A search snippet is what a search engine shows, not the page;
    a quote the service did not find on the fetched page is not on record as read there."""
    if evidence.get("read_from") == "search_snippet":
        return "it is a search snippet, not the page itself"
    if evidence.get("quote_found") is False:
        return "its quote was not found on the page"
    return None


def web_record(arguments: dict, *, quote_found: bool | str, check: str | None, at: datetime | None = None) -> dict:
    """The evidence record of one web check. `fetched_at` is the service's clock; a time the host sends is refused
    before this point."""
    recorded = (at or datetime.fromisoformat(storage.now())).astimezone(timezone.utc).replace(microsecond=0)
    kind, read_from = arguments["type"], arguments.get("read_from", "page")
    record = dict(id=identifier("EV"), kind="web", source=urlsplit(arguments["url"]).hostname, url=arguments["url"],
                  quote=arguments["quote"], claim=arguments["claim"], type=kind, read_from=read_from,
                  quote_found=quote_found, value=dict(url=arguments["url"], quote=arguments["quote"], claim=arguments["claim"]),
                  fetched_at=recorded.isoformat(), valid_until=(recorded + timedelta(hours=WEB_VALID_HOURS[kind])).isoformat(),
                  stale=False, synthetic=False, notes=[note for note in [check] if note],
                  date=arguments.get("date"), place_id=None, facts=[])
    reason = uncounted(record)
    record.update(counts_as_checked=reason is None, **({"not_counted": reason} if reason else {}))
    return record


def locate(plan: dict, target: dict):
    block_id, anchor_id = target.get("block_id"), target.get("anchor_id")
    if bool(block_id) == bool(anchor_id):
        raise ValueError("A fact target needs exactly one block_id or anchor_id")
    for day in plan.get("days", []):
        if day["date"] != target.get("date"):
            continue
        if anchor_id:
            for anchor in day["anchors"]:
                if anchor["id"] == anchor_id:
                    return day, anchor["subject"], anchor, anchor.get("place"), anchor.get("destination")
        for candidate in day["plans"]:
            for track in candidate["tracks"]:
                for position, block in enumerate(track["blocks"]):
                    if block["id"] == block_id:
                        origin = block.get("origin") or (track["blocks"][position - 1].get("place") if position else None)
                        return day, track["subject"], block, origin, block.get("destination") or block.get("place")
    raise ValueError("Fact target is absent from this plan or date")


def snapshot(plan: dict, target: dict) -> dict:
    day, subject, node, origin, destination = locate(plan, target)
    places = {place["id"]: place for place in plan["places"]}
    related = {origin, destination, node.get("place")} - {None}
    return dict(date=day["date"], subject=sorted(subject), block_id=target.get("block_id"), anchor_id=target.get("anchor_id"),
                node={key: value for key, value in node.items() if key != "evidence_ids"},
                places={place_id: {key: places[place_id].get(key) for key in ("id", "name_local", "coords", "transit_id")}
                        for place_id in sorted(related)})


def request_for(plan: dict, target: dict) -> tuple[str, dict]:
    day, subject, node, origin, destination = locate(plan, target)
    if target.get("place_id", node.get("place")) != node.get("place"):
        raise ValueError("Fact target branch does not match the planned branch")
    places = {place["id"]: place for place in plan["places"]}
    fact = target["fact"]
    if fact == "weather":
        place = places.get(node.get("place"))
        if not place or not place.get("coords"):
            raise ValueError("Weather verification needs the exact planned branch coordinates")
        return "weather", dict(place={"name": place["name_local"], "lat": place["coords"][0], "lon": place["coords"][1]}, date=day["date"])
    if fact not in ("timetable", "hard_node"):
        raise ValueError("No configured first-hand provider can verify this fact")
    if (fact == "hard_node") != bool(target.get("anchor_id")) or (fact == "timetable" and node.get("kind") != "travel"):
        raise ValueError("The requested fact does not apply to this plan object")

    def location(place_id):
        place = places.get(place_id)
        if not place:
            raise ValueError("Timetable verification needs both planned route endpoints")
        if place.get("transit_id"):
            return place["transit_id"]
        if place.get("coords"):
            return ",".join(str(coordinate) for coordinate in place["coords"])
        raise ValueError("Timetable endpoints need exact coordinates or transit_id; branch names alone are ambiguous")

    instant = local_instant(day["date"], node.get("start") or node["time"], plan["timezone"])
    if instant is None:
        raise ValueError("The planned local time is ambiguous or nonexistent")
    return "transit", dict(origin=location(origin), destination=location(destination), when=instant.isoformat(),
                           arrive_by=False, limit=5)


def supports(plan: dict, target: dict, kind: str, value) -> bool:
    day, subject, node, origin, destination = locate(plan, target)
    if kind == "weather" and target["fact"] == "weather":
        place = next(place for place in plan["places"] if place["id"] == node["place"])
        return (isinstance(value, dict) and value.get("date") == day["date"] and bool(value.get("hourly"))
                and value.get("latitude") == place["coords"][0] and value.get("longitude") == place["coords"][1])
    if kind != "transit" or target["fact"] not in ("timetable", "hard_node"):
        return False
    start = local_instant(day["date"], node.get("start") or node["time"], plan["timezone"])
    end = local_instant(day["date"], node["end"], plan["timezone"]) if node.get("end") else None
    for itinerary in value:
        legs = getattr(itinerary, "legs", [])
        transit = [leg for leg in legs if leg.mode != "walk"]
        if not transit or any(leg.extra.get("cancelled") for leg in legs):
            continue
        mode = node.get("mode")
        if mode and not any(leg.mode == mode or mode == "rail" and "rail" in leg.mode for leg in transit):
            continue
        if node.get("line") and not any(leg.line == node["line"] for leg in transit):
            continue
        if itinerary.depart == start and (end is None or itinerary.arrive <= end):
            return True
    return False


def outdoor(block: dict, place: dict | None) -> bool:
    return bool(place and block["kind"] in ("visit", "flex", "reserve") and
                (place.get("rain_ok") is False or place.get("rain_ok") is not True and place["kind"] in ("park", "view", "viewpoint", "hiking")))
