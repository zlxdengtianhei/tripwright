"""A10 (D-67): the frame of “最晚几点出发去 X” is the server's; the first part is drafted by the server.

- A hard node's meaning comes from its fields (one table, ANCHOR_MEANINGS): a node with a line (or the destination it
  rides to) and a place is that vehicle's boarding time at the place, never an arrival deadline at the lookup's
  destination; the search goes to that place, as board_at does. `by`, `board_at` or another destination beside it is
  refused with the fix. (a9b: the 22:42 RJX at the main station was read as “be at the airport by 22:42”.)
- latest_departure without anchor_id, by and board_at answers tonight's last connection: searched continuously up to
  tonight's end (A9's until paging, within the daily budget), the latest time to leave the origin, how far the search
  went, and the plan's next hard node at the destination with its own latest departure; no deadline is made up.
- turn returns `first_part` (server-written from stored data, at most 8 lines, the clock on line 1, stored last/only
  claims as archive) and `next` says to send it as it is first.
- check_answer's `frame_matches_server`: the second part's first-line “最晚/末班” names a time of this question's frame.
- Synthetic data marks every sentence and summary as fictional.

Offline: a fictional MOTIS (public Vienna stop names, made-up times). Each rule has a bad sample and a legal control."""
from __future__ import annotations

import asyncio
import sys
from copy import deepcopy
from datetime import datetime, timedelta

import httpx
import pytest

import tripwright.events as events_module
import tripwright.service as service_module
import tripwright.storage as storage_module
from tripwright.repo import snapshot as snapshot_module
from test_a8_transit_facts import AIRPORT, DAY, HBF, NIGHT, STREET, leg, local, stop, vienna_plan
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import ANCHOR_MEANINGS, QueryRouter, anchor_meaning
from tripwright.render.check_answer import check_draft
from tripwright.service import Tripwright
from tripwright.storage import Store

NEXT_DAY = "2026-11-05"
TRAM = "at-PTA-Eastern-Region-Flex-2026_at:49:9001"
# a9b, turn 1 (no personal data): the host's only text before its second part, and the second part's first line
STATUS_LINE = ("Right now I'm checking tonight's trains from the station to the airport and the latest time you can leave. "
               "The second part will follow once the check is back.")
WRONG_FRAME = "最晚 {leave} 出门，{reach} 到机场。"
REX = dict(id="hn-rex", time="22:53", text="在总站坐 REX 去机场", subject=["p1"], place="hbf", destination="airport", line="REX 7")
QUEUE = dict(id="hn-queue", time="05:45", text="到证件柜台排队", subject=["p1"], place="airport", deadline="plan")
DESK = dict(id="hn-desk", time="07:05", text="证件柜台关", subject=["p1"], place="airport", deadline="external",
            consequence="误了 22:53，只剩 23:53 末班。")


