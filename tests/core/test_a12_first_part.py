"""A12 (quality judge 6, A E F): the first part answers this question, the stored tickets are used, the second part is
short. Each with a bad sample in the replay's shape and a legal control.

- A: the first part was the plan's airport template whatever was asked. The user's second question (just out of a
  climbing gym, hungry, would like to see Halloween events in town, must they rush to the airport?) got the same lines as
  the first (“最晚几点去机场”), with the clock changed. Now the host passes the question's points to `turn`; the first part
  answers each one from what the trip stores, line 2 says where the user is, and check_answer holds it to them.
- E: a stored 24-hour ticket with its €2.30 airport supplement, while every connection said “票价和通票没查” and the
  answer said “市内票可能不够”. Now the lookup says the stored tickets once, and the routes say need=stored.
- F: the second part ran to about 20 body lines. check_answer counts them as quality card 3 does; the server's sentences
  say the last train and the next time to keep once.

Offline: the fictional MOTIS of test_a10_frame (public Vienna stop names, made-up times), fictional gyms, places to eat
and tickets."""
from __future__ import annotations

import asyncio
import sys
from copy import deepcopy

import httpx
import pytest

from test_a8_transit_facts import NIGHT, local
from test_a10_frame import DESK, QUEUE, REX, Evening, airport_plan, fixed, problems, query, second_part
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.render.check_answer import check_draft
from tripwright.render.first_part import draft
from tripwright.service import Tripwright
from tripwright.storage import Store

BUSES = ("00:15", "00:45", "01:15", "01:45", "02:15", "02:45", "03:15", "03:45")  # airport buses all night
NORTH = dict(id="gym-north", name="北边的岩馆", name_local="Kletterhalle Nordbahn, Beispielgasse 1, 1200 Wien", kind="climbing",
             coords=[48.2315, 16.3679])  # where the plan has the user in the evening (the replay's second gym)
SOUTH = dict(id="gym-south", name="南边的岩馆", name_local="Boulderhalle Süd, Beispielgasse 2, 1050 Wien", kind="climbing",
             coords=[48.1890, 16.3520])  # where the user says they just left, 1.9 km from the station (the replay's first gym)
FOOD = [dict(id="hbf-burger", name="汉堡店（总站店，一层）", name_local="Burger Beispiel, Wien Hauptbahnhof", kind="food",
             coords=[48.1851, 16.3762], opening_hours=["Mo-Su 06:00-24:00"]),
        dict(id="hbf-market", name="超市小店（总站，一层）", name_local="Markt Beispiel, Wien Hauptbahnhof", kind="food",
             coords=[48.1851, 16.3762], opening_hours=["Mo-Su 06:00-23:00"]),
        dict(id="hbf-snack", name="小吃摊（总站，地下一层）", name_local="Imbiss Beispiel, Wien Hauptbahnhof", kind="food",
             coords=[48.1851, 16.3762], opening_hours=["Mo-Su 09:00-21:00"])]  # closed at 21:52
EVENING = [dict(id="b-north", start="18:00", end="21:50", kind="visit", place="gym-north")]
TICKETS = [dict(id="bk-day", date="2026-11-04", kind="ticket", what="24 小时市内票：11-04 13:50 买，到 11-05 13:50", subject=["p1"],
                status="bought", fares=[dict(id="f-day", segment="市内的车，盖到去机场火车的市内段", ticket="24 小时市内票", subject=["p1"],
                                             amount_per_person=9.7, currency="EUR")]),
           dict(id="bk-airport", date="2026-11-04", kind="ticket", what="去机场：机场段补票", subject=["p1"], status="on_site", place="hbf",
                fares=[dict(id="f-airport", segment="市界 → 机场", ticket="机场段补票", subject=["p1"], amount_per_person=2.3,
                            currency="EUR", estimated=True),
                       dict(id="f-airport-full", segment="补票买不到时：总站 → 机场", ticket="全程票", subject=["p1"],
                            amount_per_person=5.5, currency="EUR", estimated=True, alternative=True)])]
