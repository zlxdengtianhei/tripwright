"""Round 4 review (2026-11-19): the road answer opens before the lookups, and the save comes after the answer.

The baseline (15 real questions) had no first part in 13 of them, because the workspace had no plan for that day; the
model then checked and saved for 4 to 19 minutes before its first text, and in 8 of 15 the first tool that wrote came
before that text (the user, 2026-10-29: “你先给我终端输出我现在的走法，然后再去更新网站”).

So `turn` now says so itself, in the one field the host reads first:
- a road question with no `first_part` sends one or two short sentences before any lookup, naming what is unchecked;
- in every scenario the walk or the answer comes before `propose`/`commit`, and the save and the page follow it.
"""
from __future__ import annotations

import pytest

from tripwright.flow import FULL, SHORT_ROAD
from tripwright.service import ROAD_OPENING, SAY_BEFORE_SAVE


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_REPLY_FLOW", raising=False)


def test_a_road_question_with_no_first_part_opens_with_a_short_message_before_any_lookup(seeded):
    turn = seeded.turn("alice", "我饿了，现在去哪吃？", "r4-opening-1", scenario="quick")
    assert turn["first_part"] is None
    assert ROAD_OPENING["zh"] in turn["next"] and "在查任何东西之前" in turn["next"]
    # the same order in the flow field, so the two fields of one receipt cannot contradict each other: the whole flow
    # (the first turn) says it too, and later turns carry the short line
    assert turn["flow"]["full"] == FULL["zh"]["quick_road"] and "在查任何东西之前" in turn["flow"]["full"]
    again = seeded.turn("alice", "现在去咖啡馆怎么走？", "r4-opening-2", scenario="quick")
    assert again["flow"]["reminder"] == SHORT_ROAD["zh"]
    # the second part the short message promised has its heading
    assert turn["second_part_title"] == "核过之后"
    assert len(turn["next"]) <= 600


def test_the_road_opening_and_the_order_in_english(seeded):
    turn = seeded.turn("alice", "I'm hungry, where can I eat now?", "r4-opening-en", scenario="quick")
    assert turn["first_part"] is None
    assert ROAD_OPENING["en"] in turn["next"] and SAY_BEFORE_SAVE["en"] in turn["next"]
    assert turn["flow"]["full"] == FULL["en"]["quick_road"]
    again = seeded.turn("alice", "How do I walk to the cafe?", "r4-opening-en-2", scenario="quick")
    assert again["flow"]["reminder"] == SHORT_ROAD["en"]
    assert len(turn["next"]) <= 600


def test_the_answer_comes_before_propose_and_commit_in_every_scenario(seeded):
    """A preference-only message gets the same order: an acknowledgement first, the save after it (the user's order)."""
    for number, (text, scenario) in enumerate([("把第一天的晚饭改到 19:00。", "replan"),
                                               ("我喜欢慢一点。", "plan"),
                                               ("看看现在的计划。", "status")]):
        turn = seeded.turn("alice", text, f"r4-order-{number}", scenario=scenario)
        assert turn["first_part"] is None
        assert SAY_BEFORE_SAVE["zh"] in turn["next"]
        assert "完成必要的查询和写入后" not in turn["next"]  # the old wording had the save before the answer
        assert len(turn["next"]) <= 600