class Evening:
    """MOTIS answering a departure search with the next `numItineraries` after `time`. To Wien Hauptbahnhof from the
    street: walk 3 minutes, tram D every 10 minutes (12 minutes). To the airport: REX 7 at `trains` from Hauptbahnhof
    (from the street, the tram 20 minutes before), and coaches at `coaches` from Hauptbahnhof."""

    def __init__(self, trains=NIGHT, coaches=(), buses=(), mixed=()):
        self.trains, self.coaches = [local(clock) for clock in trains], [local(clock) for clock in coaches]
        self.buses = [local(clock) for clock in buses]  # airport buses (their own fare, not a coach) from Hauptbahnhof
        self.mixed = [local(clock) for clock in mixed]  # a train to Praterstern, then the VAB 5 bus to the airport
        first, last = local("20:00"), local("11:00") + timedelta(hours=-6)  # trams 20:00 — 05:00 the next morning
        self.trams = [first + timedelta(minutes=10 * step) for step in range(int((last - first).total_seconds() // 600) + 1)]
        self.requests: list[httpx.Request] = []

    @staticmethod
    def tram(start: datetime) -> list[dict]:
        return [leg("WALK", {"name": "START", "lat": 48.189, "lon": 16.352}, stop("Wien Matzleinsdorfer Platz", TRAM), start - timedelta(minutes=3), start),
                leg("TRAM", stop("Wien Matzleinsdorfer Platz", TRAM), stop("Wien Hauptbahnhof", HBF), start, start + timedelta(minutes=12),
                    displayName="D", headsign="Hauptbahnhof", realTime=False)]

    def to_airport(self, start: datetime, from_street: bool, coach: bool, bus: str = "FlixBus N877") -> list[dict]:
        ride = (leg("BUS", stop("Wien Hauptbahnhof", HBF), stop("Flughafen Wien Bahnhof", AIRPORT, 48.12, 16.56), start, start + timedelta(minutes=30),
                    displayName=bus, headsign="Flughafen Wien", realTime=False) if coach else
                leg("REGIONAL_RAIL", stop("Wien Hauptbahnhof", HBF), stop("Flughafen Wien Bahnhof", AIRPORT, 48.12, 16.56), start,
                    start + timedelta(minutes=22), displayName="REX 7", headsign="Flughafen Wien", realTime=False))
        return (self.tram(start - timedelta(minutes=20)) if from_street else []) + [ride]

    @staticmethod
    def train_then_bus(start: datetime) -> list[dict]:
        praterstern = "at-Railway-Current-Reference-Data-2026_Pat:49:1500"
        return [leg("SUBURBAN", stop("Wien Hauptbahnhof", HBF), stop("Wien Praterstern", praterstern), start, start + timedelta(minutes=8),
                    displayName="S1", headsign="Floridsdorf", realTime=False),
                leg("BUS", stop("Wien Praterstern", praterstern), stop("Flughafen Wien Bahnhof", AIRPORT, 48.12, 16.56), start + timedelta(minutes=12),
                    start + timedelta(minutes=40), displayName="VAB 5", headsign="Flughafen Wien", realTime=False)]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        from_street, moment = "," in params["fromPlace"], datetime.fromisoformat(params["time"])
        if params["toPlace"] == HBF:
            options = [self.tram(start) for start in self.trams]
        else:
            options = [self.to_airport(start, from_street, False) for start in self.trains] + \
                [self.to_airport(start, from_street, True) for start in self.coaches] + \
                [self.to_airport(start, from_street, True, "VAB 1") for start in self.buses] + \
                [self.train_then_bus(start) for start in self.mixed]
        starts = sorted((legs for legs in options if datetime.fromisoformat(legs[0]["startTime"].replace("Z", "+00:00")) >= moment),
                        key=lambda legs: legs[0]["startTime"])[:int(params["numItineraries"])]
        body = {"from": {"name": "START", "lat": 48.189, "lon": 16.352} if from_street else stop("Wien Hauptbahnhof", HBF),
                "to": stop("Wien Hauptbahnhof", HBF) if params["toPlace"] == HBF else stop("Flughafen Wien Bahnhof", AIRPORT, 48.12, 16.56),
                "direct": [], "itineraries": [{"startTime": legs[0]["startTime"], "endTime": legs[-1]["endTime"], "duration": 0, "legs": legs}
                                              for legs in starts]}
        return httpx.Response(200, json=body)


def airport_plan(today=(), tomorrow=()) -> dict:
    plan = vienna_plan(today)
    area = plan["places"][0]["area"]
    plan["places"] += [dict(id="airport", name="维也纳机场", name_local="Flughafen Wien", area=area, kind="airport", coords=[48.1203, 16.5636],
                            transit_id=AIRPORT),
                       dict(id="hbf", name="维也纳总站", name_local="Wien Hauptbahnhof", area=area, kind="station", coords=[48.1850, 16.3760],
                            transit_id=HBF)]
    plan["days"].append(dict(date=NEXT_DAY, title="Flight", area=plan["days"][0]["area"], anchors=list(tomorrow),
                             plans=[dict(name="Recommended", tracks=[dict(subject=["p1", "p2", "p3"], blocks=[])])]))
    plan["end"] = NEXT_DAY
    return plan


def fixed(monkeypatch, moment: datetime):
    for module in (service_module, storage_module, events_module, snapshot_module):
        monkeypatch.setattr(module, "now", lambda: moment.isoformat())


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream=None, *, at="21:01", today=(), tomorrow=(), budget=None, mode=None):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a10-{len(made)}.sqlite3")
        if mode == "synthetic":
            router = QueryRouter(store, mode="synthetic")
        else:
            transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport((upstream or Evening()).handle)), min_interval=0,
                                         clock=lambda: moment, **({"routing_per_day": budget} if budget else {}))
            router = QueryRouter(store, transit=transit)
        service = Tripwright(store, query_router=router)
        made.append((service, store))
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0,
                                plan=airport_plan([deepcopy(anchor) for anchor in today], [deepcopy(anchor) for anchor in tomorrow]),
                                idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def query(service, kind, **arguments):
    return asyncio.run(service.query("local", kind, arguments))


