"""Options for competing preferences: whole-day totals, user orders and independent trade-offs (design 9.5, red-team E5)."""
from tripwright.render.answer import render_answer
from tripwright.render.options import build_options, options_problems, unauthorized_choices

SAVE, EASY, MORE, CALM = "I-300713-s4v", "I-300715-e5y", "I-300716-m3k", "I-300716-c4n"
NAMES = {SAVE: "省钱", EASY: "轻松", MORE: "多看", CALM: "从容"}
NONE = [{"between": [SAVE, EASY], "status": "none", "order": None, "source_event": None}]
USER = [{"between": [SAVE, EASY], "status": "user", "order": [EASY, SAVE], "source_event": "E-300716-o1r"}]


def small_points() -> list[dict]:
    """Three trade-offs that each stay under a per-point threshold (10 minutes on foot for €4)."""
    return [{"id": f"p{index}", "where": where, "topic": "交通",
             "choices": {SAVE: {"how": "走路", "walk_minutes": 10, "euros": 0}, EASY: {"how": "坐车", "walk_minutes": 0, "euros": 4}}}
            for index, where in enumerate(("车站到码头", "码头到老城", "老城到住处"), start=1)]


def test_three_small_trade_offs_add_up_over_the_day_and_both_sides_stay_with_the_user():
    result = build_options(small_points(), NONE, names=NAMES, decide_before="出门前定")
    options = result["options"]
    assert [variant["label"] for variant in options["variants"]] == ["A 省钱", "B 轻松"]
    assert options["variants"][1]["diff"] == "比 A 少走 30 分钟，每人多花 €12.00。"
    assert result["totals"]["A"]["walk_minutes"] == 30 and result["totals"]["B"]["euros"] == 12
    assert unauthorized_choices(small_points(), NONE, options["variants"]) == {"unauthorized": 0, "extra_choices": 0, "variants": 2}
    assert options_problems(options) == [] and options["recommend"] is None and options["default"] is None


def test_a_user_order_is_applied_as_stated_and_adds_no_choice():
    result = build_options(small_points(), USER, names=NAMES)
    assert result["options"] is None
    assert {entry["item_id"] for entry in result["applied"]} == {EASY}
    assert result["applied_lines"] == ["按你定的次序（原话 E-300716-o1r）：车站到码头坐车；码头到老城坐车；老城到住处坐车。"]


def independent_points() -> list[dict]:
    return small_points() + [{"id": "p4", "where": "下午", "topic": "下午",
                              "choices": {MORE: {"how": "看两个馆", "duration_minutes": 240},
                                          CALM: {"how": "只看一个馆，留 2 小时散步", "duration_minutes": 240}}}]


def test_two_independent_trade_offs_go_into_complete_options_and_can_be_mixed():
    result = build_options(independent_points(), NONE, names=NAMES, decide_before="出门前定")
    options = result["options"]
    assert len(options["variants"]) == 2
    assert [variant["item_ids"] for variant in options["variants"]] == [[SAVE, MORE], [EASY, CALM]]
    assert all("下午" in variant["how"] and "车站到码头" in variant["how"] for variant in options["variants"])
    assert options["common"][-1] == "交通、下午可以分开选，比如交通按 A、下午按 B。"
    assert unauthorized_choices(independent_points(), NONE, options["variants"])["unauthorized"] == 0
    render_answer(dict(load_first(), options=options))  # the renderer accepts what the builder made


def test_options_that_offer_only_one_side_or_reopen_a_user_order_are_counted():
    one_sided = [{"label": "A 省钱", "item_ids": [SAVE]}, {"label": "B 省钱多看", "item_ids": [SAVE, MORE]}]
    counted = unauthorized_choices(independent_points(), NONE, one_sided)
    assert counted["unauthorized"] == 4  # three transport points chose 省钱 for the user, the afternoon chose 多看
    reopened = [{"label": "A", "item_ids": [SAVE]}, {"label": "B", "item_ids": [EASY]}]
    assert unauthorized_choices(small_points(), USER, reopened)["extra_choices"] == 3


def test_a_missing_figure_is_marked_not_checked_rather_than_left_out():
    points = small_points()
    points[1]["choices"][EASY]["euros"] = None
    diff = build_options(points, NONE, names=NAMES)["options"]["variants"][1]["diff"]
    assert diff == "比 A 少走 30 分钟，花费没核。"


def test_a_three_way_point_gives_three_options():
    point = {"id": "p1", "where": "去机场", "choices": {SAVE: {"how": "坐公交", "euros": 3, "walk_minutes": 15},
                                                     EASY: {"how": "坐机场快线", "euros": 12, "walk_minutes": 5},
                                                     "I-300716-t9x": {"how": "打车", "euros": 45, "walk_minutes": 0}}}
    options = build_options([point], [], names=dict(NAMES, **{"I-300716-t9x": "最快"}))["options"]
    assert [variant["label"] for variant in options["variants"]] == ["A 省钱", "B 轻松", "C 最快"]
    assert options["variants"][2]["diff"] == "比 A 少走 15 分钟，每人多花 €42.00。"


def load_first() -> dict:
    from render_samples import load
    return load("answer_first.json")
