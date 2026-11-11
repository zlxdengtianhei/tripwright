"""A7 (D-44): a pinned clock for replays and check_answer's checks. Offline: the fictional MOTIS and web of A5,
fixed moments. Fictional people and places."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tripwright.storage as storage
from a5_upstream import DESTINATION, ORIGIN, Timetable, page_reader
from test_a3_anchor_deadline import anchored_plan
from test_a6_check_answer import GOOD_SECOND, NOW, RULE, check, failing, fixed, seed
from tripwright.cli import main
from tripwright.query import QueryRouter, checked_arguments
from tripwright.service import Tripwright
from tripwright.storage import Store

ROOT = Path(__file__).resolve().parents[2]


# --- pinned clock ---------------------------------------------------------------------------------------------------------

def test_the_clock_pins_to_tripwright_now_and_runs_on_from_there(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_NOW", raising=False)
    assert abs(datetime.fromisoformat(storage.now()) - datetime.now(timezone.utc)) < timedelta(seconds=5)
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T16:40:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time() - 90))  # pinned 90 s ago: the clock has run on since
    shown = datetime.fromisoformat(storage.now())
    assert timedelta(seconds=89) <= shown - datetime(2030, 6, 10, 14, 40, tzinfo=timezone.utc) <= timedelta(seconds=95)
    assert storage.identifier("EV").startswith("EV-300610-")
    monkeypatch.setenv("TRIPWRIGHT_NOW", "16:40")
    with pytest.raises(ValueError, match="TRIPWRIGHT_NOW must be an ISO time with offset"):
        storage.now()


def test_evidence_reads_the_pinned_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T20:45:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    store = Store(tmp_path / "pinned.sqlite3")
    service = Tripwright(store, query_router=QueryRouter(store, transit=Timetable().provider(), pages=page_reader()))
    try:
        turn = service.turn("alice", "Create the trip.", "seed")
        draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=anchored_plan(), idempotency_key="seed")
        service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
        asked = service.turn("alice", "How late can I leave?", "q1", scenario="quick")
        assert asked["now"]["date"] == "2030-06-10" and asked["now"]["time"] == "20:45"
        assert asked["pack"]["markdown"].startswith("# 现在 20:45（Europe/Oslo，06-10 周一）")
        latest = asyncio.run(service.query("alice", "latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-counter")))
        web = asyncio.run(service.query("alice", "web_evidence", RULE))
        for evidence in (latest["evidence"], web["evidence"]):
            assert evidence["fetched_at"].startswith("2030-06-10T18:4"), evidence["fetched_at"]
        assert web["evidence"]["id"].startswith("EV-300610-")
        assert asyncio.run(service.query("alice", "check_answer", dict(text="x", part="first")))["now"]["time"] == "20:45"
    finally:
        service.jobs.stop()
        store.close()


def test_a_replay_pins_the_server_and_the_hosts_date(tmp_path):
    spec = importlib.util.spec_from_file_location("host_replay", ROOT / "scripts/host_replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    variables = replay.pin_clock(tmp_path, "2030-06-10T16:40:00+02:00")
    assert variables["TRIPWRIGHT_NOW"] == "2030-06-10T16:40:00+02:00" and variables["PATH"].startswith(str(tmp_path / "clock"))
    shown = subprocess.run(["sh", "-c", "date -u +%Y-%m-%dT%H:%M; TZ=Europe/Oslo date +%H:%M"], env=dict(os.environ, **variables),
                           capture_output=True, text=True, check=True).stdout.split()
    assert shown == ["2030-06-10T14:40", "16:40"] or shown == ["2030-06-10T14:41", "16:41"]  # a minute may turn while it runs
    with pytest.raises(SystemExit, match="--now needs an ISO time with offset"):
        replay.pin_clock(tmp_path / "other", "16:40")


SECOND = "# 第二段 · 核过了（20:45）\n和第一段不同：没有不同。\n网上说法：码头咖啡开到 23:00（搜索摘要）。\n## 完整安排\n21:30 出门 · 走 5 分钟到 Nordkai"


# --- check_answer: the new checks --------------------------------------------------------------------------------------------

@pytest.fixture
def trip(tmp_path, monkeypatch):
    fixed(monkeypatch)
    path = tmp_path / "a7.sqlite3"
    seed(path)
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    yield service
    service.jobs.stop()
    store.close()


@pytest.mark.parametrize("line, ok", [
    ("从这里赶不上 21:35 那班。", False),  # judgment 3, on_r2 / on_r4: the tool lists 21:35 as reachable
    ("赶不上 23:35 那班，它 23:58 才到。", True),  # the tool's 23:35 arrives after the limit
    ("要是 21:31 还没出门，就赶不上 21:35 那班。", True),  # a condition, with the tool's result behind it
    ("没核：从岩馆走能不能赶上 21:35，到了 Nordkai 再看。", True),
])
def test_a_cannot_reach_sentence_needs_the_tools_result(trip, line, ok):
    result = check(trip, GOOD_SECOND + "\n" + line)
    assert ("unreachable_needs_lookup" not in failing(result)) is ok, result["problems"]
    if not ok:
        assert result["problems"][0]["why"] == "说“赶不上”，但工具查到 21:35 那班赶得上"


def test_a_cannot_reach_sentence_without_any_lookup_is_refused(tmp_path, monkeypatch):
    fixed(monkeypatch)
    path = tmp_path / "bare.sqlite3"
    seed(path, lookups=False)
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    try:
        result = check(service, SECOND + "\n末班车来不及了，打车吧。")
        assert failing(result) == {"unreachable_needs_lookup": [6]} and "这一趟没有最晚出发的查询结果撑" in result["problems"][0]["why"]
    finally:
        service.jobs.stop()
        store.close()


HUNGRY_PLAN = anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"})
HUNGRY_PLAN["places"] += [
    dict(id="kaikafe", name="Kaikafé Nord", name_local="Kaikafé Nord", area="Oslo", kind="food", coords=[60.101, 10.701],
         opening_hours=["Mo-Su 06:00-23:00"], hours_source=dict(url="https://kaikafe.example.org/hours")),
    dict(id="grill", name="Fergeterminal Grill", name_local="Fergeterminal Grill", area="Oslo", kind="food", coords=[60.199, 10.899],
         opening_hours=["Mo-Fr 05:00-24:00", "Sa,Su 07:00-24:00"], hours_source=dict(url="https://ferry.example.org/food")),
]
MEALS = ("\n## 吃的\nKaikafé Nord\n码头咖啡馆 · 开到 23:00 · Google 地图搜 Kaikafé Nord\n"
         "Fergeterminal Grill\n渡轮站烤肉 · 营业到 24:00 · Google 地图搜 Fergeterminal Grill")


@pytest.fixture
def hungry(tmp_path, monkeypatch):
    fixed(monkeypatch)
    store = Store(tmp_path / "hungry.sqlite3")
    service = Tripwright(store, query_router=QueryRouter(store, transit=Timetable().provider(clock=lambda: NOW), pages=page_reader()))
    turn = service.turn("local", "Create the trip.", "seed")
    draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=HUNGRY_PLAN, idempotency_key="seed")
    service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
    service.turn("local", "我有点饿，想先吃点东西再去码头，最晚几点走？", "hungry")
    asyncio.run(service.query("local", "latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive")))
    yield service
    service.jobs.stop()
    store.close()


def test_a_hungry_question_gets_two_named_places_with_the_source_of_their_hours(hungry):
    plain = GOOD_SECOND.replace("核过：柜台开船前 40 分钟关（原句：“The ferry counter closes 40 minutes before departure.”）。\n", "")
    assert check(hungry, plain + MEALS, request_id="hungry")["ok"]
    one = check(hungry, plain + MEALS.split("\nFergeterminal Grill")[0], request_id="hungry")  # judgment 3: one place or none
    assert failing(one) == {"meals_named": [None]} and "只有 1 处" in one["problems"][0]["why"]
    unknown = check(hungry, plain + MEALS.replace("Fergeterminal Grill", "Kiosk Fjordblick"), request_id="hungry")
    assert "Kiosk Fjordblick（营业时间没有出处）" in unknown["problems"][0]["why"]
    asyncio.run(hungry.query("local", "web_evidence", dict(url="https://kiosk.example.org/", quote="Kiosk Fjordblick: daily until 24:00",
                                                            claim="Kiosk Fjordblick is open until midnight", type="opening_hours")))
    assert check(hungry, plain + MEALS.replace("Fergeterminal Grill", "Kiosk Fjordblick"), request_id="hungry")["ok"]  # a page says so
    no_hours = check(hungry, plain + MEALS.replace(" · 营业到 24:00", ""), request_id="hungry")
    assert "Fergeterminal Grill（没写营业时间）" in no_hours["problems"][0]["why"]
    # 11-04: meals_named is a hint; the draft passes with it listed
    assert "meals_named" in failing(check(hungry, plain)) and check(hungry, plain)["ok"]  # without request_id: the hungry one
    seed_question = check(hungry, plain, request_id="seed")
    assert seed_question["ok"], seed_question["problems"]  # not a hungry question: no meal check


@pytest.mark.parametrize("line, words", [
    ("最晚 21:30 出门，赶 23:10 的外部截止。", "外部截止"),  # judgment 3: internal terms in the answer
    ("22:20 是计划目标。", "计划目标"),
    ("来源 EV-301101-abcdef。", "EV-301101-abcdef"),
    ("primary_leave 是 21:30。", "primary_leave"),
])
def test_internal_words_stay_out_of_the_answer(trip, line, words):
    result = check(trip, GOOD_SECOND + "\n" + line)
    assert failing(result) == {"internal_words": [9]} and words in result["problems"][0]["why"]
    assert check(trip, GOOD_SECOND + "\n柜台 23:10 关，计划 22:20 到。")["ok"]


def test_the_first_part_is_not_held_to_a_number_of_lines(trip):
    # 11-04 (checker audit: the card judges content, not line counts): first_part_lines is gone
    head = "计划里 21:30 出门，坐 21:35 的车去码头（存档，没核）。\n按现在 20:45 算，你在 Nordkai 附近。"
    nine = head + "".join(f"\n第 {n} 件事。" for n in range(3, 10))
    assert check(trip, nine, "first")["ok"] and failing(check(trip, nine, "first")) == {}


def test_the_replay_script_runs_the_a7_checks_apart(tmp_path):
    spec = importlib.util.spec_from_file_location("answer_checks", ROOT / "scripts/answer_checks.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    run = tmp_path / "run"
    (run / "ws").mkdir(parents=True)
    (run / "ws" / "tripwright.yaml").write_text("format: 1\ntimezone: Europe/Oslo\n", encoding="utf-8")
    first = "最晚 21:30 出门。\n按现在 20:45 算。" + "".join(f"\n第 {n} 件事。" for n in range(3, 10))  # 9 lines: judgment 3, off_r2
    second = "# 第二段 · 核过了（20:46）\n赶不上 21:35 的火车，外部截止是 23:10。\n## 完整安排\n21:36 出门"
    events = [{"type": "assistant", "parent_tool_use_id": None, "timestamp": stamp, "message": {"content": [{"type": "text", "text": text}]}}
              for stamp, text in (("2030-06-10T18:45:30Z", first), ("2030-06-10T18:46:30Z", second))]
    (run / "turn1.stream.jsonl").write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")
    (run / "turn1.json").write_text(json.dumps({"message": "我饿了，最晚几点去码头？"}, ensure_ascii=False), encoding="utf-8")
    report = script.run_checks(run)
    assert set(report["turns"]["turn1"]) == {"two_parts", "no_extra_text", "later_when_no_subagents", "clock_in_first_part", "title_time_not_later", "no_tags", "first_line_times_checked",
                                             "checked_needs_tool", "fallback_later", "only_inside_window", "denied_needs_coverage", "count_needs_continuous", "direct_needs_one_ride", "checked_has_evidence"}
    # A10 adds frame_matches_server and first_part_copied; this stream has no framed lookup and no turn call
    assert {name: result["ok"] for name, result in report["more"]["turn1"].items()} == dict(
        internal_words=False, unreachable_needs_lookup=False, meals_named=False,
        frame_matches_server=True, first_part_copied=True)
    assert report["passed"] is False


# --- D-47: check_answer from the command line; the replay checks read the pinned clock --------------------------------------

def fact_record(subject="p1") -> dict:
    return dict(facts=[dict(key="companions", value=[], text="Today p1 is alone", subject=[subject], scope={"start": "2030-06-10", "end": "2030-06-10"},
                            true_from="2030-06-10T09:00:00+02:00", invalidated_by="Day ends", source=dict(event="this", basis="user_explicit"))])


def cli_check(database: Path, text: str, tmp_path: Path, capsys) -> dict:
    draft = tmp_path / "draft.md"
    draft.write_text(text, encoding="utf-8")
    code = main(["--db", str(database), "query", "check_answer", "--part", "second", "--text-file", str(draft), "--providers", "synthetic"])
    printed = capsys.readouterr()
    assert code == 0, printed.err  # was: "tripwright: 'receipt'", exit 1
    return json.loads(printed.out)


def test_check_answer_from_the_command_line_reads_the_latest_question_past_change_records(tmp_path, monkeypatch, capsys):
    """A turn that records writes change records into the events table, without a receipt (the answer layer's
    evaluation repository: 46 of 65). Without request_id, check_answer takes the latest message as the question."""
    fixed(monkeypatch)
    database = tmp_path / "ledger.sqlite3"
    store = Store(database)
    service = Tripwright(store, query_router=QueryRouter(store, transit=Timetable().provider(clock=lambda: NOW), pages=page_reader()))
    try:
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=HUNGRY_PLAN, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        service.turn("local", "我有点饿，今天就我一个人，最晚几点走？", "hungry", nature="user_verbatim", native_ref="fixture:hungry", record=fact_record())
        asyncio.run(service.query("local", "latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive")))
        events = store.all("events", "local")
        assert any("receipt" not in event for event in events) and events[-1].get("receipt") is None  # a change record is the newest event
    finally:
        service.jobs.stop()
        store.close()
    plain = GOOD_SECOND.replace("核过：柜台开船前 40 分钟关（原句：“The ferry counter closes 40 minutes before departure.”）。\n", "")
    hungry_check = cli_check(database, plain, tmp_path, capsys)
    assert [entry["check"] for entry in hungry_check["problems"]] == ["meals_named"]  # the latest question is the hungry one
    assert cli_check(database, plain + MEALS, tmp_path, capsys)["ok"]
    # a later question that is not about food, recorded with its own change records: no meal check
    store = Store(database)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    try:
        service.turn("local", "今天还是我一个人，几点出门？", "later", nature="user_verbatim", native_ref="fixture:later",
                     record=dict(facts=[dict(fact_record()["facts"][0], true_from="2030-06-10T20:00:00+02:00")]))
        assert store.all("events", "local")[-1].get("receipt") is None
    finally:
        service.jobs.stop()
        store.close()
    assert cli_check(database, plain, tmp_path, capsys)["ok"]


def test_check_answer_without_any_message_reads_no_question(tmp_path, monkeypatch, capsys):
    fixed(monkeypatch)
    database = tmp_path / "empty.sqlite3"
    Store(database).close()
    draft = GOOD_SECOND.replace("核过：", "没核：").replace("（20:45）", "")
    # S4h-b: with no recorded turn at all the command line refuses (an unanchored check once passed as "本问原话不明")
    code = main(["--db", str(database), "query", "check_answer", "--part", "second", "--text", draft, "--providers", "synthetic"])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "no turn of the last minutes" in captured.err and "--request-id" in captured.err and "tripwright" in captured.err
    # the service itself still answers with no question (no meal check), as over MCP
    result = over_mcp(database, monkeypatch, text=draft)
    assert "meals_named" not in {entry["check"] for entry in result["problems"]}


def replay_run(tmp_path: Path, now: str | None, turns: list[tuple[list[tuple[float, str]], float]]) -> Path:
    """A host_replay run directory: per turn its texts (seconds after the message was sent, text) and its length; the
    records carry the real time of a later day (05:53 in Oslo), as a pinned replay's do."""
    run = tmp_path / "run"
    (run / "ws").mkdir(parents=True)
    (run / "ws" / "tripwright.yaml").write_text("format: 1\ntimezone: Europe/Oslo\n", encoding="utf-8")
    real = datetime(2026, 11, 1, 4, 53, tzinfo=timezone.utc)
    summaries = []
    for n, (shown, seconds) in enumerate(turns, 1):
        events = [{"type": "assistant", "parent_tool_use_id": None, "timestamp": (real + timedelta(seconds=at)).isoformat().replace("+00:00", "Z"),
                   "message": {"content": [{"type": "text", "text": text}]}} for at, text in shown]
        (run / f"turn{n}.stream.jsonl").write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")
        (run / f"turn{n}.json").write_text(json.dumps({"message": "最晚几点去码头？"}, ensure_ascii=False), encoding="utf-8")
        summaries.append({"turn": n, "seconds": seconds, "text_at": [at for at, _ in shown]})
        real += timedelta(seconds=seconds)
    (run / "run.json").write_text(json.dumps({"now": now, "turns": summaries}), encoding="utf-8")
    return run


