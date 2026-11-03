"""A6 (D-43): the service checks a host's draft before it is sent. `query(kind="check_answer")` takes one part's text and
returns ok and one problem per failing line ({check, line, text, why, fix}), against the clock in the trip's zone and
this conversation's evidence. Each check fails on the shape the v2d replays and the second quality judgment showed and
passes a good line; the command line prints the same; latest_departure answers compactly by default. Offline: the
fictional MOTIS and web of A5, and a fixed moment. Fictional people, places and lines only."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pydantic_core
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import tripwright.events as events_module
import tripwright.service as service_module
import tripwright.storage as storage_module
from a5_upstream import DESTINATION, ORIGIN, Timetable, page_reader, z
from test_a3_anchor_deadline import anchored_plan
from test_b3d_mcp import environment
from tripwright.adapters.mcp import create_server
from tripwright.cli import main
from tripwright.query import QueryRouter
from tripwright.render import check_answer as checks
from tripwright.service import Tripwright
from tripwright.storage import Store

NOW = datetime(2030, 6, 10, 18, 45, tzinfo=timezone.utc)  # 20:45 in Oslo, before the evening's trains
PLAN = anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"})
RULE = dict(url="https://ferry.example.org/rules", quote="The ferry counter closes 40 minutes before departure.",
            claim="The counter closes 40 minutes before the ferry leaves", type="rule")
SNIPPET = dict(url="https://cafe.example.org/", quote="Harbour cafe open until 23:00", claim="The harbour cafe is open until 23:00",
               type="opening_hours", read_from="search_snippet")

GOOD_FIRST = """计划里 21:30 出门，坐 21:35 的车去码头（存档，没核）。
按现在 20:45 算，你在 Nordkai 附近。不在就告诉我在哪。

