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
from tripwright.service import FIRST_ARCHIVED, ROAD_OPENING, SAY_BEFORE_SAVE, SECOND_TITLES, SEPARATE_PARTS, WORK_NOTES


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


# --- Round 4 review 3 (2026-11-19): the short message has to be useful, archived times stay unchecked, one part per
# message, and no work notes. Baseline (`AFTER.md` §5): 5 of 10 short messages gave nothing to act on, one read the
# stored “last service 23:53” as tonight's last service, 5 merged both parts into one message, and one opened with
# “I'll start by recording your message.” Each rule below is checked in the text `turn` returns, because that is what
# the host reads before it writes.
def test_the_short_message_must_carry_one_thing_to_act_on(seeded):
    turn = seeded.turn("alice", "我饿了，现在去哪吃？", "r4-useful-1", scenario="quick")
    assert turn["first_part"] is None
    assert "至少给一条能照着走的内容" in turn["next"] and "只说“我去查”不算" in turn["next"]
    assert "存的计划里没有这一段，先给你一个保底" in turn["next"]
    # the whole flow of the first turn says the same, so the two fields of one receipt cannot contradict each other
    assert "只说“我去查”不算" in turn["flow"]["full"]
    english = seeded.turn("alice", "I'm hungry, where can I eat now?", "r4-useful-1-en", scenario="quick")
    assert "one thing to act on" in english["next"] and "not just “I'll check”" in english["next"]
    assert "fallback" in english["next"]


def test_plan_times_in_the_opening_stay_archived_and_unchecked(seeded):
    latest = seeded.turn("alice", "今晚最晚几点回车站？", "r4-archived-1", scenario="quick")
    assert "存档写 X，还没核" in latest["next"]
    # a latest or last-service question: the opening gives the stored value and keeps the conclusion for part two
    assert "问最晚、末班时这条只给存档值加“没核”，结论放第二段" in latest["next"]
    assert "存档写 X，还没核" in latest["flow"]["full"]
    english = seeded.turn("alice", "What is the last train back tonight?", "r4-archived-1-en", scenario="quick")
    assert "“the plan stores X, not checked yet”" in english["next"] and "no conclusion" in english["next"]


def test_the_first_part_goes_alone_and_the_plan_times_in_it_stay_archived(service, plan):
    plan["days"][0]["anchors"] = [dict(id="home-train", time="23:00", text="Train home", subject=["p1"],
                                       place="oslo-station", destination="oslo-station", line="DEMO")]
    created = service.turn("alice", "创建示例行程。")
    draft = service.propose("alice", ctx_id=created["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    turn = service.turn("alice", "今晚最晚几点回车站？", scenario="quick",
                        points=[dict(kind="go", said="回车站", place="oslo-station")])
    assert turn["first_part"] is not None
    # the first field the host reads sends the first part and ends the reply: the second part is a separate message,
    # its heading not in the same message
    assert SEPARATE_PARTS["zh"] in turn["next"] and WORK_NOTES["zh"] in turn["next"]
    assert SECOND_TITLES["zh"] not in turn["next"]
    # what comes after the first part repeats both rules, so a host that reads only this field cannot merge them either
    assert SEPARATE_PARTS["zh"] in turn["after_first_part"] and WORK_NOTES["zh"] in turn["after_first_part"]
    assert "存档写 X，还没核" in turn["after_first_part"] and "结论放第二段" in turn["after_first_part"]
    english = service.turn("alice", "What is the last train back tonight?", scenario="quick",
                           points=[dict(kind="go", said="back to the station", place="oslo-station")])
    assert SEPARATE_PARTS["en"] in (english["next"] + english["after_first_part"])
    assert FIRST_ARCHIVED["en"] in english["after_first_part"]


def test_every_message_to_the_user_is_the_answer_itself(seeded):
    road = seeded.turn("alice", "我饿了，现在去哪吃？", "r4-notes-1", scenario="quick")
    assert WORK_NOTES["zh"] in road["next"]
    assert "我不先记录消息" not in road["next"]  # the rule names the bad note to keep out
    status = seeded.turn("alice", "看看现在的计划。", "r4-notes-2", scenario="status")
    assert WORK_NOTES["zh"] in status["next"]
    english = seeded.turn("alice", "I'm hungry, where can I eat now?", "r4-notes-3", scenario="quick")
    assert WORK_NOTES["en"] in english["next"]
