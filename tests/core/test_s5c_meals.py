"""S5c (Claude Code replay s5b_mcponly_r2, question 2): check_answer's meals_named refused a second part that named two
places to eat with their hours and a source, four times running, because each place's Chinese line put the dot right
after a full-width bracket (“麦当劳（中央车站店，一层）· 今晚开到 00:00 …”) and the place lines were only recognized with
“ · ”. The host gave up and sent the part with a line about the failing check. The dot separates with or without spaces;
a place without hours is still refused.

The sample is the replay's draft cut to its meal lines; the places and pages are the public Vienna ones it named."""
from __future__ import annotations

from tripwright.render import check_answer as checks

QUESTION = "我现在刚出来，然后我有点饿，我想吃点东西，然后我还想去市区里面看看有没有什么万圣节的活动。"
DRAFT = """# 第二段 · 吃饭、万圣节、去机场（21:55）
今晚不用赶：夜里一直有车到 Flughafen Wien (VIE), Schwechat；火车最后一班 00:53 REX 22787（最晚 00:53 在 Wien Hauptbahnhof 上车）
按现在 21:55 算，你刚出 Blockfabrik 抱石馆，先去中央车站麦当劳吃饭
McDonald's, Wien Hauptbahnhof (EG)
麦当劳（中央车站店，一层）· 今晚开到 00:00（核过，hauptbahnhofcity.oebb.at 原句“Mo-Sa: 06:00-00:00”）· 离你约 1.3 公里，用 Google Maps 搜 McDonald's Wien Hauptbahnhof
Burger King, Wien Hauptbahnhof (EG)
汉堡王（中央车站店，一层）· 今晚开到 23:00（核过，hauptbahnhofcity.oebb.at 原句“Mo-So: 09:00-23:00”）· 同一栋楼，用 Google Maps 搜 Burger King Wien Hauptbahnhof
## 完整安排
方案一（轻松、省钱）：吃完坐计划里的 22:42 RJX 19959，22:39 从麦当劳出门就赶得上，早到机场早歇。"""
# the trip's stored places (open_places listed them with their hours) and the two pages the host recorded with web_evidence
PLACES = [dict(id="hbf-mcdonalds", name="麦当劳（中央车站店，一层）", name_local="McDonald's, Wien Hauptbahnhof (EG)",
               opening_hours=["Mo-Sa 06:00-24:00", "Su 07:00-24:00"]),
          dict(id="hbf-burger-king", name="汉堡王（中央车站店，一层）", name_local="Burger King, Wien Hauptbahnhof (EG)",
               opening_hours=["Mo-Su 09:00-23:00"])]
PAGES = dict(checks.NO_EVIDENCE, sources=[
    "https://hauptbahnhofcity.oebb.at/de/gastronomie/mcdonalds McDonald's Wien Hauptbahnhof 周六开到 00:00 Mo-Sa: 06:00-00:00",
    "https://hauptbahnhofcity.oebb.at/de/gastronomie/burger-king Burger King Wien Hauptbahnhof 每天开到 23:00 Mo-So: 09:00-23:00"])


def test_two_places_whose_dot_follows_a_full_width_bracket_are_named_with_hours_and_source():
    # bad sample: “只有 0 处具名吃饭地点带营业时间和出处”, from the stored places and from the recorded pages alike
    assert checks.meal_problems(DRAFT, QUESTION, checks.NO_EVIDENCE, PLACES) == []
    assert checks.meal_problems(DRAFT, QUESTION, PAGES, []) == []
    assert "meals_named" not in {entry["check"] for entry in checks.check_draft(DRAFT, "second", 21 * 60 + 55, PAGES,
                                                                                    question=QUESTION, places=PLACES)}
    # the same with spaces around the dot, the way the fix text shows it
    assert checks.meal_problems(DRAFT.replace("）· ", "） · "), QUESTION, checks.NO_EVIDENCE, PLACES) == []


def test_the_same_places_without_their_hours_are_still_refused():
    # control: the replay's first draft named a place with no hours; with the dot after the bracket it is still caught
    bare = DRAFT.replace("· 今晚开到 00:00（核过，hauptbahnhofcity.oebb.at 原句“Mo-Sa: 06:00-00:00”）", "").replace(
        "· 今晚开到 23:00（核过，hauptbahnhofcity.oebb.at 原句“Mo-So: 09:00-23:00”）", "")
    assert "开到" not in bare and "）· 离你" in bare and "）· 同一栋楼" in bare
    [found] = checks.meal_problems(bare, QUESTION, PAGES, PLACES)
    assert found["check"] == "meals_named" and "只有 0 处" in found["why"]
    assert "McDonald's, Wien Hauptbahnhof (EG)（没写营业时间）" in found["why"] and "Burger King, Wien Hauptbahnhof (EG)（没写营业时间）" in found["why"]


def test_a_dot_inside_words_makes_no_place_line():
    # control: “U1·U4” separates nothing, so the line before it is no place name and both lines still count as body lines
    text = "# 第二段 · 核过了（21:55）\n今晚不用赶\n换乘：U1·U4 到 Schwedenplatz"
    assert checks.place_blocks(text) == []
