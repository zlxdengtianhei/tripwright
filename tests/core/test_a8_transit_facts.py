"""A8 (D-57, D-60): transit facts are written by the service, written right, and say their limits.

- "The last one", "only … left", "nothing later", "the first one", "at the latest" and "direct" pass check_answer only
  when a lookup of this question covered what they say; the fix is a sentence the host can copy.
- Transit and latest_departure results carry sentences a host copies: every ride with its own stops, how many changes,
  timetable or real time; the server's own sentences pass the check. What the timetable cannot say (real-time delays,
  platforms, disruptions) is not added to each sentence: `how_to_check` says apart which app, what to search and which
  steps (D-61, which replaced D-60's reminder on every sentence).
- A date after the timetable change says the data is not in yet, not that there is no service; a typed name says
  which stop was searched.

Offline: fake upstreams in MOTIS's shapes with public Vienna stop names and invented times, and real Transitous answers
from tests/providers/fixtures/transitous. Each check has a bad sample and a valid control."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

import tripwright.events as events_module
import tripwright.service as service_module
import tripwright.storage as storage_module
from tripwright.demo import example
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.render import check_answer as checks
from tripwright.service import Tripwright
from tripwright.storage import Store

VIENNA = ZoneInfo("Europe/Vienna")
DAY = "2026-11-04"
HBF = "at-Railway-Current-Reference-Data-2026_Pat:49:1349"
AIRPORT = "at-Railway-Current-Reference-Data-2026_Pat:43:4708"
STREET = "48.1890,16.3520"  # a street in Margareten (coordinates: no stop lookup)
FIXTURES = Path(__file__).parents[1] / "providers" / "fixtures" / "transitous"
# REX from Wien Hbf to Flughafen Wien, 22 minutes; the last at 00:53, the first again at 04:22 (shape of judgment 4)
NIGHT = ("22:53", "23:23", "23:53", "00:23", "00:53", "04:22", "04:52")


def local(clock: str, day: str = DAY) -> datetime:
    moment = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=VIENNA)
    return moment + timedelta(days=1) if clock < "12:00" else moment


def z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def leg(mode, origin, destination, start, end, **more) -> dict:
    return {"mode": mode, "from": origin, "to": destination, "startTime": z(start), "endTime": z(end),
            "duration": int((end - start).total_seconds()), **more}


def stop(name, stop_id, lat=48.19, lon=16.37) -> dict:
    return {"name": name, "stopId": stop_id, "lat": lat, "lon": lon}


class Night:
    """MOTIS answering a departure search: the next `numItineraries` trains after `time`. From the street it walks
    10 minutes to the station first, so the door leaves 10 minutes before the train."""

    def __init__(self, trains=NIGHT, *, walk=10):
        self.trains = [local(clock) for clock in trains]
        self.walk = timedelta(minutes=walk)
        self.requests: list[httpx.Request] = []

    def itinerary(self, train: datetime, from_street: bool) -> dict:
        rex = leg("REGIONAL_RAIL", stop("Wien Hauptbahnhof", HBF), stop("Flughafen Wien Bahnhof", AIRPORT, 48.12, 16.56),
                  train, train + timedelta(minutes=22), displayName="REX 7", headsign="Flughafen Wien", realTime=False)
        legs = ([leg("WALK", {"name": "START", "lat": 48.189, "lon": 16.352}, stop("Wien Hauptbahnhof", HBF), train - self.walk, train)]
                if from_street else []) + [rex]
        return {"startTime": legs[0]["startTime"], "endTime": rex["endTime"], "duration": 0, "legs": legs}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        from_street = "," in params["fromPlace"]
        moment = datetime.fromisoformat(params["time"])
        lead = self.walk if from_street else timedelta()
        if params["arriveBy"] == "true":
            chosen = [train for train in self.trains if train + timedelta(minutes=22) <= moment][-int(params["numItineraries"]):]
        else:
            chosen = [train for train in self.trains if train - lead >= moment][:int(params["numItineraries"])]
        body = {"from": {"name": "START", "lat": 48.189, "lon": 16.352} if from_street else stop("Wien Hauptbahnhof", HBF),
                "to": stop("Flughafen Wien Bahnhof", AIRPORT, 48.12, 16.56), "direct": [],
                "itineraries": [self.itinerary(train, from_street) for train in chosen]}
        return httpx.Response(200, json=body)


class Bus13A:
    """From a street in Margareten: 13A to Kolschitzkygasse, then tram 1 to Stephansplatz (the route "13A … →
    Stephansplatz" was read as direct in judgment 4); and to Hauptbahnhof, 13A alone."""

    async def handle(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        start = local("22:00")
        lane = "at-PTA-Eastern-Region-Flex-2026_at:49:"
        walk = leg("WALK", {"name": "START", "lat": 48.189, "lon": 16.352}, stop("Wien Leibenfrostgasse", lane + "1"), start - timedelta(minutes=5), start)
        if params["toPlace"] == "48.2085,16.3731":  # Stephansplatz
            bus = leg("BUS", stop("Wien Leibenfrostgasse", lane + "1"), stop("Wien Kolschitzkygasse", lane + "2"), start, start + timedelta(minutes=5),
                      displayName="13A", headsign="Wien Hauptbahnhof", realTime=False)
            change = leg("WALK", stop("Wien Kolschitzkygasse", lane + "2"), stop("Wien Kolschitzkygasse", lane + "3"), start + timedelta(minutes=5),
                         start + timedelta(minutes=7))
            tram = leg("TRAM", stop("Wien Kolschitzkygasse", lane + "3"), stop("Wien Stephansplatz", lane + "4"), start + timedelta(minutes=7),
                       start + timedelta(minutes=13), displayName="1", headsign="Prater Hauptallee", realTime=False)
            legs = [walk, bus, change, tram]
        else:
            legs = [walk, leg("BUS", stop("Wien Leibenfrostgasse", lane + "1"), stop("Wien Hauptbahnhof", lane + "5"), start,
                              start + timedelta(minutes=7), displayName="13A", headsign="Wien Hauptbahnhof", realTime=False)]
        body = {"from": {"name": "START", "lat": 48.189, "lon": 16.352}, "to": {"name": "END", "lat": 48.2, "lon": 16.37}, "direct": [],
                "itineraries": [{"startTime": legs[0]["startTime"], "endTime": legs[-1]["endTime"], "duration": 0, "legs": legs}]}
        return httpx.Response(200, json=body)


class Recorded:
    """Real Transitous answers (2026-11-01): one geocode fixture and one plan fixture; the plan's ends name what was asked."""

    def __init__(self, geocode: str, plan: str):
        self.geocode = json.loads((FIXTURES / geocode).read_text(encoding="utf-8"))
        self.plan = json.loads((FIXTURES / plan).read_text(encoding="utf-8"))

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("geocode"):
            return httpx.Response(200, json=self.geocode)
        body = deepcopy(self.plan)
        hits = {hit["id"]: hit for hit in self.geocode}
        for key, param, word in (("from", "fromPlace", "START"), ("to", "toPlace", "END")):
            asked = request.url.params[param]
            if asked in hits:
                body[key] = {"name": hits[asked]["name"], "stopId": asked, "lat": hits[asked]["lat"], "lon": hits[asked]["lon"]}
            elif "," in asked:  # coordinates: MOTIS names the end START or END
                lat, lon = (float(part) for part in asked.split(","))
                body[key] = {"name": word, "lat": lat, "lon": lon}
        return httpx.Response(200, json=body)


def vienna_plan(anchors=()) -> dict:
    plan = deepcopy(example()["trip"])
    plan.update(timezone="Europe/Vienna", start=DAY, end=DAY, bookings=[], days=[dict(plan["days"][0], date=DAY, anchors=list(anchors))])
    plan["places"].append(dict(id="ischl-station", name="巴德伊舍火车站", name_local="Bad Ischl Bahnhof", area=plan["places"][0]["area"],
                               kind="station", coords=[47.7120, 13.6272]))
    return plan


def fixed(monkeypatch, moment: datetime):
    for module in (service_module, storage_module, events_module):
        monkeypatch.setattr(module, "now", lambda: moment.isoformat())


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream, *, at="22:30", anchors=()):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a8-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)), min_interval=0,
                                     clock=lambda: moment)
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        made.append((service, store))
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=vienna_plan(anchors), idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def query(service, kind, **arguments):
    return asyncio.run(service.query("local", kind, arguments))


