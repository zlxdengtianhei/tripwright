"""A11 (quality judge 6): plain errors in the server's own sentences, each with a bad sample in the replay's shape and a
legal control.

- B: “火车最后一班” names the ride that reaches the destination by rail (no tram, subway or bus), with that ride's own time
  and line (replay: “火车最后一班 00:47 18” named the tram to the station of the 00:53 REX).
- C: a hard node done on a phone (`where: online`, an online check-in closing) is no time to be at the airport and has no
  latest departure; the rule is one row of ANCHOR_MEANINGS (replay: “要 05:45 前到，最晚 05:13 出门”).
- D: how_to_check names the connection the first line recommends, not the night's last bus (replay: the link opened
  03:03 or 03:09 while the answer took the 00:53 REX).
- Places: a stored place is looked up by its stored point, never by its name; a name the trip does not store that
  resolves far from every place of the trip is refused (replay: a climbing gym's name became Hannover Ahlem).
- A failed or timed-out lookup writes no stored “末班、只剩”, nor does the first part; a node's time that no source
  checked is quoted “（存档，没核）” (replay: “07:05 证件柜台关” as a fact; the timed-out lookup answered with the plan's
  “23:53 末班”).
- No process talk or writing note in the server's sentences, and check_answer refuses both (replay: “（4 次查询，每页接着
  上一页查）”, “（交通那段说一次就够，不用每条都写）”).

Offline: the fictional MOTIS of test_a10_frame (public Vienna stop names, made-up times), a fictional gym and stops."""
from __future__ import annotations

import asyncio
import re
from copy import deepcopy

import httpx
import pytest

from test_a8_transit_facts import DAY, HBF, NIGHT, STREET, local
from test_a10_frame import DESK, NEXT_DAY, QUEUE, Evening, airport_plan, fixed, problems, query, second_part
from tripwright.checks import check_plan
from tripwright.providers.base import ProviderUnavailable
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import RAIL, QueryRouter, arrival_name
from tripwright.render.check_answer import PROCESS_NOTES, WRITING_NOTES
from tripwright.render.first_part import draft
from tripwright.service import Tripwright
from tripwright.storage import Store
from tripwright.trip import Anchor

BUSES = ("00:15", "00:45", "01:15", "01:45", "02:15", "02:45", "03:15", "03:45")  # airport buses all night
CHECKIN = dict(id="hn-checkin", time="05:45", text="网上值机截止（起飞前 2 小时）", subject=["p1"], place="airport", deadline="external")
GYM = dict(id="gym", name="北边的岩馆", name_local="Kletterhalle Nordbahn, Beispielgasse 1, 1200 Wien", kind="climbing",
           coords=[48.2315, 16.3679])
FAR = dict(type="STOP", name="Nordhausen Bahnhof", lat=51.5050, lon=10.7900, id="de-DELFI_de:16062:1", modes=["REGIONAL_RAIL"], areas=[])
NEAR = dict(type="STOP", name="Wien Hauptbahnhof", lat=48.1850, lon=16.3760, id=HBF, modes=["REGIONAL_RAIL"], areas=[])
DOWN = ProviderUnavailable("transitous", "upstream down")


class Named(Evening):
    """Evening, with stop names resolved by `hits` (text → geocode results)."""

    def __init__(self, hits: dict, **more):
        super().__init__(**more)
        self.hits = hits

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/geocode"):
            self.requests.append(request)
            return httpx.Response(200, json=self.hits.get(request.url.params["text"], []))
        return await super().handle(request)


class ToPoint(Evening):
    """Evening, answering a destination given as coordinates as MOTIS does: the plan's `to` is named END."""

    async def handle(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle(request)
        if "," not in request.url.params["toPlace"]:
            return response
        body = response.json()
        body["to"] = {"name": "END", "lat": 48.1203, "lon": 16.5636}
        return httpx.Response(200, json=body)


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream=None, *, at="21:01", today=(), tomorrow=(), places=()):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a11-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport((upstream or Evening()).handle)),
                                     min_interval=0, clock=lambda: moment)
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        made.append((service, store))
        plan = airport_plan([deepcopy(anchor) for anchor in today], [deepcopy(anchor) for anchor in tomorrow])
        plan["places"] += [dict(place, area=plan["places"][0]["area"]) for place in places]
        turn = service.turn("local", "Create the trip.", "seed")
        made_draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", made_draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