def second_part(first: str, *rest: str, at: str = "21:01") -> str:
    return "\n".join([f"# 第二段 · 核过了（{at}）", first, "和第一段不同：最晚已查到。", *rest, "## 完整安排", "21:10 出门 · 去 Wien Hbf"])


def problems(service, text: str) -> dict[str, list[str]]:
    found = {}
    for entry in query(service, "check_answer", text=text, part="second")["problems"]:
        found.setdefault(entry["check"], []).append(entry["why"] + " | " + entry["fix"])
    return found


# --- 1. a hard node's meaning comes from its fields ----------------------------------------------------------------------

def test_the_rule_table_reads_fields_never_ids():
    assert [meaning for meaning, _, _ in ANCHOR_MEANINGS] == ["online_deadline", "boarding_time", "arrival_deadline"]  # A11 added the first
    assert anchor_meaning(dict(id="x", line="RJX", place="hbf")) == "boarding_time"
    assert anchor_meaning(dict(id="x", destination="airport", place="hbf")) == "boarding_time"
    assert anchor_meaning(dict(id="x", line="RJX")) == "arrival_deadline"  # a line without a place to board at
    assert anchor_meaning(dict(id="x", place="airport", deadline="external")) == "arrival_deadline"
    # the same fields under the a9b anchor's kind of ID, or any other, mean the same
    assert anchor_meaning(dict(REX, id="a1031-railjet")) == anchor_meaning(dict(REX, id="zz")) == "boarding_time"
    assert anchor_meaning(dict(QUEUE, id="a1031-railjet")) == "arrival_deadline"