def second(*lines: str, at: str = "22:30") -> str:
    return "\n".join([f"# 第二段 · 核过了（{at}）", "和第一段不同：没有不同。", *lines, "## 完整安排", "22:30 出门 · 走到 Wien Hbf",
                      "以上交通时刻按时刻表，不是实时。"])  # S4h2: the timetable said once


def problems(service, text) -> dict[str, list[str]]:
    found = {}
    for entry in query(service, "check_answer", text=text, part="second")["problems"]:
        found.setdefault(entry["check"], []).append(entry["why"] + " | " + entry["fix"])
    return found


# --- 1. negative and universal sentences need a window that covers them -----------------------------------------------

def test_only_left_is_refused_when_the_window_found_a_later_train(make):
    service = make(Night(), at="23:30")
    found = query(service, "transit", origin=STREET, destination=AIRPORT, when=local("23:30").isoformat(), limit=2)
    assert found["window"]["to"].startswith(f"{DAY}T00:13") or found["window"]["to"][11:16] == "00:13"  # 23:53 and 00:23 boards
    bad = problems(service, second("只剩 23:53 那班 REX，坐它去机场。", at="23:30"))
    assert list(bad) == ["only_inside_window"] and "工具查到 23:53 之后还有 00:23 REX 7" in bad["only_inside_window"][0]
    assert "写成“查到 00:13 为止；之后的没查”" in bad["only_inside_window"][0]
    # control: the same lookup, said as what was found
    assert problems(service, second("查到 23:53 和 00:23 两班 REX；查到 00:13 为止；之后的没查。", at="23:30")) == {}