def answer_checks():
    spec = importlib.util.spec_from_file_location("answer_checks", ROOT / "scripts/answer_checks.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_the_replay_checks_read_the_first_parts_clock_from_the_pinned_one(tmp_path):
    script = answer_checks()
    first = lambda clock: f"最晚 21:30 出门，坐 21:35 的车去码头。\n按现在 {clock} 算，你在 Nordkai 附近。\n没核：21:35 这班车。"  # 3 lines
    clock_ok = lambda run: [script.run_checks(run)["turns"][f"turn{n}"]["clock_in_first_part"]["ok"] for n in (1, 2)]
    # several moments: message N at moment N, plus the seconds until the text appeared (20:45:00 + 40 s; 21:52:00 + 50 s)
    turns = [([(40.0, first("20:45")), (70.0, SECOND)], 90.0), ([(50.0, first("21:52")), (80.0, SECOND)], 95.0)]
    assert clock_ok(replay_run(tmp_path / "a", "2030-06-10T20:45:00+02:00,2030-06-10T21:52:00+02:00", turns)) == [True, True]
    # one moment: the clock runs on from it (turn 2 starts 90 s after 20:45 and its text shows 50 s later: 20:47)
    turns[1] = ([(50.0, first("20:47")), (80.0, SECOND)], 95.0)
    assert clock_ok(replay_run(tmp_path / "b", "2030-06-10T20:45:00+02:00", turns)) == [True, True]
    # no pinned clock: the records' own time (05:53 in Oslo) decides, and 20:45 is not it
    assert clock_ok(replay_run(tmp_path / "c", None, turns)) == [False, False]
    assert clock_ok(replay_run(tmp_path / "d", None, [([(40.0, first("05:53"))], 90.0), ([(50.0, first("05:55"))], 95.0)])) == [True, True]


# --- second_heading --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("draft, why", [
    (GOOD_SECOND.replace("# 第二段 · 核过了（20:45）\n", "有变化：没有。\n"), "没有“第二段”标题行"),  # fz_on_r1, first question
    (GOOD_SECOND.replace("（20:45）", ""), "“第二段”标题里没有时刻"),
    (GOOD_SECOND.replace("# 第二段 · 核过了（20:45）", "第二段 · 核过了（20:45）"), "没有“第二段”标题行"),  # not a heading
])
def test_a_second_part_has_its_heading_with_the_time(trip, draft, why):
    found = [entry for entry in check(trip, draft)["problems"] if entry["check"] == "second_heading"]
    assert [entry["why"] for entry in found] == [why] and found[0]["fix"] == "可以改得更好（不改也能发）：第一行写 `# 第二段 · …（HH:MM）`"
    assert found[0]["severity"] == "hint"


