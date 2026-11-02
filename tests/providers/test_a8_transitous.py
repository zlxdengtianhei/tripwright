"""A8 (D-57, D-60): the Transitous provider says which stop it searched, cleans what names nothing, knows where and
when its data runs, and keeps to Transitous's terms. Offline: real Transitous answers from 2026-11-01
(tests/providers/fixtures/transitous) behind a fake upstream. Each check has a bad sample and a valid control."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from tripwright.providers import coverage
from tripwright.providers.base import ProviderUnavailable
from tripwright.providers.common import PROJECT_URL, RollingBudget, user_agent
from tripwright.providers.transitous import ATTRIBUTION, TransitousProvider, headsign, normalize

FIXTURES = Path(__file__).parent / "fixtures" / "transitous"
VIENNA = ZoneInfo("Europe/Vienna")


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Recorded:
    """Answers geocode with one fixture and plan with another; the plan's own from/to name the stops asked for, as the
    real upstream's do."""

    def __init__(self, geocode: str | None = None, plan: str = "plan_wienhbf_badischl_1104.json"):
        self.geocode = fixture(geocode) if geocode else []
        self.plan = fixture(plan)
        self.requests: list[httpx.Request] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("geocode"):
            return httpx.Response(200, json=self.geocode)
        if request.url.path.endswith("one-to-many"):
            return httpx.Response(200, json=[{"duration": 600} for _ in request.url.params["many"].split(",")])
        body = json.loads(json.dumps(self.plan))
        stops = {hit["id"]: hit for hit in self.geocode}
        for key, param, word in (("from", "fromPlace", "START"), ("to", "toPlace", "END")):
            asked = request.url.params[param]
            if asked in stops:
                body[key] = {"name": stops[asked]["name"], "stopId": asked, "lat": stops[asked]["lat"], "lon": stops[asked]["lon"]}
            elif "," in asked:  # coordinates: MOTIS names the end START or END
                lat, lon = (float(part) for part in asked.split(","))
                body[key] = {"name": word, "lat": lat, "lon": lon, "vertexType": "NORMAL"}
        return httpx.Response(200, json=body)

    def provider(self, **options) -> TransitousProvider:
        return TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)), min_interval=0, **options)


# --- place names: not the first match unseen --------------------------------------------------------------------------

@pytest.mark.parametrize("text, geocode, chosen, how", [
    ("Bad Ischl", "geocode_bad_ischl.json", "Bad Ischl Bahnhof", "train station"),  # first match: Bad Ischl Sueß, 4 km off
    ("Hallstatt", "geocode_hallstatt.json", "Hallstatt Lahn (Busterminal)", "hub"),  # the station is across the lake
    ("Hallstatt Bahnhof", "geocode_hallstatt.json", "Hallstatt Bahnhst", "train station"),  # asked for the station: given it
    ("Wien Hbf", "geocode_wien_hbf.json", "Wien Hbf", "first stop match"),  # control: the first match is right
    ("Graz Hbf", "geocode_graz_hbf.json", "Graz Hauptbahnhof", "first stop match"),  # not the night train's "GRAZ HBF"
])
def test_a_typed_place_resolves_to_its_station_or_hub_and_says_so(text, geocode, chosen, how):
    upstream = Recorded(geocode)
    resolved = asyncio.run(upstream.provider().resolve(text)).value
    assert (resolved["label"], resolved["how"], resolved["said"]) == (chosen, how, text)
    assert resolved["alternatives"] and chosen not in resolved["alternatives"]
    assert fixture(geocode)[0]["name"] in [chosen, *resolved["alternatives"]]


def test_a_plan_says_which_stops_it_searched():
    upstream = Recorded("geocode_bad_ischl.json")
    result = asyncio.run(upstream.provider().plan("Bad Ischl", "47.7393,13.4478", datetime(2026, 11, 4, 10, tzinfo=VIENNA)))
    origin, destination = result.meta["origin"], result.meta["destination"]
    assert origin["said"] == "Bad Ischl" and origin["name"] == "Bad Ischl Bahnhof" and origin["how"] == "train station"
    assert origin["stop_id"] == "at-Railway-Current-Reference-Data-2026_Pat:44:46500" and "Bad Ischl Sueß" in origin["alternatives"]
    assert upstream.requests[-1].url.params["fromPlace"] == origin["stop_id"]
    assert destination["how"] == "coordinates" and destination["name"] is None
    assert result.meta["days"] == ["2026-11-04"] and any(stop.startswith("at-") for stop in result.meta["stops"])
    # the DRF from St. Pölten comes from a German dataset: a lookup's regions are its stops' feed lists
    assert upstream.provider().feed_lists(result.meta) >= {"at", "de"}


