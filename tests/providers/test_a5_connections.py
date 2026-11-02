"""A5: a raw Transitous answer read the way the second quality judgment found it misread. Its times are UTC; its
itineraries start and end with walking. The per-leg view gives each leg's local times in the trip's zone, marks the
walking legs, and keeps the door-to-door start apart from the train's departure; the window says how far the results
reach. Fictional stops and lines only."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from tripwright.providers.connections import connection_view, localized, window
from tripwright.providers.transitous import normalize

OSLO = ZoneInfo("Europe/Oslo")
UTC = ZoneInfo("UTC")

# Shapes as Transitous sends them: UTC instants, a walk to the platform, the train, a walk on.
RAW = {"itineraries": [
    {"startTime": "2030-11-03T03:04:00Z", "endTime": "2030-11-03T03:33:00Z", "legs": [
        {"mode": "WALK", "from": {"name": "START"}, "to": {"name": "Nordkai"}, "startTime": "2030-11-03T03:04:00Z",
         "endTime": "2030-11-03T03:10:00Z"},
        {"mode": "REGIONAL_RAIL", "displayName": "REX 7", "headsign": "Fergeterminal", "from": {"name": "Nordkai", "track": "2"},
         "to": {"name": "Fergeterminal"}, "startTime": "2030-11-03T03:10:00Z", "endTime": "2030-11-03T03:30:00Z"},
        {"mode": "WALK", "from": {"name": "Fergeterminal"}, "to": {"name": "END"}, "startTime": "2030-11-03T03:30:00Z",
         "endTime": "2030-11-03T03:33:00Z"}]},
    {"startTime": "2030-11-03T03:34:00Z", "endTime": "2030-11-03T04:03:00Z", "legs": [
        {"mode": "WALK", "from": {"name": "START"}, "to": {"name": "Nordkai"}, "startTime": "2030-11-03T03:34:00Z",
         "endTime": "2030-11-03T03:40:00Z"},
        {"mode": "REGIONAL_RAIL", "displayName": "REX 9", "from": {"name": "Nordkai"}, "to": {"name": "Fergeterminal"},
         "startTime": "2030-11-03T03:40:00Z", "endTime": "2030-11-03T04:00:00Z", "cancelled": True},
        {"mode": "WALK", "from": {"name": "Fergeterminal"}, "to": {"name": "END"}, "startTime": "2030-11-03T04:00:00Z",
         "endTime": "2030-11-03T04:03:00Z"}]},
]}


def test_utc_upstream_times_are_shown_in_the_trip_zone():
    itineraries = normalize(RAW, UTC)  # what a reader gets when it keeps the upstream's zone
    view = connection_view(itineraries[0], OSLO)
    train = view["legs"][1]
    # 03:10 UTC is 04:10 in Oslo in November (CET); reading the raw clock as local would say 03:10
    assert (train["depart"], train["arrive"], train["depart_at"]) == ("04:10", "04:30", "2030-11-03T04:10:00+01:00")
    assert localized(itineraries, OSLO)[0].legs[1].depart.isoformat() == "2030-11-03T04:10:00+01:00"


def test_walking_legs_are_marked_and_the_door_to_door_start_is_not_the_trains_departure():
    view = connection_view(normalize(RAW, OSLO)[0], OSLO)
    assert [(leg["mode"], leg["walk"], leg["from"], leg["to"], leg["depart"], leg["arrive"]) for leg in view["legs"]] == [
        ("walk", True, "START", "Nordkai", "04:04", "04:10"),
        ("regional_rail", False, "Nordkai", "Fergeterminal", "04:10", "04:30"),
        ("walk", True, "Fergeterminal", "END", "04:30", "04:33")]
    assert (view["leave"], view["reach"]) == ("04:04", "04:33")
    assert view["boards"] == dict(stop="Nordkai", time="04:10", at="2030-11-03T04:10:00+01:00", line="REX 7")
    assert view["alights"]["time"] == "04:30" and view["walk_minutes"] == 9 and view["rides"] == 1
    assert view["legs"][1]["line"] == "REX 7" and view["legs"][0]["line"] is None and view["legs"][1]["platform"] == "2"
    assert view["summary"] == "步行 6 分钟（04:04—04:10）；04:10 REX 7 Nordkai → Fergeterminal 04:30；步行 3 分钟（04:30—04:33）"


def test_a_cancelled_ride_is_marked():
    view = connection_view(normalize(RAW, OSLO)[1], OSLO, "en")
    assert view["cancelled"] and view["legs"][1]["cancelled"] and "(cancelled)" in view["summary"]


def test_the_window_ends_at_the_last_result_and_says_nothing_was_searched_after_it():
    itineraries = normalize(RAW, OSLO)
    start = itineraries[0].depart
    span = window(itineraries, OSLO, start=start)
    assert (span["from"], span["to"], span["by"], span["count"]) == ("2030-11-03T04:04:00+01:00", "2030-11-03T04:34:00+01:00", "departure", 2)
    assert span["text"] == "查到 04:04—04:34 之间出发的 2 条；04:34 之后出发的没查"
    arriving = window(itineraries, OSLO, end=itineraries[-1].arrive, arrive_by=True, language="en")
    assert arriving["by"] == "arrival" and arriving["text"] == "2 found arriving 04:33–05:03; nothing searched before 04:33"
    assert window([], OSLO, start=start)["to"] is None