def test_a_second_part_heading_may_follow_a_write_conflict_line(trip):
    for draft in (GOOD_SECOND, "写入冲突：刚才那条没存上，请再说一次。\n" + GOOD_SECOND,
                  GOOD_SECOND.replace("# 第二段 · 核过了（20:45）", "# 第二段 · 没核完：渡轮没查到（20:45）")):
        assert "second_heading" not in {entry["check"] for entry in check(trip, draft)["problems"]}, draft[:20]


# --- 五补: the question check_answer reads without request_id ---------------------------------------------------------------

UNKNOWN = "本问原话不明，吃的检查没做。"
PLAIN_SECOND = GOOD_SECOND.replace("核过：柜台开船前 40 分钟关（原句：“The ferry counter closes 40 minutes before departure.”）。\n", "")


def at_moment(database: Path, monkeypatch, moment: datetime, act):
    fixed(monkeypatch, moment)
    store = Store(database)
    service = Tripwright(store, query_router=QueryRouter(store, transit=Timetable().provider(clock=lambda: NOW), pages=page_reader()))
    try:
        return act(service)
    finally:
        service.jobs.stop()
        store.close()


@pytest.fixture
def asked_db(tmp_path, monkeypatch):
    """The user said they were hungry 40 minutes ago; 5 minutes ago a setup message (channel system) named a meal, as
    the evaluation repository's did when its places to eat were written; this evening's lookups now (NOW)."""
    database = tmp_path / "asked.sqlite3"

    def create(service):
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=HUNGRY_PLAN, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        service.turn("local", "我饿了，最晚几点去码头？", "hungry", nature="user_verbatim", native_ref="fixture:hungry")

    at_moment(database, monkeypatch, NOW - timedelta(minutes=40), create)
    at_moment(database, monkeypatch, NOW - timedelta(minutes=5), lambda service: service.turn(
        "local", "评测设置：写入吃饭地点 Kaikafé Nord、Fergeterminal Grill。", "setup", channel="system"))
    at_moment(database, monkeypatch, NOW, lambda service: asyncio.run(service.query(
        "local", "latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive"))))
    return database


