"""definite.judge: whether the user's words give the new value of a field (D-19, 2026-11-13). “左右”, “大概”, “around”
make a value rough: per the user's point 2 the agent asks first. A bare “9 点” is 09:00 or 21:00 (the plan says which)."""
from datetime import date

import pytest

from tripwright.definite import judge

ON = date(2030, 6, 11)


@pytest.mark.parametrize("field, new, words, status", [
    ("time", "09:00", "改坐早上 9 点的", "definite"),
    ("time", "09:00", "改坐九点的车", "definite"),
    ("time", "09:30", "九点半那班", "definite"),
    ("time", "16:30", "下午4点半", "definite"),
    ("time", "21:00", "晚上 9 点", "definite"),
    ("time", "09:00", "晚上 9 点", "other"),
    ("time", "09:00", "改坐 10 点的", "other"),
    ("time", "09:00", "不坐下午 16:30 的了，改坐 09:00 的", "definite"),
    ("time", "09:00", "改坐早上 9 点左右的", "approximate"),
    ("time", "09:00", "大概 9 点", "approximate"),
    ("time", "09:00", "改坐早一点的车", "missing"),
    ("time", "09:00", "晚一点也行", "missing"),
    ("time", "09:00", "可以，就按这样存吧。", "missing"),
    ("time", "09:00", "take the 9am train instead", "definite"),
    ("time", "09:00", "the train at 9", "definite"),
    ("time", "09:00", "nine o'clock", "definite"),
    ("time", "09:00", "the train around 9", "approximate"),
    ("time", "09:00", "an earlier train please", "missing"),
    ("time", "09:00", "Yes, save it like this.", "missing"),
    ("date", "2030-06-12", "改到 12 号", "definite"),
    ("date", "2030-06-12", "改到6月12日", "definite"),
    ("date", "2030-06-12", "move it to June 12", "definite"),
    ("date", "2030-06-12", "the 12th", "definite"),
    ("date", "2030-06-12", "明天", "definite"),
    ("date", "2030-06-12", "改到 13 号", "other"),
    ("date", "2030-06-12", "改到六月十二号", "definite"),
    ("date", "2030-06-12", "改天吧", "missing"),
    ("line", "IC 23", "改坐 IC23", "definite"),
    ("line", "IC 23", "改坐另一班", "missing"),
    ("buffer_minutes", 30, "留 30 分钟", "definite"),
    ("buffer_minutes", 30, "多留点时间", "missing"),
    ("status", "bought", "我买了票", "free"),
])
def test_judge(field, new, words, status):
    assert judge(field, new, words, said_on=ON)["status"] == status


def test_a_place_counts_by_its_name():
    place = dict(id="bergen-view", name="卑尔根观景点", name_local="Example Bergen Viewpoint")
    assert judge("place", "bergen-view", "改去 Example Bergen Viewpoint", place=place)["status"] == "definite"
    assert judge("place", "bergen-view", "换个地方吧", place=place)["status"] == "missing"