Fergeterminal
渡轮码头 · 今晚在这坐船 · Google 地图搜 Fergeterminal · [Google 地图](https://www.google.com/maps/search/?api=1&query=Fergeterminal)
没核：21:35 这班车，第二段核完再发。"""

GOOD_SECOND = """# 第二段 · 核过了（20:45）
和第一段不同：没有不同。
核过：最晚 21:30 出门，坐 21:35 REX 7 Nordkai → Fergeterminal，21:55 到。
核过：误了 21:35 还有 22:05 那班，22:25 到。
核过：柜台开船前 40 分钟关（原句：“The ferry counter closes 40 minutes before departure.”）。
网上说法：码头咖啡开到 23:00（搜索摘要）。
## 完整安排
21:30 出门 · 走 5 分钟到 Nordkai · Google 地图搜 Nordkai（交通时刻按时刻表，不是实时）"""


def fixed(monkeypatch, moment: datetime = NOW):
    """The service's clock, the web evidence's recorded time and the time a message is received at `moment` (check_answer
    reads this question from the messages of the last 30 minutes, D-48)."""
    monkeypatch.setattr(service_module, "now", lambda: moment.isoformat())
    monkeypatch.setattr(storage_module, "now", lambda: moment.isoformat())
    monkeypatch.setattr(events_module, "now", lambda: moment.isoformat())


def seed(path, *, timetable=None, lookups=True) -> None:
    """A trip with three late-evening hard nodes, and this evening's lookups recorded at NOW: the latest departure for
    the plan target, a page quote that counts and a search snippet that does not."""
    store = Store(path)
    router = QueryRouter(store, transit=(timetable or Timetable()).provider(clock=lambda: NOW), pages=page_reader())
    service = Tripwright(store, query_router=router)
    try:
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=PLAN, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        service.turn("local", "How late can I leave for the ferry?", "q1")
        if lookups:
            for kind, arguments in (("latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive")),
                                    ("web_evidence", RULE), ("web_evidence", SNIPPET)):
                asyncio.run(service.query("local", kind, arguments))
    finally:
        service.jobs.stop()
        store.close()


@pytest.fixture
def trip(tmp_path, monkeypatch):
    fixed(monkeypatch)
    path = tmp_path / "a6.sqlite3"
    seed(path)
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    yield service
    service.jobs.stop()
    store.close()


def check(service, text, part="second", **arguments):
    return asyncio.run(service.query("local", "check_answer", dict(text=text, part=part, **arguments)))


def failing(result) -> dict[str, list[int | None]]:
    found = {}
    for entry in result["problems"]:
        found.setdefault(entry["check"], []).append(entry["line"])
    return found


# --- good drafts pass --------------------------------------------------------------------------------------------------

def test_good_drafts_pass_and_problems_have_their_shape(trip):
    first = check(trip, GOOD_FIRST, "first")
    second = check(trip, GOOD_SECOND, request_id="q1")
    assert first["ok"] and second["ok"], (first["problems"], second["problems"])
    assert second["summary"] == "草稿可以发。" and second["now"]["time"] == "20:45" and second["now"]["timezone"] == "Europe/Oslo"
    assert len(second["evidence"]["ids"]) == 2  # the latest departure and the page quote; not the snippet
    bad = check(trip, GOOD_SECOND.replace("## 完整安排", "完整安排："))
    entry = bad["problems"][0]
    assert set(entry) == {"check", "line", "text", "why", "fix"} and (entry["line"], entry["text"]) == (7, "完整安排：")
    assert bad["summary"].startswith("要改 1 处再发：第 7 行 完整安排没有 Markdown 标题")


# --- second part: each check, bad and good -----------------------------------------------------------------------------

@pytest.mark.parametrize("line, why", [
    # judgment 2 / v2d: what a web page or a host's own reading says, written as 核过
    ("核过：柜台 07:05 关，登机口 07:15 关（航空公司网页）。", "时刻 07:05、07:15 不在这一趟工具查到的结果里"),
    ("核过：夜里地铁 15 分钟一班（城市网站）。", "这一趟没有对得上的工具证据"),
    ("核过：码头咖啡开到 23:00。", "时刻 23:00 不在这一趟工具查到的结果里"),  # only a search snippet says so
    ("核过：坐 21:35 那班，比 23:10 柜台关早得多。", "时刻 23:10 不在"),  # a timetable time and a stored deadline on one line
])
def test_a_checked_line_without_matching_evidence_is_refused(trip, line, why):
    result = check(trip, GOOD_SECOND.replace("核过：最晚 21:30 出门", line + "\n核过：最晚 21:30 出门"))
    problems = [entry for entry in result["problems"] if entry["check"] == "checked_has_evidence"]
    assert [entry["line"] for entry in problems] == [3] and why in problems[0]["why"], result["problems"]
    assert problems[0]["fix"].startswith("改成“网上说法（来源）”")
    # rewritten as the fix says, it passes
    assert check(trip, GOOD_SECOND.replace("核过：最晚 21:30 出门", line.replace("核过", "网上说法") + "\n核过：最晚 21:30 出门"))["ok"]


def test_a_named_evidence_id_must_be_one_that_counts(trip):
    ids = {record["kind"] + ("-snippet" if record.get("read_from") == "search_snippet" else ""): record["id"]
           for record in trip.store.all("evidence", "local")}
    named = check(trip, GOOD_SECOND + f"\n核过：码头咖啡开到 23:00（{ids['web-snippet']}）。")
    assert failing(named) == {"checked_has_evidence": [9], "internal_words": [9]} and ids["web-snippet"] in named["problems"][0]["why"]
    # A7: an evidence ID is an internal word in the answer; a counted one still backs the line
    assert failing(check(trip, GOOD_SECOND + f"\n核过：柜台开船前 40 分钟关（{ids['web']}）。")) == {"internal_words": [9]}


def test_evidence_older_than_the_session_does_not_back_a_draft(trip, monkeypatch):
    fixed(monkeypatch, NOW + timedelta(hours=8))
    later = check(trip, GOOD_SECOND.replace("（20:45）", "（04:45）"))
    assert set(failing(later)) == {"checked_has_evidence"} and later["evidence"]["ids"] == []
    with pytest.raises(ValueError, match="arguments.request_id: no turn with request_id 'nope'"):
        check(trip, GOOD_SECOND, request_id="nope")


def test_only_or_last_needs_a_window_that_reaches_the_time(tmp_path, monkeypatch):
    fixed(monkeypatch)
    path = tmp_path / "window.sqlite3"
    seed(path, lookups=False)
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, transit=Timetable().provider(clock=lambda: NOW)))
    try:
        # judgment 2, on_r1: "05:15 前只有这一班" from a lookup that stopped at 04:45
        asyncio.run(service.query("local", "transit", dict(origin=ORIGIN, destination=DESTINATION, when=z(NOW + timedelta(minutes=15)), limit=2)))
        plain = "# 第二段 · 核过了（20:45）\n和第一段不同：没有不同。\n## 完整安排\n21:00 出门 · 走 5 分钟到 Nordkai（按时刻表，不是实时）"
        bad = check(service, plain + "\n23:00 前只有这一班。")
        assert failing(bad) == {"only_inside_window": [5]} and "查询窗口只到 21:30" in bad["problems"][0]["why"]
        assert check(service, plain + "\n21:05 那班是 21:30 前最后一班。")["ok"]
    finally:
        service.jobs.stop()
        store.close()


@pytest.mark.parametrize("line, ok", [
    ("误了 22:05 就坐 21:35 那班。", False),  # judgment 2, on_r1: the fallback leaves before the missed train
    ("21:35 那班误了，还有 21:05 那班。", False),
    ("误了 22:05 还有 22:35 那班。", True),
])
def test_a_fallback_leaves_after_the_missed_choice(trip, line, ok):
    result = check(trip, GOOD_SECOND + "\n网上说法：" + line)
    assert ("fallback_later" not in failing(result)) is ok
    if not ok:
        assert result["problems"][0]["fix"].startswith("后备换成比")


@pytest.mark.parametrize("heading, ok", [("完整安排：", False), ("**完整安排**", False), ("## 完整安排", True)])
def test_the_full_plan_has_a_markdown_heading(trip, heading, ok):
    assert ("plan_heading" not in failing(check(trip, GOOD_SECOND.replace("## 完整安排", heading)))) is ok


@pytest.mark.parametrize("extra, check_name", [
    ("（行程助手这次没连上。）", "no_tags"),  # a tool state
    ("已收件，还没生效。", "no_tags"),
    ("<br>", "no_tags"),
    ("这班 counts_as_checked 为真。", "internal_words"),  # an internal field name (A7 moved it from no_tags)
    ("I'm still checking the ferry.", "no_tags"),  # v2d: an English status line
    ("[Google 地图](https://www.google.com/maps/search/?api=1&query=Nordkai_Bus)", None),  # a link is not read
])
def test_no_tags_or_internal_words(trip, extra, check_name):
    assert set(failing(check(trip, GOOD_SECOND + "\n" + extra))) == ({check_name} if check_name else set())


def test_the_title_time_is_not_later_than_now(trip):
    late = check(trip, GOOD_SECOND.replace("（20:45）", "（21:10）"))
    assert failing(late) == {"title_time_not_later": [1]} and "21:10 比现在 20:45 晚" in late["problems"][0]["why"]
    assert check(trip, GOOD_SECOND.replace("（20:45）", "（20:44）"))["ok"]


# --- first part ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("head, problem", [
    ("最晚 21:30 出门。\n按你还在码头算（计划里这时你在这）。", "前两行没有接近现在 20:45 的时刻"),  # replays r10, r11
    ("最晚 21:30 出门。\n按现在 19:40 算。", "写的现在 19:40 和钟 20:45 差超过 5 分钟"),  # r9: "now" from the plan
    ("最晚 21:30 出门。\n按现在 20:45 算（latest_departure 没跑）。", "证据号或字段名：latest_departure"),
    (GOOD_FIRST, None),
    ("不用现在就走，计划里 21:30 出门（存档，没核）。\n按现在 20:47 算。", None),  # "现在" as a word is not a clock claim
    # A16 (quality judge 8, off_r1): a last or latest time before this question's lookup, unless an earlier one is quoted
    ("最晚 21:30 出门。\n按现在 20:45 算。", "第一段写了“最晚”和时刻：这一问还没查过"),
    ("计划里存的最后一班是 23:53 REX。\n按现在 20:45 算。", "第一段写了“最后一班”和时刻：这一问还没查过"),
    ("去码头：今晚最后一班 23:05 REX 7，Nordkai 开（20:30 查过）\n按现在 20:45 算。", None),
])
def test_the_first_part_names_the_clock_and_no_internal_words(trip, head, problem):
    result = check(trip, head, "first")
    assert result["ok"] is (problem is None), result["problems"]
    if problem:
        assert any(problem in entry["why"] for entry in result["problems"]), result["problems"]


def test_wrong_arguments_name_their_field(trip):
    with pytest.raises(ValueError, match="arguments.part is required"):
        asyncio.run(trip.query("local", "check_answer", dict(text="x")))
    with pytest.raises(ValueError, match="arguments.part must be 'first' or 'second', got string"):
        check(trip, "x", "third")


# --- command line, stdio ---------------------------------------------------------------------------------------------------

def test_the_command_line_prints_what_the_mcp_tool_returns(tmp_path, monkeypatch, capsys):
    fixed(monkeypatch)
    path = tmp_path / "a6.sqlite3"
    seed(path)
    draft = tmp_path / "second.md"
    draft.write_text(GOOD_SECOND.replace("## 完整安排", "完整安排："), encoding="utf-8")
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    try:
        answer = asyncio.run(create_server(service, manage_jobs=False).call_tool(
            "query", dict(kind="check_answer", arguments=dict(text=draft.read_text(encoding="utf-8"), part="second", request_id="q1"))))
        over_tool = answer.structured_content
    finally:
        service.jobs.stop()
        store.close()
    assert main(["--db", str(path), "query", "check_answer", "--part", "second", "--request-id", "q1", "--text-file", str(draft),
                 "--providers", "synthetic"]) == 0
    printed = capsys.readouterr()
    assert json.loads(printed.out) == over_tool and over_tool["ok"] is False
    assert printed.err.strip() == over_tool["summary"]


async def probe(directory, draft) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            schema = {tool.name: tool.input_schema for tool in (await session.list_tools()).tools}["query"]
            turn = (await session.call_tool("turn", dict(text="Create the trip.", request_id="seed"))).structured_content
            made = (await session.call_tool("propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=PLAN, idempotency_key="seed"))).structured_content
            await session.call_tool("commit", dict(draft_id=made["draft_id"], idempotency_key="seed", accept_unverified=True))
            await session.call_tool("query", dict(kind="latest_departure", arguments=dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive")))
            checked = await session.call_tool("query", dict(kind="check_answer", arguments=dict(text=draft, part="second", request_id="seed")))
            return dict(kinds=schema["properties"]["kind"]["enum"], properties=set(schema["properties"]["arguments"]["properties"]),
                        error=checked.is_error, result=checked.structured_content)


def test_check_answer_over_real_stdio_and_the_command_line(tmp_path):
    # the clock of a real run is not 20:45: the title (it must carry a time, D-48) takes a time just before now in Oslo
    stamp = (datetime.now(ZoneInfo("Europe/Oslo")) - timedelta(minutes=3)).strftime("%H:%M")
    draft = GOOD_SECOND.replace("（20:45）", f"（{stamp}）")
    found = asyncio.run(probe(tmp_path, draft))
    assert "check_answer" in found["kinds"] and {"part", "request_id", "detail", "question"} <= found["properties"]
    assert not found["error"] and found["result"]["ok"] is False
    # demo data never backs 核过: the three checked lines are refused
    assert [entry["line"] for entry in found["result"]["problems"] if entry["check"] == "checked_has_evidence"] == [3, 4, 5]
    path = tmp_path / "draft.md"
    path.write_text(draft, encoding="utf-8")
    run = subprocess.run([sys.executable, "-m", "tripwright.cli", "--db", str(tmp_path / "stdio.sqlite3"), "query", "check_answer",
                          "--part", "second", "--text-file", str(path), "--providers", "synthetic"],
                         env=environment(), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["problems"] == found["result"]["problems"]
    # S4h-b: without --request-id the command line takes the latest turn (the seed turn the MCP call named) and says so
    assert "using the latest turn's request_id 'seed'" in run.stderr
    assert run.stderr.strip().splitlines()[-1].startswith("要改 3 处再发")


# --- latest_departure compact -------------------------------------------------------------------------------------------

class StreetTimetable(Timetable):
    """From a street address: walk to a tram, ride it, change on foot, take the train every 10 minutes, walk on."""

    def itinerary(self, train):
        trip = super().itinerary(train)
        walk, ride, onward = trip["legs"]
        start = datetime.fromisoformat(walk["startTime"].replace("Z", "+00:00")) - timedelta(minutes=14)
        tram_from, tram_to = start + timedelta(minutes=6), start + timedelta(minutes=16)
        legs = [dict(mode="WALK", **{"from": {"name": "Lindenallee 12, Nordstadt (fictional)"}}, to={"name": "Lindenplatz"},
                     startTime=z(start), endTime=z(tram_from)),
                dict(mode="TRAM", displayName="T4", headsign="Hafen über Altmarkt und Nordkai (fictional)", agencyName="Nordstadt Trams",
                     **{"from": {"name": "Lindenplatz", "track": "B"}}, to={"name": "Nordkai Vorplatz"}, startTime=z(tram_from), endTime=z(tram_to)),
                dict(walk, **{"from": {"name": "Nordkai Vorplatz"}}, startTime=z(tram_to)), ride, onward]
        return dict(trip, startTime=z(start), legs=legs)


def test_latest_departure_is_compact_by_default_and_full_on_request(tmp_path, monkeypatch):
    fixed(monkeypatch)
    trains = [f"{hour:02d}:{minute:02d}" for hour in (19, 20, 21, 22, 23) for minute in range(5, 60, 10)]
    timetable = StreetTimetable(trains)
    store = Store(tmp_path / "street.sqlite3")
    service = Tripwright(store, query_router=QueryRouter(store, transit=timetable.provider(clock=lambda: NOW)))
    try:
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=PLAN, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        arguments = dict(origin="Lindenallee 12, Nordstadt", destination=DESTINATION, anchor_id="hn-arrive", after="2030-06-10T19:00:00+02:00")
        compact = asyncio.run(service.query("local", "latest_departure", arguments))
        full = asyncio.run(service.query("local", "latest_departure", dict(arguments, detail="full")))
    finally:
        service.jobs.stop()
        store.close()
    size = len(pydantic_core.to_json(compact, indent=2))  # what the MCP text content carries
    assert size <= 10_000 < len(pydantic_core.to_json(full, indent=2)), size
    assert compact["detail"] == "compact" and "connections" not in compact and compact["connections_count"] == len(full["connections"])
    assert compact["primary"]["boards"] == {"stop": "Lindenplatz", "time": full["primary"]["boards"]["time"], "line": "T4"}
    assert [leg["mode"] for leg in compact["primary"]["legs"]] == ["walk", "tram", "walk", "regional_rail", "walk"]
    assert compact["summary"] == full["summary"] and compact["window"] == full["window"] and compact["fallback"]["leave"] == full["fallback"]["leave"]
    assert all(view["past_plan_target"] for view in compact["later"]) and "summary" not in compact["primary"]
    assert [ride["mode"] for ride in compact["later"][0]["rides"]] == ["tram", "regional_rail"]  # later ones: rides only
    assert {"first_choice", "fastest"} <= {route for entry in compact["candidates"] for route in entry["route"]}
    assert len(compact["later"]) == 3 < compact["later_count"] == len(full["later"])
    stored = Store(tmp_path / "street.sqlite3")
    try:
        assert len(stored.get("evidence", "local", compact["evidence"]["id"])["value"]["later"]) == compact["later_count"]
    finally:
        stored.close()
