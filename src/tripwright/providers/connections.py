"""Transit results as an answer reads them (A5).

Each connection lists its legs with the mode, the line, the stop where the leg starts and the stop where it ends, and
the local departure and arrival in the trip's time zone; walking legs are marked apart. `leave` and `reach` are the
door-to-door start and end, walking included: they are not a vehicle's times, which are the legs' own (`boards` and
`alights` name the first and the last ride). `window` says which span the results cover, so an answer says "the only"
or "the last" only inside it. Upstream times are instants; they are shown in the trip's zone, never as UTC clock times.

`sentence` writes a connection the way a host copies it into an answer (A8, D-57): every ride in order with the stop
where it ends, how many changes, the arrival, and—only where a ride is real time—whether the times are real time;
never the first ride's line with the last stop, which read as a direct ride ("13A … → Stephansplatz" was 13A, then a
change). The plain "timetable, not real time" basis is not on every sentence (A9, D-64): the query result carries it
once as `note`. What the timetable cannot say (real-time delays, platforms, disruptions) is not added to each
sentence: the query result says where to look for it apart (`how_to_check`, D-61).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, tzinfo

from .base import Itinerary, Leg

WORDS = {
    "zh": dict(walk="步行 {minutes} 分钟（{depart}—{arrive}）", ride="{depart} {line}{origin} → {destination} {arrive}",
               cancelled="（取消）", none="这段时间没查到班次；不能说没车（这一带或这段日期可能不在数据里）",
               departures="查到 {start}—{end} 之间出发的 {count} 条；{end} 之后出发的没查",
               arrivals="查到 {start}—{end} 之间到达的 {count} 条；{start} 之前到达的没查",
               then="，换 ", direct="不用换车", changes="换 {count} 次车", leave="{leave} 出门，{changes}：{rides}；{reach} 到",
               walk_only="步行 {minutes} 分钟，{leave} 出门，{reach} 到", timetable="按时刻表，不是实时", live="实时",
               some_live="{lines} 是实时，其余按时刻表", basis="（{basis}）",
               resolved="{end}按 {name} 查（你写的是 {said}）", origin="起点", destination="终点", toward="（往 {headsign}）"),
    "en": dict(walk="walk {minutes} min ({depart}–{arrive})", ride="{depart} {line}{origin} → {destination} {arrive}",
               cancelled=" (cancelled)", none="No connections found in this span; do not say there is no service (this area or date may not be in the data)",
               departures="{count} found departing {start}–{end}; nothing searched after {end}",
               arrivals="{count} found arriving {start}–{end}; nothing searched before {start}",
               then=", then ", direct="no change", changes="{count} change(s)", leave="Leave {leave}, {changes}: {rides}; arrive {reach}",
               walk_only="Walk {minutes} min, leave {leave}, arrive {reach}", timetable="timetable, not real time", live="real time",
               some_live="{lines} real time, the rest timetable", basis=" ({basis})",
               resolved="{end} looked up as {name} (you wrote {said})", origin="Start", destination="Destination",
               toward=" (toward {headsign})"),
}


def words(language: str) -> dict:
    return WORDS.get(language, WORDS["en"])


def clock(moment: datetime, zone: tzinfo) -> str:
    """HH:MM in the trip's zone."""
    return moment.astimezone(zone).strftime("%H:%M")


def stamp(moment: datetime, zone: tzinfo) -> str:
    return moment.astimezone(zone).isoformat(timespec="seconds")


def localized(itineraries: list[Itinerary], zone: tzinfo) -> list[Itinerary]:
    """The same instants with the trip's offset, so a stored result never shows a UTC clock time."""
    return [replace(itinerary, legs=[replace(leg, depart=leg.depart.astimezone(zone), arrive=leg.arrive.astimezone(zone))
                                     for leg in itinerary.legs]) for itinerary in itineraries]


def leg_view(leg: Leg, zone: tzinfo) -> dict:
    walk = leg.mode == "walk"
    view = {"mode": leg.mode, "walk": walk, "line": None if walk else leg.line, "from": leg.origin, "to": leg.destination,
            "depart": clock(leg.depart, zone), "arrive": clock(leg.arrive, zone),
            "depart_at": stamp(leg.depart, zone), "arrive_at": stamp(leg.arrive, zone),
            "minutes": round((leg.arrive - leg.depart).total_seconds() / 60)}
    if not walk:
        view.update(platform=leg.platform, headsign=leg.extra.get("headsign"), operator=leg.extra.get("agencyName"),
                    real_time=bool(leg.extra.get("realTime")), cancelled=bool(leg.extra.get("cancelled")))
    return view


def leg_line(leg: dict, language: str, toward: bool = False) -> str:
    """One leg; with `toward`, a ride with a destination sign says which way it runs (“5B（往 Praterstern）”: the
    quality judge of 11-04 found a bus that stops both ways named without its direction)."""
    said = words(language)
    if leg["walk"]:
        return said["walk"].format(**leg)
    sign = said["toward"].format(headsign=leg["headsign"]) if toward and leg.get("headsign") else ""
    named = f"{leg['line']}{sign}" if leg["line"] else sign.strip()
    text = said["ride"].format(depart=leg["depart"], line=named + ("" if named.endswith("）") else " ") if named else "", origin=leg["from"],
                               destination=leg["to"], arrive=leg["arrive"])
    return text + (said["cancelled"] if leg["cancelled"] else "")


