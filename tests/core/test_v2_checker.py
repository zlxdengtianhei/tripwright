"""11-04 (the user: judge an answer by whether its facts and guidance are right, not by a fixed form; checker audit
`checker-audit.md` with its probes): check_answer gives each finding a severity.

- block: the draft contradicts this question's tool results, the stored conditions or the clock, claims a check it
  has no evidence for, says a change as a direct ride, or says an unsearched span is certain. `ok` reads these only.
- hint: everything else. A draft with hints only passes: it is recorded as passed, and the summary says it may go out
  as it is. A hint's fix reads “可以改得更好”.
- Gone: first_part_lines, ends_with_later, plan_heading, second_part_lines.

Each misblock of the audit's probes (their inputs, fictional data) now passes with a hint at most; the true content
errors (a train said 别指望 that runs, “赶不上” a reachable one, “直达” a change, “5B 到 Wien Hauptbahnhof” where the 5B
ends at Praterstern, a 核过 time no evidence has, a wrong “现在”) still block."""
from __future__ import annotations

from test_a8_transit_facts import NIGHT, STREET
from test_a10_frame import Evening, query
from test_a12_first_part import BUSES
from test_a16_fixes import make  # noqa: F401 (the fixture)
from test_v2_content import Brigitta
from tripwright.render import check_answer as c

CLOCK = 21 * 60 + 55


def conn(leave, rides):
    return c.connection_row(leave, rides[-1][3], [dict(line=line, depart=depart, arrive=arrive, **{"from": origin, "to": to})
                                                  for line, depart, origin, arrive, to in rides])


