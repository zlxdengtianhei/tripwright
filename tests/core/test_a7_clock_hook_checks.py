"""A7 (D-44), first half: a pinned clock for replays, the text that is sent bound to the text check_answer passed (a
Stop hook), and check_answer's new checks: a sentence that says a connection cannot be made needs the tool's result,
a hungry user gets two named places to eat with the source of their hours, internal words stay out of the answer, and
the first part keeps to 8 lines. Offline: the fictional MOTIS and web of A5, fixed moments. Fictional people and places."""
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
from test_b3d_mcp import environment
from tripwright.cli import main
from tripwright.query import QueryRouter, checked_arguments
from tripwright.render import check_answer as checks
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


def test_turn_lookups_and_evidence_read_the_pinned_clock(tmp_path, monkeypatch):
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


# --- what was sent is what was checked ---------------------------------------------------------------------------------------

SECOND = "# 第二段 · 核过了（20:45）\n和第一段不同：没有不同。\n网上说法：码头咖啡开到 23:00（搜索摘要）。\n## 完整安排\n21:30 出门 · 走 5 分钟到 Nordkai"


def transcript(path: Path, sent: str, asked_at: float) -> Path:
    """A Claude Code session record: the user's message, a tool call and its result, a subagent's line, the answer."""
    stamp = datetime.fromtimestamp(asked_at, timezone.utc).isoformat().replace("+00:00", "Z")
    entries = [
        {"type": "user", "timestamp": stamp, "message": {"role": "user", "content": "How late can I leave for the ferry?"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "最晚 21:30 出门。\n按现在 20:45 算。"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__tripwright__query", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}},
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "# 第二段 subagent notes"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": sent}]}},
    ]
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    return path


def hook(database: Path, payload: dict, monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["--db", str(database), "hook", "stop"]) == 0
    return capsys.readouterr().out


@pytest.fixture
def checked(tmp_path, monkeypatch):
    """A database whose second part SECOND passed check_answer just now."""
    fixed(monkeypatch)
    database = tmp_path / "a7.sqlite3"
    seed(database, lookups=False)
    store = Store(database)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    try:
        asked = time.time() - 30
        result = asyncio.run(service.query("local", "check_answer", dict(text=SECOND + "\n", part="second", request_id="q1")))
    finally:
        service.jobs.stop()
        store.close()
    assert result["ok"] and result["passed"]["remembered"] and result["passed"]["sha256"] == checks.draft_digest(SECOND)
    return database, asked


def test_a_second_part_equal_to_the_passed_draft_goes_through(checked, tmp_path, monkeypatch, capsys):
    database, asked = checked
    stored = json.loads(database.with_name("a7.answers.json").read_text(encoding="utf-8"))["answers"]["q1"]
    assert stored["text"] == SECOND + "\n" and stored["at"].startswith("2030-06-10T18:45")
    path = transcript(tmp_path / "same.jsonl", SECOND.replace("\n", "  \r\n") + "\n\n", asked)  # trailing spaces, line endings
    assert hook(database, dict(transcript_path=str(path), hook_event_name="Stop"), monkeypatch, capsys) == ""
    assert database.with_name("hook.log").read_text(encoding="utf-8").splitlines()[-1].endswith("stop pass matches a passed draft")


def test_a_changed_or_never_checked_second_part_is_blocked(checked, tmp_path, monkeypatch, capsys):
    database, asked = checked
    changed = transcript(tmp_path / "changed.jsonl", SECOND.replace("21:30", "21:20"), asked)
    assert json.loads(hook(database, dict(transcript_path=str(changed)), monkeypatch, capsys)) == {
        "decision": "block", "reason": "第二段和核过的草稿不一致，请把发出的文字交 check_answer 再核"
        "；这段用户已经看到了：过了就结束，别再发一遍；没过只发一条“更正：”，写改了的几行"}  # A14: never sent twice
    later = transcript(tmp_path / "later.jsonl", SECOND, time.time() + 60)  # a newer question: nothing passed since it
    assert json.loads(hook(database, dict(transcript_path=str(later)), monkeypatch, capsys))["reason"].startswith("这一问的第二段还没过 check_answer")
    # D-48: stop_hook_active no longer lets it through by itself; after 3 blocks for the question it does, and logs it
    assert [hook(database, dict(transcript_path=str(changed), stop_hook_active=True), monkeypatch, capsys) == "" for _ in range(3)] == [False, False, True]
    assert "let through after 3 blocks for this question" in database.with_name("hook.log").read_text(encoding="utf-8")
    # the host's own last message, when the input carries it, is what is compared
    assert hook(database, dict(transcript_path=str(changed), last_assistant_message=SECOND), monkeypatch, capsys) == ""


def test_a_quick_answer_or_a_chat_line_is_not_blocked_and_errors_let_it_through(checked, tmp_path, monkeypatch, capsys):
    """A quick answer: nothing looked up, no second part promised (D-48). With a lookup the turn would go on (below)."""
    database, asked = checked
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T20:45:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    quick = record(tmp_path / "quick.jsonl", [asked_entry("最晚几点去码头？", asked), said("最晚 21:30 出门。\n按现在 20:45 算。\n计划里存的，10 点查的。")])
    assert hook(database, dict(transcript_path=str(quick)), monkeypatch, capsys) == ""
    chat = record(tmp_path / "chat.jsonl", [asked_entry("谢谢！", asked), said("不客气，路上顺利。")])
    assert hook(database, dict(transcript_path=str(chat)), monkeypatch, capsys) == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert main(["--db", str(database), "hook", "stop"]) == 0 and capsys.readouterr().out == ""
    assert "stop error let through: JSONDecodeError" in database.with_name("hook.log").read_text(encoding="utf-8")


def test_the_hook_as_claude_code_runs_it(checked, tmp_path):
    database, asked = checked
    payload = json.dumps(dict(session_id="s", transcript_path=str(transcript(tmp_path / "run.jsonl", SECOND + "\n多一行。", asked)),
                              hook_event_name="Stop", stop_hook_active=False))
    started = time.time()
    run = subprocess.run([sys.executable, "-m", "tripwright.cli", "--db", str(database), "hook", "stop"], input=payload,
                         env=environment(), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and json.loads(run.stdout)["decision"] == "block", run.stderr
    assert time.time() - started < 20


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
    assert check(hungry, plain)["ok"] is False  # without request_id the latest question counts: the hungry one
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


def test_the_first_part_keeps_to_eight_lines(trip):
    head = "计划里 21:30 出门，坐 21:35 的车去码头（存档，没核）。\n按现在 20:45 算，你在 Nordkai 附近。"
    place = "\nFergeterminal\n渡轮码头 · 今晚在这坐船 · Google 地图搜 Fergeterminal"
    eight = head + place + "".join(f"\n第 {n} 件事。" for n in range(3, 9))
    assert check(trip, eight, "first")["ok"]  # place lines do not count
    nine = eight + "\n第 9 件事。"
    result = check(trip, nine, "first")
    assert failing(result) == {"first_part_lines": [11]} and "第一段正文 9 行" in result["problems"][0]["why"]


# --- the first part before any lookup (PreToolUse) -------------------------------------------------------------------------

FIRST = "最晚 21:30 出门，坐 21:35 的车去码头。\n按现在 20:45 算，你在 Nordkai 附近。\n没核：21:35 这班车，第二段核完再发。"
NOTIFICATION = {"type": "user", "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": "<task-notification>done</task-notification>"}}


def session(path: Path, texts: list[str], *, asked_at: float, extra: list[dict] = ()) -> Path:
    stamp = lambda at: datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    entries = [{"type": "user", "timestamp": stamp(asked_at), "message": {"role": "user", "content": "最晚几点去码头？"}}]
    entries += [{"type": "assistant", "timestamp": stamp(asked_at + 5), "message": {"content": [{"type": "text", "text": text}]}} for text in texts]
    entries += list(extra)
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A trip repository's folder as the hook sees it (its zone in tripwright.yaml), and the pinned clock at 20:45."""
    folder = tmp_path / "ws"
    folder.mkdir()
    (folder / "tripwright.yaml").write_text("format: 1\ntimezone: Europe/Oslo\n", encoding="utf-8")
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T20:45:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    return folder


def first_hook(folder: Path, payload: dict, monkeypatch, capsys) -> dict | None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["--repo", str(folder), "hook", "first-part"]) == 0
    printed = capsys.readouterr().out
    return json.loads(printed)["hookSpecificOutput"] if printed else None


def lookup(path: Path, tool="Agent", **extra) -> dict:
    return dict(dict(session_id="s", transcript_path=str(path), hook_event_name="PreToolUse", tool_name=tool,
                     tool_input=extra.pop("tool_input", {"prompt": "find the last train"})), **extra)


def test_a_lookup_before_the_first_part_is_denied(workspace, tmp_path, monkeypatch, capsys):
    path = session(tmp_path / "none.jsonl", [], asked_at=time.time())  # v2f s1_on_r1: the first part went into thinking
    denied = first_hook(workspace, lookup(path), monkeypatch, capsys)
    assert denied == {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": "先把第一段发给用户（现在怎么走；没查过的最晚、末班不写），再查；还没有给用户的文字；"
                                                  "核查请在后台派 researcher（Agent 加 run_in_background: true）"}
    status = session(tmp_path / "status.jsonl", ["I'm still checking your trip records…"], asked_at=time.time())
    reason = first_hook(workspace, lookup(status, "mcp__tripwright__query", tool_input={"kind": "latest_departure"}), monkeypatch, capsys)
    assert "clock_in_first_part" in reason["permissionDecisionReason"] and "no_tags" in reason["permissionDecisionReason"]
    assert reason["permissionDecisionReason"].endswith("；核查请在后台派 researcher（Agent 加 run_in_background: true）")
    log = (workspace / ".tripwright" / "hook.log").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ")[1:3] for line in log] == [["first-part", "block"], ["first-part", "block"]]


def test_a_lookup_after_the_first_part_and_calls_that_are_not_lookups_go_through(workspace, tmp_path, monkeypatch, capsys):
    sent = session(tmp_path / "sent.jsonl", ["好的。", FIRST], asked_at=time.time(), extra=[NOTIFICATION])  # a notification is not a question
    for tool, tool_input in [("Agent", {}), ("WebSearch", {"query": "ferry"}), ("WebFetch", {"url": "https://ferry.example.org"}),
                             ("mcp__tripwright__query", {"kind": "transit"}),
                             ("Bash", {"command": "uvx --from ./tools/t.whl tripwright --repo . query latest_departure --anchor hn-arrive"})]:
        assert first_hook(workspace, lookup(sent, tool, tool_input=tool_input), monkeypatch, capsys) is None, tool
    empty = session(tmp_path / "empty.jsonl", [], asked_at=time.time())
    for tool, tool_input in [("mcp__tripwright__turn", {"text": "x"}), ("Read", {"file_path": "README.md"}), ("Skill", {"skill": "x"}),
                             ("mcp__tripwright__query", {"kind": "check_answer"}), ("Bash", {"command": "ls"}),
                             ("Bash", {"command": "tripwright --repo . query check_answer --part first --text-file a.md"})]:
        assert first_hook(workspace, lookup(empty, tool, tool_input=tool_input), monkeypatch, capsys) is None, tool
    assert first_hook(workspace, lookup(empty, "WebSearch", agent_id="a1", agent_type="tripwright-researcher"), monkeypatch, capsys) is None


def test_the_clock_of_the_first_part_is_the_pinned_one_at_the_moment_it_was_shown(workspace, tmp_path, monkeypatch, capsys):
    stale = session(tmp_path / "stale.jsonl", [FIRST.replace("20:45", "19:30")], asked_at=time.time())
    assert "clock_in_first_part" in first_hook(workspace, lookup(stale), monkeypatch, capsys)["permissionDecisionReason"]
    earlier = session(tmp_path / "earlier.jsonl", [FIRST.replace("20:45", "20:35")], asked_at=time.time() - 600)  # shown 10 min ago
    assert first_hook(workspace, lookup(earlier), monkeypatch, capsys) is None


def test_the_first_part_hook_gives_way_after_three_denials(workspace, tmp_path, monkeypatch, capsys):
    path = session(tmp_path / "stuck.jsonl", [], asked_at=time.time())
    assert [first_hook(workspace, lookup(path), monkeypatch, capsys) is None for _ in range(4)] == [False, False, False, True]
    assert "let through after 3 denials" in (workspace / ".tripwright" / "hook.log").read_text(encoding="utf-8")


def test_the_first_part_hook_as_claude_code_runs_it(workspace, tmp_path):
    path = session(tmp_path / "run.jsonl", [], asked_at=time.time())
    variables = dict(environment(), TRIPWRIGHT_NOW=os.environ["TRIPWRIGHT_NOW"], TRIPWRIGHT_NOW_SET_AT=os.environ["TRIPWRIGHT_NOW_SET_AT"])
    started = time.time()
    run = subprocess.run([sys.executable, "-m", "tripwright.cli", "--repo", str(workspace), "hook", "first-part"],
                         input=json.dumps(lookup(path)), env=variables, capture_output=True, text=True, timeout=60)
    took = time.time() - started
    assert run.returncode == 0 and json.loads(run.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", run.stderr
    assert took < 3, took  # about 0.3 s on this machine; uvx adds its own start


def test_a_second_part_after_a_task_notification_is_compared_with_this_questions_check(checked, tmp_path, monkeypatch, capsys):
    database, asked = checked
    path = transcript(tmp_path / "notified.jsonl", SECOND, asked)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(-1, json.dumps(NOTIFICATION, ensure_ascii=False))  # the researcher's report arrives, then the second part
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert hook(database, dict(transcript_path=str(path)), monkeypatch, capsys) == ""


# --- D-46: background dispatches go first; a question that looked something up ends with a first part ------------------------

STATUS_LINE = "I've dispatched the researcher to check the evening trains; I'll come back with the details."


def said(text: str, at: float | None = None) -> dict:
    entry = {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    return entry | ({"timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")} if at else {})


def called(tool: str, **tool_input) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t-{tool}", "name": tool, "input": tool_input}]}}


def asked(text: str, at: float) -> dict:
    return {"type": "user", "timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z"),
            "message": {"role": "user", "content": text}}


asked_entry = asked  # for tests whose fixture calls its own moment `asked`


def record(path: Path, entries: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    return path


def stop_hook(folder: Path, payload: dict, monkeypatch, capsys) -> dict | None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["--repo", str(folder), "hook", "stop"]) == 0
    printed = capsys.readouterr().out
    return json.loads(printed) if printed else None


DISPATCH = called("Agent", description="check trains", prompt="find the last train", subagent_type="tripwright-researcher", run_in_background=True)


def done(tool_use: str) -> dict:
    """Claude Code's notification that background work ended (2.1.301), naming the tool use that started it."""
    return {"type": "user", "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": (
        f"<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>{tool_use}</tool-use-id>\n<status>completed</status>\n</task-notification>")}}


