"""A9 (D-64): "最晚、末班、还有几班"由服务端连续查清；存档的否定句不进回答。

- transit 的 `until`：从 `when` 到 `until` 的全部班次由服务端一页页接（每页一次 /plan，守每天 720 次；额度到限就停，
  写明查到哪），返回连续覆盖（window.continuous/covered/until）、每班一句和一句总结（到 until 为止，再下一班另写）。
- latest_departure 分清到达期限（by）与上车时刻（board_at）：target.meaning 写明；把中途站的上车时刻当 by 传，
  报错并给改法。
- check_answer 的两个新检查：别指望/别靠/都误了（denied_needs_coverage）与 还有 N 班（count_needs_continuous）——
  句中的时刻要落在本问某次查询的 [from, to] 里（两次查询的空档不算），计数句的区间要连续查过且查到的班次写全；
  “这次没查到”不豁免结论句，存档/网上说法/没核 豁免。
- turn 的硬节点与 pack 里，存档的“末班、只剩、最晚”标成“计划里写过…（存档，写于…，没核）”；这轮查到更晚的同方向
  车次就标作废；turn.next 提醒第一段不照用。

Offline：假上游（MOTIS 形状，公开的维也纳站名与编造的时刻）。每条都有坏样本与合法对照。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

import tripwright.events as events_module
import tripwright.service as service_module
import tripwright.storage as storage_module
from test_a8_transit_facts import NIGHT, Night, local, second, vienna_plan
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

VIENNA = ZoneInfo("Europe/Vienna")
DAY = "2026-11-04"
HBF = "at-Railway-Current-Reference-Data-2026_Pat:49:1349"
AIRPORT = "at-Railway-Current-Reference-Data-2026_Pat:43:4708"
STREET = "48.1890,16.3520"  # a street in Margareten (coordinates: no stop lookup)

# judgment 5, r2 第 1 问（已去个人信息）：存档的“末班”照抄，这次没查到就“别靠”
DENIED_BAD = "计划里存的 23:53 末班 REX 这次没查到，别靠它们。"
# judgment 5, off 第 1 问：23:13—23:30 的空档里漏了 23:23
COUNT_BAD = "误了 22:53 之后火车还有 23:53、00:23、00:53 三班。"
COUNT_GOOD = "误了 22:53 之后还有 23:23、23:53、00:23、00:53 四班（查到 01:00 为止）。"


def fixed(monkeypatch, moment: datetime):
    for module in (service_module, storage_module, events_module):
        monkeypatch.setattr(module, "now", lambda: moment.isoformat())


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream, *, at="21:01", anchors=(), budget=None):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a9-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)), min_interval=0,
                                     clock=lambda: moment, **({"routing_per_day": budget} if budget else {}))
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        made.append((service, store))
        turn = service.turn("local", "Create the trip.", "seed")
        plan = vienna_plan(anchors)
        plan["places"].append(dict(id="airport", name="维也纳机场", name_local="Flughafen Wien", area=plan["places"][0]["area"],
                                   kind="airport", coords=[48.1203, 16.5636]))
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def query(service, kind, **arguments):
    return asyncio.run(service.query("local", kind, arguments))


def problems(service, text) -> dict[str, list[str]]:
    found = {}
    for entry in query(service, "check_answer", text=text, part="second")["problems"]:
        found.setdefault(entry["check"], []).append(entry["why"] + " | " + entry["fix"])
    return found


# --- 1. until：连续覆盖的查法 ------------------------------------------------------------------------------------------

def test_until_pages_until_the_window_is_covered_and_tells_every_departure(make):
    service = make(Night(), at="21:01")
    found = query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="01:00")
    window = found["window"]
    assert window["covered"] and window["continuous"]  # 01:00 早于 21:00，until 滚到次日
    assert window["until"].startswith("2026-11-05T01:00") and window["from"][11:16] == "21:00"
    assert window["count"] == len(NIGHT)  # 一页装下全部；04:22、04:52 是“过了 01:00”的证据
    boards = [(view["boards"]["time"], view["boards"]["line"]) for view in found["connections"]]
    assert boards == [("22:53", "REX 7"), ("23:23", "REX 7"), ("23:53", "REX 7"), ("00:23", "REX 7"), ("00:53", "REX 7"),
                      ("04:22", "REX 7"), ("04:52", "REX 7")]
    # 每班一句，不带“按时刻表”；总结到 01:00 为止，再下一班另写；note 一次
    per_train = [sentence for sentence in found["sentences"] if "出门" in sentence or "REX" in sentence]
    assert len([view for view in found["connections"]]) == 7 and any("22:53 REX 7" in s for s in per_train)
    assert not any("按时刻表" in sentence for sentence in per_train)
    assert "按时刻表，不是实时" in found["note"]
    summary = found["summary"]
    assert "Wien Hauptbahnhof → Flughafen Wien Bahnhof" in summary and "00:53 REX 7" in summary
    assert "再下一班 04:22 REX 7 开" in summary and "04:52" not in summary.split("再下一班")[1]
    assert "之间出发的车都在这里" in summary and "01:00 之后的没查" in summary  # A16: the user's words, not “连续查全”
    # 总结句自己过 check_answer；坏样本（少写 23:23、数错）被拦
    assert problems(service, second(found["summary"], at="21:01")) == {}
    assert problems(service, second(COUNT_GOOD, at="21:01")) == {}
    bad = problems(service, second(COUNT_BAD, at="21:01"))
    assert list(bad) == ["count_needs_continuous"] and "23:23 REX 7 没写" in bad["count_needs_continuous"][0]


def test_until_stops_at_the_daily_budget_and_says_how_far_it_got(make):
    trains = Night(tuple(NIGHT))
    service = make(trains, at="21:01", budget=1)  # 一页一次 /plan；只够一页
    found = query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="01:00")
    # 一页 8 条装下全部 7 班：覆盖已到，不再要第二页（对照：额度没花也能答）
    assert found["window"]["covered"]
    tight = Night(tuple(f"22:{minute:02d}" for minute in range(53, 60)) + tuple(NIGHT[1:]))
    service = make(tight, at="21:01", budget=1)  # 一页 8 班车不够用一页装下：第二页时额度到限
    found = query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="01:00")
    window = found["window"]
    assert not window["covered"] and window["limited"] and "查询次数到上限" in window["text"] and "查到" in window["text"]
    assert "之后的没查" in window["text"]


def test_until_needs_transit_and_a_later_time(make):
    service = make(Night(), at="21:01")
    with pytest.raises(ValueError, match="until"):
        query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="20:00", date=DAY)
    with pytest.raises(ValueError, match="until"):
        query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="2026-11-04T20:00:00+01:00")
    with pytest.raises(ValueError, match="until"):
        query(service, "latest_departure", origin=HBF, destination=AIRPORT, by="23:59", date=DAY, until="23:00")


def test_gaps_between_queries_and_times_outside_them_are_refused(make):
    service = make(Night(), at="23:30")
    # 判词 off 第 1 问的窗口：22:30—23:23、00:40 起，中间空档 23:23—00:40
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("22:30").isoformat(), limit=2)
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("00:40").isoformat(), limit=2)
    bad = problems(service, second(DENIED_BAD, at="23:30"))
    assert list(bad) == ["denied_needs_coverage"] and "23:53 落在这次查询的窗口外" in bad["denied_needs_coverage"][0]
    assert "连续查法" in bad["denied_needs_coverage"][0]
    bad = problems(service, second(COUNT_BAD, at="23:30"))
    assert list(bad) == ["count_needs_continuous"]
    # “没查到”不豁免结论；存档/没核的写法豁免
    assert problems(service, second("计划里写过“误了 22:53，只剩 23:53 末班 REX”（存档，写于 11-01 13:49，没核）。", at="23:30")) == {}
    # 对照：连续覆盖后同一处境的合法说法
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="01:00")
    assert problems(service, second(COUNT_GOOD, at="23:30")) == {}
    bad = problems(service, second("别指望 23:53 那班。", at="23:30"))
    assert "denied_needs_coverage" in bad and "工具查到 23:53 这班" in bad["denied_needs_coverage"][0]


# --- 2. latest_departure：到达期限与上车时刻分清 -------------------------------------------------------------------------

def test_board_at_is_a_boarding_time_not_an_arrival_deadline(make):
    service = make(Night(), at="22:20")
    found = query(service, "latest_departure", origin=STREET, destination=AIRPORT, board_at="23:23", date=DAY)
    assert found["target"]["meaning"] == "boarding_time" and found["target"]["deadline"] == "board"
    # A10: a boarding target says where the vehicle is caught (“要 23:23 前到” read as reaching the destination)
    assert found["sentences"][0].startswith("赶 23:23 在 Flughafen Wien Bahnhof 开的车，最晚 22:43 出门")
    arrival = query(service, "latest_departure", origin=STREET, destination=AIRPORT, by="23:59", date=DAY)
    assert arrival["target"]["meaning"] == "arrival_deadline"


def test_a_boarding_time_passed_as_by_is_refused_with_the_fix(make):
    service = make(Night(), at="22:20")
    # 判词 r2：22:42 是 RJX 从 Hbf 开车的时刻，被当成到机场的期限
    with pytest.raises(ValueError, match="board_at"):
        query(service, "latest_departure", origin=STREET, destination=AIRPORT, by="22:53", date=DAY)
    # 对照：真的到达期限不拦
    assert query(service, "latest_departure", origin=STREET, destination=AIRPORT, by="23:59", date=DAY)["status"] == "sourced"


# --- 3. 存档的否定句不进回答 ---------------------------------------------------------------------------------------------

ARCHIVE_ANCHOR = dict(id="hn-flight", time="07:05", text="证件柜台关（误了 22:42 和 22:53，只剩 23:53 末班 REX）",
                      subject=["p1"], place="airport", consequence="误了 22:53，只剩 23:53 末班。",
                      alternative="明早 04:22 起还有。", deadline="external")


def test_stored_last_train_claims_are_marked_archived_in_turn(make):
    service = make(Night(), at="21:01", anchors=[deepcopy(ARCHIVE_ANCHOR)])
    turn = service.turn("local", "最晚几点出发去机场？", "q1")
    node = next(node for node in turn["hard_nodes"] if node["id"] == ARCHIVE_ANCHOR["id"])
    assert node["text"].startswith("证件柜台关")
    assert "计划里写过“误了 22:42 和 22:53，只剩 23:53 末班 REX”（存档，写于" in node["text"] and "没核" in node["text"]
    assert node["archived_claims"] and node["archived_claims"][0]["superseded"] is None
    assert node["consequence"].startswith("计划里写过")
    assert "存档" in turn["next"] and "until" in turn["next"]
    # pack 里同样标存档
    assert "计划里写过" in turn["pack"]["markdown"] and "存档" in turn["pack"]["markdown"]
    # 没有存档句的对照：next 不提
    plain = make(Night(), at="21:01", anchors=[dict(id="hn-flight", time="07:05", text="证件柜台关", subject=["p1"],
                                                    place="airport", consequence="误机。", alternative="改签。", deadline="external")])
    assert "存档" not in plain.turn("local", "最晚几点出发去机场？", "q1")["next"]


def test_a_later_departure_found_this_session_voids_the_stored_claim(make):
    service = make(Night(), at="21:01", anchors=[deepcopy(ARCHIVE_ANCHOR)])
    query(service, "transit", origin=HBF, destination=AIRPORT, when=local("21:00").isoformat(), until="01:00")
    turn = service.turn("local", "还有车吗？", "q2")
    node = next(node for node in turn["hard_nodes"] if node["id"] == ARCHIVE_ANCHOR["id"])
    assert node["archived_claims"][0]["superseded"] == "00:23"  # 查到更晚的同方向车：作废
    assert "作废" in node["text"]


def test_pack_marks_the_claim_and_check_answer_lets_the_marked_form_pass(make):
    service = make(Night(), at="21:01", anchors=[deepcopy(ARCHIVE_ANCHOR)])
    turn = service.turn("local", "今晚怎么去机场？", "q1")
    assert "没核" in turn["pack"]["markdown"]
    # 宿主照抄标好的存档句进第二段：不当作现在的事实，check_answer 不拦
    node = next(node for node in turn["hard_nodes"] if node["id"] == ARCHIVE_ANCHOR["id"])
    assert problems(service, second(node["text"], at="21:01")) == {}
