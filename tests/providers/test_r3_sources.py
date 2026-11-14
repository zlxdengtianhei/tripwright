"""R-07, R-08: a deployment's own MOTIS beside Transitous (providers/sources.py). A search goes to the source whose
area holds both ends; anything else goes to Transitous as before. Each promise has a bad sample and a legal one."""
import asyncio
import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from tripwright.providers import sources
from tripwright.providers.base import Place
from tripwright.providers.transitous import TransitousProvider

TZ = ZoneInfo("Europe/Berlin")
WHEN = datetime(2030, 6, 10, 9, 0, tzinfo=TZ)
ENTRY = dict(id="avv", name={"en": "Aachen (AVV)", "zh": "亚琛 AVV"}, api="http://motis.test/api", bbox=[5.9, 50.6, 6.5, 50.95],
             datasets=["avv"], match=["aachen", "亚琛"], realtime=True, attribution="Timetable: AVV; map data © OpenStreetMap")
LOCAL_STOPS = {"Aachen, Bushof": ("avv_100101", 50.7776, 6.0909), "Aachen West": ("avv_108670", 50.7802, 6.0710)}
FAR_STOPS = {"Wien Hbf": ("at-x_1", 48.185, 16.376), "Aachen West": ("de-x_2", 50.7802, 6.0710),
             "Aachen, Bushof": ("de-x_3", 50.7776, 6.0909)}


def server(stops: dict, calls: list, name: str, *, down=False):
    def handle(request: httpx.Request):
        calls.append((name, request.url.path))
        if down:
            return httpx.Response(503)
        if request.url.path.endswith("/v1/geocode"):
            text = request.url.params["text"].casefold()
            hits = [dict(type="STOP", name=stop, id=values[0], lat=values[1], lon=values[2], areas=[], modes=["BUS"])
                    for stop, values in stops.items() if all(word in stop.casefold() for word in text.replace(",", " ").split())]
            return httpx.Response(200, json=hits)
        if request.url.path.endswith("/v6/plan"):
            ends = [request.url.params["fromPlace"], request.url.params["toPlace"]]
            named = {values[0]: stop for stop, values in stops.items()}
            leg = dict(mode="BUS", routeShortName="1", realTime=name == "local", startTime="2030-06-10T07:01:00Z",
                       endTime="2030-06-10T07:12:00Z", **{"from": dict(name=named.get(ends[0], "A"), stopId=ends[0]),
                                                           "to": dict(name=named.get(ends[1], "B"), stopId=ends[1])})
            return httpx.Response(200, json=dict(itineraries=[dict(startTime=leg["startTime"], endTime=leg["endTime"], legs=[leg])],
                                                 direct=[], **{"from": leg["from"], "to": leg["to"]}))
        return httpx.Response(404)
    return handle


def routed(*, local_down=False):
    calls = []
    default = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(server(FAR_STOPS, calls, "transitous"))),
                                 min_interval=0)
    entry = sources.check_entry(ENTRY)
    local = TransitousProvider(entry["api"], source="motis:avv", attribution=entry["attribution"], min_interval=0,
                               client=httpx.AsyncClient(transport=httpx.MockTransport(server(LOCAL_STOPS, calls, "local", down=local_down))))
    return sources.RoutedTransit(default, [sources.Local(entry, local)]), calls


def run(coroutine):
    return asyncio.run(coroutine)


# --- the sources file ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [dict(id="a_b"), dict(api="motis:8080"), dict(bbox=[6.5, 50.6, 5.9, 50.95]), dict(attribution=""),
                                 dict(colour="red")])
def test_a_sources_file_entry_is_refused_with_what_is_wrong(bad):
    with pytest.raises(ValueError):
        sources.check_entry({**ENTRY, **bad})
    assert sources.check_entry(ENTRY)["datasets"] == ["avv"]
    assert sources.check_entry({key: value for key, value in ENTRY.items() if key != "datasets"})["datasets"] == ["avv"]