def connection_view(itinerary: Itinerary, zone: tzinfo, language: str = "zh") -> dict:
    legs = [leg_view(leg, zone) for leg in itinerary.legs]
    rides = [leg for leg in legs if not leg["walk"]]

    def stop(leg, end):
        return None if leg is None else dict(stop=leg["from" if end == "depart" else "to"], time=leg[end], at=leg[f"{end}_at"],
                                             line=leg["line"])

    return dict(leave=legs[0]["depart"], reach=legs[-1]["arrive"], leave_at=legs[0]["depart_at"], reach_at=legs[-1]["arrive_at"],
                minutes=round((itinerary.arrive - itinerary.depart).total_seconds() / 60), rides=len(rides),
                walk_minutes=sum(leg["minutes"] for leg in legs if leg["walk"]),
                boards=stop(rides[0] if rides else None, "depart"), alights=stop(rides[-1] if rides else None, "arrive"),
                cancelled=any(leg["cancelled"] for leg in rides), legs=legs,
                summary="；".join(leg_line(leg, language) for leg in legs) if language == "zh"
                else "; ".join(leg_line(leg, language) for leg in legs))


def window(itineraries: list[Itinerary], zone: tzinfo, *, start: datetime | None = None, end: datetime | None = None,
           arrive_by: bool = False, language: str = "zh") -> dict:
    """The span the results cover. Departing after `start`: from `start` to the last result's departure. Arriving by
    `end`: from the earliest result's arrival to `end`. Outside it nothing was searched."""
    said = words(language)
    if not itineraries:
        return {"from": stamp(start or end, zone) if (start or end) else None, "to": None,
                "by": "arrival" if arrive_by else "departure", "count": 0, "text": said["none"]}
    if arrive_by:
        first = min(itinerary.arrive for itinerary in itineraries)
        last = end or max(itinerary.arrive for itinerary in itineraries)
        key = "arrivals"
    else:
        first = start or min(itinerary.depart for itinerary in itineraries)
        last = max(itinerary.depart for itinerary in itineraries)
        key = "departures"
    return {"from": stamp(first, zone), "to": stamp(last, zone), "by": "arrival" if arrive_by else "departure",
            "count": len(itineraries),
            "text": said[key].format(start=clock(first, zone), end=clock(last, zone), count=len(itineraries))}


def basis(rides: list[dict], language: str = "zh") -> str:
    """Whether a connection's times are the timetable or real time, ride by ride: Transitous has real time only where
    an operator publishes it (in Austria, Styria and FlixBus)."""
    said = words(language)
    live = [ride for ride in rides if ride.get("real_time")]
    if not live:
        return said["timetable"]
    if len(live) == len(rides):
        return said["live"]
    names = "、".join(dict.fromkeys(ride.get("line") or ride["mode"] for ride in live)) if language == "zh" else \
        ", ".join(dict.fromkeys(ride.get("line") or ride["mode"] for ride in live))
    return said["some_live"].format(lines=names)


def basis_suffix(rides: list[dict], language: str = "zh") -> str:
    """The per-connection basis only when it says more than \"timetable\" (A9, D-64: \"timetable, not real time\" is said
    once per answer, from the result's `note`, not on every line); a connection with real-time rides keeps its own."""
    note = basis(rides, language)
    return "" if note == words(language)["timetable"] else words(language)["basis"].format(basis=note)


NOTE = {"zh": dict(timetable="以上交通时刻按时刻表，不是实时。",
                   live="以上交通时刻是实时的。", mixed="以上标“实时”的是实时时刻，其余按时刻表。"),
        "en": dict(timetable="The transit times above are timetable, not real time.",
                   live="The transit times above are real time.", mixed="Rides marked real time are; the rest is the timetable.")}


def result_note(connections: list[dict], language: str = "zh") -> str | None:
    """The one timetable/real-time note of a result (A9, D-64): the answer's transit section says it once."""
    rides = [leg for view in connections for leg in view.get("legs", []) if not leg.get("walk")]
    if not rides:
        return None
    live = [ride for ride in rides if ride.get("real_time")]
    said = NOTE.get(language, NOTE["en"])
    return said["timetable"] if not live else (said["live"] if len(live) == len(rides) else said["mixed"])


def sentence(view: dict, language: str = "zh", toward: bool = False) -> str:
    """One connection as a host copies it: when to leave, how many changes, every ride (time, line, from, to, arrival;
    with `toward` its direction), the arrival, and whether the times are the timetable or real time."""
    said = words(language)
    rides = [leg for leg in view["legs"] if not leg["walk"]]
    if not rides:
        return said["walk_only"].format(minutes=view["minutes"], leave=view["leave"], reach=view["reach"])
    shown = said["then"].join(leg_line(leg, language, toward) for leg in rides)
    changes = said["direct"] if len(rides) == 1 else said["changes"].format(count=len(rides) - 1)
    text = said["leave"].format(leave=view["leave"], changes=changes, rides=shown, reach=view["reach"])
    return text + basis_suffix(rides, language)


def resolved_sentence(end: str, found: dict | None, language: str = "zh") -> str | None:
    """"起点按 Bad Ischl Bahnhof 查（你写的是 Bad Ischl）": said when the stop searched is not what was typed (A8)."""
    if not found or not found.get("name") or not found.get("said"):
        return None
    if found["name"].casefold() == str(found["said"]).casefold() or found.get("how") in ("coordinates", "place", "stop id"):
        return None
    said = words(language)
    return said["resolved"].format(end=said[end], name=found["name"], said=found["said"])
