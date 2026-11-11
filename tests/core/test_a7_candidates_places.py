"""A7 (D-44), second half: latest_departure gives routes to compare, not one first choice (judgment 3: one lookup
returned a coach and the host inferred the train "cannot be caught"), and looks up a place of the trip by what the trip
stores, never by geocoding its address; places store opening hours with their source, written through web evidence and
a plan proposal, and open_places lists what is open near somewhere. Offline: a fictional MOTIS with a coach, a tram and
a train, and the fictional web of A5. Fictional people, places and lines only."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import tripwright.service as service_module
import tripwright.storage as storage_module
from a5_upstream import DAY, ZONE, Timetable, page_reader, z
from test_a3_anchor_deadline import anchored_plan
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter, match_place, stations
from tripwright.service import Tripwright
from tripwright.storage import Store

NOW = datetime(2030, 6, 10, 18, 45, tzinfo=timezone.utc)  # 20:45 in Oslo
GYM, STATION, TERMINAL = "60.15,10.75", "60.1,10.7", "60.2,10.9"
PLACES = [
    dict(id="gym", name="Climbing hall North", name_local="Kletterhalle Nord", area="Oslo", kind="climbing", coords=[60.15, 10.75]),
    dict(id="nordkai", name="Nordkai station", name_local="Nordkai", area="Oslo", kind="station", coords=[60.1, 10.7]),
    dict(id="terminal", name="Ferry terminal", name_local="Fergeterminal", area="Oslo", kind="transport", coords=[60.2, 10.9]),
    dict(id="kaikafe", name="Quay café", name_local="Kaikafé Nord", area="Oslo", kind="food", coords=[60.101, 10.701],
         opening_hours=["Mo-Su 06:00-23:00"], hours_source=dict(url="https://kaikafe.example.org/hours")),
    dict(id="grill", name="Terminal grill", name_local="Fergeterminal Grill", area="Oslo", kind="food", coords=[60.199, 10.899],
         opening_hours=["Mo-Th 05:00-21:00", "Fr 18:00-02:00"], hours_source=dict(url="https://ferry.example.org/food")),
    dict(id="kiosk", name="Kiosk", name_local="Kiosk Fjordblick", area="Oslo", kind="food", coords=[60.102, 10.702]),
]


def at(clock: str) -> datetime:
    return datetime.fromisoformat(f"{DAY}T{clock}").replace(tzinfo=ZONE)


class Network(Timetable):
    """From the climbing hall a coach goes straight to the ferry (what the router prefers); a tram goes to Nordkai,
    where the train of A5 leaves. Each answers in UTC, walking legs included."""

    def leg(self, mode, start, end, origin, destination, line=None, agency=None):
        return {"mode": mode, "from": {"name": origin}, "to": {"name": destination}, "startTime": z(start), "endTime": z(end),
                **({"displayName": line} if line else {}), **({"agencyName": agency} if agency else {})}

    def coach(self, departs):
        start, alight = departs - timedelta(minutes=4), departs + timedelta(minutes=25)
        legs = [self.leg("WALK", start, departs, "START", "Lindenallee"),
                self.leg("COACH", departs, alight, "Lindenallee", "Fergeterminal Bus", "FB 901", "Fjordbuss (fictional)"),
                self.leg("WALK", alight, alight + timedelta(minutes=2), "Fergeterminal Bus", "END")]
        return {"startTime": legs[0]["startTime"], "endTime": legs[-1]["endTime"], "legs": legs}

    def tram(self, departs):
        start, alight = departs - timedelta(minutes=3), departs + timedelta(minutes=12)
        legs = [self.leg("WALK", start, departs, "START", "Lindenplatz"),
                self.leg("TRAM", departs, alight, "Lindenplatz", "Nordkai Vorplatz", "T4"),
                self.leg("WALK", alight, alight + timedelta(minutes=1), "Nordkai Vorplatz", "END")]
        return {"startTime": legs[0]["startTime"], "endTime": legs[-1]["endTime"], "legs": legs}

    async def handle(self, request):
        self.requests.append(request)
        if request.url.path.endswith("geocode"):
            return httpx.Response(200, json=[{"type": "STOP", "name": "Somewhere else", "id": "xx:1", "lat": 59.0, "lon": 11.0}])
        params = request.url.params
        moment, count = datetime.fromisoformat(params["time"]), int(params["numItineraries"])
        route = (params["fromPlace"], params["toPlace"])
        if route == (GYM, TERMINAL):
            options = [self.coach(at(clock)) for clock in ("21:10", "21:40", "22:10", "22:40", "23:10")]
        elif route == (GYM, STATION):
            options = [self.tram(at("20:00") + timedelta(minutes=10 * step)) for step in range(24)]
        elif route == (STATION, TERMINAL):
            options = [self.itinerary(train) for train in self.trains]
        else:
            options = []
        chosen = [option for option in options if datetime.fromisoformat(option["startTime"].replace("Z", "+00:00")) >= moment][:count]
        return httpx.Response(200, json={"itineraries": chosen, "direct": []})

    def provider(self, clock=None):
        return TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)), min_interval=0, clock=clock)


def plan_with(places=PLACES, **deadlines):
    plan = anchored_plan(**(deadlines or {"hn-counter": "external", "hn-arrive": "plan"}))
    plan["places"] += deepcopy(places)
    return plan


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "now", lambda: NOW.isoformat())
    monkeypatch.setattr(storage_module, "now", lambda: NOW.isoformat())
    made = []

    def build(plan, network=None):
        store = Store(tmp_path / f"a7-{len(made)}.sqlite3")
        network = network or Network()
        service = Tripwright(store, query_router=QueryRouter(store, transit=network.provider(clock=lambda: NOW), pages=page_reader()))
        made.append((service, store))
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        return service, network

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def query(service, kind, **arguments):
    return asyncio.run(service.query("local", kind, arguments))


# --- places of the trip are looked up by what the trip stores ------------------------------------------------------------

def test_a_place_named_with_its_address_uses_the_stored_coordinates():
    plan = plan_with()
    assert match_place(plan, "Kletterhalle Nord, Lindenallee 12")["id"] == "gym"  # judgment 3: the address went elsewhere
    assert match_place(plan, "kletterhalle nord")["id"] == "gym" and match_place(plan, "gym")["id"] == "gym"
    assert match_place(plan, "Kletterhalle Nordstadt") is None and match_place(plan, "60.15,10.75") is None
    assert match_place(plan, "Climbing hall North (Oslo)")["id"] == "gym"


def test_the_trips_station_comes_from_a_planned_train_toward_the_destination():
    plan = plan_with()
    assert [place["id"] for place in stations(plan, "gym", "terminal")] == ["nordkai"]  # a station kind, without a planned train
    with_train = deepcopy(plan)
    with_train["places"].append(dict(id="sentral", name="Central", name_local="Sentral", area="Oslo", kind="transport", coords=[60.12, 10.72]))
    with_train["days"][0]["plans"].append(dict(name="By train", tracks=[dict(subject=["p1"], blocks=[dict(
        id="d1-train", start="21:35", end="21:55", kind="travel", mode="rail", origin="sentral", place="terminal")])]))
    assert [place["id"] for place in stations(with_train, "gym", "terminal")] == ["sentral"]  # the planned train wins
    assert stations(with_train, "sentral", "terminal") == []  # starting at the station: no detour through it


# --- routes to compare ------------------------------------------------------------------------------------------------------

def test_latest_departure_compares_the_coach_with_the_train_from_the_trips_station(make):
    service, network = make(plan_with())
    result = query(service, "latest_departure", origin="Kletterhalle Nord, Lindenallee 12", destination="terminal", anchor_id="hn-arrive")
    assert result["resolved"]["origin"] == dict(said="Kletterhalle Nord, Lindenallee 12", place="gym", looked_up=GYM)
    assert not any(request.url.path.endswith("geocode") for request in network.requests)  # the address was never geocoded
    assert result["primary"]["boards"]["line"] == "FB 901" and result["primary"]["leave"] == "21:36"
    routes = {tuple(entry["route"]): entry for entry in result["candidates"]}
    coach, train = routes[("first_choice", "fastest")], routes[("via_station",)]
    assert coach["ticket"] == dict(need="extra", why="长途大巴 FB 901 要单独买票") and coach["reach"] == "22:07"
    assert (train["via"], train["label"], train["leave"], train["reach"], train["past_plan_target"]) == (
        "Nordkai", "经 Nordkai 坐火车", "21:07", "21:58", False)
    assert [(ride["line"], ride["depart"]) for ride in train["rides"]] == [("T4", "21:10"), ("REX 7", "21:35")]
    assert train["ticket"]["need"] == "unknown"
    assert "别的走法：经 Nordkai 坐火车最晚 21:07 出门，21:58 到" in result["summary"]
    # the evidence keeps every route
    stored = service.store.get("evidence", "local", result["evidence"]["id"])
    assert {tuple(entry["route"]) for entry in stored["value"]["candidates"]} == {("first_choice", "fastest"), ("via_station",)}


def test_the_full_result_has_every_leg_of_every_route_and_the_compact_one_stays_small(make):
    import pydantic_core

    service, _ = make(plan_with())
    arguments = dict(origin="gym", destination="terminal", anchor_id="hn-arrive")
    compact = query(service, "latest_departure", **arguments)
    full = query(service, "latest_departure", detail="full", **arguments)
    assert len(pydantic_core.to_json(compact, indent=2)) <= 10_000
    via = next(entry for entry in full["candidates"] if "via_station" in entry["route"])
    assert [leg["mode"] for leg in via["legs"]] == ["walk", "tram", "walk", "walk", "regional_rail", "walk"]


def test_without_a_station_the_fastest_route_is_still_offered(make):
    places = [place for place in PLACES if place["id"] != "nordkai"]
    service, _ = make(plan_with(places))
    result = query(service, "latest_departure", origin="gym", destination="terminal", anchor_id="hn-arrive")
    assert [entry["route"] for entry in result["candidates"]] == [["first_choice", "fastest"]]


# --- opening hours ----------------------------------------------------------------------------------------------------------

def test_open_places_lists_what_is_open_near_somewhere_with_where_the_hours_were_read(make):
    service, _ = make(plan_with())
    evening = query(service, "open_places", near="Nordkai", km=20, text="food")  # now: 20:45 on a Monday
    assert [(row["id"], row["closes"]) for row in evening["open"]] == [("kaikafe", "23:00"), ("grill", "21:00")]  # nearest first
    tonight = query(service, "open_places", near="Nordkai", km=20, text="food", at="21:30", date=DAY)
    assert [(row["id"], row["closes"]) for row in tonight["open"]] == [("kaikafe", "23:00")]
    assert tonight["open"][0]["hours_source"] == dict(url="https://kaikafe.example.org/hours") and tonight["open"][0]["distance_km"] < 0.2
    assert [row["id"] for row in tonight["closed"]] == ["grill"] and [row["id"] for row in tonight["unknown"]] == ["kiosk"]
    assert tonight["summary"].startswith("21:30 开着、离 Nordkai 20 公里内：Kaikafé Nord（") and "营业时间没存：Kiosk Fjordblick" in tonight["summary"]
    friday = query(service, "open_places", near="terminal", at="2030-06-15T01:30:00+02:00", km=1)  # Saturday 01:30: Friday's late hours
    assert [(row["id"], row["closes"]) for row in friday["open"]] == [("grill", "02:00")]
    late = query(service, "open_places", near="terminal", at="2030-06-14T23:00:00+02:00", km=1)
    assert late["open"][0]["closes"] == "02:00（次日）"
    with pytest.raises(ValueError, match="arguments.near: not a place of this trip with coordinates, nor 'lat,lon', got 'Atlantis'"):
        query(service, "open_places", near="Atlantis")


def test_hours_read_on_a_page_are_written_into_the_place_through_evidence_and_a_proposal(make):
    service, _ = make(plan_with())
    recorded = query(service, "web_evidence", url="https://kiosk.example.org/", quote="Kiosk Fjordblick: daily 07:00-24:00",
                     claim="Kiosk Fjordblick is open daily 07:00–24:00", type="opening_hours", check_quote=False)
    plan = service.state("local")["plan"]
    kiosk = next(place for place in plan["places"] if place["id"] == "kiosk")
    turn = service.turn("local", "Store the kiosk's hours.", "hours")
    wrong = deepcopy(plan)
    next(place for place in wrong["places"] if place["id"] == "kiosk").update(opening_hours=["daily 7-24"])
    with pytest.raises(ValueError, match=r"plan.places.\d+.opening_hours.0: must be weekdays and times like 'Mo-Fr 06:00-22:00'"):
        service.propose("local", ctx_id=turn["ctx_id"], base_version=1, plan=wrong, idempotency_key="wrong")
    kiosk.update(opening_hours=["Mo-Su 07:00-24:00"], hours_source=dict(url="https://kiosk.example.org/", evidence_id="EV-300610-nothere"))
    with pytest.raises(ValueError, match=r"plan.places.\d+.hours_source.evidence_id: no evidence 'EV-300610-nothere' in this trip"):
        service.propose("local", ctx_id=turn["ctx_id"], base_version=1, plan=plan, idempotency_key="missing")
    kiosk["hours_source"]["evidence_id"] = recorded["evidence"]["id"]
    draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=1, plan=plan, idempotency_key="hours")
    service.commit("local", draft["draft_id"], "hours", accept_unverified=True)
    listed = query(service, "open_places", near="Nordkai", text="food")
    assert {row["id"] for row in listed["open"]} == {"kaikafe", "kiosk"} and listed["unknown"] == []
    assert next(row for row in listed["open"] if row["id"] == "kiosk")["hours_source"]["evidence_id"] == recorded["evidence"]["id"]
