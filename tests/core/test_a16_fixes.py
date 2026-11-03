"""A16 (quality judge 8): the server's sentences, fixed where they are written; each with a bad sample in the replay's
shape and a legal control.

1. One line says one thing once: the plan's margin apart from the deadline, the tickets as one sentence (no record text,
   no “/人” for one traveller), a tram as a route, the next connection by when to leave, no lookup words.
2. The lookup starts where this question goes next (to eat at the station), and “怎么查” follows it.
3. No time to leave an origin later than it closes.
4. “怎么查” is the first choice's first ride: its stop and its time.
5. The first question's first part gives the plan's own time to leave and way, archived.
6. A place the host or the user names in other words is resolved from the stored names.
7. check_answer: search_matches_main (stop and time), leave_before_closing, per_person_alone; latest_needs_lookup.
8. Without the tools: the command line drafts the same first part, with what to do with it.

Offline: the fictional MOTIS of test_a10_frame (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

import json
import re
from copy import deepcopy

import httpx
import pytest

from test_a8_transit_facts import HBF, NIGHT, STREET, local
from test_a10_frame import DESK, QUEUE, REX, Evening, airport_plan, fixed, problems, query, second_part
from test_a12_first_part import ASKED, BUSES, FIRST, FOOD, NORTH, SECOND, SOUTH, TICKETS
from test_a14_fixes import GO, LUNCH, TRACKS
from tripwright import cli, packs
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter, match_place
from tripwright.render.check_answer import check_draft
from tripwright.service import Tripwright
from tripwright.storage import Store

# the replay's two nodes of tomorrow in their stored shape: the margin says so in its own words, the counter in brackets
MARGIN = dict(QUEUE, text="拿非欧盟护照的，05:45 前到证件柜台排队最稳（计划留的余量；App 里验证通过就不用去）")
COUNTER = dict(DESK, text="证件柜台关（起飞前 40 分钟）")
# the places to eat at the station, looked up from the station's stop
STATION_FOOD = [dict(place, transit_id=HBF) for place in FOOD]
# the user's own way from the evening gym to the station, as a plan stores it (its later option after “；”)
WAY = "步行 5′ 到 Beispielplatz，21:51 D → Wien Hauptbahnhof 22:03；晚一班：22:01 D"
WAYS = [dict(track, blocks=[dict(block, mode=WAY) if block["id"] == "b-to-hbf" else block for block in track["blocks"]])
        if track["subject"] == ["p1"] else track for track in TRACKS]


class Tram18(Evening):
    """Evening with the tram to the station numbered (the replay's “00:47 18 <站名>”)."""

    @staticmethod
    def tram(start):
        walk, ride = Evening.tram(start)
        return [walk, dict(ride, displayName="18")]


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream=None, *, at="21:01", today=(REX,), tomorrow=(MARGIN, COUNTER), places=(NORTH, SOUTH, *FOOD), bookings=(),
              tracks=TRACKS):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a16-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport((upstream or Evening(trains=NIGHT[:5], buses=BUSES)).handle)),
                                     min_interval=0, clock=lambda: moment)
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        service.db_path = tmp_path / f"a16-{len(made)}.sqlite3"
        made.append((service, store))
        plan = airport_plan([deepcopy(anchor) for anchor in today], [deepcopy(anchor) for anchor in tomorrow])
        area = plan["places"][0]["area"]
        plan["places"] += [dict(deepcopy(place), area=area) for place in (*places, LUNCH)]
        plan["bookings"] = deepcopy(list(bookings))
        plan["days"][0]["plans"] = [dict(name="Recommended", tracks=deepcopy(tracks))]
        turn = service.turn("local", "Create the trip.", "seed")
        made_draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", made_draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def first_checks(service, text: str, request_id: str | None = None) -> dict[str, list[str]]:
    found = {}
    arguments = dict(text=text, part="first", **(dict(request_id=request_id) if request_id else {}))
    for entry in query(service, "check_answer", **arguments)["problems"]:
        found.setdefault(entry["check"], []).append(entry["why"])
    return found


# --- 1. one line, one thing, once ----------------------------------------------------------------------------------------------