def test_a_background_dispatch_goes_through_before_the_first_part_and_the_hosts_own_lookups_wait(workspace, tmp_path, monkeypatch, capsys):
    path = session(tmp_path / "none.jsonl", [], asked_at=time.time())  # nothing shown to the user yet
    for number, tool in enumerate(["Agent", "Task"]):
        background = lookup(path, tool, session_id=f"b{number}", tool_input={"prompt": "find the last train", "run_in_background": True})
        assert first_hook(workspace, background, monkeypatch, capsys) is None, tool
    log = (workspace / ".tripwright" / "hook.log").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in log] == ["first-part pass background dispatch (Agent)", "first-part pass background dispatch (Task)"]
    for number, (tool, tool_input) in enumerate([("Agent", {"prompt": "find the last train"}),
                                                 ("Agent", {"prompt": "find the last train", "run_in_background": False}),
                                                 ("Task", {"prompt": "x", "run_in_background": "yes"}),
                                                 ("mcp__tripwright__query", {"kind": "latest_departure", "run_in_background": True}),
                                                 ("WebSearch", {"query": "last ferry"}), ("WebFetch", {"url": "https://ferry.example.org"}),
                                                 ("Bash", {"command": "tripwright --repo . query transit --from a --to b"})]):
        denied = first_hook(workspace, lookup(path, tool, session_id=f"f{number}", tool_input=tool_input), monkeypatch, capsys)
        assert denied and denied["permissionDecision"] == "deny", (tool, tool_input)
        assert denied["permissionDecisionReason"].endswith("核查请在后台派 researcher（Agent 加 run_in_background: true）")
    # check_answer is never held, and a subagent's own lookups are not the main agent's
    assert first_hook(workspace, lookup(path, "mcp__tripwright__query", session_id="c", tool_input={"kind": "check_answer"}), monkeypatch, capsys) is None
    assert first_hook(workspace, lookup(path, "WebFetch", session_id="r", agent_id="a1", tool_input={"url": "https://x.example.org"}),
                      monkeypatch, capsys) is None