def test_the_last_train_needs_a_lookup_that_found_the_next_one(make):
    service = make(Night(), at="23:30")
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("23:40").isoformat(), limit=1)  # only the 23:53
    bad = problems(service, second("23:53 是末班。", at="23:30"))
    assert list(bad) == ["only_inside_window"] and "查询没查到 23:53 之后的班次" in bad["only_inside_window"][0]
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("00:40").isoformat(), limit=2)  # 00:53, then 04:22
    assert problems(service, second("00:53 之后到 04:22 没有火车。", at="23:30")) == {}
    assert problems(service, second("末班 00:53，下一班 04:22。", at="23:30")) == {}
    wrong = problems(service, second("23:53 之后到 04:22 没有火车。", at="23:30"))
    assert list(wrong) == ["only_inside_window"] and "还有 00:53 REX 7" in wrong["only_inside_window"][0]


def test_nothing_later_without_a_time_needs_a_search_that_went_past_the_target(make):
    service = make(Night(), at="22:20", anchors=[dict(id="hn-check-in", time="23:30", text="Check-in closes", subject=["p1"],
                                                       consequence="Missed flight.", alternative="Next flight.", deadline="external")])
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("22:30").isoformat(), limit=1)
    bad = problems(service, second("没有更晚的走法，只剩夜里的后备车。", at="22:20"))
    assert list(bad) == ["only_inside_window"] and "没写到几点" in bad["only_inside_window"][0]
    latest = query(service, "latest_departure", origin=HBF, destination=AIRPORT, anchor_id="hn-check-in")
    assert latest["window"]["covers_target"]
    assert problems(service, second("没有更晚的走法，误了这班就赶不上值机。", at="22:20")) == {}