def test_without_the_setting_the_service_uses_transitous_alone(monkeypatch, tmp_path):
    monkeypatch.delenv(sources.ENV, raising=False)
    assert type(sources.transit(usage_file=tmp_path / "u.log")) is TransitousProvider  # bad sample would be a router with nothing in it
    path = tmp_path / "sources.yaml"
    path.write_text("sources:\n  - id: avv\n    api: http://motis:8080/api\n    bbox: [5.9, 50.6, 6.5, 50.95]\n"
                    "    attribution: AVV\n    match: [aachen]\n    realtime: true\n", encoding="utf-8")
    monkeypatch.setenv(sources.ENV, str(path))
    provider = sources.transit(usage_file=tmp_path / "u.log")
    assert isinstance(provider, sources.RoutedTransit) and provider.sources[0].provider.source == "motis:avv"
    assert provider.sources[0].provider.routing is None  # Transitous's 720-a-day terms do not count the deployment's own server
    assert [entry["id"] for entry in sources.for_areas("aachen, eifel")] == ["avv"] and sources.for_areas("wien") == []
    run(provider.aclose())


def test_the_setting_can_hold_the_list_itself():
    entries = sources.read('[{"id": "avv", "api": "http://motis:8080/api", "bbox": [5.9, 50.6, 6.5, 50.95], "attribution": "AVV"}]')
    assert entries[0]["api"] == "http://motis:8080/api" and entries[0]["name"] == {"en": "avv", "zh": "avv"}
    with pytest.raises(ValueError):  # bad sample: two sources under one id would make the routing ambiguous
        sources.read("[{id: a, api: 'http://a/api', bbox: [0, 0, 1, 1], attribution: x}, {id: a, api: 'http://b/api', bbox: [0, 0, 1, 1], attribution: y}]")


def test_bbox_is_read_from_a_gtfs_zip(tmp_path):
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n1,A,50.70,6.00\n2,B,50.80,6.20\n3,C,0,0\n")
    assert sources.bbox(path) == [5.99, 50.69, 6.21, 50.81]  # the 0,0 stop (a missing position) does not stretch it


# --- which source answers -------------------------------------------------------------------------------------------

def test_a_search_inside_the_area_goes_to_the_deployments_source():
    provider, calls = routed()
    result = run(provider.plan("Aachen Bushof", "Aachen West", WHEN, limit=3))
    assert result.source == "motis:avv" and result.value[0].legs[0].extra["realTime"] is True
    assert ENTRY["attribution"] in result.notes and not any("Transitous" in note for note in result.notes)
    assert ("transitous", "/api/v6/plan") not in calls
    assert provider.coverage(result.meta) == [] and provider.feed_lists(result.meta) == set()


def test_a_search_with_one_end_outside_goes_to_transitous():
    provider, calls = routed()
    result = run(provider.plan("Aachen West", "Wien Hbf", WHEN))
    assert result.source == "transitous"  # bad sample: the local source cannot route past its area's edge
    assert ("local", "/api/v6/plan") not in calls


def test_a_name_the_source_does_not_fully_match_goes_to_transitous():
    provider, _ = routed()
    assert run(provider.resolve("Wien Hbf")).source == "transitous"
    assert run(provider.resolve("Aachen, Bushof")).source == "motis:avv"


def test_places_and_coordinates_go_by_the_bbox():
    provider, _ = routed()
    inside = [Place("A", 50.77, 6.09), Place("B", 50.78, 6.07)]
    assert asyncio.run(provider._pick(inside[0], "50.78,6.07")) is provider.sources[0].provider
    assert asyncio.run(provider._pick(inside[0], Place("Wien", 48.2, 16.37))) is provider.default
    assert asyncio.run(provider._pick("avv_100101", "avv_108670")) is provider.sources[0].provider


def test_when_the_source_is_down_transitous_answers():
    provider, calls = routed(local_down=True)
    result = run(provider.plan("Aachen Bushof", "Aachen West", WHEN))
    assert result.source == "transitous" and ("local", "/api/v1/geocode") in calls