def test_stop_holds_a_question_that_looked_something_up_but_sent_only_a_status_line(workspace, tmp_path, monkeypatch, capsys):
    now = time.time()
    status = record(tmp_path / "status.jsonl", [asked("最晚几点去码头？", now - 20), DISPATCH, said(STATUS_LINE, now - 5)])  # v2f's shape
    blocked = stop_hook(workspace, dict(transcript_path=str(status), session_id="s1", hook_event_name="Stop", last_assistant_message=STATUS_LINE),
                        monkeypatch, capsys)
    assert blocked["decision"] == "block" and blocked["reason"].startswith("把第一段写成回复文字再结束（现在怎么走；没查过的最晚、末班不写）；最后一段文字没过：")
    assert "clock_in_first_part" in blocked["reason"]
    # the host's own query counts as much as a dispatch; with nothing shown at all the reason says so
    own = record(tmp_path / "own.jsonl", [asked("最晚几点去码头？", now - 20), called("mcp__tripwright__query", kind="latest_departure")])
    assert stop_hook(workspace, dict(transcript_path=str(own), session_id="s2"), monkeypatch, capsys)["reason"] == \
        "把第一段写成回复文字再结束（现在怎么走；没查过的最晚、末班不写）；还没有给用户的文字"
    # stop_hook_active no longer lets it through by itself (D-48): the cap does, 3 blocks per question, logged
    again = [stop_hook(workspace, dict(transcript_path=str(status), session_id="s1", stop_hook_active=True), monkeypatch, capsys) for _ in range(3)]
    assert [answer is None for answer in again] == [False, False, True]
    log = (workspace / ".tripwright" / "hook.log").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ")[2] for line in log] == ["block", "block", "block", "block", "pass"]
    assert log[0].endswith("blocked: Agent this question but no first part") and "let through after 3 blocks for this question" in log[-1]