def test_a_node_with_a_line_and_a_place_is_caught_there_not_a_deadline_at_the_destination(make):
    service = make(today=[REX], tomorrow=[QUEUE, DESK])
    # a9b's call: the boarding node with the airport as destination
    found = query(service, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-rex")
    target = found["target"]
    assert (target["meaning"], target["deadline"], target["board_place"], target["line"]) == ("boarding_time", "board", "Wien Hauptbahnhof", "REX 7")
    assert found["resolved"]["destination"] == dict(said="airport", place="hbf", looked_up=HBF, boarding=True)
    assert found["sentences"][0] == ("赶 22:53 在 Wien Hauptbahnhof 开的 REX 7，最晚 22:37 出门，不用换车：22:40 D Wien Matzleinsdorfer Platz "
                                     "→ Wien Hauptbahnhof 22:52；22:52 到")
    assert not any(sentence.startswith("要 22:53 前到") for sentence in found["sentences"])  # the airport frame of a9b
    # control: a node with no vehicle is a time to be at its place
    queue = query(service, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-queue")
    assert queue["target"]["meaning"] == "arrival_deadline" and queue["target"]["limit"] == "07:05"


def test_a_boarding_node_with_a_deadline_or_another_destination_is_refused_with_the_fix(make):
    service = make(today=[REX], tomorrow=[QUEUE, DESK])
    with pytest.raises(ValueError, match=r"is a boarding time: REX 7 leaves Wien Hauptbahnhof at 22:53 toward Flughafen Wien.*arguments\.by"
                                         r".*latest_departure with origin and destination only"):
        query(service, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-rex", by="22:53", date=DAY)
    with pytest.raises(ValueError, match="is neither Wien Hauptbahnhof nor Flughafen Wien"):
        query(service, "latest_departure", origin=STREET, destination="oslo-art", anchor_id="hn-rex")
    with pytest.raises(ValueError, match="is a time to be at Flughafen Wien by 05:45 .*arguments.board_at"):
        query(service, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-queue", board_at="05:00", date=NEXT_DAY)
    # control: the boarding stop itself as destination
    assert query(service, "latest_departure", origin=STREET, destination="hbf", anchor_id="hn-rex")["status"] == "sourced"


# --- 2. tonight's last connection: the server's frame ----------------------------------------------------------------------

def test_without_a_deadline_the_answer_is_tonights_last_connection_and_the_next_hard_node(make):
    service = make(today=[REX], tomorrow=[QUEUE, DESK])
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert found["target"]["meaning"] == "last_connection" and found["target"]["until"] == "03:00"
    window = found["window"]
    assert window["covered"] and window["continuous"] and window["from"][11:16] == "21:01" and window["until"].startswith(f"{NEXT_DAY}T03:00")
    first = found["first_line"]
    assert first == ("今晚最后一班：最晚 00:30 出门，换 1 次车：00:33 D Wien Matzleinsdorfer Platz → Wien Hauptbahnhof 00:45，换 00:53 REX 7 "
                     "Wien Hauptbahnhof → Flughafen Wien Bahnhof 01:15；01:15 到；下一班 明早 04:02 D 开")
    assert found["sentences"][:2] == [first, window["text"]] and "连续查全" in window["text"]
    node = found["hard_node"]
    assert (node["anchor_id"], node["deadline"], node["external"]["anchor_id"], node["external"]["time"]) == ("hn-queue", "plan", "hn-desk", "07:05")
    # a12b: the plan's own train, found among this lookup's departures
    assert found["sentences"][2] == "计划里的 22:53 REX 7 在这次的查询结果里（22:30 出门）"
    assert found["sentences"][3] == "之后要守的时间：你计划明早 05:45 前到（留的余量）：到证件柜台排队；明早 07:05 证件柜台关（存档，没核）"
    # A14: the plan's margin (05:45) gets no latest departure of its own; this line is for the counter behind it (07:05).
    # The fixture has no train after 04:52: its own search does not reach 07:05, so it says “可以坐”, not “最晚”
    assert node["latest"]["leave"] == "04:29" and found["sentences"][4].startswith("明早直接从 起点 去：要 07:05 前到，可以坐：04:29 出门")
    assert found["sentences"][4].endswith("（今晚就到 Flughafen Wien 的话用不上）")
    assert "按时刻表，不是实时" in found["note"] and found["status"] == "sourced"
    # every sentence the server wrote passes check_answer, its first line as the second part's first line
    assert problems(service, second_part(first, *found["sentences"][1:])) == {}


def test_a_first_line_in_another_frame_is_refused_with_the_servers_line(make):
    service = make(today=[REX], tomorrow=[QUEUE, DESK])
    boarding = query(service, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-rex")
    last = query(service, "latest_departure", origin=STREET, destination="airport")
    # a9b's first line: the boarding node's time read as the airport deadline (here leave 22:37 for the 22:53)
    leave = boarding["primary"]["leave"]
    wrong = problems(service, second_part(WRONG_FRAME.format(leave=leave, reach="23:15")))
    assert list(wrong)[0] == "frame_matches_server"
    assert f"{leave}，这是赶某班车的时刻；这一问服务端定的框是今晚最后一班" in wrong["frame_matches_server"][0]
    assert wrong["frame_matches_server"][0].endswith("首行照抄服务端的句子：" + last["first_line"])
    # a time in no frame at all
    assert "对不上" in problems(service, second_part("最晚 21:53 出门，22:41 到机场。"))["frame_matches_server"][0]
    # controls: the server's line; and a deadline frame alone (no last-connection lookup) holds its own times
    assert problems(service, second_part(last["first_line"])) == {}
    alone = make(today=[REX], tomorrow=[QUEUE, DESK])
    query(alone, "latest_departure", origin=STREET, destination="airport", anchor_id="hn-rex")
    assert "frame_matches_server" not in problems(alone, second_part(boarding["sentences"][0]))


def test_without_a_hard_node_no_deadline_is_made_up(make):
    service = make(today=[], tomorrow=[])
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert found["hard_node"] is None
    assert found["sentences"][-1] == "计划里没有到 Flughafen Wien 之后要守的时间"
    assert not any("前到" in sentence or "要守的时间：" in sentence for sentence in found["sentences"])
    # control: the same lookup with the node stored names it
    named = query(make(tomorrow=[QUEUE]), "latest_departure", origin=STREET, destination="airport")
    assert named["hard_node"]["anchor_id"] == "hn-queue" and named["sentences"][2].startswith("之后要守的时间：你计划明早 05:45 前")


def test_a_last_coach_comes_with_the_last_one_without_an_extra_ticket(make):
    service = make(Evening(coaches=("01:25",)))
    found = query(service, "latest_departure", origin="hbf", destination="airport")
    assert found["first_line"].startswith("今晚最后一班：最晚 01:25 出门，不用换车：01:25 FlixBus N877")
    assert "（长途大巴 FlixBus N877 要单独买票）" in found["first_line"]
    assert found["sentences"][1].startswith("不用另买票的最后一班：最晚 00:53 出门，不用换车：00:53 REX 7")
    assert found["last_without_extra_ticket"]["ticket"]["need"] == "unknown"
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:])) == {}
    # control: without the coach the REX is the last one, and there is no second line
    plain = query(make(Evening()), "latest_departure", origin="hbf", destination="airport")
    assert plain["first_line"].startswith("今晚最后一班：最晚 00:53 出门") and not plain["sentences"][1].startswith("不用另买票")


def test_buses_all_night_are_said_and_the_last_train_is_named(make):
    """Real Transitous (11-07, Wien Hbf → airport): REX until 00:53, airport buses all night. No “末班” for the night;
    the first line says there is no hurry, then the last train and the next time to keep (A10 replay)."""
    buses = ("00:15", "00:45", "01:15", "01:45", "02:15", "02:45", "03:15", "03:45")
    # 02:20: S1 to Praterstern, then the VAB 5 bus — one ride is rail, the one that reaches the airport is not
    service = make(Evening(trains=NIGHT[:5], buses=buses, mixed=("02:20",)), tomorrow=[QUEUE, DESK])
    found = query(service, "latest_departure", origin="hbf", destination="airport")
    assert found["first_line"] == ("今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:53 出门）；"
                                   "之后要守的时间：你计划明早 05:45 前到（留的余量）：到证件柜台排队")
    assert not found["first_line"].startswith("03:00 前最后开的是")  # the replay's first line
    # A12: the first line named the last train and the next time to keep; the lines below do not say them again
    assert found["sentences"][1] == ("这班火车怎么坐：00:53 出门，不用换车：00:53 REX 7 Wien Hauptbahnhof → Flughafen Wien "
                                     "Bahnhof 01:15；01:15 到")
    assert sum("你计划明早 05:45 前" in sentence for sentence in found["sentences"]) == 1
    assert "另外要守：明早 07:05 证件柜台关（存档，没核）" in found["sentences"]
    assert found["sentences"][2].startswith("03:00 前最后开的是：02:45 出门") and "也有车" in found["sentences"][2]
    # the 02:20 train-then-bus is no “last train” (the replay's mislabel); the 00:53 REX is
    assert found["tonight_ends_after_last"] is False and found["last_train"]["boards"]["time"] == "00:53"
    assert not any("火车最后一班" in sentence and "VAB 5" in sentence for sentence in found["sentences"])
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:])) == {}
    assert problems(service, second_part(found["sentences"][1])) == {}  # the last train as the first line: in the frame
    # control: with no night buses the night has a break, and the first line is tonight's last train
    plain = query(make(Evening(trains=NIGHT), tomorrow=[QUEUE, DESK]), "latest_departure", origin="hbf", destination="airport")
    assert plain["first_line"].startswith("今晚最后一班：最晚 00:53 出门") and plain["last_train"] is None
    # bad sample: the night's last bus called “末班” (buses run on after 03:00)
    bad = problems(service, second_part("今晚末班 02:45 VAB 1（02:45 出门），03:15 到机场。"))
    assert "only_inside_window" in bad and "工具查到 02:45 之后还有" in bad["only_inside_window"][0]


