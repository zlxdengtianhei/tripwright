"""trip60 (2026-11-22): every trip message opens with a short first message written before any other tool.

Replays of the user's 15 real questions (opencode with DeepSeek, the round-4 harness, clocks pinned to each question)
measured the first usable message at a median 179.5 s on `81208e1`, 13 of 15 over 60 s. Where the time went:
- `next` asked for the first message only on the road, and in the same field invited reads that came first
  (“state(view='memory') 可看”, “按坐标或站名查”); planning questions were told to look things up before answering;
- a turn refused for its points (an eighth point, a bad kind) left the host without any of this, and it read the plan,
  the memory and the files for minutes;
- the service's first_part was sent as it was, and in 2 of 3 runs it did not answer the question (“要不要赶去机场”).
With the first message rule below the same replay measured a median of 35.4 s, 12 of 15 within 60 s.
"""
from __future__ import annotations

import pytest

from tripwright.service import OPEN_FIRST, SAY_BEFORE_SAVE, WORK_NOTES


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_REPLY_FLOW", raising=False)


@pytest.mark.parametrize("text, scenario", [("我饿了，现在去哪吃？", "quick"), ("明天去哪个岩馆好？", "plan"),
                                            ("把第一天的晚饭改到 19:00。", "replan"), ("看看现在的计划。", "status")])
def test_every_trip_message_opens_with_the_first_message_and_nothing_before_it(seeded, text, scenario):
    turn = seeded.turn("alice", text, f"t60-open-{scenario}", scenario=scenario)
    assert turn["next"].startswith(OPEN_FIRST["zh"])
    # nothing in the first field invites a read or a lookup before the first message
    for invitation in ("state(view='memory')", "query(kind=", "state(view='day'", "propose", "先派"):
        assert invitation not in turn["next"], invitation
    # what follows the first message keeps the order the user set: answer first, then the save
    assert turn["after_first_part"].startswith("第一条发出去以后照这里做") and SAY_BEFORE_SAVE["zh"] in turn["after_first_part"]
    assert turn["second_part_title"] == "核过之后" and "opening_material" in turn


def test_the_system_scenario_and_the_one_message_flow_keep_their_own_next(seeded, monkeypatch):
    assert not seeded.turn("alice", "换成英文界面。", "t60-system", scenario="system")["next"].startswith(OPEN_FIRST["zh"])
    monkeypatch.setenv("TRIPWRIGHT_REPLY_FLOW", "one")
    assert not seeded.turn("alice", "我饿了，现在去哪吃？", "t60-one", scenario="quick")["next"].startswith(OPEN_FIRST["zh"])


def test_the_first_message_is_written_not_sent_as_it_is_and_needs_no_continue(service, plan):
    plan["days"][0]["anchors"] = [dict(id="home-train", time="23:00", text="Train home", subject=["p1"],
                                       place="oslo-station", destination="oslo-station", line="DEMO")]
    created = service.turn("alice", "创建示例行程。")
    draft = service.propose("alice", ctx_id=created["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    turn = service.turn("alice", "今晚最晚几点回车站？", scenario="quick",
                        points=[dict(kind="go", said="回车站", place="oslo-station")])
    assert turn["first_part"] is not None and "later" not in turn["first_part"]  # no “回我一句继续”
    assert "原样发出" not in turn["next"] and "first_part" in turn["next"] and WORK_NOTES["zh"] in turn["next"]


def test_competing_preferences_and_questions_shape_the_first_message_itself(seeded):
    """What the user would otherwise have to ask for (options, the questions in ask) is in `next`, before any tool."""
    turn = seeded.turn("alice", "明天想睡懒觉，也想早上去看日出。", "t60-options", scenario="plan")
    if turn["ask"]:
        assert "ask 里的问题照问" in turn["next"]
    # weighing every entry is for the full answer: in the replays it sent hosts to read the ledger before writing
    assert "逐条看 consider" not in turn["next"] and "逐条看 consider" in turn["after_first_part"]


def test_a_day_with_nothing_stored_says_so_in_the_opening_material(seeded, monkeypatch):
    import time
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-12T12:00:00+02:00")  # the seeded trip stores 2030-06-10 only
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = seeded.turn("alice", "我饿了，现在去哪吃？", "t60-no-day", scenario="quick")
    [empty] = [item for item in turn["opening_material"] if item["kind"] == "no_day"]
    assert empty["date"] == "2030-06-12" and "不用先取 state" in empty["text"]