def answer_check(database: Path, text: str, tmp_path: Path, monkeypatch, capsys, *flags: str, stdin: str | None = None) -> dict:
    draft = tmp_path / "draft.md"
    draft.write_text(text, encoding="utf-8")
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main(["--db", str(database), "query", "check_answer", "--part", "second", "--text-file", str(draft), "--providers", "synthetic", *flags])
    printed = capsys.readouterr()
    assert code == 0, printed.err
    result = json.loads(printed.out)
    # S4h-b: without --request-id the command line may say on stderr which turn it took, before the summary
    assert printed.err.strip().splitlines()[-1] == result["summary"]
    return result


def over_mcp(database: Path, monkeypatch, **arguments) -> dict:
    checked = checked_arguments("check_answer", dict(part="second", **arguments))  # the tool's boundary takes `question`
    return at_moment(database, monkeypatch, NOW, lambda service: asyncio.run(service.query("local", "check_answer", checked)))


def test_without_question_or_request_id_check_answer_reads_only_a_recent_users_message(asked_db, tmp_path, monkeypatch, capsys):
    # S4h3 (replay s4h2_ns_off_r1): the command line takes the session's latest turn only when it came in the last
    # RECENT_QUESTION_MINUTES, as the MCP path does; the hungry one 40 minutes old is not taken, the CLI refuses and names
    # --request-id and --question (it once took a turn of another hour and checked the draft against it)
    draft = tmp_path / "draft.md"
    draft.write_text(PLAIN_SECOND, encoding="utf-8")
    code = main(["--db", str(asked_db), "query", "check_answer", "--part", "second", "--text-file", str(draft), "--providers", "synthetic"])
    refused = capsys.readouterr()
    assert code == 1 and refused.out == "" and "no turn of the last minutes" in refused.err and "--question" in refused.err
    unknown = over_mcp(asked_db, monkeypatch, text=PLAIN_SECOND)
    assert unknown["ok"] and unknown["summary"] == "草稿可以发。别向用户提这次检查、核对或工具回执。" + UNKNOWN
    # the user asks again 10 minutes ago: that message is this question
    at_moment(asked_db, monkeypatch, NOW - timedelta(minutes=10), lambda service: service.turn("local", "饿了，路上能吃点什么？", "again"))
    fixed(monkeypatch, NOW)
    hungry = answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys)
    assert failing(hungry) == {"meals_named": [None]} and UNKNOWN not in hungry["summary"]
    # a later setup message changes nothing; a later user message that is not about food does
    at_moment(asked_db, monkeypatch, NOW - timedelta(minutes=3), lambda service: service.turn("local", "设置：吃饭地点已写入。", "setup2", channel="system"))
    fixed(monkeypatch, NOW)
    assert failing(answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys)) == {"meals_named": [None]}
    at_moment(asked_db, monkeypatch, NOW - timedelta(minutes=2), lambda service: service.turn("local", "那最晚几点出门？", "leave"))
    fixed(monkeypatch, NOW)
    plain = answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys)
    assert plain["ok"] and plain["summary"] == "草稿可以发。别向用户提这次检查、核对或工具回执。"


