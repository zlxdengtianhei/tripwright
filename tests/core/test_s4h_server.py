"""S4h-a (stage 4 exit judge: 6 replays, none passed; the hosts did not do as told and check_answer let it through). The
service now checks this turn's arguments and shape instead of telling the host:

1. A quick question whose turn asked for the latest departure keeps the service's frame: a lookup naming a hard node
   is refused with what to send (codex_r1 question 1: the plan's 05:45 queue as anchor_id, and the main choice became
   leaving at dawn instead of the stored night at the airport).
2. A point whose place and words name two stored places is refused (ns_on_r1 question 2: the user said “Block Factory”,
   the host sent the plan's gym). A word for a kind of place names the one stored place of that kind (mcponly_r1
   question 1: “airport”); a place still not recognized is said in `next`.
3. The “继续” turn carries the question it continues: its points (where the user goes next) and its frame reach the
   lookups after it (mcponly_r1 question 2: the last connection from the gym, not from the meal first).
4. The stored closing of a lookup's origin may stand on a line that says 核过 when it says it is stored (every run's
   first question: “22:00 关门出来” refused as not in the evidence, and the time to leave the gym dropped).
5. A shared answer another replay fetched before this replay's pinned clock began is not dated before it (ns_on,
   ns_off, mcponly question 2: “（20:51 查过）” for a lookup made at 21:05 after a question at 21:01).

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

import asyncio
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from test_a8_transit_facts import DAY, NIGHT, local
from test_a10_frame import REX, Evening, NEXT_DAY, airport_plan, fixed, problems, query, second_part
from test_a12_first_part import ASKED, BUSES, FIRST, FOOD, NORTH, SECOND, SOUTH
from test_a14_fixes import GO, LUNCH, TRACKS
from test_a16_fixes import COUNTER, MARGIN, STATION_FOOD, WAYS, make  # noqa: F401 (make is a fixture)
from tripwright import cli, packs
from tripwright.providers.common import SharedLookups
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store
from tripwright.trip import aware

GO_ON = "继续"


# --- 1. a quick latest question keeps the service's frame ------------------------------------------------------------------------

def test_a_quick_latest_question_refuses_a_lookup_that_names_a_hard_node(make, monkeypatch):
    service = make(tracks=WAYS)
    fixed(monkeypatch, local("21:02"))
    asked = service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    assert asked["first_part"]["asks_latest"] and "不给 anchor_id、by、board_at" in asked["next"]
    framed = query(service, "latest_departure", origin="gym-north", destination="airport")
    assert framed["target"]["meaning"] == "last_connection"
    # bad sample (codex_r1 question 1): a second lookup with the plan's 05:45 queue; its “明早最晚 04:56 出门” became the
    # second part's main choice
    with pytest.raises(ValueError, match=r"arguments\.anchor_id: this question \(turn request_id q1\) asks for the latest departure, "
                                         r"and the service frames it: send origin and destination only"):
        query(service, "latest_departure", origin="gym-north", destination="airport", anchor_id="hn-queue")
    # the same node through by (its own time on its day) is the same frame
    with pytest.raises(ValueError, match=r"arguments\.by: .*'05:45' is hard node 'hn-queue'"):
        query(service, "latest_departure", origin="gym-north", destination="airport", by="05:45", date=NEXT_DAY)
    # controls: a time no node has (one the user names) passes; so does any lookup half an hour after the turn, or after
    # a turn of another scenario
    own = query(service, "latest_departure", origin="gym-north", destination="airport", by="23:40", date=DAY)
    assert own["target"]["meaning"] == "arrival_deadline"
    fixed(monkeypatch, local("21:40"))
    assert query(service, "latest_departure", origin="gym-north", destination="airport", anchor_id="hn-queue")["target"]["anchor_id"] == "hn-queue"
    planning = make(tracks=WAYS)
    planning.turn("local", "明天最晚几点出发去机场，帮我排一下明早", "p1", scenario="plan", points=GO)
    assert query(planning, "latest_departure", origin="gym-north", destination="airport", anchor_id="hn-queue")["target"]["anchor_id"] == "hn-queue"


# --- 2. the place a point names ------------------------------------------------------------------------------------------------

def test_a_place_the_users_words_contradict_is_refused_before_anything_is_recorded(make, capsys):
    service = make(at="21:52", places=(NORTH, SOUTH, *STATION_FOOD))
    said = "从这个 Boulder Hall South 还是什么的那个，第一个岩馆，刚出来"
    wrong = [dict(kind="at", said=said, place="gym-north"), dict(kind="eat", said="有点饿想吃点东西"),
             dict(kind="go", said="是不是得赶紧去机场", place="airport")]
    # bad sample (ns_on_r1 question 2): the host sent the plan's gym for the one the user named; the first part had
    # “你在北边的岩馆（你说的“…Boulder Hall South…”）” and the last connection started there
    received = len(service.store.all("events", "local"))
    with pytest.raises(ValueError, match=r"Invalid points: points\.0: place 'gym-north' is the stored 'gym-north' .* names the stored "
                                         r"'gym-south' .*send place 'gym-south'"):
        service.turn("local", SECOND, "q2", scenario="quick", points=wrong)
    assert len(service.store.all("events", "local")) == received  # nothing recorded: the same request_id goes again
    right = service.turn("local", SECOND, "q2", scenario="quick", points=[dict(wrong[0], place="gym-south"), *wrong[1:]])
    assert right["first_part"]["lines"][1] == f"按现在 21:52 算，你在南边的岩馆（你说的“{said}”）"
    # the command line drafts no first part for it either
    code = cli.main(["--db", str(service.db_path), "first-part", "--text", SECOND, "--points", json.dumps(wrong, ensure_ascii=False)])
    assert code == 1 and "points.0: place 'gym-north'" in capsys.readouterr().err
    # controls: words that name no stored place agree with any place; words for the place sent agree with it
    plain = [dict(kind="at", said="刚从第一个岩馆出来", place="gym-north")]
    assert service.turn("local", SECOND, "q3", scenario="quick", points=plain)["first_part"]["lines"][1].startswith("按现在 21:52 算，你在北边的岩馆")
    same = [dict(kind="at", said="从 Kletterhalle Nordbahn 刚出来", place="gym-north")]
    assert service.turn("local", SECOND, "q4", scenario="quick", points=same)["points"]["at"]["place"] == "gym-north"


def swapped(value):
    """`value` with every field that names the airport place ("airport") naming "vie-airport" instead."""
    if isinstance(value, dict):
        return {key: swapped(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [swapped(entry) for entry in value]
    return "vie-airport" if value == "airport" else value


@pytest.fixture
def renamed(tmp_path, monkeypatch):
    """The a16 trip with the airport stored as the replay stores it: ID vie-airport, kind transport."""
    made = []

    def build(at="21:01"):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"s4h-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(Evening(trains=NIGHT[:5], buses=BUSES).handle)),
                                     min_interval=0, clock=lambda: moment)
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        made.append((service, store))
        plan = airport_plan([deepcopy(REX)], [deepcopy(MARGIN), deepcopy(COUNTER)])
        area = plan["places"][0]["area"]
        plan["places"] += [dict(deepcopy(place), area=area) for place in (NORTH, SOUTH, *FOOD, LUNCH)]
        plan["days"][0]["plans"] = [dict(name="Recommended", tracks=deepcopy(WAYS))]
        plan = swapped(plan)
        next(place for place in plan["places"] if place["id"] == "vie-airport")["kind"] = "transport"
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def test_a_word_for_a_kind_of_place_names_the_one_stored_place_of_that_kind(renamed):
    service = renamed()
    # bad sample (mcponly_r1 question 1): the host's English “airport”; nothing matched and the first part had only “几点出发要查”
    lines = service.turn("local", FIRST, "q1", scenario="quick", points=[dict(kind="go", said="最晚几点出发机场 怎么走", place="airport")])["first_part"]["lines"]
    assert lines[0] == ("去维也纳机场：计划里是 21:45 从北边的岩馆出门，22:13 到 Wien Hauptbahnhof，坐今晚 22:53 REX 7（存档，没核）；"
                        "还有没有更晚的车，第二段核完给。")
    plan = service.versions.current("local")["plan"]
    for word in ("airport", "Flughafen", "机场", "the airport", "aeropuerto"):
        assert packs.resolve_place(plan, word)["id"] == "vie-airport", word
    # controls: a shop inside the airport (a note in brackets, an address) is not the airport; two stations name none
    shop = dict(FOOD[0], id="vie-burger", name="汉堡店（机场到达大厅）", name_local="Burger Beispiel, Flughafen Wien, Ankunft", coords=[48.12, 16.56])
    assert packs.resolve_place(dict(plan, places=[*plan["places"], shop]), "airport")["id"] == "vie-airport"
    assert packs.resolve_place(plan, "station") is None  # the trip stores two: Vienna's and Bad Ischl's
    assert packs.resolve_place(plan, "Prater") is None


def test_a_place_not_recognized_is_said_and_never_taken_for_the_plans(make):
    service = make(at="21:52", places=(NORTH, SOUTH, *STATION_FOOD))
    found = service.turn("local", SECOND, "q2", scenario="quick",
                         points=[dict(kind="at", said="在一家没存的咖啡馆", place="Café Nirgendwo"), dict(kind="do", said="万圣节活动", place="Prater")])
    # bad sample shape: the unknown place left no trace; now `next` opens with it and the stored IDs to send
    assert found["next"].startswith("points 里这些地方不是这趟存的地点，也认不出：“Prater”、“Café Nirgendwo”。第一段没给它们存的车和时刻，"
                                    "也没拿计划里的地点顶替。是存的地点就改成地点编号（")
    # where the user is stays the user's words, not the plan's place of the moment
    assert found["first_part"]["lines"][1] == "按现在 21:52 算，你在“在一家没存的咖啡馆”"
    assert found["first_part"]["at"]["place"] is None and found["first_part"]["at"]["unknown"] == "Café Nirgendwo"
    # control: coordinates and stored IDs are known places
    known = service.turn("local", SECOND, "q3", scenario="quick", points=[dict(kind="at", said="在这儿", place="48.19,16.35"), *ASKED[1:]])
    assert not known["next"].startswith("points 里这些地方")


# --- 3. the “继续” turn carries the question it continues -------------------------------------------------------------------------

def test_the_lookups_after_go_on_start_where_the_continued_question_goes_next(make, monkeypatch):
    service = make(places=(NORTH, SOUTH, *STATION_FOOD))
    fixed(monkeypatch, local("21:52"))
    question = service.turn("local", SECOND, "q2", scenario="quick", points=ASKED)
    assert question["points"]["next"]["place"] == "hbf-burger"
    fixed(monkeypatch, local("21:53"))
    going_on = service.turn("local", GO_ON, "q3", scenario="quick", points=[])
    assert going_on["second_part_due"]["request_id"] == "q2"
    # bad sample (mcponly_r1 question 2): after “继续” the last connection started at the gym, as if the user went straight to
    # the airport; without the “继续” (sub_on, codex) it started from the meal at the station
    found = query(service, "latest_departure", origin="gym-south", destination="airport")
    assert found["resolved"]["origin"]["next_step"]["place"] == "hbf-burger"
    assert found["sentences"][1] == "按你先去汉堡店算（你说的“有点饿想吃点东西”）"
    # and the question's frame: no hard node in its lookups
    with pytest.raises(ValueError, match=r"turn request_id q2\) asks for the latest departure"):
        query(service, "latest_departure", origin="gym-south", destination="airport", anchor_id="hn-queue")
    # a second “继续” still points at q2 and carries it too
    fixed(monkeypatch, local("21:54"))
    assert service.turn("local", GO_ON, "q4", scenario="quick", points=[])["second_part_due"]["request_id"] == "q2"
    assert query(service, "latest_departure", origin="gym-south", destination="airport")["resolved"]["origin"]["next_step"]["place"] == "hbf-burger"
    # control: a new question with points is its own; its lookups follow it
    fixed(monkeypatch, local("21:55"))
    service.turn("local", FIRST, "q5", scenario="quick", points=[dict(kind="at", said="刚从第一个岩馆出来", place="gym-south"), *GO])
    assert "next_step" not in query(service, "latest_departure", origin="gym-south", destination="airport")["resolved"]["origin"]


# --- 4. the stored closing on a line that says 核过 -----------------------------------------------------------------------------

def test_the_stored_closing_time_may_stand_on_a_checked_line_when_it_says_so(make):
    service = make(places=(dict(NORTH, opening_hours=["Mo-Su 09:00-22:10"]), SOUTH, *FOOD))
    found = query(service, "latest_departure", origin="gym-north", destination="airport")
    # bad sample (ns_on_r1, mcponly_r1, sub_on_r1 question 1): the service's sentence under the host's 核过 was refused for the
    # closing time with “改成网上说法”, and the second part lost the time to leave the gym; now the fix keeps the time
    old = "主选（核过，时刻表）：计划里的 22:53 REX 7 赶得上：22:10 关门出来，走到 Wien Matzleinsdorfer Platz 坐 22:33 的 D。"
    assert problems(service, second_part(found["first_line"], old))["checked_has_evidence"] == [
        "“核过”对不上证据：时刻 22:10 是起点存的关门时刻，不是查到的车 | 照 latest_departure 的句子写“22:10 关门出来（按存的营业时间）”，时刻留着"]
    # the service's sentence says the closing is stored, and a checked line may carry it
    planned = next(line for line in found["sentences"] if line.startswith("计划里的 22:53 REX 7 赶得上"))
    assert planned == "计划里的 22:53 REX 7 赶得上：22:10 关门出来（按存的营业时间），走到 Wien Matzleinsdorfer Platz，换 1 次车：22:33 D（往 Hauptbahnhof）Wien Matzleinsdorfer Platz → Wien Hauptbahnhof 22:45，换 22:53 REX 7（往 Flughafen Wien）Wien Hauptbahnhof → Flughafen Wien Bahnhof 23:15；23:15 到；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走"
    assert "checked_has_evidence" not in problems(service, second_part(found["first_line"], f"主选（核过，时刻表）：{planned}。"))
    # the closing said as checked without its mark is refused, with the sentence that keeps it
    bare = problems(service, second_part(found["first_line"], "主选：22:10 出门，走到 Wien Matzleinsdorfer Platz 坐 22:33 的 D，赶计划里的 22:53 REX 7（核过）。"))
    assert bare["checked_has_evidence"] == ["“核过”对不上证据：时刻 22:10 是起点存的关门时刻，不是查到的车 | "
                                            "照 latest_departure 的句子写“22:10 关门出来（按存的营业时间）”，时刻留着"]
    # controls: a node's stored time marked so passes on a checked line; a time neither found nor marked is still refused
    assert "checked_has_evidence" not in problems(service, second_part(found["first_line"], "核过：22:33 的 D 到总站，赶 22:53 REX 7；明早 07:05 证件柜台关（存档，没核）"))
    made_up = problems(service, second_part(found["first_line"], "核过：22:41 的 D 到总站，赶 22:53 REX 7"))
    assert made_up["checked_has_evidence"][0].startswith("“核过”对不上证据：时刻 22:41 不在这一趟工具查到的结果里")


# --- 5. a shared answer from before this replay's clock began -------------------------------------------------------------------

def test_an_answer_another_replay_fetched_earlier_is_not_dated_before_this_replays_question(tmp_path, monkeypatch):
    folder = tmp_path / "shared"
    upstream = Evening(trains=NIGHT[:5], buses=BUSES)

    def trip(moment, clock=None):
        store = Store(tmp_path / f"s4h-{len(list(tmp_path.glob('*.sqlite3')))}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)), min_interval=0,
                                     clock=clock, shared=SharedLookups(folder, min_interval=0))
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        plan = airport_plan([dict(id="hn-rex", time="22:53", text="在总站坐 REX 去机场", subject=["p1"], place="hbf", destination="airport", line="REX 7")],
                            [MARGIN, COUNTER])
        plan["places"] += [dict(deepcopy(place), area=plan["places"][0]["area"]) for place in (NORTH, SOUTH, *FOOD, LUNCH)]
        plan["days"][0]["plans"] = [dict(name="Recommended", tracks=deepcopy(TRACKS))]
        seed = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=seed["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        return service, store

    asked = local("21:01")
    lookup = dict(origin="gym-north", destination="airport", after="21:01", date=DAY)  # one request on both runs
    # another replay of the same evening ran 14 minutes earlier (wall time) and fetched these searches
    with monkeypatch.context() as earlier:
        fixed(earlier, asked)
        other, other_store = trip(asked, clock=lambda: asked)
        query(other, "latest_departure", **lookup)
        other.jobs.stop()
        other_store.close()
    for path in (folder / "answers").glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(dict(entry, fetched=entry["fetched"] - 840, good_until=entry["good_until"] - 840)), encoding="utf-8")
    sent = len(upstream.requests)
    # this replay: its clock pinned to the question's moment from now on
    monkeypatch.setenv("TRIPWRIGHT_NOW", asked.isoformat())
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", repr(time.time()))
    service, store = trip(asked)
    try:
        service.turn("local", FIRST, "q1", scenario="quick", points=GO)
        found = query(service, "latest_departure", **lookup)
        assert len(upstream.requests) == sent  # answered from the shared folder
        fetched = found["evidence"]["fetched_at"]
        # bad sample (ns_on_r1: fetched 20:51 for a lookup at 21:05; mcponly 20:35, ns_off 20:46): 14 minutes before the question
        assert aware(fetched) >= asked, fetched
        line = service.turn("local", SECOND, "q2", scenario="quick", points=GO)["first_part"]["lines"][0]
        looked = re.search(r"（(\d\d:\d\d) 查过）", line)
        assert looked and looked.group(1) >= "21:01", line
    finally:
        service.jobs.stop()
        store.close()


def test_on_a_pinned_clock_an_answer_fetched_after_the_pin_keeps_its_age(tmp_path, monkeypatch):
    """Control: since the pin began, the pinned clock keeps step with the wall clock; an answer fetched then is as old as
    it is (s4b), as without a pin. One fetched before the pin began counts from the lookup that uses it."""
    wall = [1_000_000.0]
    clock = [datetime(2026, 11, 4, 20, tzinfo=timezone.utc)]
    monkeypatch.setenv("TRIPWRIGHT_NOW", clock[0].isoformat())
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", repr(wall[0] - 60))  # the pin began a minute before the first fetch
    upstream = Evening(trains=NIGHT[:5])
    clients = [httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)) for _ in range(3)]
    first, second, third = (TransitousProvider(client=client, min_interval=0, clock=lambda: clock[0],
                                               shared=SharedLookups(tmp_path / "shared", min_interval=0, wall=lambda: wall[0]))
                            for client in clients)
    route, when = ("48.1890,16.3520", "48.1850,16.3760"), local("21:00")
    asked = asyncio.run(first.plan(*route, when))
    wall[0] += 600
    clock[0] += timedelta(minutes=10)
    reused = asyncio.run(second.plan(*route, when))
    assert len(upstream.requests) == 1 and reused.fetched_at == asked.fetched_at  # ten minutes old, not new
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", repr(wall[0] - 60))  # a new pin, after that fetch
    wall[0] += 60
    clock[0] += timedelta(minutes=1)
    later = asyncio.run(third.plan(*route, when))
    assert len(upstream.requests) == 1 and later.fetched_at == clock[0]
    for client in clients:
        asyncio.run(client.aclose())
