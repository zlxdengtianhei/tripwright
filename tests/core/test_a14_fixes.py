"""A14 (quality judge 7): plain errors the verdict names, each with a bad sample in the replay's shape and a legal control.

1. The assumed place came from the group's line (lunch, 11:40—13:30) while the user's own line had the gym (17:30—21:45)
   at 21:01.
2. The plan's margin (05:45 at the counter) and an online check-in got “要 05:45 前到，最晚 05:12 出门”; only a time to be
   there gets a latest departure.
3. “怎么查” searched the night's last train while the first choice was the plan's 22:42 (3 of 3 runs).
4. The first part carried every hard node, and its first line did not answer (“真正最晚要查”).
5. “火车最后一班 00:53” on the first line, “不能说 00:53 之后没有火车” on the last: a lookup to 01:00 (the answer layer's
   old “tonight”) against one to 03:00.
6. A status line saying the first part was “above” when nothing was.

Offline: the fictional MOTIS of test_a10_frame (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

from copy import deepcopy

import httpx
import pytest

from test_a8_transit_facts import NIGHT, STREET, local
from test_a10_frame import DESK, QUEUE, REX, Evening, airport_plan, fixed, query
from test_a12_first_part import BUSES, FOOD, NORTH, SOUTH
from tripwright import packs
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

LUNCH = dict(id="lunch-hbf", name="总站午饭店", name_local="Mittag Beispiel, Am Hauptbahnhof 1, 1100 Wien", kind="food",
             coords=[48.1851, 16.3762], opening_hours=["Mo-Su 11:00-22:00"])
# the replay's day: everyone at lunch, then the mates' way to the airport, then the user's own afternoon and evening
TRACKS = [dict(subject=["p1", "p2", "p3"], blocks=[dict(id="b-lunch", start="11:40", end="13:30", kind="meal", place="lunch-hbf")]),
          dict(subject=["p2", "p3"], blocks=[dict(id="b-mates", start="13:30", end="14:00", kind="reserve", origin="hbf", destination="airport", text="总站 → 机场")]),
          dict(subject=["p1"], blocks=[dict(id="b-south", start="14:07", end="15:40", kind="visit", place="gym-south"),
                                      dict(id="b-ride", start="16:48", end="17:14", kind="travel", origin="gym-south", destination="gym-north", text="南馆 → 北馆"),
                                      dict(id="b-north", start="17:30", end="21:45", kind="visit", place="gym-north"),
                                      dict(id="b-to-hbf", start="21:45", end="22:13", kind="travel", origin="gym-north", destination="hbf", text="北馆 → 总站")])]
CHECKIN = dict(id="hn-checkin", time="05:45", text="网上值机截止（起飞前 2 小时）", subject=["p1"], place="airport", deadline="external")
GO = [dict(kind="go", said="最晚几点出发去机场", place="airport")]


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream=None, *, at="21:01", today=(REX,), tomorrow=(QUEUE, DESK), tracks=TRACKS):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a14-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport((upstream or Evening(trains=NIGHT[:5], buses=BUSES)).handle)),
                                     min_interval=0, clock=lambda: moment)
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        made.append((service, store))
        plan = airport_plan([deepcopy(anchor) for anchor in today], [deepcopy(anchor) for anchor in tomorrow])
        area = plan["places"][0]["area"]
        plan["places"] += [dict(deepcopy(place), area=area) for place in (NORTH, SOUTH, LUNCH, *FOOD)]
        plan["days"][0]["plans"] = [dict(name="Recommended", tracks=deepcopy(tracks))]
        turn = service.turn("local", "Create the trip.", "seed")
        made_draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", made_draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


# --- 1. the assumed place is the user's own block of the moment ----------------------------------------------------------

def test_the_assumed_place_is_the_users_own_block_of_the_moment(make):
    service = make()
    turn = service.turn("local", "最晚几点出发去机场", "q1", scenario="quick", points=GO)
    assert turn["first_part"]["lines"][2] == "按现在 21:01 算，假设你在北边的岩馆（计划里这时在这；不在的话说一声）"
    # controls: on the way the place is where the way starts; after it, where it ends; another person's own line
    plan = service.versions.current("local")["plan"]
    day = plan["days"][0]["date"]
    assert packs.plan_place(plan, day, "21:50", "p1") == "gym-north"
    assert packs.plan_place(plan, day, "22:20", "p1") == "hbf"
    assert packs.plan_place(plan, day, "21:01", "p2") == "airport"
    assert packs.plan_place(plan, day, "09:00", "p1") is None
    assert packs.asker(plan) == "p1" and packs.asker(plan, speaker="p3") == "p3"


# --- 2. only a time to be there gets a latest departure ----------------------------------------------------------------------

def test_the_plans_margin_and_an_online_deadline_get_no_latest_departure(make):
    service = make(tomorrow=(dict(CHECKIN, where="online"), QUEUE, DESK))
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    said = "\n".join(found["sentences"])
    assert "你计划留的余量：明早 05:45 前，到证件柜台排队" in said
    assert "要 05:45 前到" not in said and "直接从 起点 去：要 07:05 前到" in said
    assert found["hard_node"]["anchor_id"] == "hn-queue" and found["hard_node"]["latest"]["target"]["time"] == "07:05"
    # bad sample (the replay's data): the online check-in stored without `where: online` is a time to be at the airport
    unmarked = query(make(tomorrow=(CHECKIN, QUEUE, DESK)), "latest_departure", origin=STREET, destination="airport")
    assert any("要 05:45 前到" in sentence for sentence in unmarked["sentences"])
    # control: a plan margin with no operator's deadline behind it gets no latest departure at all
    alone = query(make(tomorrow=(QUEUE,)), "latest_departure", origin=STREET, destination="airport")
    assert not any("前到，" in sentence for sentence in alone["sentences"]) and alone["hard_node"]["latest"] is None


# --- 3. “怎么查” searches the first choice ----------------------------------------------------------------------------------

def test_how_to_check_searches_the_first_choice_and_the_last_one_apart(make):
    service = make()
    found = query(service, "latest_departure", origin="hbf", destination="airport")
    text = found["how_to_check"]["text"]
    assert "出发时间填 22:53" in text.splitlines()[0]
    assert text.splitlines()[1] == "误了主选，查末班：在 ÖBB App 搜 Wien Hauptbahnhof → Flughafen Wien Bahnhof，出发时间填 00:53。"
    assert found["how_to_check"]["last"]["clock"] == "00:53"
    # control: without a train in the plan the first choice is the last train, and searching it is right
    plain = make(today=())
    last = query(plain, "latest_departure", origin="hbf", destination="airport")
    assert "出发时间填 00:53" in last["how_to_check"]["text"] and "误了主选" not in last["how_to_check"]["text"]


# --- 4. the first part: the stored train first, one hard node ----------------------------------------------------------------

def test_the_first_part_gives_the_stored_train_first_and_one_hard_node(make):
    service = make(tomorrow=(dict(CHECKIN, where="online"), QUEUE, DESK, dict(DESK, id="hn-gate", time="07:15", text="登机口关")))
    lines = service.turn("local", "最晚几点出发去机场", "q4", scenario="quick", points=GO)["first_part"]["lines"]
    # A16: the plan's own way from the user's block to that train says when to leave
    assert lines[1] == "去维也纳机场：计划里是 21:45 从北边的岩馆出门，22:13 到 Wien Hauptbahnhof，坐今晚 22:53 REX 7（存档，没核）；还有没有更晚的车，第二段核完给。"
    # bad sample (the replay's first part): the check-in, the margin, the counter and the gate, four lines of tomorrow
    assert [line for line in lines if line.startswith("明早")] == ["明早 07:05 证件柜台关（存档，没核）"]
    assert "05:45" not in "\n".join(lines) and "07:15" not in "\n".join(lines)
    # control: without a train to the place the first line still says the latest follows in part 2
    bare = make(today=()).turn("local", "最晚几点出发去机场", "q5", scenario="quick", points=GO)["first_part"]
    assert bare["lines"][0] == "按存的计划先说（时刻还没核）"
    assert "明早 07:05 证件柜台关（存档，没核）" in bare["lines"]
    assert not any(line.endswith("第二段给。") for line in bare["lines"])


# --- 5. a coverage note that contradicts the search of tonight ----------------------------------------------------------------

# --- 6. no status line, no second part twice ---------------------------------------------------------------------------------

def test_a_status_line_is_not_a_first_part(make, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_REPLY_FLOW", "one")  # the one-reply wording
    service = make()
    turn = service.turn("local", "最晚几点出发去机场", "q6", scenario="quick", points=GO)
    assert turn["next"].startswith("现在就把 first_part.text 发给用户（用户在等这第一段）；发出去之后再调 query。")