# --- B. the last train is the train ride ----------------------------------------------------------------------------------

def test_the_last_train_names_the_train_ride_not_the_tram_to_the_station(make):
    """From a street (the replay's gym): tram D to Hauptbahnhof, then REX 7; airport buses all night."""
    service = make(Evening(trains=NIGHT[:5], buses=BUSES), tomorrow=[QUEUE, DESK])
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert [leg["mode"] for leg in found["last_train"]["legs"] if not leg["walk"]] == ["tram", "regional_rail"]
    first = found["first_line"]
    assert first.startswith("今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:30 出门）")
    assert "火车最后一班 00:33 D" not in first  # the replay's shape: the tram's time and line called the train
    # A12: all night, the first line names the last train once; the next line says how to take it
    assert found["sentences"][1].startswith("这班火车怎么坐：00:30 出门，换 1 次车：00:33 D Wien Matzleinsdorfer Platz")
    assert problems(service, second_part(first, *found["sentences"][1:])) == {}
    # controls: tram, subway and bus are no rail; from the station the train is the only ride
    assert not {"tram", "subway", "bus", "coach"} & RAIL
    station = query(make(Evening(trains=NIGHT[:5], buses=BUSES), tomorrow=[QUEUE, DESK]), "latest_departure", origin="hbf",
                    destination="airport")
    assert station["first_line"].endswith("；火车最后一班 00:53 REX 7（最晚 00:53 出门）")


# --- C. an online deadline is no time to be there ------------------------------------------------------------------------