def test_each_line_says_one_thing_once(make):
    service = make(Tram18(trains=NIGHT[:5], buses=BUSES), bookings=TICKETS)
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    said = found["sentences"]
    # the first line answers; the time to keep and the plan's margin have a line each, the margin said once as the plan's
    assert said[0] == "今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:30 出门）"
    assert "之后要守：明早 07:05 证件柜台关（起飞前 40 分钟；存档，没核）" in said
    assert [line for line in said if "05:45" in line] == ["你计划留的余量：拿非欧盟护照的，明早 05:45 前到证件柜台排队最稳（App 里验证通过就不用去）"]
    # the tickets as a sentence: no record text in brackets, an estimate with “约”, no “/人” for fares of one person
    assert found["tickets"]["text"] == "票：已买 24 小时市内票（市内的车）；现场买机场段补票（市界 → 机场），约 €2.30，买不到就买全程票，约 €5.50"
    assert found["tickets"]["party"] == 1
    # a tram is a route; the next connection after tonight's end is when to leave, never a first ride's line
    assert "00:33 18 路 Wien Matzleinsdorfer Platz → Wien Hauptbahnhof 00:45" in said[1]
    through = next(line for line in said if line.startswith("03:00 前最后开的是"))
    assert re.search(r"03:00 之后也有车，下一班 凌晨 \d\d:\d\d 出门$", through)
    # no lookup words: the covered search is said by these lines, the plan's train “赶得上”
    assert "计划里的 22:53 REX 7 赶得上：22:30 出门" in said
    assert not any(re.search(r"连续查全|在这次的查询结果里|/人", line) for line in said)
    assert problems(service, second_part(said[0], *said[1:])) == {}
    # bad samples (the replay's lines): the lookup's words, a price per person for one traveller
    bad = problems(service, second_part(said[0], "从 21:01 到 03:00 连续查全，56 班；03:00 之后出发的没查",
                                        "计划里的 22:53 REX 7 在这次的查询结果里（22:30 出门）", "现场买：机场段补票 €2.30/人（估的）"))
    assert len(bad["no_process_notes"]) == 2 and bad["per_person_alone"] == ["一个人出行，价钱后面写了“/人” | 去掉“/人”，照 tickets 的句子写"]
    # control: fares for two people are per person
    pair = [dict(booking, subject=["p1", "p2"], fares=[dict(fare, subject=["p1", "p2"]) for fare in booking["fares"]]) for booking in TICKETS]
    two = make(bookings=pair)
    shared = query(two, "latest_departure", origin=STREET, destination="airport")
    assert "约 €2.30/人" in shared["tickets"]["text"] and shared["tickets"]["party"] == 2
    assert "per_person_alone" not in problems(two, second_part(shared["first_line"], "现场买：机场段补票 €2.30/人（估的）"))


# --- 2. the lookup starts where the question goes next -------------------------------------------------------------------------

def test_the_lookup_starts_where_this_question_goes_next(make, monkeypatch):
    service = make(places=(NORTH, SOUTH, *STATION_FOOD))
    fixed(monkeypatch, local("21:52"))
    turn = service.turn("local", SECOND, "q2", scenario="quick", points=ASKED)
    # the meal has no place: the first open place the first part names is where the user goes next
    assert turn["points"]["next"] == dict(kind="eat", said="有点饿想吃点东西", place="hbf-burger", name="汉堡店")
    found = query(service, "latest_departure", origin="gym-south", destination="airport")
    assert found["resolved"]["origin"]["said"] == "gym-south" and found["resolved"]["origin"]["next_step"]["place"] == "hbf-burger"
    assert found["sentences"][1] == "按你先去汉堡店算（你说的“有点饿想吃点东西”）"
    # from the station the last train is boarded there (the burger place closes at midnight, A16 3), and “怎么查” is the
    # plan's 22:53 there; bad sample (the replay's): the gym's stop and its time
    assert found["first_line"].endswith("火车最后一班 00:53 REX 7（最晚 00:53 在 Wien Hauptbahnhof 上车）")
    how = found["how_to_check"]["text"]
    assert "搜 Wien Hauptbahnhof → Flughafen Wien Bahnhof，出发时间填 22:53" in how
    ending = ["## 怎么查、怎么问"]
    bad = problems(service, second_part(found["first_line"], *ending, "出发前在 ÖBB App 搜 Wien Matzleinsdorfer Platz → Flughafen Wien Bahnhof，出发时间填 22:33。", at="21:52"))
    assert bad["search_matches_main"][0].startswith("“怎么查”搜的是 22:33 从 Wien Matzleinsdorfer Platz 上车，这一问的主选第一程是 22:53 从 Wien Hauptbahnhof 上车")
    assert "search_matches_main" not in problems(service, second_part(found["first_line"], *ending, *how.splitlines(), at="21:52"))
    # controls: a lookup from elsewhere, or after the question's half hour, starts where it was asked from
    elsewhere = query(service, "latest_departure", origin="gym-north", destination="airport")
    assert "next_step" not in elsewhere["resolved"]["origin"]
    fixed(monkeypatch, local("22:30"))
    late = query(service, "latest_departure", origin="gym-south", destination="airport")
    assert "next_step" not in late["resolved"]["origin"]
    # and a question that names no next place (only when to leave) changes nothing
    plain = make(places=(NORTH, SOUTH, *STATION_FOOD))
    plain.turn("local", FIRST, "q1", scenario="quick", points=GO)
    assert "next_step" not in query(plain, "latest_departure", origin="gym-north", destination="airport")["resolved"]["origin"]