def test_stop_lets_a_question_end_while_its_check_runs_or_once_its_second_part_is_out(workspace, tmp_path, monkeypatch, capsys):
    now = time.time()
    question = asked("最晚几点去码头？", now - 20)
    passing = [
        # v2e's order: the researcher in the background, then the first part as the turn's last text
        [question, DISPATCH, said(FIRST, now - 5)],
        # the first part only in the hook's input (not yet written to the record)
        [question, DISPATCH],
        # a notification for some other background work: this question's check is still running
        [question, DISPATCH, said(FIRST, now - 15), done("t-other"), said("我再等一下核查。", now - 2)],
        # a chat, or a turn that only records: no lookup, nothing to hold
        [question, called("mcp__tripwright__turn", text="x"), called("Read", file_path="README.md"), said("记下了。", now - 5)],
        # the lookups of an earlier question do not count for this one
        [asked("帮我查末班车", now - 300), DISPATCH, said(STATUS_LINE, now - 290), asked("谢谢", now - 10), said("不客气。", now - 5)],
        # this question's second part was sent; a line after it ends the turn
        [question, DISPATCH, said(FIRST.replace("20:45", "20:30"), now - 15), said(SECOND, now - 8), said("有变化再告诉我。", now - 2)],
    ]
    for number, entries in enumerate(passing):
        payload = dict(transcript_path=str(record(tmp_path / f"p{number}.jsonl", entries)))
        payload |= dict(last_assistant_message=FIRST) if number == 1 else {}
        assert stop_hook(workspace, payload, monkeypatch, capsys) is None, number
    # a second part goes the old way: compared with the draft check_answer passed (here none, so held for that)
    second = record(tmp_path / "second.jsonl", [question, DISPATCH, said(SECOND, now - 5)])
    assert stop_hook(workspace, dict(transcript_path=str(second)), monkeypatch, capsys)["reason"].startswith("这一问的第二段还没过 check_answer")