def test_an_online_deadline_is_no_time_to_be_at_the_airport(make):
    # bad sample: the replay's data (the online check-in stored as a node at the airport) is read as an arrival deadline
    onsite = query(make(tomorrow=[CHECKIN, DESK]), "latest_departure", origin=STREET, destination="airport")
    assert onsite["hard_node"]["anchor_id"] == "hn-checkin" and any("要 05:45 前到" in sentence for sentence in onsite["sentences"])
    # marked online: the next time to be there is the counter's, and the check-in has no latest departure
    service = make(tomorrow=[dict(CHECKIN, where="online"), DESK])
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert found["hard_node"]["anchor_id"] == "hn-desk" and found["hard_node"]["latest"]["target"]["time"] == "07:05"
    assert not any("05:45" in sentence for sentence in found["sentences"])
    with pytest.raises(ValueError, match="'hn-checkin' is done online .*has no latest departure"):
        query(service, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-checkin")
    turn = service.turn("local", "最晚几点出发去机场？", "q1", scenario="quick")
    assert turn["first_part"] is None  # no points: no stored answer to a named point, hence no shell
    record = next(node for node in turn["hard_nodes"] if node["id"] == "hn-checkin")
    assert record["where"] == "online" and record["latest_departure"] is None
    # the plan check asks nobody to reach the airport for it; the counter still is a place to reach
    plan = airport_plan(tomorrow=[dict(CHECKIN, where="online"), DESK])
    reach = {check["id"].split(":")[3] for check in check_plan(service.store, "local", plan)["checks"] if check["id"].startswith("anchor:")}
    assert "hn-desk" in reach and "hn-checkin" not in reach
    with pytest.raises(ValueError, match="needs no travel"):
        Anchor.model_validate(dict(CHECKIN, where="online", travel_minutes=40))


# --- D. how_to_check names the recommended connection ---------------------------------------------------------------------

def test_how_to_check_points_at_the_connection_the_first_line_recommends(make):
    found = query(make(Evening(trains=NIGHT[:5], buses=BUSES)), "latest_departure", origin=STREET, destination="airport")
    how, last = found["how_to_check"], found["last"]
    # bad sample: the night's last bus (its tram at 02:55) was the link while the first line names the 00:53 REX
    assert last["boards"]["time"] == "02:55" and "02:55" not in how["text"] and "time=02:55" not in how["link"]["url"]
    assert "搜 Wien Matzleinsdorfer Platz → Flughafen Wien Bahnhof，出发时间填 00:33" in how["text"] and "time=00:33" in how["link"]["url"]
    # all night without a train: the next connection, not the last bus
    buses = query(make(Evening(trains=(), buses=BUSES)), "latest_departure", origin=STREET, destination="airport")
    assert buses["last_train"] is None and "出发时间填 23:55" in buses["how_to_check"]["text"]
    # controls: with a night break the first line's own connection is tonight's last; a deadline keeps its first choice
    plain = query(make(Evening()), "latest_departure", origin="hbf", destination="airport")
    assert plain["first_line"].startswith("今晚最后一班：最晚 00:53 出门") and "出发时间填 00:53" in plain["how_to_check"]["text"]
    queue = query(make(tomorrow=[QUEUE, DESK]), "latest_departure", origin="hbf", destination="airport", anchor_id="hn-queue")
    assert f"出发时间填 {queue['primary']['boards']['time']}" in queue["how_to_check"]["text"]


# --- places: a stored place by its stored point; a far name is refused ------------------------------------------------------

def test_a_stored_place_is_looked_up_by_its_point_and_a_far_name_is_refused(make):
    upstream = Named({"Kletterhalle Nordbahn": [FAR], "Kletterhalle Süd": [FAR], "Hauptbahnhof Wien": [NEAR]})
    service = make(upstream, places=[GYM])
    # the replay's shape: the gym's name without its address, which the stored name carries after a comma
    found = query(service, "latest_departure", origin="Kletterhalle Nordbahn", destination="airport")
    assert found["resolved"]["origin"] == dict(said="Kletterhalle Nordbahn", place="gym", looked_up="48.2315,16.3679")
    assert not any(request.url.path.endswith("/geocode") for request in upstream.requests)
    # a name the trip does not store that lands 500 km away: refused before any route search, never used silently
    upstream.requests.clear()
    with pytest.raises(ValueError, match=r"'Kletterhalle Süd' is no place of this trip .*'Nordhausen Bahnhof' .*km from the nearest"):
        query(service, "latest_departure", origin="Kletterhalle Süd", destination="airport")
    with pytest.raises(ValueError, match="km from the nearest place of this trip"):
        query(service, "transit", origin="Kletterhalle Süd", destination="airport", when=local("21:30").isoformat())
    assert not any(request.url.path.endswith("/plan") for request in upstream.requests)
    # control: a name that resolves inside the trip's region is used as before
    near = query(service, "latest_departure", origin="Hauptbahnhof Wien", destination="airport")
    assert near["status"] == "sourced" and near["first_line"].startswith("今晚最后一班：最晚 00:53 出门")


# --- failed lookups and unchecked nodes ---------------------------------------------------------------------------------------

STORED = dict(DESK, text="证件柜台关（误了 22:53，只剩 23:53 末班）")  # a node whose own words hold a stored claim


def test_a_failed_lookup_writes_no_stored_last_train(make, monkeypatch):
    service = make(tomorrow=[QUEUE, STORED])

    async def down(*args, **kwargs):
        raise DOWN

    # the night's search works, the node's own lookup fails: the node is quoted in its own words, unchecked
    monkeypatch.setattr(QueryRouter, "_pages", down)
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert found["hard_node"]["latest"]["status"] == "unverified"
    assert found["sentences"][1:3] == ["之后要守：明早 07:05 证件柜台关（存档，没核）", "你计划留的余量：明早 05:45 前，到证件柜台排队"]
    assert not any(re.search("末班|只剩", sentence) for sentence in found["sentences"])  # was “计划里写过“…只剩 23:53 末班”…”
    # the night's search fails: one sentence says nothing was checked, and no host is told to keep the plan
    monkeypatch.setattr(QueryRouter, "_window", down)
    failed = query(service, "latest_departure", origin=STREET, destination="airport")
    assert failed["status"] == "unverified" and failed["sentences"] == [failed["first_line"]]
    assert failed["first_line"] == "这次没查到班次（数据源没回应）：还有没有车、几点走都没核，出发前在交通 App 里查"
    assert "retain the previous safe plan" not in failed["alternative"] and not re.search("末班|只剩", failed["first_line"])
    # the first part after it names no stored “末班” either (it said “计划里存的末班说法没核”)
    first = service.turn("local", "最晚几点出发去机场？", "q2", scenario="quick")["first_part"]
    assert first is None


def test_a_timed_out_lookup_says_nothing_was_checked(make, monkeypatch):
    service = make(tomorrow=[QUEUE, STORED])
    service.query_timeout = 0.05

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(QueryRouter, "query", slow)
    # the replay's CLI result: only “Confirm locally or retain the previous safe plan.”, and the plan's “23:53 末班” followed
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert found["first_line"] == "这次没查到班次（查询超时）：还有没有车、几点走都没核，出发前在交通 App 里查"
    assert "retain the previous safe plan" not in found["alternative"]
    # control: a timed-out lookup of another kind keeps its generic result
    other = query(service, "weather", place=dict(name="Wien", lat=48.2, lon=16.37), date=NEXT_DAY)
    assert "first_line" not in other and other["alternative"] == "Confirm locally or retain the previous safe plan."


def test_a_node_no_source_checked_is_quoted_as_archive():
    plan, now = airport_plan(), dict(date=DAY, time="21:01")
    node = dict(DESK, date=NEXT_DAY, passed=False, deadline="external")
    # bad sample: “07:05 证件柜台关” with `checked: null` read as a fact
    assert "明早 07:05 证件柜台关（存档，没核）" in draft("明早怎么去机场？", now, [dict(node, checked=None)], plan)["lines"]
    # controls: a node a source checked, and the plan's own target, are quoted as they are
    checked = dict(node, checked=dict(source="www.example.at", at="2026-11-04T18:00:00+00:00"))
    assert "明早 07:05 证件柜台关" in draft("明早怎么去机场？", now, [checked], plan)["lines"]
    target = dict(QUEUE, date=NEXT_DAY, passed=False, checked=None)
    assert "你计划留的余量：明早 05:45 前，到证件柜台排队" in draft("明早怎么去机场？", now, [target], plan)["lines"]


# --- no process talk or writing notes ---------------------------------------------------------------------------------------

def test_process_talk_and_writing_notes_stay_out_of_the_answer(make):
    service = make(Evening(trains=NIGHT[:5], buses=BUSES))
    found = query(service, "latest_departure", origin="hbf", destination="airport")
    window = found["window"]["text"]
    # A16: the window in the user's words, and not a line of its own when the search covered tonight
    assert window == "21:01 到 03:00 之间出发的车都在这里；03:00 之后的没查" and found["note"] == "以上交通时刻按时刻表，不是实时。"
    assert window not in found["sentences"]
    assert not any(PROCESS_NOTES.search(text) or WRITING_NOTES.search(text) for text in [*found["sentences"], found["note"]])
    # bad samples: the replay's two lines
    process = "从 21:01 到 03:00 连续查（4 次查询，每页接着上一页查），12 班；03:00 之后出发的没查"
    writing = "以上交通时刻按时刻表，不是实时（交通那段说一次就够，不用每条都写）。"
    bad = problems(service, second_part(found["first_line"], process, writing))
    assert list(bad) == ["no_process_notes"] and len(bad["no_process_notes"]) == 2
    assert bad["no_process_notes"][0].startswith("有过程话：4 次查询、每页接着上一页")
    assert bad["no_process_notes"][1].startswith("有写作说明：说一次就够、不用每条都写")
    first = service.turn("local", "最晚几点出发去机场？", "q1", scenario="quick")["first_part"]
    assert first is None
    refused = query(service, "check_answer", text=writing, part="first")["problems"]
    assert "no_process_notes" in [entry["check"] for entry in refused]
    # controls: the server's own lines pass both parts
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:], found["note"])) == {}
    assert first is None


