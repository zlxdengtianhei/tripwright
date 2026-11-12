"""The latest-time question in the trip log was asked as “最最迟的时间”. The pattern knew 最晚 and 末班 but not 最迟, so
that turn's `next` did not say to look up the last connection, and GPT asked in Chinese looked it up in 6 of 10 runs
(in English, “latest”, 6 of 6; 2026-11-12)."""
from tripwright.render.first_part import asks_latest


def test_zui_chi_asks_for_the_latest_time_like_zui_wan():
    for text in ("你去说一个最最迟的时间，让我有一个这个底", "最迟几点走", "今晚最晚几点", "Tell me the very latest time"):
        assert asks_latest(text), text
    for text in ("迟到了也没关系", "今天吃什么"):
        assert not asks_latest(text), text


def test_a_chinese_turn_asking_zui_chi_tells_the_host_to_look_up_the_last_connection(seeded):
    result = seeded.turn("alice", "你去说一个最最迟的时间，让我有一个这个底", None, scenario="quick")
    instructions = result["next"] + str(result.get("after_first_part") or "")
    assert "query(kind='latest_departure'" in instructions