# --- headsigns -----------------------------------------------------------------------------------------------------------

def test_a_headsign_that_names_nothing_is_dropped_and_a_real_one_kept():
    itineraries = normalize(fixture("plan_wienhbf_badischl_1104.json"), VIENNA)
    rides = [leg for itinerary in itineraries for leg in itinerary.legs if leg.mode != "walk"]
    by_line = {leg.line: leg.extra.get("headsign") for leg in rides}
    assert by_line["546"] is None  # the feed sends "0"
    assert by_line["150"] == "Bad Ischl via Hof - Fuschl - St.Gilgen - Strobl" and by_line["RJX 19962"] == "Bregenz Bahnhof"
    assert [headsign(value) for value in ("0", " 12 ", "", None, "Hallstatt Gosaumühle", "U4")] == [None, None, None, None, "Hallstatt Gosaumühle", "U4"]


# --- coverage: dates and areas -------------------------------------------------------------------------------------------

def ends(result):
    return [result.meta["origin"], result.meta["destination"]]


def test_a_date_after_the_timetable_change_is_not_in_the_data_yet_and_not_no_service():
    upstream = Recorded("geocode_wien_hbf.json", plan="plan_wienhbf_badischl_1220.json")
    provider = upstream.provider()
    result = asyncio.run(provider.plan("Wien Hbf", "47.7120,13.6272", datetime(2026, 12, 20, 8, tzinfo=VIENNA)))
    assert result.value == []
    notes = provider.coverage(result.meta, "zh")
    assert [note["kind"] for note in notes] == ["not_yet"] and notes[0]["until"] == "2026-12-12"
    assert "2026-12-13 起换新时刻表" in notes[0]["text"] and "现在查不到" in notes[0]["text"] and "不能说没车" in notes[0]["text"]
    # control: the same route inside the timetable year
    inside = asyncio.run(Recorded("geocode_wien_hbf.json").provider().plan("Wien Hbf", "47.7120,13.6272", datetime(2026, 11, 4, 8, tzinfo=VIENNA)))
    assert inside.value and provider.coverage(inside.meta, "zh") == []


def test_an_end_in_carinthia_says_its_local_buses_are_not_in_the_data():
    provider = Recorded(plan="plan_klagenfurt_1104.json").provider()
    result = asyncio.run(provider.plan("46.6155,14.3127", "46.6237,14.3054", datetime(2026, 11, 4, 10, tzinfo=VIENNA)))
    notes = provider.coverage(result.meta, "zh")
    assert [note["kind"] for note in notes] == ["gap"] and notes[0]["text"].startswith("这一带的本地巴士不在数据里")
    # controls: the lake district, and places just outside Carinthia
    data = coverage.load()
    for lat, lon in [(47.7120, 13.6272), (46.8290, 12.7690), (47.1100, 13.1340), (47.1100, 14.1700), (47.0650, 14.6900)]:  # Bad Ischl, Lienz, Bad Gastein, Murau, Obdach
        assert coverage.assess([dict(lat=lat, lon=lon)], [date(2026, 11, 4)], "zh", data=data) == []
    for lat, lon in [(46.6249, 14.3050), (46.6111, 13.8558), (46.7963, 13.4950), (47.0400, 12.8400)]:  # Klagenfurt, Villach, Spittal, Heiligenblut
        assert [note["kind"] for note in coverage.assess([dict(lat=lat, lon=lon)], [date(2026, 11, 4)], "zh", data=data)] == ["gap"]


def test_regions_are_read_alike_from_the_feed_lists():
    data = coverage.load()
    assert len(data["files"]) > 100 and {"at", "de", "ch", "eu", "au-nsw"} <= set(data["files"])
    assert coverage.file_of("au-nsw-Sydney-Trains_123", data) == "au-nsw" and coverage.file_of("at-Railway-Current-Reference-Data-2026_Pat:1", data) == "at"
    assert coverage.file_of("xx-Nowhere_1", data) is None
    # a German stop: its feed list is known, no timetable-year rule is, so no date note (and no Austrian one)
    german = [dict(stop_id="de-DELFI_de:09162:6", lat=48.14, lon=11.56)]
    assert coverage.files_for(german, [], data) == {"de"}
    assert coverage.assess(german, [date(2027, 1, 10)], "zh", data=data) == []
    assert [note["kind"] for note in coverage.assess([dict(stop_id="at-PTA-Salzburg-Flex-2026_x")], [date(2027, 1, 10)], "zh", data=data)] == ["not_yet"]