# --- 3. no time to leave after the origin closes ---------------------------------------------------------------------------------

def test_no_time_to_leave_is_later_than_the_origin_closes(make):
    service = make(places=(dict(NORTH, opening_hours=["Mo-Su 09:00-22:10"]), SOUTH, *FOOD))
    found = query(service, "latest_departure", origin="gym-north", destination="airport")
    said = found["sentences"]
    assert found["origin_closes"] == "22:10"
    # the plan's train leaves 23 minutes after closing: out at closing, then to the stop; the last train an hour later
    # is its boarding alone (the replay: “22:00 关门” beside “22:11 出门”)
    # S4h2: the plan's own way from the gym (21:45, the plan's tracks) is said beside it
    assert "计划里的 22:53 REX 7 赶得上：22:10 关门出来（按存的营业时间），走到 Wien Matzleinsdorfer Platz 坐 22:33 的 D；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走" in said
    assert said[0].endswith("火车最后一班 00:53 REX 7（最晚 00:33 在 Wien Matzleinsdorfer Platz 上车）")
    assert said[1].startswith("这班火车怎么坐：00:33 在 Wien Matzleinsdorfer Platz 上车，换 1 次车：00:33 D")
    assert not any(re.search(r"2[2-3]:[1-5]\d 出门|0\d:\d\d 出门", line) for line in said[:3])
    assert problems(service, second_part(said[0], *said[1:])) == {}
    # bad samples: the replay's shape, a time to leave the lookup found but after the closing
    bad = problems(service, second_part(said[0], "计划里的 22:53 REX 7 赶得上：22:30 出门"))
    assert bad["leave_before_closing"] == ["写 22:30 出门，起点 22:10 就关门 | 照 latest_departure 的句子写“22:10 关门出来（按存的营业时间），走到 … 坐 HH:MM 的车”，"
                                           "或写从关门时刻起算的那班"]
    # control: no stored hours, nothing to hold the time to
    open_late = make()
    plain = query(open_late, "latest_departure", origin="gym-north", destination="airport")
    assert "计划里的 22:53 REX 7 赶得上：22:30 出门；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走" in plain["sentences"] and plain["origin_closes"] is None
    assert "leave_before_closing" not in problems(open_late, second_part(plain["first_line"], "计划里的 22:53 REX 7 赶得上：22:30 出门"))


# --- 4. “怎么查” is the first choice's first ride ----------------------------------------------------------------------------------

def test_how_to_check_is_the_first_ride_of_the_first_choice(make):
    service = make()
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    how = found["how_to_check"]["text"].splitlines()
    assert "搜 Wien Matzleinsdorfer Platz → Flughafen Wien Bahnhof，出发时间填 22:33" in how[0]
    ending = ["## 怎么查、怎么问"]
    assert problems(service, second_part(found["first_line"], *ending, *how)) == {}
    # bad samples: the right time from another stop; the right stop at the time to leave the door
    stop = problems(service, second_part(found["first_line"], *ending, "搜 Wien Hauptbahnhof → Flughafen Wien Bahnhof，出发时间填 22:33。"))
    door = problems(service, second_part(found["first_line"], *ending, "搜 Wien Matzleinsdorfer Platz → Flughafen Wien Bahnhof，出发时间填 22:30。"))
    assert stop["search_matches_main"][0].startswith("“怎么查”搜的是 22:33 从 Wien Hauptbahnhof 上车")
    assert door["search_matches_main"][0].startswith("“怎么查”搜的是 22:30 从 Wien Matzleinsdorfer Platz 上车")
    # control: the link carries the same stop and time; the “误了主选” line is the second search
    link = next(line for line in how if line.startswith("误了主选"))
    url = found["how_to_check"]["link"]["url"]
    assert "Matzleinsdorfer" in url and "time=22:33" in url
    assert problems(service, second_part(found["first_line"], *ending, how[0], url, link)) == {}
    assert "search_matches_main" in problems(service, second_part(found["first_line"], *ending, url.replace("22:33", "22:30")))


# --- 5. the first part gives the plan's own time to leave and way ----------------------------------------------------------------