def test_the_hooks_read_the_zone_check_answer_used(checked, tmp_path, monkeypatch, capsys):
    """A database has no tripwright.yaml: the hooks take the trip's zone from what check_answer recorded."""
    database, _ = checked
    assert json.loads(database.with_name("a7.answers.json").read_text(encoding="utf-8"))["zone"] == "Europe/Oslo"
    # the check dispatched in the background is running, so a first part ends the turn; in UTC it would not be one
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T18:45:00+00:00")  # 20:45 in Oslo
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    path = record(tmp_path / "db.jsonl", [asked("最晚几点去码头？", time.time() - 20), DISPATCH, said(FIRST, time.time() - 5)])
    assert hook(database, dict(transcript_path=str(path)), monkeypatch, capsys) == ""
    assert "first part sent" in database.with_name("hook.log").read_text(encoding="utf-8").splitlines()[-1]


def test_the_stop_hook_on_a_status_line_as_claude_code_runs_it(workspace, tmp_path):
    path = record(tmp_path / "run.jsonl", [asked("最晚几点去码头？", time.time() - 20), DISPATCH, said(STATUS_LINE, time.time() - 5)])
    variables = dict(environment(), TRIPWRIGHT_NOW=os.environ["TRIPWRIGHT_NOW"], TRIPWRIGHT_NOW_SET_AT=os.environ["TRIPWRIGHT_NOW_SET_AT"])
    started = time.time()
    run = subprocess.run([sys.executable, "-m", "tripwright.cli", "--repo", str(workspace), "hook", "stop"],
                         input=json.dumps(dict(session_id="s", transcript_path=str(path), hook_event_name="Stop", stop_hook_active=False)),
                         env=variables, capture_output=True, text=True, timeout=60)
    took = time.time() - started
    assert run.returncode == 0 and json.loads(run.stdout)["decision"] == "block", run.stderr
    assert took < 3, took