def test_at_the_latest_needs_the_search_to_cover_the_target(make):
    anchor = dict(id="hn-check-in", time="23:30", text="Check-in closes", subject=["p1"], consequence="Missed flight.",
                  alternative="Next flight.", deadline="external")
    service = make(Night(("22:53",)), at="22:20", anchors=[anchor])  # the data ends at 22:53: the search cannot pass the target
    partial = query(service, "latest_departure", origin=HBF, destination=AIRPORT, anchor_id="hn-check-in")
    assert not partial["window"]["covers_target"]
    bad = problems(service, second("最晚 22:53 坐 REX 7 去机场。", at="22:20"))
    assert list(bad) == ["only_inside_window"] and "查询没查到 22:53 之后的班次" in bad["only_inside_window"][0]
    assert not any("最晚" in sentence for sentence in partial["sentences"]) and "查到 22:53 为止；之后的没查" in partial["sentences"]
    covered = make(Night(), at="22:20", anchors=[anchor])
    latest = query(covered, "latest_departure", origin=HBF, destination=AIRPORT, anchor_id="hn-check-in")
    assert latest["window"]["covers_target"] and latest["primary"]["boards"]["time"] == "22:53"
    assert problems(covered, second("最晚 22:53 坐 REX 7 去机场。", at="22:20")) == {}
    # a later train exists (23:23), but it misses check-in: "at the latest" is about the target, not the timetable
    assert problems(covered, second("最晚 23:23 坐 REX 7 去机场。", at="22:20")).keys() == {"only_inside_window"}


def test_the_first_train_needs_a_search_from_before_it(make):
    service = make(Night(), at="23:30")
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("04:00").isoformat(), limit=2)  # 04:22, 04:52
    bad = problems(service, second("首班 04:22。", at="23:30"))
    assert list(bad) == ["only_inside_window"] and "查询不是从它之前查起的" in bad["only_inside_window"][0]
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("00:40").isoformat(), limit=2)  # 00:53, 04:22
    assert problems(service, second("首班 04:22（前一班 00:53）。", at="23:30")) == {}


# --- 2. "direct" needs a connection with one ride; summaries list every ride ---------------------------------------------

def test_a_change_is_not_summarized_as_the_first_line_to_the_last_stop(make):
    service = make(Bus13A(), at="21:52")
    found = query(service, "transit", origin=STREET, destination="48.2085,16.3731", when=local("21:52").isoformat(), limit=1)
    summary = found["summary"]
    # A16: a tram's number is a route (“1 路”)
    assert "22:00 13A Wien Leibenfrostgasse → Wien Kolschitzkygasse 22:05，换 22:07 1 路 Wien Kolschitzkygasse → Wien Stephansplatz 22:13" in summary
    assert "13A Wien Leibenfrostgasse → Wien Stephansplatz" not in summary
    told = found["sentences"][0]
    assert told == ("21:55 出门，换 1 次车：22:00 13A Wien Leibenfrostgasse → Wien Kolschitzkygasse 22:05，换 22:07 1 "
                    "Wien Kolschitzkygasse → Wien Stephansplatz 22:13；22:13 到")
    # A9: “按时刻表，不是实时” once per result (note), not on every sentence
    assert found["note"] == "以上交通时刻按时刻表，不是实时。"  # A11: no writing note for the host in the user's sentence
    # what the timetable cannot say is apart, for the end of the answer (D-61), not on each transit line
    assert not any("再看一眼" in sentence or "请在" in sentence for sentence in found["sentences"])
    how = found["how_to_check"]
    assert (how["app"], how["search"], how["clock"], how["what"]) == ("ÖBB App", "Wien Leibenfrostgasse → Wien Stephansplatz", "22:00",
                                                                      ["real_time", "platform", "disruption"])
    assert how["text"].startswith("实时晚点、站台和临时停运时刻表里没有，出发前在 ÖBB App 查。搜 Wien Leibenfrostgasse → Wien Stephansplatz，出发时间填 22:00。")
    assert how["steps"] in how["text"] and how["link"]["url"].startswith("https://")