# the replay's two questions, without personal data
FIRST = "今晚到明天 最晚几点出发机场 怎么走"
SECOND = "我现在刚从第一个岩馆出来，有点饿想吃点东西，还想去市区看看有没有万圣节活动。你的意思是我得赶紧去机场了吗？"
ASKED = [dict(kind="at", said="刚从第一个岩馆出来", place="gym-south"), dict(kind="eat", said="有点饿想吃点东西"),
         dict(kind="do", said="市区万圣节活动"), dict(kind="go", said="是不是得赶紧去机场", place="airport")]


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []

    def build(upstream=None, *, at="21:01", today=(REX,), tomorrow=(QUEUE, DESK), places=(NORTH, SOUTH, *FOOD), bookings=(),
              blocks=EVENING):
        moment = local(at)
        fixed(monkeypatch, moment)
        store = Store(tmp_path / f"a12-{len(made)}.sqlite3")
        transit = TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport((upstream or Evening(trains=NIGHT[:5], buses=BUSES)).handle)),
                                     min_interval=0, clock=lambda: moment)
        service = Tripwright(store, query_router=QueryRouter(store, transit=transit))
        made.append((service, store))
        plan = airport_plan([deepcopy(anchor) for anchor in today], [deepcopy(anchor) for anchor in tomorrow])
        area = plan["places"][0]["area"]
        plan["places"] += [dict(deepcopy(place), area=area) for place in places]
        plan["bookings"] = deepcopy(list(bookings))
        plan["days"][0]["plans"] = [dict(name="Recommended", tracks=[dict(subject=["p1", "p2", "p3"], blocks=deepcopy(list(blocks)))])]
        turn = service.turn("local", "Create the trip.", "seed")
        made_draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
        service.commit("local", made_draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def checked(service, text: str, part: str = "first", request_id: str = "q2") -> dict[str, list[str]]:
    found = {}
    for entry in query(service, "check_answer", text=text, part=part, request_id=request_id)["problems"]:
        found.setdefault(entry["check"], []).append(entry["why"])
    return found


def two_questions(service, monkeypatch):
    """The replay's order: the first question at 21:01 (its second part looked tonight up from the gym the plan has the
    user in), the second at 21:52."""
    first = service.turn("local", FIRST, "q1", scenario="quick", points=[dict(kind="go", said="最晚几点出发去机场", place="airport")])
    first["checked"] = checked(service, first["first_part"]["text"], request_id="q1")
    query(service, "latest_departure", origin="gym-north", destination="airport")
    fixed(monkeypatch, local("21:52"))
    return first, service.turn("local", SECOND, "q2", scenario="quick", points=ASKED)


# --- A. the first part answers this question ---------------------------------------------------------------------------------

def test_the_first_part_answers_each_point_of_the_question(make, monkeypatch):
    service = make()
    first, second = two_questions(service, monkeypatch)
    lines = second["first_part"]["lines"]
    # line 1 answers the most urgent: the first question's lookup says services run all night (no hurry); not from the
    # south gym, so no latest time to leave from here
    assert lines[0] == "按存的计划先说（时刻还没核）"
    assert lines[1] == "去维也纳机场：今晚不用赶，夜里一直有车；火车最后一班 00:53 REX 7，Wien Hauptbahnhof 开（21:01 查过）"
    assert lines[2] == "按现在 21:52 算，你在南边的岩馆（你说的“刚从第一个岩馆出来”）"
    assert lines[3] == "吃：汉堡店（总站店，一层）开到 00:00、超市小店（总站，一层）开到 23:00（存的营业时间，最近的离你约 1.9 公里）"
    assert "市区万圣节活动：没存，第二段给。" not in lines and not lines[-1].startswith("没核：") and len(lines) <= 8
    assert "计划里写过：22:53" not in second["first_part"]["text"]  # the plan's train: tonight's lookup answers the airport
    assert "明早 07:05 证件柜台关（存档，没核）" in lines
    assert [point["kind"] for point in second["first_part"]["points"]] == ["go", "eat", "do"]
    facts = {point["kind"]: point["facts"] for point in second["points"]["points"]}
    assert facts["go"]["frame"]["fetched"] == "21:01" and facts["go"]["frame"]["origin"] == "gym-north"
    assert [row["id"] for row in facts["eat"]["open"]] == ["hbf-burger", "hbf-market"] and facts["eat"]["stored"] == 3
    assert second["second_part_title"] == "核过之后"
    # bad sample: the A11 first part, the plan's template with the second question's clock (what the replay's host sent)
    template = draft(SECOND, second["now"], second["hard_nodes"], service.versions.current("local")["plan"])["text"]
    assert template.startswith("按现在 21:52 算：几点出发要查，第二段给。\n计划里写过：22:53")
    bad = checked(service, template)
    assert bad["answers_each_point"] == ["这一问点名的“有点饿想吃点东西”、“市区万圣节活动”第一段没回应"]
    assert bad["place_in_first_lines"] == ["用户说了在哪（“刚从第一个岩馆出来”），前两行没写"]
    # control: the first question (only when to leave for the airport): the plan's stored train first, archived, the real
    # latest from part 2 (A14), the assumed place from the user's own blocks, the next time to be at the airport
    assert first["first_part"]["lines"] == [
        "按存的计划先说（时刻还没核）",
        "去维也纳机场：计划里是今晚 22:53 在 Wien Hauptbahnhof 坐 REX 7（存档，没核）；几点出门、怎么走，第二段核完给。",
        "按现在 21:01 算，假设你在北边的岩馆（计划里这时在这；不在的话说一声）",
        "明早 07:05 证件柜台关（存档，没核）"]
    assert "latest_departure" in second["next"] and not second["next"].startswith("这一问没给 points")






def test_a_stored_time_or_price_quoted_without_its_mark_is_refused(make):
    """Quality judge 6, on_r1: “07:05 Visa/Document check 柜台关” went out as a fact; the turn had it `checked: null`."""
    service = make(bookings=TICKETS)
    turn = service.turn("local", "还要守什么时间？票要补吗？", "q6", scenario="quick",
                        points=[dict(kind="ticket", said="票要补吗"), dict(kind="other", said="还要守什么时间")])
    text = turn["first_part"]["text"]
    assert turn["first_part"]["lines"][0] == "按存的计划先说（时刻还没核）"
    stored = next(line for line in turn["first_part"]["lines"] if line.startswith("票："))
    bad = checked(service, text.replace("明早 07:05 证件柜台关（存档，没核）", "明早 07:05 证件柜台关")
                  .replace(stored, "票：补机场段，€2.30 一人"), request_id="q6")
    assert bad["archived_marked"] == ["“2.30”是存的、没核过，这行没标", "“07:05 证件柜台关”是存的、没核过，这行没标"]
    # controls: the plan's own target needs no mark; the same words checked in part 2 are not this check's
    assert "archived_marked" not in checked(service, "你计划明早 05:45 前到（留的余量）：到证件柜台排队", request_id="q6")
    assert check_draft("明早 07:05 证件柜台关", "first", 21 * 60 + 1) == check_draft("明早 07:05 证件柜台关", "first", 21 * 60 + 1, points=None)


def test_points_are_refused_with_their_path(make):
    service = make()
    with pytest.raises(ValueError, match=r"Invalid points: points\.0\.kind: Input should be 'at', 'go'"):
        service.turn("local", SECOND, "q7", points=[dict(kind="hungry", said="饿")])
    with pytest.raises(ValueError, match="at most 7 points with one 'at'"):
        service.turn("local", SECOND, "q8", points=[dict(kind="at", said="这里"), dict(kind="at", said="那里")])


def test_points_reach_the_service_over_mcp(tmp_path):
    """Harness neutral (R-62, R-69): the points are an argument of the turn tool, the draft and facts its result."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from test_b3d_mcp import environment
    from test_m1_mcp import call

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(tmp_path / "a12.sqlite3"),
                                                                         "serve", "--stdio"], env=environment())
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                turn = await call(session, "turn", dict(text="有点饿", request_id="a12-1", scenario="quick",
                                                        points=[dict(kind="eat", said="有点饿")]))
                return tools["turn"], turn

    tool, turn = asyncio.run(scenario())
    schema = tool.input_schema["properties"]["points"]
    point = next(option for option in schema["anyOf"] if option.get("type") == "array")["items"]
    assert point["type"] == "object" and "properties" not in point
    assert "points" in tool.description and "answers_each_point" not in tool.description
    assert turn["first_part"] is None  # no plan yet: nothing to answer from


# --- E. the stored tickets are used ----------------------------------------------------------------------------------------

def test_the_lookup_says_the_stored_tickets_once(make, monkeypatch):
    service = make(bookings=TICKETS)
    found = query(service, "latest_departure", origin="gym-south", destination="airport")
    # A16: one sentence, no record text, the estimate said with “约”, no “/人” for fares of one person
    said = "票：已买 24 小时市内票（市内的车）；现场买机场段补票（市界 → 机场），约 €2.30，买不到就买全程票，约 €5.50"
    assert found["tickets"]["text"] == said and found["sentences"].count(said) == 1
    assert found["last_train"]["ticket"] == dict(need="stored", why="按这趟存的票")
    assert not any("票价和通票没查" in sentence for sentence in found["sentences"])
    # the turn's points carry them; a ticket point's line is the stored sentence, marked stored
    fixed(monkeypatch, local("21:52"))
    turn = service.turn("local", "去机场票要补吗", "q9", scenario="quick",
                        points=[dict(kind="go", said="去机场", place="airport"), dict(kind="ticket", said="票要补吗")])
    go = next(point for point in turn["points"]["points"] if point["kind"] == "go")
    assert go["facts"]["tickets"]["text"] == said and said in turn["first_part"]["lines"]
    # bad sample (the replay's shape): without stored tickets every route says fares and passes were not checked
    plain = query(make(), "latest_departure", origin="gym-south", destination="airport")
    assert plain["tickets"] is None and plain["last_train"]["ticket"] == dict(need="unknown", why="票价和通票没查")
    # control: a coach still needs its own ticket, stored tickets or not
    coach = query(make(Evening(coaches=("01:25",)), bookings=TICKETS), "latest_departure", origin="hbf", destination="airport")
    assert coach["last"]["ticket"]["need"] == "extra"


# --- F. the second part is short -------------------------------------------------------------------------------------------

# on_r1's second part to the second question, shape only (17 body lines here; the replay's had 21: places, links and the
# plan's items not counted)
LONG = """# 第二段 · 查完的走法（21:52）
今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:30 出门）
按现在 21:52 算，从南边的岩馆出发。
不用马上去机场：先吃饭、去市区都来得及。
计划里写过“最晚 21:50 出门赶 22:53”（存档，没核），你现在不在那，这条不用管。
## 吃的
Burger Beispiel, Wien Hauptbahnhof
汉堡店，总站一层 · 开到 00:00 · Google Maps 搜 Burger Beispiel
营业时间是计划里存的。
## 万圣节
网上说法（某新闻站）：某酒吧万圣节派对 21:00 开到 05:00。
网上说法：公园的免费活动 14:00–17:00，今天已经结束。
搜索摘要：另一处派对 22:00 起，门票约 €30（没核）。
## 怎么走（核过）
以上交通时刻按时刻表，不是实时。
岩馆去市中心：21:55 出门，坐 13A 再换 U1。
从岩馆直接去机场：最晚 00:30 出门，00:33 D 到总站，换 00:53 REX 7。
从市中心赶 00:53 REX 7：00:38 出门。
误了它：03:15 VAB 1。
## 完整安排
现在去总站吃饭，再坐 U1 到市中心。
最晚 00:38 从市中心走赶 00:53 REX 7。
没核：派对能不能现场进 → 可能白跑 → 到门口问。
没核：实时晚点、停运 → 出发前在交通 App 看。
## 怎么查、怎么问
出发前在交通 App 查：搜 Wien Hauptbahnhof → Flughafen Wien Bahnhof，出发时间填 00:53
https://fahrplan.example/query?S=Wien+Hauptbahnhof&Z=Flughafen+Wien&time=00:53
我可以帮你起草询问内容（英语、德语）"""
SHORT = """# 第二段 · 核过了（21:55）
今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:30 出门）
按现在 21:55 算，从南边的岩馆出发；和第一段一样，不用现在去机场。
先在总站吃：
Burger Beispiel, Wien Hauptbahnhof
汉堡店，总站一层 · 开到 00:00 · Google Maps 搜 Burger Beispiel
万圣节：网上说法（某新闻站）：某酒吧派对 21:00 开到 05:00，18 岁以上。
以上交通时刻按时刻表，不是实时。
## 完整安排
21:55 出门，坐 13A 到总站吃饭。
23:00 坐 U1 到市中心。
00:38 从市中心走，00:53 REX 7 去机场。
没核：派对能不能现场进 → 到门口问。
## 怎么查、怎么问
出发前在交通 App 搜 Wien Hauptbahnhof → Flughafen Wien Bahnhof，出发时间填 00:53 · https://fahrplan.example/q?time=00:53
我可以帮你起草询问内容（英语、德语）"""


def test_a_second_part_is_not_refused_for_its_length():
    # 11-04 (checker audit: the card judges content, not line counts): second_part_lines is gone; 17 body lines pass
    for text in (LONG, SHORT):
        assert "second_part_lines" not in {entry["check"] for entry in check_draft(text, "second", 21 * 60 + 52)}


# --- a12b: from the A12 self-replay ------------------------------------------------------------------------------------------

def test_a_go_place_the_trip_does_not_store_is_never_named_with_the_users_words(make):
    """Replay: points [{kind: go, said: “最晚几点出发机场 怎么走”, place: “机场”}] gave “去最晚几点出发机场 怎么走：真正最晚要查”."""
    service = make()
    said = "最晚几点出发机场 怎么走"
    # “机场” is no place of the trip, but one place the hard nodes go to is stored with that word: that one names it
    word = service.turn("local", FIRST, "q10", scenario="quick", points=[dict(kind="go", said=said, place="机场")])
    assert word["first_part"]["lines"][1] == "去维也纳机场：计划里是今晚 22:53 在 Wien Hauptbahnhof 坐 REX 7（存档，没核）；几点出门、怎么走，第二段核完给。"
    assert next(point for point in word["points"]["points"])["place"] == "airport"
    # a word for several stored places, or none: no name at all, never the user's words (S4h-a: the user's “机场” is a word for
    # a kind of place, and names the one stored airport; words without one name nothing)
    vague = "最晚几点出发 怎么走"
    for request, point in (("q11", dict(kind="go", said=vague, place="维也纳")), ("q12", dict(kind="go", said=vague))):
        bare = service.turn("local", FIRST, request, scenario="quick", points=[point])
        assert bare["first_part"] is None
    kind = service.turn("local", FIRST, "q14", scenario="quick", points=[dict(kind="go", said=said, place="维也纳")])
    assert kind["first_part"]["lines"][1].startswith("去维也纳机场：") and said not in kind["first_part"]["text"]
    # control: a place ID names the stored place, as before
    named = service.turn("local", FIRST, "q13", scenario="quick", points=[dict(kind="go", said=said, place="airport")])
    assert named["first_part"]["lines"][1] == "去维也纳机场：计划里是今晚 22:53 在 Wien Hauptbahnhof 坐 REX 7（存档，没核）；几点出门、怎么走，第二段核完给。"


RJX = dict(id="hn-rjx", time="22:42", text="在总站坐 RJX 去机场", subject=["p1"], place="hbf", destination="airport", line="RJX 19959")


def test_the_plans_own_train_is_said_to_be_in_these_results_or_not(make):
    """Replay: “和第一段不同：计划里写的 22:42 RJX 19959 这次没查到，以下面的班次为准” — that lookup's departures had it."""
    service = make(today=[REX, RJX])
    found = query(service, "latest_departure", origin="hbf", destination="airport")
    found_line = "计划里的 22:53 REX 7 赶得上：22:53 出门，不用换车：22:53 REX 7（往 Flughafen Wien）Wien Hauptbahnhof → Flughafen Wien Bahnhof 23:15；23:15 到"
    missing = next(sentence for sentence in found["sentences"] if sentence.startswith("计划里的 22:42"))
    assert found_line in found["sentences"]
    assert missing.startswith("计划里的 22:42 RJX 19959 这次没在时刻表数据里找到（可能没收录，或车次号不同）：要坐它，先在 ")
    assert missing.endswith("核一下") and "为准" not in missing and "没查到" not in missing
    assert [(ride["anchor_id"], ride["found"]) for ride in found["plan_rides"]] == [("hn-rex", True), ("hn-rjx", False)]
    # another number for the same kind of train at that minute is the plan's train, said so
    renamed = query(make(today=[dict(REX, line="REX 22779")]), "latest_departure", origin="hbf", destination="airport")
    assert ("计划里的 22:53 REX 22779 赶得上（时刻表里叫 REX 7）：22:53 出门，不用换车：22:53 REX 7（往 Flughafen Wien）Wien Hauptbahnhof "
            "→ Flughafen Wien Bahnhof 23:15；23:15 到") in renamed["sentences"]
    # bad samples (the replay's line): a found train called not found; a missing one with “以下面的为准”
    bad = problems(service, second_part(found["first_line"], "和第一段不同：计划里写的 22:53 REX 7 这次没查到，以下面的班次为准。"))
    assert bad["missing_ride_found"][0].startswith("说 22:53 REX 7 没查到，这一问的查询结果里有这班（22:53 出门）")
    gone = problems(service, second_part(found["first_line"], "和第一段不同：计划里写的 22:42 RJX 19959 这次没查到，以下面的班次为准。"))
    assert gone["missing_ride_found"][0].startswith("说那班没查到，又说以下面的为准")
    # controls: the server's sentences pass; “没查到” about the trains after one is not about that one
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:])) == {}
    assert "missing_ride_found" not in problems(service, second_part(found["first_line"], "误了 22:53 那班之后还有没有赶得上的，没查到"))