def test_the_question_given_is_this_question(asked_db, tmp_path, monkeypatch, capsys):
    words = tmp_path / "question.txt"
    words.write_text("我饿了，最晚几点去机场？", encoding="utf-8")
    hungry = answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys, "--question", str(words))
    assert failing(hungry) == {"meals_named": [None]} and UNKNOWN not in hungry["summary"]
    assert over_mcp(asked_db, monkeypatch, text=PLAIN_SECOND, question="我饿了，最晚几点去机场？") == hungry
    fed = answer_check(asked_db, PLAIN_SECOND + MEALS, tmp_path, monkeypatch, capsys, "--question", str(words))
    assert fed["ok"], fed["problems"]
    # from standard input; a question without food is not checked for meals, and is not unknown
    calm = answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys, "--question", "-", stdin="最晚几点去机场？")
    assert calm["ok"] and calm["summary"] == "草稿可以发。别向用户提这次检查、核对或工具回执。"
    # the words given win over the turn a request_id names
    assert answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys, "--request-id", "hungry", "--question", "-", stdin="最晚几点去机场？")["ok"]
    assert failing(answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys, "--request-id", "hungry")) == {"meals_named": [None]}
    # the draft and the question cannot both come from standard input
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))
    assert main(["--db", str(asked_db), "query", "check_answer", "--part", "second", "--text-file", "-", "--question", "-"]) == 1
    assert "cannot both read standard input" in capsys.readouterr().err