def test_the_replay_counts_each_hooks_runs_and_blocks(tmp_path):
    spec = importlib.util.spec_from_file_location("host_replay", ROOT / "scripts/host_replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    log = tmp_path / "hook.log"
    log.write_text("t first-part block blocked Agent: no text yet\nt first-part pass first part sent\nt stop pass matches a passed draft\n"
                   "t stop block blocked: differs\nt stop error let through: OSError\n", encoding="utf-8")
    assert replay.hook_counts(log) == {"first-part": {"runs": 2, "blocked": 1, "errors": 0}, "stop": {"runs": 3, "blocked": 1, "errors": 1}}
    assert replay.hook_counts(tmp_path / "missing.log") == {}


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
                                             "plan_heading", "checked_needs_tool", "fallback_later", "only_inside_window", "denied_needs_coverage", "count_needs_continuous", "direct_needs_one_ride", "checked_has_evidence"}
    # A10 adds frame_matches_server and first_part_copied; this stream has no framed lookup and no turn call
    assert {name: result["ok"] for name, result in report["more"]["turn1"].items()} == dict(
        first_part_lines=False, internal_words=False, unreachable_needs_lookup=False, meals_named=False,
        frame_matches_server=True, first_part_copied=True)
    assert report["passed"] is False


# --- D-47: check_answer from the command line in a repository with change records; Stop asks only a first part's shape;
# the replay checks read the pinned clock ---------------------------------------------------------------------------------

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