def hm(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


# the probes' lookup: 22:16 S7 and 22:42 RJX to the airport; and the replay's 22:16 5B → Praterstern, U1 → Hbf, RJX
ROWS = [conn(hm("22:10"), [("S7", hm("22:16"), "Wien Hbf", hm("22:45"), "Flughafen")]),
        conn(hm("22:30"), [("RJX", hm("22:42"), "Wien Hbf", hm("23:05"), "Flughafen")])]
LOOK = [dict(kind="transit", connections=ROWS, start=hm("21:50"), end=hm("23:30"), covers=False, targets=set(),
             times=set().union(*(row["times"] for row in ROWS)), latest=set(), last=set())]
FOUND = dict(c.NO_EVIDENCE, times=LOOK[0]["times"], lookups=LOOK, windows=[hm("23:30")])
FIVE_B = conn(hm("22:10"), [("5B", hm("22:16"), "Wien Brigittaplatz", hm("22:27"), "Wien Praterstern"),
                            ("U1", hm("22:31"), "Wien Praterstern", hm("22:38"), "Wien Hauptbahnhof"),
                            ("RJX 19959", hm("22:42"), "Wien Hauptbahnhof", hm("23:00"), "Flughafen Wien Bahnhof")])
FIVE_B_LOOK = [dict(LOOK[0], connections=[FIVE_B], times=FIVE_B["times"])]
FIVE_B_FOUND = dict(c.NO_EVIDENCE, times=FIVE_B["times"], lookups=FIVE_B_LOOK, windows=[hm("23:30")])
STATION = dict(id="hbf", name="维也纳中央车站（存包、买菜）", name_local="Wien Hauptbahnhof")
HEAD = "# 第二段 · 核过了（21:55）"


def severities(text: str, part: str = "second", found: dict | None = None, **more) -> dict[str, set[str]]:
    out = {}
    for entry in c.check_draft(text, part, CLOCK, found or FOUND, **more):
        out.setdefault(entry["check"], set()).add(entry["severity"])
    return out


def blocks(text: str, part: str = "second", found: dict | None = None, **more) -> set[str]:
    return {check for check, kinds in severities(text, part, found, **more).items() if "block" in kinds}


# --- the audit's misblocks pass (a hint at most) -------------------------------------------------------------------------------

MISBLOCKS_SECOND = {
    "1 a time a train cannot be checked at": HEAD + "\n22:42 查不到实时晚点，出发前在 ÖBB 看",
    "2 the title's coverage": "# 第二段 · 核过了（查到 03:00 为止）\n不用赶。",
    "3 the platform not found": HEAD + "\n22:42 RJX 的站台没查到，到站后看屏幕",
    "5 English words": HEAD + "\nFallback: the 23:23 train from Wien Hbf.\nLet me know if you want the route.",
    "7 a conditional about an event": HEAD + "\n如果 22:30 还没吃完就来不及去看万圣节活动了",
    "8 real time said in other words": HEAD + "\n坐 22:42 RJX 去机场；实时晚点没查，出发前看 ÖBB",
    "10 a Wi-Fi name": HEAD + "\n机场 Wi-Fi 选 airport_free_wifi",
    "13 a venue's price per person": HEAD + "\n万圣节派对门票 €12/人",
    "14 a direct ride not looked up": HEAD + "\n从 Stephansplatz 坐 U1 直达 Praterstern",
    "15 no heading": "核过了：22:42 RJX 赶得上",
    "A a time worked out from checked ones": HEAD + "\n核过：22:16 S7 上车，22:45 到机场，走到值机柜台约 23:00",
    "B a ride's length": HEAD + "\n核过：坐 S7 约 29 分钟到机场",
    "C a deadline, no fallback": HEAD + "\n误了 07:05 柜台截止就上不了飞机，所以 06:50 前要到",
    "E the right count": HEAD + "\n还有 2 班：22:16 S7、22:42 RJX",
}


def test_the_audits_misblocks_of_a_second_part_now_pass():
    for name, text in MISBLOCKS_SECOND.items():
        assert blocks(text) == set(), (name, c.check_draft(text, "second", CLOCK, FOUND))
    # meals: the user ate already; two places written as a list (4, 16)
    assert blocks(HEAD + "\n不用赶。", question="我吃过了，最晚几点走") == set()
    places = [dict(name="麦当劳（中央车站）", name_local="McDonald's Wien Hbf", opening_hours=["x"]),
              dict(name="汉堡王（中央车站）", name_local="Burger King Wien Hbf", opening_hours=["x"])]
    assert blocks(HEAD + "\n- 麦当劳（中央车站）：开到 00:00\n- 汉堡王（中央车站）：开到 23:00", question="我饿了", places=places) == set()


def test_the_audits_misblocks_of_a_first_part_now_pass():
    record = dict(given=True, points=[], archived=[dict(time="07:05", text="证件柜台关")], fares=[])
    for name, text in {"6a the latest marked archived": "按现在 21:55 算\n最晚 22:39 出门赶 22:42 RJX（存档，没核）",
                       "6b a shop's latest": "按现在 21:55 算\n麦当劳最晚开到 23:00",
                       "9 “上面那段路”": "按现在 21:55 算\n上面那段路走 10 分钟",
                       "11 the clock on line 3": "不用赶，夜里一直有车\n先去中央车站吃饭\n按现在 21:55 算",
                       "12a not confirmed, in other words": "按现在 21:55 算\n证件柜台 07:05 关，这个时间还没确认",
                       "a long first part": "按现在 21:55 算\n" + "\n".join(f"第 {n} 件事" for n in range(12)),
                       "text after the later line": "按现在 21:55 算\n要核过的第二段，回我一句“继续”。\n好的"}.items():
        assert blocks(text, "first", points=record) == set(), (name, c.check_draft(text, "first", CLOCK, FOUND, points=record))


def test_a_hint_reads_as_a_way_to_do_better():
    found = c.check_draft(MISBLOCKS_SECOND["15 no heading"], "second", CLOCK, FOUND)
    heading = next(entry for entry in found if entry["check"] == "second_heading")
    assert heading["severity"] == "hint" and heading["fix"].startswith("可以改得更好")
    assert {"first_part_lines", "ends_with_later", "plan_heading", "second_part_lines"}.isdisjoint(c.FIRST_CHECKS + c.SECOND_CHECKS)


# --- the true content errors still block -----------------------------------------------------------------------------------------

def test_content_errors_still_block():
    assert blocks(HEAD + "\n22:42 RJX 别指望了") == {"denied_needs_coverage"}  # F: it runs
    assert blocks(HEAD + "\n22:42 RJX 赶不上", found=dict(FOUND, latest=1, reachable={hm("22:42")})) == {"unreachable_needs_lookup"}  # I
    change = [dict(LOOK[0], connections=[conn(hm("22:10"), [("S7", hm("22:16"), "A", hm("22:30"), "B"), ("CAT", hm("22:35"), "B", hm("22:45"), "C")])])]
    assert blocks(HEAD + "\n22:16 S7 直达机场", found=dict(FOUND, lookups=change)) == {"direct_needs_one_ride"}  # J
    assert blocks(HEAD + "\n核过：22:16 S7 上车，23:31 到机场") == {"checked_has_evidence"}  # a 核过 time nobody found
    assert blocks(HEAD + "\n核过：EV-261104-zzzzzz 说的营业时间") == {"checked_has_evidence"}  # an ID that does not count
    assert blocks(HEAD + "\n误了 22:42 RJX，后备坐 22:16 S7") == {"fallback_later"}  # two departures found, the fallback earlier
    assert blocks(HEAD + "\n22:42 RJX 没查到，以下面的为准") == {"missing_ride_found"}  # it was found
    assert blocks("现在 21:30，不用赶\n先去吃饭", "first") == {"clock_in_first_part"}  # a wrong “now”


def test_a_ride_said_to_reach_a_stop_only_a_later_ride_reaches_blocks():
    # the replay's lines (9 of 12 runs): the 5B ends at Praterstern; the U1 goes on to the station
    for line in ("走到 Wien Brigittaplatz 坐 22:16 的 5B，到 Wien Hauptbahnhof 换 RJX 19959",
                 "核过：坐 22:16 的 5B 到 Wien Hauptbahnhof，22:42 RJX 19959 去机场",
                 "坐 22:16 的 5B 到中央车站，再坐 22:42 RJX 19959"):
        found = c.check_draft(HEAD + "\n" + line, "second", CLOCK, FIVE_B_FOUND, places=[STATION])
        reach = [entry for entry in found if entry["check"] == "direct_needs_one_ride"]
        assert reach and reach[0]["severity"] == "block", line
        assert "5B 只到 Wien Praterstern" in reach[0]["why"] and "22:31 U1 Wien Praterstern → Wien Hauptbahnhof" in reach[0]["why"], line
    # controls: the ride's own stop; the change written; a line marked not checked
    for line in ("坐 22:16 的 5B 到 Wien Praterstern，换 22:31 U1 到 Wien Hauptbahnhof，再坐 22:42 RJX 19959",
                 "22:16 5B（往 Praterstern）Wien Brigittaplatz → Wien Praterstern 22:27，换 22:31 U1（往 Oberlaa）Wien Praterstern → Wien Hauptbahnhof 22:38",
                 "坐 5B 到 Praterstern 下车，换 U1 到中央车站",
                 "坐 22:16 的 5B 到 Wien Hauptbahnhof（没核）"):
        assert "direct_needs_one_ride" not in blocks(HEAD + "\n" + line, found=FIVE_B_FOUND, places=[STATION]), line


# --- the service: ok, passed, summary --------------------------------------------------------------------------------------------

def test_a_draft_with_hints_only_passes_is_recorded_and_says_it_may_go_out(make):
    service = make(Brigitta(trains=NIGHT[:5], buses=BUSES))
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    plain = "\n".join([found["first_line"], *found["sentences"][1:3]])  # no heading, no “和第一段不同”: hints
    result = query(service, "check_answer", text=plain, part="second")
    assert result["ok"] is True and result["problems"] and {entry["severity"] for entry in result["problems"]} == {"hint"}
    assert result["passed"]["sha256"] == c.draft_digest(plain)
    assert result["summary"].startswith("可以照原样发；") and "不改也行" in result["summary"]
    assert result["summary"].endswith("别向用户提这次检查、核对或工具回执。")
    # control: a block holds it and is what the summary asks to change
    wrong = query(service, "check_answer", text=plain + "\n核过：22:17 5B 到 Wien Hauptbahnhof", part="second")
    assert wrong["ok"] is False and "passed" not in wrong and wrong["summary"].startswith("要改 1 处再发：")