def test_direct_needs_a_connection_with_one_ride(make):
    service = make(Bus13A(), at="21:52")
    query(service, "transit", origin=STREET, destination="48.2085,16.3731", when=local("21:52").isoformat(), limit=1)
    bad = problems(service, second("22:00 坐 13A 直达 Stephansplatz。", at="21:52"))
    assert list(bad) == ["direct_needs_one_ride"]
    assert "要换 1 次车：22:00 13A Wien Leibenfrostgasse → Wien Kolschitzkygasse，换 22:07 1 Wien Kolschitzkygasse → Wien Stephansplatz" in bad["direct_needs_one_ride"][0]
    assert list(problems(service, second("坐 13A 直达，不用换车。", at="21:52"))) == ["direct_needs_one_ride"]  # by its line
    query(service, "transit", origin=STREET, destination="48.1850,16.3760", when=local("21:52").isoformat(), limit=1)  # Hauptbahnhof
    assert problems(service, second("22:00 坐 13A 直达 Hauptbahnhof。", at="21:52")) == {}
    assert list(problems(service, second("21:40 坐 U4 直达 Karlsplatz。", at="21:52"))) == ["direct_needs_one_ride"]  # nothing found
    assert problems(service, second("网上说法：U4 直达 Karlsplatz（Wiener Linien 网站）。", at="21:52")) == {}


# --- 3. the server's own sentences pass the check -------------------------------------------------------------------------

@pytest.mark.parametrize("trains, at", [(NIGHT, "22:20"), (("22:53", "23:03", "23:13", "23:40"), "22:20"), (("22:53",), "22:20")])
def test_the_servers_sentences_pass_check_answer(make, trains, at):
    anchor = dict(id="hn-check-in", time="23:30", text="Check-in closes", subject=["p1"], buffer_minutes=10, consequence="Missed flight.",
                  alternative="Next flight.", deadline="plan")
    limit = dict(id="hn-gate", time="23:50", text="Gate closes", subject=["p1"], consequence="Missed flight.", alternative="Next flight.",
                 deadline="external")
    service = make(Night(trains), at=at, anchors=[anchor, limit])
    latest = query(service, "latest_departure", origin=STREET, destination=AIRPORT, anchor_id="hn-check-in")
    transit = query(service, "transit", origin=STREET, destination=AIRPORT, when=local("22:30").isoformat(), limit=3)
    told = latest["sentences"] + transit["sentences"]
    # A9: the timetable note comes once per result (note); a sentence carries a basis only where a ride is real time
    assert not any("按时刻表，不是实时" in sentence for sentence in told if "出门" in sentence)
    assert latest["note"] and transit["note"] and "按时刻表，不是实时" in latest["note"]
    told += [found["how_to_check"]["text"] for found in (latest, transit) if found.get("how_to_check")]  # the answer's last section
    assert problems(service, second(*told, at=at)) == {}, told
    assert problems(service, second("；".join(told), at=at)) == {}, "one line holding them all reads the same"


def test_latest_departure_sentences_say_the_first_choice_what_follows_and_the_last(make):
    anchor = dict(id="hn-check-in", time="23:30", text="Check-in closes", subject=["p1"], consequence="Missed flight.",
                  alternative="Next flight.", deadline="external")
    service = make(Night(), at="22:20", anchors=[anchor])
    told = query(service, "latest_departure", origin=STREET, destination=AIRPORT, anchor_id="hn-check-in")["sentences"]
    assert told[0] == "要 23:30 前到，最晚 22:43 出门，不用换车：22:53 REX 7 Wien Hauptbahnhof → Flughafen Wien Bahnhof 23:15；23:15 到"
    assert told[1] == "误了 22:53 那班，之后没有赶得上 23:30 的班次"
    assert told[2].startswith("赶得上 23:30 的最后一班是 22:53 REX 7（22:43 出门）；再下一班 23:23 开，23:45 到，过了 23:30")


# --- 4. data limits are said as such ----------------------------------------------------------------------------------------