def test_refresh_regenerates_dates_and_skips_from_the_feed_lists():
    data = coverage.load()
    feeds = {"at": fixture("feeds_at.json"), "eu": fixture("feeds_eu.json")}
    now = coverage.refresh(data, feeds)
    assert now["valid"]["at-mvo"]["until"] == "2026-12-12" and now["valid"]["at-mvo"]["from"] == "2025-12-14"
    assert now["skipped"]["at-PTA-Carinthia-Flex-2026"] == "Stay Seated forever during MOTIS import"
    assert set(now["files"]) == {"at", "eu"} and now["areas"] == data["areas"] and now["periods"] == data["periods"]
    # the next timetable year arrives and Carinthia is taken back in: the data changes, the code does not
    later = json.loads(json.dumps(feeds))
    for source in list(later["at"]["sources"]):
        if source["name"].startswith("PTA-Carinthia"):
            source.pop("skip", None)
        if source.get("x-mvo-id"):
            later["at"]["sources"].append(dict(source, name=source["name"].replace("2026", "2027"), **{"x-mvo-id": source["x-mvo-id"].replace("2026", "2027")}))
    renewed = coverage.refresh(data, later)
    assert renewed["valid"]["at-mvo"]["until"] == "2027-12-11" and 2027 in renewed["valid"]["at-mvo"]["years"]
    assert not any(key.startswith("at-PTA-Carinthia") for key in renewed["skipped"])
    assert coverage.assess([dict(lat=46.6249, lon=14.3050)], [date(2026, 11, 4)], "zh", data=renewed) == []
    assert coverage.second_saturday_of_december(2026) == date(2026, 12, 12)


def test_the_refresh_command_writes_the_data_file(tmp_path, capsys):
    target = tmp_path / "coverage.json"
    target.write_text(coverage.DATA.read_text(encoding="utf-8"), encoding="utf-8")
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / "at.json").write_text((FIXTURES / "feeds_at.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert coverage.main(["refresh", str(feeds), "--data", str(target), "--commit", "abc123"]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["commit"] == "abc123" and set(written["files"]) == {"at"} and "at-mvo: 2025-12-14—2026-12-12" in capsys.readouterr().out


# --- Transitous's terms ----------------------------------------------------------------------------------------------------

def test_route_searches_stop_at_the_daily_limit_and_lookups_of_names_do_not_count(tmp_path):
    upstream = Recorded("geocode_wien_hbf.json")
    provider = upstream.provider(routing_per_day=2)
    when = datetime(2026, 11, 4, 8, tzinfo=VIENNA)

    async def run():
        await provider.stops("Wien Hbf")
        await provider.plan("47.1,13.1", "47.2,13.2", when, walk_min=20)
        await provider.walk_minutes(provider_place(), [provider_place()])
        with pytest.raises(ProviderUnavailable, match="ROUTING_BUDGET_EXCEEDED"):
            await provider.plan("47.1,13.1", "47.2,13.2", when + timedelta(hours=1), walk_min=20)
        await provider.stops("Wien Hbf", fresh=True)  # geocoding is not a route search

    asyncio.run(run())
    assert provider.routing.used() == 2 and ATTRIBUTION.count("https://transitous.org/sources/") == 1


def provider_place():
    from tripwright.providers.base import Place
    return Place("A", 47.1, 13.1)


def test_the_limit_is_shared_through_a_file_and_rolls_over_24_hours(tmp_path):
    moment = [datetime(2026, 11, 1, 12, tzinfo=timezone.utc)]
    path = tmp_path / "routing.log"
    one, two = RollingBudget(2, path=path, clock=lambda: moment[0]), RollingBudget(2, path=path, clock=lambda: moment[0])
    assert one.take() and two.take() and not one.take() and not two.take()
    moment[0] += timedelta(hours=23, minutes=59)
    assert not one.take()
    moment[0] += timedelta(minutes=2)
    assert one.take() and two.used() == 1
    alone = RollingBudget(1)
    assert alone.take() and not alone.take()
    # the real-network provider counts in the shared file by default; one with an injected client (tests) does not
    assert TransitousProvider().routing.path is not None and Recorded().provider().routing.path is None


def test_the_user_agent_names_the_app_its_version_and_the_project_not_a_person(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_USER_AGENT", raising=False)
    upstream = Recorded()
    asyncio.run(upstream.provider().plan("47.1,13.1", "47.2,13.2", datetime(2026, 11, 4, 8, tzinfo=VIENNA), walk_min=20))
    sent = upstream.requests[-1].headers["user-agent"]
    assert sent == user_agent() and sent.startswith("tripwright/0.") and PROJECT_URL in sent and "@" not in sent