# --- a11b: a destination given as coordinates is named in the sentence ---------------------------------------------------

def test_a_destination_given_as_coordinates_is_named_by_where_the_ride_gets_off(make):
    """Real Transitous (a11 check, 11-07 from a gym's coordinates to the airport's): “今晚不用赶：夜里一直有车到 None”."""
    service = make(ToPoint(trains=NIGHT[:5], buses=BUSES))
    found = query(service, "latest_departure", origin="hbf", destination="48.1203,16.5636")
    assert found["resolved"]["destination"]["place"] is None and not found["searched"]["destination"].get("name")
    assert found["first_line"].startswith("今晚不用赶：夜里一直有车到 Flughafen Wien Bahnhof；火车最后一班 00:53 REX 7")
    assert "None" not in " ".join(found["sentences"])
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:])) == {}
    # controls: a stored place keeps its own name; with no stop to name either, the sentence says 终点
    named = query(make(Evening(trains=NIGHT[:5], buses=BUSES)), "latest_departure", origin="hbf", destination="airport")
    assert named["first_line"].startswith("今晚不用赶：夜里一直有车到 Flughafen Wien；")
    assert arrival_name(None, "48.1203,16.5636", None, None, "zh") == "终点"
    assert arrival_name(None, "48.1203,16.5636", {"name": None}, {"alights": None}, "en") == "the destination"