def test_a_search_that_stops_before_tonights_end_never_says_the_last_one(make):
    trains = tuple(f"21:{minute:02d}" for minute in range(10, 60, 5)) + NIGHT
    found = query(make(Evening(trains), budget=1), "latest_departure", origin="hbf", destination="airport")
    assert not found["window"]["covered"] and "额度" in found["window"]["text"]
    assert found["first_line"].startswith("查到的班次里最后开的是：")
    assert not any(word in sentence for sentence in found["sentences"] for word in ("最后一班", "末班", "最晚"))
    assert found["reason"] == found["window"]["text"]
    # control: with the budget the same night is covered and named
    assert query(make(Evening(trains)), "latest_departure", origin="hbf", destination="airport")["first_line"].startswith("今晚最后一班")


# --- 3. turn drafts the first part --------------------------------------------------------------------------------------------

def test_turn_drafts_the_first_part_from_stored_data_and_next_says_send_it_first(make):
    service = make(today=[REX], tomorrow=[QUEUE, DESK])
    turn = service.turn("local", "今晚到明天 最晚几点出发机场 怎么走", "q1", scenario="quick")
    first = turn["first_part"]
    assert first["asks_latest"] and first["lines"] == [
        "按现在 21:01 算：真正最晚要查，第二段给。",
        "计划里写过：22:53 在 Wien Hauptbahnhof 坐 REX 7 去 Flughafen Wien（存档，没核）",
        "你计划明早 05:45 前到（留的余量）：到证件柜台排队",
        "明早 07:05 证件柜台关（存档，没核）",  # A11: no source checked the operator's time
        "计划里存的班次说法没核，第二段给。",
        "没核：今晚最后一班、最晚几点出门和要守的时间，第二段核完再发。"]
    assert first["text"] == "\n".join(first["lines"]) and len(first["lines"]) <= 8
    # D-64 two (A10 replay): the stored “只剩 23:53 末班” sentence and its “这条作废” never enter the first part
    assert "23:53" not in first["text"] and "作废" not in first["text"] and "只剩" not in first["text"]
    # A12: without the question's points, next first asks for them; then the A10 order
    assert turn["next"].startswith("这一问没给 points：first_part 只按存的计划写") and (
        "先把 first_part.text 原样发给用户（只可改称呼和语言），再去查。用户只看到第一段和第二段两条消息：第一段之前、两段之间不发任何状态句。"
        in turn["next"])
    assert "latest_departure 只给 origin 和 destination" in turn["next"] and "第二段过了 check_answer 才发，只发一次" in turn["next"]
    # the draft passes the first part's checks; a9b's English status line does not
    assert query(service, "check_answer", text=first["text"], part="first")["ok"]
    bad = query(service, "check_answer", text=STATUS_LINE, part="first")
    assert not bad["ok"] and [entry["check"] for entry in bad["problems"]] == ["clock_in_first_part"]


