"""scripts/answer_checks.py: each check fails on the shape a judged replay showed and passes a good answer.

The replays themselves hold private trips, so these samples are fictional copies of the failing shapes.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from tripwright.agents.assemble import ROOT

spec = importlib.util.spec_from_file_location("answer_checks", ROOT / "scripts/answer_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)

GOOD_HEAD = "计划里存的走法：00:40 出门，坐 00:53 的火车去机场，01:15 到；今晚待核。"
GOOD_FIRST = GOOD_HEAD + """
按现在 00:12 算，你在 Kletterhalle Nord。不在就告诉我在哪。

Flughafen Grauwald
格劳瓦尔德机场 · 今晚在这过夜 · Google 地图搜 Flughafen Grauwald · [Google 地图](https://www.google.com/maps/search/?api=1&query=Flughafen%20Grauwald)
没核：00:53 这班车，第二段核完再发。"""
GOOD_SECOND = """# 第二段 · 核过了（00:14）
和第一段不同：没有不同。
核过：00:53 S7 从 Grauwald Hbf 开，01:15 到机场；00:40 出门（时刻表查询）。
## 完整安排
00:40 出门 · 坐 U2 到 Grauwald Hbf，约 10 分钟 · Google 地图搜 Grauwald Hbf"""


# A transit lookup shaped like A5's result: ride legs in local time, the window it covered, an evidence ID.
TRANSIT = {"connections": [{"leave": "00:40", "reach": "01:20", "boards": {"stop": "Grauwald Hbf", "time": "00:53", "line": "S7"},
                            "alights": {"stop": "Flughafen Grauwald", "time": "01:15", "line": "S7"}}],
           "window": {"from": "00:20", "to": "01:30", "by": "departure", "count": 3, "text": "查到 00:20—01:30 之间出发的 3 条"},
           "evidence": {"id": "EV-261031-abcdef", "kind": "transit"}}


def stream(tmp_path: Path, name: str, texts: list[tuple[str, str]], lookup: dict | None = None, result: dict | None = None,
           turn: int = 1) -> Path:
    """A run directory whose turn file holds one tool call and its result, then one assistant event per (UTC timestamp, text).

    The tool call is a Tripwright transit `query` returning TRANSIT unless `lookup` and `result` say otherwise."""
    run = tmp_path / name
    (run / "ws").mkdir(parents=True, exist_ok=True)
    (run / "ws" / "tripwright.yaml").write_text("format: 1\ntimezone: Europe/Vienna\n", encoding="utf-8")
    call = lookup or {"name": "mcp__tripwright__query", "input": {"kind": "transit"}}
    answer = TRANSIT if result is None else result
    events = [{"type": "system", "subtype": "init"},
              {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_use", "id": f"t{turn}", **call}]}},
              {"type": "user", "parent_tool_use_id": None, "message": {"content": [
                  {"type": "tool_result", "tool_use_id": f"t{turn}", "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]}]}}]
    events += [{"type": "assistant", "parent_tool_use_id": None, "timestamp": stamp,
                "message": {"content": [{"type": "text", "text": text}]}} for stamp, text in texts]
    (run / f"turn{turn}.stream.jsonl").write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")
    return run


def results(run: Path) -> dict:
    return checks.run_checks(run)["turns"]["turn1"]


def test_a_good_answer_passes_every_check(tmp_path):
    run = stream(tmp_path, "good", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", GOOD_SECOND)])
    assert {name: result["ok"] for name, result in results(run).items()} == dict.fromkeys(
        ["two_parts", "clock_in_first_part", "title_time_not_later", "no_tags", "first_line_times_checked",
         "plan_heading", "checked_needs_tool", "fallback_later", "only_inside_window", "denied_needs_coverage", "count_needs_continuous", "direct_needs_one_ride", "checked_has_evidence"], True)
    assert checks.main([str(run)]) == 0 and (run / "answer_checks.json").is_file()


def test_a_stray_status_line_and_a_tag_are_caught(tmp_path):
    # The shape of r10's first question: a tag and a process line before the first part.
    run = stream(tmp_path, "tag", [("2026-10-31T23:12:05Z", "回<thinking_mode> Let me invoke it. </thinking_mode>"),
                                    ("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", GOOD_SECOND + "\n另外：行程助手这次没连上。")])
    found = results(run)
    assert not found["two_parts"]["ok"] and not found["no_tags"]["ok"]
    assert found["clock_in_first_part"]["ok"], "the first part is the answer before the second part, not the stray line"


@pytest.mark.parametrize("head", [
    "最晚 21:50 出岩馆，坐 22:42 的车去机场。\n按你现在还在 Kletterhalle Nord 算（计划里这时你在这）。",  # no time at all (r10, r11)
    "不用赶：最晚 22:25 出发。\n按现在是下午 15:40 左右算。",  # "now" is not the clock (r9, second question)
])
def test_a_first_part_without_the_clock_fails(tmp_path, head):
    first = head + "\n\nFlughafen Grauwald\n机场 · Google 地图搜 Flughafen Grauwald"
    run = stream(tmp_path, "clock", [("2026-10-31T23:25:40Z", first), ("2026-10-31T23:27:10Z", GOOD_SECOND.replace("00:14", "00:26"))])
    assert not results(run)["clock_in_first_part"]["ok"]


def test_a_second_part_title_or_checked_time_after_sending_fails(tmp_path):
    # r7's first question: sent at 00:07, titled 00:25 and "00:20 查".
    second = GOOD_SECOND.replace("（00:14）", "（00:25 查）").replace("（时刻表查询）", "（时刻表查询，00:20 查）")
    run = stream(tmp_path, "late", [("2026-10-31T23:05:40Z", GOOD_FIRST.replace("00:12", "00:05")), ("2026-10-31T23:07:58Z", second)])
    found = results(run)
    assert not found["title_time_not_later"]["ok"] and "00:25" in found["title_time_not_later"]["detail"]


def test_a_first_line_time_the_second_part_never_checks_fails(tmp_path):
    # r8's first question: a stored last train in the first line, never checked or corrected.
    first = GOOD_FIRST.replace(GOOD_HEAD, "末班火车（23:53）已经开走了。最晚 03:45 坐夜班巴士，04:10 到。")
    second = "# 第二段 · 没核完：首班火车没查到（00:14）\n核过：夜班巴士 03:45 开，04:10 到（运营方时刻表）。\n没核：首班火车。"
    run = stream(tmp_path, "unchecked", [("2026-10-31T23:12:40Z", first), ("2026-10-31T23:14:30Z", second)])
    found = results(run)
    assert not found["first_line_times_checked"]["ok"] and "23:53" in found["first_line_times_checked"]["detail"]


def test_times_after_midnight_compare_on_the_dial():
    assert checks.ahead(5, 1435) == 10 and checks.ahead(1435, 5) == -10 and checks.ahead(25, 7) == 18


def test_bullets_under_a_bold_checked_heading_count_and_a_stored_check_time_is_skipped(tmp_path):
    first = GOOD_FIRST.replace(GOOD_HEAD,
                               "00:40 出门，坐 00:53 的火车去机场，01:15 到（计划里存的，10-31 13:50 查的）。")
    second = ("# 第二段 · 核过了（00:14）\n**和第一段不同**\n- 没有不同。\n**核过**\n- 00:53 S7，01:15 到机场；最晚 00:40 出门（运营方时刻表）。\n"
              "**完整安排**\n- 00:40 出门")
    run = stream(tmp_path, "bold", [("2026-10-31T23:12:40Z", first), ("2026-10-31T23:14:30Z", second)])
    assert results(run)["first_line_times_checked"]["ok"]


@pytest.mark.parametrize("stamp, ok", [
    ("（10-31 13:50 在 Transitous 上查的）", True),  # when a stored time was checked (v2c off_r4)
    ("，11-01 01:05 那班更稳", False),  # a dated time to act on is still a time to act on
])
def test_a_dated_check_stamp_is_skipped_but_a_dated_departure_is_not(tmp_path, stamp, ok):
    first = GOOD_FIRST.replace("01:15 到；今晚待核。", "01:15 到" + stamp + "；今晚待核。")
    run = stream(tmp_path, "dated", [("2026-10-31T23:12:40Z", first), ("2026-10-31T23:14:30Z", GOOD_SECOND)])
    assert results(run)["first_line_times_checked"]["ok"] is ok


def test_a_time_inside_the_title_reason_is_not_the_title_time(tmp_path):
    second = GOOD_SECOND.replace("# 第二段 · 核过了（00:14）", "# 第二段 · 没核完：05:45 截止没查到官方说法（00:14）")
    run = stream(tmp_path, "reason", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", second)])
    assert results(run)["title_time_not_later"]["ok"]


WEB = {"name": "WebFetch", "input": {"url": "https://example.org/timetable"}}


@pytest.mark.parametrize("call, second, ok", [
    # v2c fix_off_r1: no Tripwright lookup ran, yet a web timetable is called 核过.
    (WEB, GOOD_SECOND, False),
    (WEB, GOOD_SECOND.replace("# 第二段 · 核过了", "# 第二段 · 没核完：车次只有网上说法").replace("核过：", "网上说法："), True),
    ({"name": "Bash", "input": {"command": "tripwright query transit --from 'Grauwald Hbf'"}}, GOOD_SECOND, True),
])
def test_checked_needs_a_tripwright_lookup(tmp_path, call, second, ok):
    run = stream(tmp_path, "lookup", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", second)], lookup=call)
    assert results(run)["checked_needs_tool"]["ok"] is ok


@pytest.mark.parametrize("line, ok", [
    ("00:53 坐 S7，01:15 到机场。误了就走 3 分钟到南口，坐 00:45 的 N7", False),  # v2c fix_on_r1: the bus leaves first
    ("误了 00:53：早一点的后备是 00:30 的夜班巴士", False),  # v2c fix_on_r2, first part
    ("00:53 坐 S7，01:15 到机场；误了就等 03:15 的 N7", True),
    ("误了 00:53 还有 01:23 那班，01:45 到", True),
    ("硬节点：02:45 到柜台是计划的目标，03:45 起飞；00:45 的大巴误了还有 00:53 的 S7", True),  # v2d s1_on_r2: the missed bus is in its clause
])
def test_a_fallback_must_leave_after_the_missed_choice(tmp_path, line, ok):
    second = GOOD_SECOND + "\n" + line
    run = stream(tmp_path, "fallback", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", second)])
    assert results(run)["fallback_later"]["ok"] is ok


@pytest.mark.parametrize("heading, ok", [("完整安排：", False), ("**完整安排**", False), ("## 完整安排", True), ("# 完整安排（从现在起）", True)])
def test_the_full_plan_has_a_heading(tmp_path, heading, ok):
    second = GOOD_SECOND.replace("## 完整安排", heading)
    run = stream(tmp_path, "heading", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", second)])
    assert results(run)["plan_heading"]["ok"] is ok


@pytest.mark.parametrize("to, ok", [("00:45", False), ("01:30", True), (None, False), ("2026-11-01T01:30:00+01:00", True)])
def test_only_or_last_needs_a_window_that_reaches_the_time(tmp_path, to, ok):
    # v2c fix_on_r1: "05:15 前只有这一班" from a lookup that stopped at 04:45.
    result = dict(TRANSIT, window=dict(TRANSIT["window"], to=to)) if to else {k: v for k, v in TRANSIT.items() if k != "window"}
    second = GOOD_SECOND + "\n核过：01:15 前只有这一班火车（时刻表查询）。"
    run = stream(tmp_path, "window", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", second)], result=result)
    assert results(run)["only_inside_window"]["ok"] is ok


WEB_CALL = {"name": "mcp__tripwright__query", "input": {"kind": "web_evidence"}}


@pytest.mark.parametrize("line, result, ok", [
    ("核过：周六夜里地铁 30 分钟一班（城市网站）。", TRANSIT, False),  # v2c fix_on_r2: inferred, no evidence
    ("核过：柜台起飞前 40 分钟关（航空公司帮助页）。",
     {"counts_as_checked": False, "evidence": {"id": "EV-261031-zzzzzz", "claim": "柜台起飞前 40 分钟关", "quote": "closes 40 minutes"}}, False),
    ("核过：柜台起飞前 40 分钟关（航空公司帮助页）。",
     {"counts_as_checked": True, "evidence": {"id": "EV-261031-qwerty", "claim": "柜台起飞前 40 分钟关", "quote": "closes 40 minutes before"}}, True),
])
def test_checked_lines_match_evidence(tmp_path, line, result, ok):
    call = WEB_CALL if "counts_as_checked" in result else None
    run = stream(tmp_path, "evidence", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", GOOD_SECOND.replace(
        "核过：00:53 S7", line + "\n网上说法：00:53 S7"))], lookup=call, result=result)
    assert results(run)["checked_has_evidence"]["ok"] is ok


def test_evidence_from_an_earlier_turn_backs_a_later_answer(tmp_path):
    stream(tmp_path, "turns", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", GOOD_SECOND)])
    run = stream(tmp_path, "turns", [("2026-10-31T23:20:40Z", GOOD_FIRST.replace("00:12", "00:20")), ("2026-10-31T23:22:30Z", GOOD_SECOND.replace("00:14", "00:22"))],
                 lookup={"name": "WebFetch", "input": {"url": "https://example.org"}}, result={}, turn=2)
    assert checks.run_checks(run)["turns"]["turn2"]["checked_has_evidence"]["ok"]


def test_under_a_bare_checked_label_only_list_items_need_evidence(tmp_path):
    # v2d off_r2: a place line and a note sat under "核过：" and were taken for checked claims.
    second = GOOD_SECOND.replace("核过：00:53 S7 从 Grauwald Hbf 开，01:15 到机场；00:40 出门（时刻表查询）。",
                                 "核过：\n- 00:53 S7 从 Grauwald Hbf 开，01:15 到机场（时刻表查询）。\nWürstelstand Ostring\n香肠摊开到 04:00，搜索摘要。")
    run = stream(tmp_path, "label", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", second)])
    assert results(run)["checked_has_evidence"]["ok"]
    bad = second.replace("- 00:53 S7", "- 周六夜里地铁 30 分钟一班；00:53 S7").replace("（时刻表查询）。", "（时刻表查询）。\n- 地铁 30 分钟一班（城市网站）。")
    run = stream(tmp_path, "label_bad", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", bad)])
    assert not results(run)["checked_has_evidence"]["ok"]




def test_now_used_as_a_word_is_not_a_clock_claim(tmp_path):
    # v2d fix_on_r1: "不用现在就冲去机场，最晚 05:23" names a departure, not the time now.
    first = GOOD_FIRST.replace(GOOD_HEAD, "不用现在就冲去机场，00:40 出门，坐 00:53 的火车，01:15 到。")
    run = stream(tmp_path, "word", [("2026-10-31T23:12:40Z", first), ("2026-10-31T23:14:30Z", GOOD_SECOND)])
    assert results(run)["clock_in_first_part"]["ok"]


def transit_evidence(connections):
    """The raw, sourced transit evidence that the service includes with its display connections."""
    return dict(TRANSIT, evidence={"id": "EV-261101-v2gabc", "kind": "transit", "fetched_at": "2026-11-01T00:10:00+01:00",
                                   "value": connections, "window": TRANSIT["window"], "synthetic": False})


def transit_ride(depart, arrive, line="S7"):
    return {"mode": "train", "line": line, "depart": depart, "arrive": arrive,
            "origin": "Grauwald Hbf", "destination": "Flughafen Grauwald"}


@pytest.mark.parametrize("later, ok", [(True, False), (False, True)])
def test_window_scoring_uses_the_departures_found_not_only_the_window_end(tmp_path, later, ok):
    itineraries = [{"legs": [transit_ride("00:53", "01:15")]}]
    if later:
        itineraries.append({"legs": [transit_ride("01:23", "01:45")]})
    result = transit_evidence(itineraries)
    second = GOOD_SECOND + "\n00:53 之后到 01:30 没有火车。"
    run = stream(tmp_path, "later_departure", [("2026-10-31T23:12:40Z", GOOD_FIRST),
                                               ("2026-10-31T23:14:30Z", second)], result=result)
    assert results(run)["only_inside_window"]["ok"] is ok


@pytest.mark.parametrize("transfer, compact, ok", [(True, False, False), (False, False, True),
                                                   (True, True, False), (False, True, True)])
def test_direct_scoring_keeps_transfers_in_full_and_compact_results(tmp_path, transfer, compact, ok):
    legs = [transit_ride("00:53", "01:05" if transfer else "01:15")]
    if transfer:
        legs.append(transit_ride("01:07", "01:15", "U2"))
    result = transit_evidence([{"legs": legs}])
    if compact:
        result = dict(TRANSIT, connections=[{"leave": "00:53", "reach": "01:15", "legs": [
            {"depart": leg["depart"], "arrive": leg["arrive"], "line": leg["line"],
             "from": leg["origin"], "to": leg["destination"], "walk": False} for leg in legs]}])
    second = GOOD_SECOND + "\n00:53 坐 S7 直达机场，不用换车。"
    run = stream(tmp_path, "direct", [("2026-10-31T23:12:40Z", GOOD_FIRST),
                                      ("2026-10-31T23:14:30Z", second)], result=result)
    assert results(run)["direct_needs_one_ride"]["ok"] is ok


def test_normalized_connections_remain_available_in_the_next_replay_turn(tmp_path):
    result = transit_evidence([{"legs": [transit_ride("00:53", "01:05"), transit_ride("01:07", "01:15", "U2")]}])
    stream(tmp_path, "later_turn", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", GOOD_SECOND)], result=result)
    run = stream(tmp_path, "later_turn", [("2026-10-31T23:20:40Z", GOOD_FIRST.replace("00:12", "00:20")),
                                         ("2026-10-31T23:22:30Z", GOOD_SECOND.replace("00:14", "00:22") + "\n00:53 坐 S7 直达机场。")],
                 lookup=WEB, result={}, turn=2)
    assert not checks.run_checks(run)["turns"]["turn2"]["direct_needs_one_ride"]["ok"]


@pytest.mark.parametrize("transfer, full_evidence", [(True, True), (False, True), (True, False), (False, False)])
def test_latest_departure_results_supply_the_same_direct_check(tmp_path, transfer, full_evidence):
    rides = [{"line": "S7", "depart": "00:53", "arrive": "01:05" if transfer else "01:15",
              "from": "Grauwald Hbf", "to": "Umstieg" if transfer else "Flughafen Grauwald"}]
    if transfer:
        rides.append({"line": "U2", "depart": "01:07", "arrive": "01:15", "from": "Umstieg", "to": "Flughafen Grauwald"})
    value = {"departures": [{"leave": "00:40", "reach": "01:15", "rides": rides}],
             "window": dict(TRANSIT["window"], covers_target=False)}
    result = dict(value)
    if full_evidence:
        result["evidence"] = {"id": "EV-261101-v2glat", "kind": "latest_departure", "value": value,
                              "fetched_at": "2026-11-01T00:10:00+01:00", "synthetic": False}
    run = stream(tmp_path, "latest_direct", [("2026-10-31T23:12:40Z", GOOD_FIRST),
                                             ("2026-10-31T23:14:30Z", GOOD_SECOND + "\n00:53 坐 S7 直达机场。")],
                 lookup={"name": "mcp__tripwright__query", "input": {"kind": "latest_departure"}}, result=result)
    assert results(run)["direct_needs_one_ride"]["ok"] is (not transfer)


def coverage_result(start, end, departures, compact):
    raw = [{"legs": [transit_ride(depart, arrive)]} for depart, arrive in departures]
    result = transit_evidence(raw)
    window = {"from": start, "to": end, "by": "departure", "continuous": True, "covered": True, "until": end}
    result["window"] = result["evidence"]["window"] = window
    result["connections"] = [{"leave": depart, "reach": arrive, "legs": [
        {"depart": depart, "arrive": arrive, "line": "S7", "from": "Grauwald Hbf", "to": "Flughafen Grauwald", "walk": False}]}
        for depart, arrive in departures]
    if compact:
        del result["evidence"]["value"]
    return result


def coverage_run(tmp_path, samples, claim):
    first = "计划里的交通时刻待核。\n按现在 20:45 算。\n没核：今晚车次，第二段核完再发。"
    second = "# 第二段 · 核过了（20:46）\n和第一段不同：车次如下。\n" + claim + "\n## 完整安排\n按查到的班次出发。"
    run = stream(tmp_path, "a9_coverage", [("2026-11-01T19:45:10Z", first), ("2026-11-01T19:46:10Z", second)], result=samples[0])
    # Keep each lookup distinct: a later query cannot fill an earlier query's unsearched gap.
    path = run / "turn1.stream.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    extra = []
    for index, sample in enumerate(samples[1:], 2):
        extra.extend([
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"a9-{index}",
                "name": "mcp__tripwright__query", "input": {"kind": "transit"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"a9-{index}",
                "content": [{"type": "text", "text": json.dumps(sample)}]}]}},
        ])
    events[-2:-2] = extra
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    return results(run)


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("claim, split, ok", [
    ("23:10 的火车别指望了。", False, False),  # outside all searched spans
    ("21:45 的火车别指望了。", True, False),  # between two queries, despite a late window end
    ("21:10 的火车别指望了。", False, False),  # the tool actually found this departure
    ("21:45 的火车别指望了。", False, True),  # covered, and the tool did not find this departure
])
def test_a9_denials_use_the_separate_lookup_spans(tmp_path, compact, claim, split, ok):
    samples = ([coverage_result("20:45", "21:30", [("21:10", "21:25")], compact),
                coverage_result("22:00", "23:00", [("22:40", "22:55")], compact)] if split else
               [coverage_result("20:45", "23:00", [("21:10", "21:25"), ("22:40", "22:55")], compact)])
    assert coverage_run(tmp_path, samples, claim)["denied_needs_coverage"]["ok"] is ok


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("split, omitted, ok", [(True, False, False), (False, True, False), (False, False, True)])
def test_a9_remaining_count_needs_continuous_coverage_and_every_departure(tmp_path, compact, split, omitted, ok):
    if split:
        samples = [coverage_result("20:45", "21:30", [("21:10", "21:25")], compact),
                   coverage_result("22:00", "23:00", [("22:40", "22:55")], compact)]
    else:
        departures = [("21:10", "21:25"), ("22:40", "22:55")]
        if omitted:
            departures.insert(1, ("21:50", "22:05"))
        samples = [coverage_result("20:45", "23:00", departures, compact)]
    claim = "误了 20:50 那班，之后还有 2 班：21:10、22:40。"
    assert coverage_run(tmp_path, samples, claim)["count_needs_continuous"]["ok"] is ok


@pytest.mark.parametrize("end, ok", [("01:00", True), ("00:30", False)])
def test_a9_remaining_count_keeps_the_next_day_window(tmp_path, end, ok):
    departures = [("00:23", "00:45"), ("00:53", "01:15")] if ok else [("00:23", "00:45")]
    sample = coverage_result("23:00", end, departures, compact=True)
    claim = "误了 23:53 那班，之后还有 2 班：00:23、00:53。"
    assert coverage_run(tmp_path, [sample], claim)["count_needs_continuous"]["ok"] is ok


# --- A10 (D-67): the second part's first line keeps the service's frame; the first part is turn's first_part ------------

def ride(line, origin, destination, depart, arrive, mode="rail") -> dict:
    return {"mode": mode, "walk": False, "line": line, "from": origin, "to": destination, "depart": depart, "arrive": arrive}


# latest_departure without a deadline, compact, as a host gets it (fictional places, made-up times)
LAST_LINE = ("今晚最后一班：最晚 00:40 出门，换 1 次车：00:43 U2 Kletterhalle Nord → Grauwald Hbf 00:50，换 00:53 S7 Grauwald Hbf → "
             "Flughafen Grauwald 01:15；01:15 到；下一班 明早 04:22 S7 开")
LAST_RESULT = {
    "kind": "latest_departure", "status": "sourced", "first_line": LAST_LINE,
    "target": {"meaning": "last_connection", "deadline": None, "date": "2026-11-01", "time": "03:00", "until": "03:00",
               "until_at": "2026-11-01T03:00:00+01:00", "plan_by": None, "limit": None},
    "sentences": [LAST_LINE, "从 00:10 到 03:00 连续查全（2 次查询，每页接着上一页查），9 班；03:00 之后出发的没查"],
    "last": {"leave": "00:40", "reach": "01:15", "boards": {"stop": "Kletterhalle Nord", "time": "00:43", "line": "U2"},
             "alights": {"stop": "Flughafen Grauwald", "time": "01:15", "line": "S7"}, "cancelled": False,
             "legs": [{"mode": "walk", "walk": True, "from": "START", "to": "Kletterhalle Nord", "depart": "00:40", "arrive": "00:43"},
                      ride("U2", "Kletterhalle Nord", "Grauwald Hbf", "00:43", "00:50", "subway"),
                      ride("S7", "Grauwald Hbf", "Flughafen Grauwald", "00:53", "01:15")]},
    "last_without_extra_ticket": None, "last_train": None, "tonight_ends_after_last": True,
    "next_after": {"leave": "04:22", "reach": "04:44", "boards": {"stop": "Grauwald Hbf", "time": "04:22", "line": "S7"},
                   "legs": [ride("S7", "Grauwald Hbf", "Flughafen Grauwald", "04:22", "04:44")]},
    "window": {"from": "2026-11-01T00:10:00+01:00", "to": "2026-11-01T03:00:00+01:00", "by": "departure", "count": 9,
               "text": "从 00:10 到 03:00 连续查全（2 次查询，每页接着上一页查），9 班；03:00 之后出发的没查", "continuous": True,
               "covered": True, "until": "2026-11-01T03:00:00+01:00", "pages": 2, "covers_target": True, "target": "2026-11-01T03:00:00+01:00"},
    "evidence": {"id": "EV-261101-a10a10", "kind": "latest_departure", "source": "transitous", "fetched_at": "2026-10-31T23:11:00+00:00",
                 "valid_until": None, "stale": False, "synthetic": False}}
LAST_CALL = {"name": "mcp__tripwright__query", "input": {"kind": "latest_departure", "arguments": {"origin": "kletterhalle", "destination": "airport"}}}


def framed_second(first_line: str) -> str:
    return f"# 第二段 · 核过了（00:14）\n{first_line}\n和第一段不同：最晚已查到。\n## 完整安排\n00:40 出门 · 坐 U2 到 Grauwald Hbf"


@pytest.mark.parametrize("first_line, ok", [
    (LAST_LINE, True),  # the service's first line
    ("最晚 00:20 出门，00:58 到机场。", False),  # a9b's shape: a latest time the service's frame does not hold
])
def test_a10_the_second_parts_first_line_keeps_the_services_frame(tmp_path, first_line, ok):
    run = stream(tmp_path, "frame", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", framed_second(first_line))],
                 lookup=LAST_CALL, result=LAST_RESULT)
    report = checks.run_checks(run)
    frame = report["more"]["turn1"]["frame_matches_server"]
    assert frame["ok"] is ok, frame
    if ok:  # the server's own line also passes the window check in a replay, which sees only the compact result
        assert report["turns"]["turn1"]["only_inside_window"]["ok"], report["turns"]["turn1"]["only_inside_window"]
    else:
        assert "00:20" in frame["detail"] and "今晚最后一班" in frame["detail"]


DRAFT = "按现在 00:12 算：真正最晚要查，第二段给。\n没核：今晚最后一班、最晚几点出门，第二段核完再发。"


def turn_stream(tmp_path: Path, name: str, sent: str) -> Path:
    """A turn whose `turn` call returned DRAFT as first_part, then the first part sent and a second part."""
    run = stream(tmp_path, name, [("2026-10-31T23:12:40Z", sent), ("2026-10-31T23:14:30Z", GOOD_SECOND)])
    path = run / "turn1.stream.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    turn = [{"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
                {"type": "tool_use", "id": "turn1", "name": "mcp__tripwright__turn", "input": {"text": "最晚几点出发去机场？"}}]}},
            {"type": "user", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_result", "tool_use_id": "turn1", "content": [
                {"type": "text", "text": json.dumps({"first_part": {"text": DRAFT, "lines": DRAFT.split("\n"), "asks_latest": True}},
                                                    ensure_ascii=False)}]}]}}]
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events[:1] + turn + events[1:]) + "\n", encoding="utf-8")
    return run


@pytest.mark.parametrize("sent, ok", [
    (DRAFT, True),  # as it is (two lines: the first part however short)
    ("Lin，" + DRAFT.replace("你", "您"), True),  # a form of address and 您
    ("As of 00:12, the real latest departure needs checking; part 2 follows.\n"
     "Not checked: tonight's last connection and when to leave; part 2 follows.", True),  # another language, same lines and times
    (STATUS := "Right now I'm checking tonight's trains and the latest time you can leave. The second part will follow.", False),  # a9b
    ("按现在 00:12 算：最晚 21:50 出门，坐 22:42 的车。\n没核：今晚最后一班、最晚几点出门，第二段核完再发。", False),  # rewritten line 1
])
def test_a10_the_first_part_is_turns_first_part_sent_as_it_is(tmp_path, sent, ok):
    report = checks.run_checks(turn_stream(tmp_path, "copied", sent))
    copied = report["more"]["turn1"]["first_part_copied"]
    assert copied["ok"] is ok, copied
    if ok:
        assert report["turns"]["turn1"]["clock_in_first_part"]["ok"], "the copied draft is the first part"
    elif sent == STATUS:  # what was sent instead of the draft is compared, and has neither its lines nor its clock
        assert copied["detail"].startswith("in another language, but 1 lines") and not report["turns"]["turn1"]["clock_in_first_part"]["ok"]
    else:
        assert copied["detail"].startswith("line 1 is")


def test_a10_a_stream_without_a_turn_call_has_no_draft_to_compare(tmp_path):
    run = stream(tmp_path, "noturn", [("2026-10-31T23:12:40Z", GOOD_FIRST), ("2026-10-31T23:14:30Z", GOOD_SECOND)])
    more = checks.run_checks(run)["more"]["turn1"]
    assert more["first_part_copied"]["ok"] and more["frame_matches_server"]["ok"]


# a10c: the hard node behind a last-connection result, read as its own lookup (replay turn 2 L17)
NODE_LINE = ("明早直接从 Kletterhalle Nord 去：要 05:45 前到，最晚 04:40 出门，换 1 次车：04:43 U2 Kletterhalle Nord → Grauwald Hbf 04:50，"
             "换 04:53 S7 Grauwald Hbf → Flughafen Grauwald 05:15；05:15 到（今晚就到 Flughafen Grauwald 的话用不上）")
NODE_CONNECTION = {"leave": "04:40", "reach": "05:15", "boards": {"stop": "Kletterhalle Nord", "time": "04:43", "line": "U2"},
                   "legs": [{"mode": "walk", "walk": True, "from": "START", "to": "Kletterhalle Nord", "depart": "04:40", "arrive": "04:43"},
                            ride("U2", "Kletterhalle Nord", "Grauwald Hbf", "04:43", "04:50", "subway"),
                            ride("S7", "Grauwald Hbf", "Flughafen Grauwald", "04:53", "05:15")]}
# another origin's lookup: a bus from the station that leaves after 04:40 and arrives by 05:45
STATION_BUS = {"connections": [{"leave": "05:05", "reach": "05:35", "boards": {"stop": "Grauwald Hbf Süd", "time": "05:05", "line": "X9"},
                                "legs": [ride("X9", "Grauwald Hbf Süd", "Flughafen Grauwald", "05:05", "05:35", "bus")]}],
               "window": {"from": "2026-11-01T04:30:00+01:00", "to": "2026-11-01T05:30:00+01:00", "by": "departure", "count": 1, "text": "…"},
               "evidence": {"id": "EV-261101-a10c01", "kind": "transit", "fetched_at": "2026-10-31T23:11:30+00:00"}}


@pytest.mark.parametrize("with_connection, ok", [(True, True), (False, False)])
def test_a10c_the_hard_nodes_own_lookup_backs_its_latest_departure(tmp_path, with_connection, ok):
    node = {"anchor_id": "hn-queue", "time": "05:45", "deadline": "plan",
            "latest": {"leave": "04:40", "board": "04:43", "first_line": NODE_LINE.split("：", 1)[1], "status": "sourced",
                       **({"covers_target": True, "target": {"time": "05:45", "plan_by": "05:45", "limit": "07:05"},
                           "connection": NODE_CONNECTION} if with_connection else {})}}
    run = stream(tmp_path, f"node{with_connection}", [("2026-10-31T23:12:40Z", GOOD_FIRST),
                                                       ("2026-10-31T23:14:30Z", framed_second(LAST_LINE).replace("## 完整安排", NODE_LINE + "\n## 完整安排"))],
                 lookup=LAST_CALL, result=dict(LAST_RESULT, hard_node=node))
    path = run / "turn1.stream.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    extra = [{"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
                 {"type": "tool_use", "id": "t9", "name": "mcp__tripwright__query", "input": {"kind": "transit"}}]}},
             {"type": "user", "parent_tool_use_id": None, "message": {"content": [{"type": "tool_result", "tool_use_id": "t9", "content": [
                 {"type": "text", "text": json.dumps(STATION_BUS, ensure_ascii=False)}]}]}}]
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events[:3] + extra + events[3:]) + "\n", encoding="utf-8")
    window = checks.run_checks(run)["turns"]["turn1"]["only_inside_window"]
    assert window["ok"] is ok, window  # without the node's connection (the replay's result shape) the line has no lookup
