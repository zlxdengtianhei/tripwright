"""Latest-departure answers are compact by default. Offline: the fictional MOTIS and web of A5 at a fixed moment.
Fictional people, places and lines only."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pydantic_core

from a5_upstream import DESTINATION, Timetable, z
from test_a3_anchor_deadline import anchored_plan
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

NOW = datetime(2030, 6, 10, 18, 45, tzinfo=timezone.utc)
PLAN = anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"})
RULE = dict(url="https://ferry.example.org/rules", quote="The ferry counter closes 40 minutes before departure.",
            claim="The counter closes 40 minutes before the ferry leaves", type="rule")


def fixed(monkeypatch, moment: datetime = NOW):
    import tripwright.events as events_module
    import tripwright.service as service_module
    import tripwright.storage as storage_module

    monkeypatch.setattr(service_module, "now", lambda: moment.isoformat())
    monkeypatch.setattr(storage_module, "now", lambda: moment.isoformat())
    monkeypatch.setattr(events_module, "now", lambda: moment.isoformat())


class StreetTimetable(Timetable):
    """From a street address: walk to a tram, ride it, change on foot, take the train every 10 minutes, walk on."""

    def itinerary(self, train):
        trip = super().itinerary(train)
        walk, ride, onward = trip["legs"]
        start = datetime.fromisoformat(walk["startTime"].replace("Z", "+00:00")) - timedelta(minutes=14)
        tram_from, tram_to = start + timedelta(minutes=6), start + timedelta(minutes=16)
        legs = [dict(mode="WALK", **{"from": {"name": "Lindenallee 12, Nordstadt (fictional)"}}, to={"name": "Lindenplatz"},
                     startTime=z(start), endTime=z(tram_from)),
                dict(mode="TRAM", displayName="T4", headsign="Hafen über Altmarkt und Nordkai (fictional)", agencyName="Nordstadt Trams",
                     **{"from": {"name": "Lindenplatz", "track": "B"}}, to={"name": "Nordkai Vorplatz"}, startTime=z(tram_from), endTime=z(tram_to)),
                dict(walk, **{"from": {"name": "Nordkai Vorplatz"}}, startTime=z(tram_to)), ride, onward]
        return dict(trip, startTime=z(start), legs=legs)


def test_latest_departure_is_compact_by_default_and_full_on_request(tmp_path, monkeypatch):
    fixed(monkeypatch)
    trains = [f"{hour:02d}:{minute:02d}" for hour in (19, 20, 21, 22, 23) for minute in range(5, 60, 10)]
    timetable = StreetTimetable(trains)
    store = Store(tmp_path / "street.sqlite3")
    service = Tripwright(store, query_router=QueryRouter(store, transit=timetable.provider(clock=lambda: NOW)))
    try:
        turn = service.turn("local", "创建行程。", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=PLAN, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        arguments = dict(origin="Lindenallee 12, Nordstadt", destination=DESTINATION, anchor_id="hn-arrive", after="2030-06-10T19:00:00+02:00")
        compact = asyncio.run(service.query("local", "latest_departure", arguments))
        full = asyncio.run(service.query("local", "latest_departure", dict(arguments, detail="full")))
    finally:
        service.jobs.stop()
        store.close()
    size = len(pydantic_core.to_json(compact, indent=2))
    assert size <= 10_000 < len(pydantic_core.to_json(full, indent=2)), size
    assert compact["detail"] == "compact" and "connections" not in compact and compact["connections_count"] == len(full["connections"])
    assert compact["primary"]["boards"] == {"stop": "Lindenplatz", "time": full["primary"]["boards"]["time"], "line": "T4"}
    assert [leg["mode"] for leg in compact["primary"]["legs"]] == ["walk", "tram", "walk", "regional_rail", "walk"]
    assert compact["summary"] == full["summary"] and compact["window"] == full["window"] and compact["fallback"]["leave"] == full["fallback"]["leave"]
    assert all(view["past_plan_target"] for view in compact["later"]) and "summary" not in compact["primary"]
    assert [ride["mode"] for ride in compact["later"][0]["rides"]] == ["tram", "regional_rail"]
    assert {"first_choice", "fastest"} <= {route for entry in compact["candidates"] for route in entry["route"]}
    assert len(compact["later"]) == 3 < compact["later_count"] == len(full["later"])
    stored = Store(tmp_path / "street.sqlite3")
    try:
        assert len(stored.get("evidence", "local", compact["evidence"]["id"])["value"]["later"]) == compact["later_count"]
    finally:
        stored.close()
