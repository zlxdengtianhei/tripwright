"""render_answer: byte-for-byte against the contract samples, and the option rules of CONTRACTS 3.4."""
import copy

import pytest
from render_samples import load, text

from tripwright.render.answer import render_answer

SAMPLES = ["answer_quick", "answer_first", "answer_second", "answer_en"]


@pytest.mark.parametrize("name", SAMPLES)
def test_rendered_answer_matches_the_sample_byte_for_byte(name):
    data = load(f"{name}.json")
    before = copy.deepcopy(data)
    assert render_answer(data) == text(f"{name}.md")
    assert data == before


def test_conflict_notice_is_the_first_line():
    assert render_answer(load("answer_quick.json")).splitlines()[0] == "上次的安排已在别处改动，需要重排。"


def test_every_leg_and_place_says_which_app_and_what_to_search():
    second = render_answer(load("answer_second.json"))
    assert "13:24 坐 471 路 · ÖBB App 搜 Altmoor Bahnhof → Seeufer Nord · [ÖBB / Scotty](" in second
    quick = render_answer(load("answer_quick.json"))
    assert "北湖岸步道口 · 湖边路起点 · Google 地图搜 Uferweg Nord, Seeufer · [Google 地图](" in quick


@pytest.mark.parametrize("state,verdict,accepted,title", [
    ("received", "passed", True, "# 第二段 · 核过的安排（草稿）"),
    ("effective", "passed", True, "# 第二段 · 核过的安排"),
    ("effective", "incomplete", True, "# 第二段 · 安排（没核完）"),
    ("pending_sync", "passed", False, "# 第二段 · 安排（没核完）（草稿）"),
])
def test_second_reply_title_follows_verdict_commit_and_write_state(state, verdict, accepted, title):
    data = load("answer_second.json")
    data["write"]["state"], data["result"]["verdict"], data["commit"]["accepted"] = state, verdict, accepted
    assert render_answer(data).splitlines()[0] == title


def options_answer(**changes) -> dict:
    data = load("answer_first.json")
    data["options"].update(changes)
    return data


@pytest.mark.parametrize("changes,message", [
    ({"variants": [dict(load("answer_first.json")["options"]["variants"][0], diff="比 B 多走 40 分钟"),
                   load("answer_first.json")["options"]["variants"][1]]}, "baseline"),
    ({"recommend": {"label": "B", "reason": "你更在乎轻松。"}}, "cares about more"),
    ({"recommend": {"label": "B", "reason": "B 比较好。"}}, "factual reason"),
    ({"default": {"label": "A", "reason": "能改回。"}}, "must be made now"),
    ({"common": ["省钱这条权衡掉了。"]}, "traded off"),
    ({"variants": [load("answer_first.json")["options"]["variants"][0],
                   dict(load("answer_first.json")["options"]["variants"][1], diff="更轻松")]}, "numbers"),
])
def test_options_that_choose_for_the_user_or_lack_a_baseline_are_refused(changes, message):
    with pytest.raises(ValueError, match=message):
        render_answer(options_answer(**changes))


def test_a_factual_recommendation_and_an_immediate_default_render_with_their_labels():
    data = options_answer(decide_before=None, recommend={"label": "B", "reason": "A 只剩 8 分钟余量。"},
                          default={"label": "A", "reason": "到湖边前还能改。"})
    lines = render_answer(data).splitlines()
    assert "现在定 A 或 B：" in lines and "推荐 B：A 只剩 8 分钟余量。" in lines and "默认 A：到湖边前还能改。" in lines


def test_traded_off_wording_is_refused_when_two_preferences_have_no_user_order():
    orders = load("pack_narrow.json")["orders"]
    data = load("answer_quick.json")
    data["answer"] = ["坐 471 路去湖边；省钱权衡掉了。"]
    with pytest.raises(ValueError, match="traded off"):
        render_answer(data, orders=orders)
    assert render_answer(data)  # without the pack's orders the renderer cannot tell, so it renders
    data["answer"] = ["出站往湖的方向走 6 分钟，就到步道口。"]
    assert render_answer(data, orders=orders) == text("answer_quick.md")