def test_a_question_that_is_not_about_the_latest_gets_the_plan_draft(make):
    service = make(today=[REX], tomorrow=[QUEUE, DESK])
    turn = service.turn("local", "我饿了，想吃点东西", "q2", scenario="quick")
    first = turn["first_part"]
    assert not first["asks_latest"] and first["lines"][0] == "按现在 21:01 算：先按存的计划说，要核的第二段给。"
    assert first["lines"][-1] == "没核：上面的时刻是存的计划，第二段核完再发。"
    assert "问最晚、末班" not in turn["next"] and "。先把 first_part.text 原样发给用户" in turn["next"]
    assert check_draft(first["text"], "first", 21 * 60 + 1) == []


def test_first_part_and_next_reach_a_host_over_mcp(tmp_path):
    """Harness neutral (R-62, R-69): a host that reads no skill and runs no hook gets the draft in turn's result."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from test_b3d_mcp import environment
    from test_m1_mcp import call

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(tmp_path / "a10.sqlite3"),
                                                                         "serve", "--stdio"], env=environment())
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return await call(session, "turn", dict(text="最晚几点出发去机场？", request_id="a10-1", scenario="quick"))

    turn = asyncio.run(scenario())
    assert turn["first_part"]["lines"][0].endswith("算：这趟还没有存的计划，第二段给。") and turn["first_part"]["asks_latest"]
    assert turn["next"].startswith("先把 first_part.text 原样发给用户")


# --- 5. synthetic data says so ----------------------------------------------------------------------------------------------

def test_synthetic_data_marks_every_sentence_and_summary(make):
    service = make(mode="synthetic", tomorrow=[QUEUE, DESK])
    mark = "（虚构演示数据，不能用于出行）"
    for kind, arguments in (("latest_departure", dict(origin="hbf", destination="airport", anchor_id="hn-queue")),
                            ("latest_departure", dict(origin="hbf", destination="airport")),
                            ("transit", dict(origin="hbf", destination="airport", when=local("21:00").isoformat()))):
        found = query(service, kind, **arguments)
        assert found["sentences"] and all(sentence.startswith(mark) for sentence in found["sentences"]), (kind, found["sentences"])
        assert found["summary"].startswith(mark), kind
    # control: timetable data is not marked
    live = query(make(today=[REX]), "transit", origin="hbf", destination="airport", when=local("21:00").isoformat())
    assert not any(mark in sentence for sentence in live["sentences"]) and mark not in live["summary"]


def test_an_english_first_part_keeps_a_clock_the_first_part_check_reads():
    """a10b: “As of 21:01: …” put a colon after the clock, which the clock pattern does not read as a time."""
    from tripwright.render import first_part

    anchors = [dict(QUEUE, date=NEXT_DAY, passed=False), dict(DESK, date=NEXT_DAY, passed=False)]
    plan = airport_plan()
    draft = first_part.draft("What is the latest I can leave for the airport?", dict(date=DAY, time="21:01"), anchors, plan, "en")
    assert draft["lines"][0] == "As of 21:01, the real latest departure needs checking; part 2 follows."
    assert check_draft(draft["text"], "first", 21 * 60 + 1) == []
    bad = draft["text"].replace("As of 21:01,", "As of 21:01:")
    assert [entry["check"] for entry in check_draft(bad, "first", 21 * 60 + 1)] == ["clock_in_first_part"]



def test_a_node_whose_text_holds_a_stored_claim_keeps_only_its_own_words_in_the_first_part(make):
    """The claim inside a node's own text (packs.archived_claims rewrites it in place) is left out too."""
    desk = dict(DESK, text="证件柜台关（误了 22:53，只剩 23:53 末班）", consequence="误机。")
    first = make(tomorrow=[QUEUE, desk]).turn("local", "最晚几点出发去机场？", "q3", scenario="quick")["first_part"]
    assert "明早 07:05 证件柜台关（存档，没核）" in first["lines"] and "计划里存的班次说法没核，第二段给。" in first["lines"]
    assert "23:53" not in first["text"]
    # control: nothing stored about the last train, no such line
    plain = make(tomorrow=[QUEUE, dict(DESK, consequence="误机。")]).turn("local", "最晚几点出发去机场？", "q3", scenario="quick")["first_part"]
    assert "计划里存的班次说法没核，第二段给。" not in plain["lines"]