NINE_LINES = FIRST + "".join(f"\n第 {n} 件事。" for n in range(4, 10))


def test_stop_asks_only_a_first_parts_shape_and_first_part_the_whole_check(workspace, tmp_path, monkeypatch, capsys):
    """D-47: a first part of 9 lines, or with an internal word, ends the turn; a status line does not. Before the main
    agent's own lookup the first-part hook still asks for the whole check."""
    now = time.time()
    for number, text in enumerate([NINE_LINES, FIRST.replace("没核：21:35 这班车", "没核：21:35 这班车（计划目标 22:20）")]):
        assert checks.first_part_problems(text, 20 * 60 + 45) and not checks.first_part_shape(text, 20 * 60 + 45)
        path = record(tmp_path / f"shape{number}.jsonl", [asked("最晚几点去码头？", now - 20), DISPATCH, said(text, now - 5)])
        assert stop_hook(workspace, dict(transcript_path=str(path)), monkeypatch, capsys) is None, number
        denied = first_hook(workspace, lookup(path, "WebSearch", session_id=f"w{number}", tool_input={"query": "ferry"}), monkeypatch, capsys)
        assert denied["permissionDecision"] == "deny" and ("first_part_lines" if number == 0 else "internal_words") in denied["permissionDecisionReason"]
    for number, text in enumerate([STATUS_LINE, "<thinking>最晚 21:30 出门</thinking>\n按现在 20:45 算。"]):
        path = record(tmp_path / f"status{number}.jsonl", [asked("最晚几点去码头？", now - 20), DISPATCH, said(text, now - 5)])
        assert stop_hook(workspace, dict(transcript_path=str(path)), monkeypatch, capsys)["decision"] == "block", number


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


# --- D-48: Stop pushes a question on until its second part is out, at most 3 blocks; second_heading -----------------------

QUICK = "最晚 21:30 出门，坐 21:35 的车去码头。\n按现在 20:45 算，你在 Nordkai 附近。\n21:30 和 21:35 是计划里存的，10 点查的。"
PUSH = "在后台派 researcher 核查，再写第二段（先过 check_answer）"


def test_stop_pushes_a_question_on_until_its_second_part_is_out(workspace, tmp_path, monkeypatch, capsys):
    now = time.time()
    question = asked("最晚几点去码头？", now - 20)
    held = [
        # fz_on_r1, second question: turn, the place table, a first part that says a second part follows, end
        [question, called("mcp__tripwright__turn", text="x"), called("Bash", command="cat trips/t/plan/places.yaml"), said(FIRST, now - 5)],
        # the researcher's report came back (the notification names its tool use); the host said a line and ended
        [question, DISPATCH, said(FIRST, now - 15), done("t-Agent"), said("researcher 回来了，我再核一遍。", now - 2)],
        # the same with a notification that names no tool use: it ends the oldest running check
        [question, DISPATCH, said(FIRST, now - 15), NOTIFICATION, said("researcher 回来了。", now - 2)],
        # the host looked something up itself and sent a first part that promises nothing
        [question, called("mcp__tripwright__query", kind="latest_departure"), said(QUICK, now - 5)],
        # fz_on_r1, first question: the second part opened with "有变化：", without its heading
        [question, DISPATCH, said(FIRST, now - 15), done("t-Agent"), said("有变化：21:35 那班取消了，改坐 22:05。", now - 2)],
    ]
    for number, entries in enumerate(held):
        payload = dict(transcript_path=str(record(tmp_path / f"h{number}.jsonl", entries)), session_id=f"h{number}")
        assert stop_hook(workspace, payload, monkeypatch, capsys) == {"decision": "block", "reason": PUSH}, number
    log = (workspace / ".tripwright" / "hook.log").read_text(encoding="utf-8").splitlines()
    assert log[0].endswith("blocked: second part promised, no second part, no check running")
    assert log[3].endswith("blocked: mcp__tripwright__query this question, no second part, no check running")
    # a quick question: nothing promised, nothing looked up
    quick = record(tmp_path / "quick.jsonl", [question, called("mcp__tripwright__turn", text="x"), said(QUICK, now - 5)])
    assert stop_hook(workspace, dict(transcript_path=str(quick), session_id="q"), monkeypatch, capsys) is None
    # the words that promise a second part, from the skeleton (zh and en)
    assert all(checks.PROMISE.search(text) for text in ("没核：21:35 这班车，第二段核完再发。", "附近多半关门，第二段确认。",
                                                         "Not checked: the 21:35 train; the second part follows."))