def test_a_date_after_the_timetable_change_says_not_in_the_data_yet(make):
    service = make(Recorded("geocode_wien_hbf.json", "plan_wienhbf_badischl_1220.json"), at="22:00")
    found = query(service, "transit", origin="Wien Hbf", destination="47.7120,13.6272", when="2026-12-20T08:00:00+01:00")
    assert found["status"] == "unverified" and found["connections"] == []
    assert found["window"]["text"].startswith("2026-12-13 起换新时刻表") and "现在查不到" in found["window"]["text"]
    assert found["reason"] == found["window"]["text"] and [note["kind"] for note in found["coverage"]] == ["not_yet"]
    assert not any("没车了" in sentence or "没有车" in sentence for sentence in found["sentences"])
    assert problems(service, second(*found["sentences"], at="22:00")) == {}
    assert found["how_to_check"] is None  # the destination was coordinates: nothing to type
    named = query(service, "transit", origin="Wien Hbf", destination="ischl-station", when="2026-12-20T08:00:00+01:00")
    assert named["how_to_check"]["what"] == ["not_yet"] and named["how_to_check"]["search"] == "Wien Hbf → Bad Ischl Bahnhof"
    assert named["how_to_check"]["text"].startswith("这段日期的班次还没进数据，在 ÖBB App 查。搜 Wien Hbf → Bad Ischl Bahnhof。")
    inside = query(make(Recorded("geocode_wien_hbf.json", "plan_wienhbf_badischl_1104.json"), at="22:00"), "transit",
                   origin="Wien Hbf", destination="47.7120,13.6272", when="2026-11-04T08:00:00+01:00")
    assert inside["status"] == "sourced" and inside["coverage"] == [] and inside["connections"]


def test_a_typed_name_says_which_stop_was_searched(make):
    service = make(Recorded("geocode_bad_ischl.json", "plan_badischl_stwolfgang_1104.json"), at="09:00")
    found = query(service, "transit", origin="Bad Ischl", destination="47.7393,13.4478", when="2026-11-04T10:00:00+01:00")
    assert found["sentences"][0] == "起点按 Bad Ischl Bahnhof 查（你写的是 Bad Ischl）"
    assert found["searched"]["origin"]["how"] == "train station" and "Bad Ischl Sueß" in found["searched"]["origin"]["alternatives"]
    assert all(leg.get("headsign") != "0" for view in found["connections"] for leg in view["legs"])
    hbf = query(make(Recorded("geocode_wien_hbf.json", "plan_wienhbf_badischl_1104.json"), at="07:00"), "transit",
                origin="Wien Hbf", destination="47.7120,13.6272", when="2026-11-04T08:00:00+01:00")
    assert not any(sentence.startswith("起点按") for sentence in hbf["sentences"])  # control: the first match was right
    assert "换 2 次车" in hbf["sentences"][1] or "换 1 次车" in hbf["sentences"][0]


def test_an_area_without_its_local_buses_says_so(make):
    found = query(make(Recorded("geocode_wien_hbf.json", "plan_klagenfurt_1104.json"), at="09:00"), "transit",
                  origin="46.6155,14.3127", destination="46.6237,14.3054", when="2026-11-04T10:00:00+01:00")
    assert found["sentences"][0] == "这一带的本地巴士不在数据里，下面只有火车和步行；以当地运营方网站为准"
    assert [note["kind"] for note in found["coverage"]] == ["gap"]


def test_how_to_check_names_the_regions_timetable_app_and_only_what_is_missing(make):
    found = query(make(Night(), at="22:20"), "transit", origin=HBF, destination=AIRPORT, when=local("22:30").isoformat(), limit=1)
    assert (found["how_to_check"]["app"], found["how_to_check"]["search"]) == ("ÖBB App", "Wien Hauptbahnhof → Flughafen Wien Bahnhof")
    assert "connections" in found and all("check" not in view for view in found["connections"])
    # a ride that is real time with its platform leaves only disruptions to check
    live = Night()
    live_itinerary = live.itinerary

    def with_live(train, from_street):
        found = live_itinerary(train, from_street)
        found["legs"][-1].update(realTime=True)
        found["legs"][-1]["from"]["track"] = "1"
        return found

    live.itinerary = with_live
    found = query(make(live, at="22:20"), "transit", origin=HBF, destination=AIRPORT, when=local("22:30").isoformat(), limit=1)
    assert found["how_to_check"]["what"] == ["disruption"] and found["how_to_check"]["text"].startswith("临时停运时刻表里没有")
    assert found["sentences"][0].endswith("（实时）")
    # outside the regions with a known timetable app: the map app, searched by the destination
    assert checks.claim_word("末班车来不及了") is not None and checks.claim_word("最晚 22:00 前吃完") is None