# --- 5. a deadline sentence: what leaves later but arrives after the deadline is no counterexample -------------------------

def test_a_later_departure_that_misses_the_deadline_does_not_refute_at_the_latest(make):
    """A10 replay turn1 L25, turn2 L17: “要 05:45 前到，最晚 04:56 出门” was refused for a 05:39 train arriving after 05:45."""
    service = make(Evening(trains=NIGHT + ("05:30", "06:10")))  # 06:10: the window reaches 06:00
    query(service, "transit", origin="hbf", destination="airport", when=local("04:00").isoformat(), until="06:00")
    ride = "不用换车：04:52 REX 7 Wien Hauptbahnhof → Flughafen Wien Bahnhof 05:14；05:14 到"
    assert problems(service, second_part(f"要 05:45 前到，最晚 04:52 出门，{ride}")) == {}  # 05:30 arrives 05:52: too late
    # control: with a deadline the 05:30 makes, it does refute “at the latest 04:52”
    bad = problems(service, second_part(f"要 06:00 前到，最晚 04:52 出门，{ride}"))
    assert "only_inside_window" in bad and "04:52 之后还有 05:30 REX 7" in bad["only_inside_window"][0]


# --- a10d: a denied claim is no claim; 最晚/末班 names the time next to it (A10 replay turn 1 L4, L32) ---------------------

