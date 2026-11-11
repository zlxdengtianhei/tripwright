"""Query reads a naive ISO date-time in the query's zone, without relaxing aware() elsewhere."""
from __future__ import annotations

import asyncio

import pytest

from a5_upstream import DESTINATION, ORIGIN, Timetable
from test_a3_anchor_deadline import anchored_plan
from tripwright.query import QueryRouter
from tripwright.storage import Store


@pytest.fixture
def router():
    store = Store(":memory:")
    yield QueryRouter(store, transit=Timetable().provider())
    store.close()


def query(router, kind, arguments, *, plan=None):
    return asyncio.run(router.query("alice", kind, arguments, anchored_plan() if plan is None else plan,
                                    at="2030-06-10T18:00:00Z"))


def test_transit_naive_when_equals_the_same_instant_with_the_trip_offset(router):
    arguments = dict(origin=ORIGIN, destination=DESTINATION, when="2030-06-10T21:00")
    naive = query(router, "transit", arguments)
    offset = query(router, "transit", dict(arguments, when="2030-06-10T21:00:00+02:00"))
    assert naive["window"] == offset["window"] and naive["connections"] == offset["connections"]
    assert arguments["when"] == "2030-06-10T21:00"


def test_open_places_naive_at_equals_the_same_instant_with_the_trip_offset(router):
    arguments = dict(near="60.1,10.7", at="2030-06-10T21:30:15")
    naive = query(router, "open_places", arguments)
    offset = query(router, "open_places", dict(arguments, at="2030-06-10T21:30:15+02:00"))
    assert naive["at"] == offset["at"] == "2030-06-10T21:30:15+02:00"
    assert naive["open"] == offset["open"] and naive["closed"] == offset["closed"]


def test_latest_departure_naive_by_equals_the_same_instant_with_the_trip_offset(router):
    arguments = dict(origin=ORIGIN, destination=DESTINATION, by="2030-06-10T23:10")
    naive = query(router, "latest_departure", arguments)
    offset = query(router, "latest_departure", dict(arguments, by="2030-06-10T23:10:00+02:00"))
    assert naive["target"] == offset["target"] and naive["primary"] == offset["primary"]


def test_arguments_timezone_overrides_the_trip_zone_for_a_naive_time(router):
    naive = query(router, "open_places", dict(near="60.1,10.7", at="2030-06-10T21:30", timezone="UTC"))
    offset = query(router, "open_places", dict(near="60.1,10.7", at="2030-06-10T21:30:00Z", timezone="UTC"))
    assert naive["at"] == offset["at"] == "2030-06-10T21:30:00+00:00" and naive["timezone"] == "UTC"


@pytest.mark.parametrize("at", ["2030-03-31T02:30", "2030-10-27T02:30"])
def test_a_naive_time_without_one_unique_instant_is_refused(router, at):
    with pytest.raises(ValueError, match="add a timezone offset"):
        query(router, "open_places", dict(near="60.1,10.7", at=at))


def test_a_naive_time_without_any_zone_names_both_fixes(router):
    with pytest.raises(ValueError, match=r"timezone offset.*arguments.timezone"):
        query(router, "open_places", dict(near="60.1,10.7", at="2030-06-10T21:30"), plan={})