def test_the_first_part_gives_the_stored_way_to_the_planned_train(make):
    service = make(tracks=WAYS)
    lines = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]["lines"]
    assert lines[0] == ("去维也纳机场：计划里是 21:45 从北边的岩馆出门，22:13 到 Wien Hauptbahnhof，坐今晚 22:53 REX 7（存档，没核）；"
                        "还有没有更晚的车，第二段核完给。")
    assert lines[1] == "按现在 21:01 算，假设你在北边的岩馆（计划里这时在这；不在的话说一声）"
    assert lines[2] == "怎么走（存档，没核）：步行 5′ 到 Beispielplatz，21:51 D → Wien Hauptbahnhof 22:03"
    assert first_checks(service, "\n".join(lines), "q1") == {}
    # bad sample (both MCP runs): the stored train alone, no time to leave and no way although the plan stores both
    assert "21:45" in lines[0] and "怎么走" in lines[2]
    # controls: from another place, or once the way has begun, nothing stored applies: part 2 gives it, nothing is made up
    there = service.turn("local", SECOND, "q2", scenario="quick", points=[dict(kind="at", said="刚从第一个岩馆出来", place="gym-south"), *GO])
    assert there["first_part"]["lines"][0] == "去维也纳机场：计划里是今晚 22:53 在 Wien Hauptbahnhof 坐 REX 7（存档，没核）；几点出门、怎么走，第二段核完给。"
    late = make(at="21:50", tracks=WAYS).turn("local", FIRST, "q3", scenario="quick", points=GO)["first_part"]["lines"]
    assert "几点出门、怎么走" in late[0] and not any(line.startswith("怎么走") for line in late)


# --- 6. a place said in other words ----------------------------------------------------------------------------------------------

def test_a_place_in_other_words_is_resolved_from_the_stored_names(make, monkeypatch):
    service = make(at="21:52", places=(NORTH, SOUTH, *STATION_FOOD))
    plan = service.versions.current("local")["plan"]
    # bad sample (on_r1): the host passed its own name for the gym; the old match found nothing, the first part asked again
    said = [dict(kind="at", said="从 Boulder Hall South 刚出来", place="Boulder Hall South"), dict(kind="eat", said="有点饿")]
    assert match_place(plan, "Boulder Hall South") is None
    lines = service.turn("local", SECOND, "q6", scenario="quick", points=said)["first_part"]["lines"]
    assert lines[1] == "按现在 21:52 算，你在南边的岩馆（你说的“从 Boulder Hall South 刚出来”）"
    assert lines[0].startswith("吃：汉堡店（总站店，一层）开到 00:00")
    # the user's words alone do it too; the ID as before
    assert packs.resolve_place(plan, None, "刚从 Boulder Hall South 出来")["id"] == "gym-south"
    assert packs.resolve_place(plan, "gym-north")["id"] == "gym-north"
    # controls: words like no stored name, or like two, resolve to nothing (never a guess)
    assert packs.resolve_place(plan, "Some Other Hall") is None
    east = dict(NORTH, id="gym-east", name="东边的岩馆", name_local="Kletterhalle Ost, Beispielgasse 9, 1030 Wien", coords=[48.20, 16.39])
    twins = dict(plan, places=[*plan["places"], dict(east, area=plan["places"][0]["area"])])
    assert packs.resolve_place(twins, "Kletterhalle") is None
    assert packs.resolve_place(plan, "Kletterhalle")["id"] == "gym-north"


# --- 8. without the tools ----------------------------------------------------------------------------------------------------------

def test_without_the_tools_the_command_line_drafts_the_same_first_part(make, capsys):
    service = make(tracks=WAYS)
    received = len(service.store.all("events", "local"))
    code = cli.main(["--db", str(service.db_path), "first-part", "--text", FIRST, "--points", json.dumps(GO, ensure_ascii=False)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and len(service.store.all("events", "local")) == received  # nothing recorded
    assert "不发任何状态句" in out["next"] and "用户看不到工具结果" in out["next"]
    turn = service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    assert out["first_part"]["text"] == turn["first_part"]["text"]
    # no last or latest time before this question's lookup; the draft passes the first part's checks
    assert not re.search(r"最晚|末班|最后一班", out["first_part"]["text"]) and first_checks(service, out["first_part"]["text"], "q1") == {}
    # bad sample (off_r1): the host's own first part from the plan's files
    host = ("最晚 21:45 从北边的岩馆出门，坐 22:53 REX 7 去机场（这是计划里存的车次，还没核）\n按现在 21:01 算，假设你还在北边的岩馆\n"
            "计划里存的最后一班是 23:53 REX → 00:15 到机场；还有没有更晚的车，第二段确认")
    bad = [entry for entry in check_draft(host, "first", 21 * 60 + 1) if entry["check"] == "latest_needs_lookup"]
    assert [entry["line"] for entry in bad] == [1, 3]
    # control: an earlier lookup of tonight, quoted with when it was looked up
    quoted = "去维也纳机场：今晚不用赶，夜里一直有车；火车最后一班 00:53 REX 7，Wien Hauptbahnhof 开（21:01 查过）\n按现在 21:52 算。"
    assert not [entry for entry in check_draft(quoted, "first", 21 * 60 + 52) if entry["check"] == "latest_needs_lookup"]
    # the command line refuses a message it cannot draft for
    assert cli.main(["--db", str(service.db_path), "first-part", "--points", "[]"]) == 1