def test_a_denied_last_train_is_not_a_last_train_claim(make):
    service = make(Evening())
    query(service, "transit", origin="hbf", destination="airport", when=local("21:00").isoformat(), until="01:00")
    # L4's shape: “23:53 不是末班” was read as the claim “末班 23:53” and refuted with 04:22
    assert problems(service, second_part("和第一段不同：23:53 不是末班，之后还有 00:23 REX 7 和 00:53 REX 7。")) == {}
    # control: the claim itself is refuted
    bad = problems(service, second_part("23:53 是末班。"))
    assert "only_inside_window" in bad and "23:53 之后还有 00:23 REX 7" in bad["only_inside_window"][0]


def test_at_the_latest_names_the_time_and_line_next_to_it(make):
    service = make(Evening(buses=("01:15",)))
    query(service, "transit", origin="hbf", destination="airport", when=local("21:00").isoformat(), until="01:00")
    # L32's shape: several times; “最晚” was read as the earliest (22:53) and refuted with 23:23, 23:53 …
    assert problems(service, second_part("22:53 坐 REX 7，23:15 到机场（误了坐 23:23 REX 7，最晚 00:53 REX 7）。")) == {}
    # controls: the time next to it is refuted by a later REX the same night; without the line, by the 01:15 bus;
    # the 04:22 REX after the night break is the next day's, so it never refutes 00:53
    later = problems(service, second_part("22:53 坐 REX 7（误了坐 23:23 REX 7，最晚 00:23 REX 7）。"))
    assert "00:23 之后还有 00:53 REX 7" in later["only_inside_window"][0]
    bus = problems(service, second_part("22:53 坐 REX 7（误了坐 23:23 REX 7，最晚 00:53 出门）。"))
    assert "00:53 之后还有 01:15 VAB 1" in bus["only_inside_window"][0] and "04:22" not in bus["only_inside_window"][0]


def test_the_mcp_tools_say_what_hosts_need_without_a_skill(make):
    """Harness neutral: the tool descriptions carry first_part, the last-connection frame and (a10d) the sync view's
    blocked write and local storage, for hosts that read no skill."""
    from tripwright.adapters.mcp import create_server

    tools = {tool.name: tool.description for tool in asyncio.run(create_server(make()).list_tools())}
    assert "first_part {text, lines, asks_latest, points, at}" in tools["turn"]  # A12: with the question's points
    assert "latest_departure: with origin and destination only, it answers tonight's last connection" in tools["query"]
    assert ("sync shows the canonical head, unsynced writes and how old the head is. view=sync also carries blocked "
            "{kind, since, hint} while a write cannot be pushed or committed, and storage {kind: local, note} when there is "
            "no remote repository.") in tools["state"]
    assert "blocked" not in tools["explain"]  # control: only the state tool says it