def test_stop_gives_way_after_three_blocks_of_a_question_whatever_their_reasons(workspace, tmp_path, monkeypatch, capsys):
    now = time.time()
    lookup_then = [asked("最晚几点去码头？", now - 30), called("mcp__tripwright__query", kind="transit")]
    status = record(tmp_path / "status.jsonl", lookup_then + [said(STATUS_LINE, now - 20)])
    first = record(tmp_path / "first.jsonl", lookup_then + [said(STATUS_LINE, now - 20), said(FIRST, now - 5)])
    answers = [stop_hook(workspace, dict(transcript_path=str(path), session_id="s", stop_hook_active=n > 0), monkeypatch, capsys)
               for n, path in enumerate([status, first, first, first])]
    assert [answer and answer["reason"].split("（")[0] for answer in answers] == ["把第一段写成回复文字再结束", "在后台派 researcher 核查，再写第二段", "在后台派 researcher 核查，再写第二段", None]
    assert "let through after 3 blocks for this question" in (workspace / ".tripwright" / "hook.log").read_text(encoding="utf-8").splitlines()[-1]
    # a new question starts its own count
    later = record(tmp_path / "later.jsonl", [asked("那明天呢？", now - 2), called("mcp__tripwright__query", kind="transit"), said(STATUS_LINE, now - 1)])
    assert stop_hook(workspace, dict(transcript_path=str(later), session_id="s"), monkeypatch, capsys)["decision"] == "block"


@pytest.mark.parametrize("draft, why", [
    (GOOD_SECOND.replace("# 第二段 · 核过了（20:45）\n", "有变化：没有。\n"), "没有“第二段”标题行"),  # fz_on_r1, first question
    (GOOD_SECOND.replace("（20:45）", ""), "“第二段”标题里没有时刻"),
    (GOOD_SECOND.replace("# 第二段 · 核过了（20:45）", "第二段 · 核过了（20:45）"), "没有“第二段”标题行"),  # not a heading
])
def test_a_second_part_has_its_heading_with_the_time(trip, draft, why):
    found = [entry for entry in check(trip, draft)["problems"] if entry["check"] == "second_heading"]
    assert [entry["why"] for entry in found] == [why] and found[0]["fix"] == "第一行写 `# 第二段 · …（HH:MM）`"


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
    assert unknown["ok"] and unknown["summary"] == "草稿可以发。" + UNKNOWN
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
    assert plain["ok"] and plain["summary"] == "草稿可以发。"


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
    assert calm["ok"] and calm["summary"] == "草稿可以发。"
    # the words given win over the turn a request_id names
    assert answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys, "--request-id", "hungry", "--question", "-", stdin="最晚几点去机场？")["ok"]
    assert failing(answer_check(asked_db, PLAIN_SECOND, tmp_path, monkeypatch, capsys, "--request-id", "hungry")) == {"meals_named": [None]}
    # the draft and the question cannot both come from standard input
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))
    assert main(["--db", str(asked_db), "query", "check_answer", "--part", "second", "--text-file", "-", "--question", "-"]) == 1
    assert "cannot both read standard input" in capsys.readouterr().err
