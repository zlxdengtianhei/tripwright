"""A2 on the service: turn and every state view carry `now` in the trip's zone; turn's hard nodes say where their time
comes from and whether it is already behind us; the day view writes the replan line once, from the version's state."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tripwright.demo import example
from tripwright.repo import snapshot as snapshots
from tripwright.service import Tripwright
from tripwright.storage import Store, now

NOON = "2030-06-10T12:00:00+02:00"


def anchored_plan() -> dict:
    plan = deepcopy(example()["trip"])
    plan["days"] = [plan["days"][0]]
    plan["bookings"] = []
    plan["days"][0]["anchors"] = [
        dict(id="hn-morning", time="07:00", text="Morning ferry (fictional)", subject=["p1"], consequence="Take the next one.",
             alternative="Walk."),
        dict(id="hn-evening", time="23:30", text="Evening ferry (fictional)", subject=["p1"], buffer_minutes=20,
             consequence="Last one; stay overnight.", alternative="Call the hostel."),
    ]
    return plan


@pytest.fixture
def anchored(tmp_path):
    store = Store(tmp_path / "a2.sqlite3")
    service = Tripwright(store)
    turn = service.turn("alice", "Create the example.", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=anchored_plan(), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    yield service
    service.jobs.stop()
    store.close()


def test_turn_says_now_in_the_trip_zone_and_the_pack_starts_with_it(anchored, monkeypatch):
    monkeypatch.setattr(snapshots, "now", lambda: "2030-06-10T10:25:00+00:00")
    turn = anchored.turn("alice", "How late can I leave?", "q1", scenario="quick")
    assert turn["now"] == dict(at="2030-06-10T12:25:00+02:00", date="2030-06-10", time="12:25", weekday=1, timezone="Europe/Oslo",
                               text="现在 12:25（Europe/Oslo，06-10 周一）")
    assert turn["pack"]["markdown"].startswith("# 现在 12:25（Europe/Oslo，06-10 周一）｜约束包 ")
    assert turn["pack"]["meta"]["header"]["now"] == {key: value for key, value in turn["now"].items() if key != "text"}


def test_turn_marks_passed_hard_nodes_lists_upcoming_first_and_says_where_each_time_comes_from(anchored, monkeypatch):
    monkeypatch.setattr(snapshots, "now", lambda: NOON)
    nodes = anchored.turn("alice", "Do I have to hurry?", "q2", scenario="quick")["hard_nodes"]
    assert [(node["id"], node["passed"]) for node in nodes] == [("hn-evening", False), ("hn-morning", True)]
    version = anchored.store.all("versions", "alice")[0]
    for node in nodes:
        assert node["deadline"] == "unmarked" and node["checked"] is None and node["refresh_hours"] == 6
        assert node["stored"] == {"version": 1, "at": version["committed_at"]}
    assert nodes[0]["latest_departure"] is not None or nodes[0]["travel_minutes"] is None


def test_every_state_view_carries_now(anchored):
    turn = anchored.turn("alice", "Plan check.", "q3")
    draft = anchored.propose("alice", ctx_id=turn["ctx_id"], base_version=1, plan=anchored_plan(), idempotency_key="again")
    views = dict(current={}, day=dict(date="2030-06-10"), ledger={}, decisions={}, profile={}, memory={}, pack=dict(draft_id=draft["draft_id"]),
                 sync={}, export={})
    before = now()
    for view, arguments in views.items():
        shown = anchored.state("alice", view, **arguments)
        assert shown["now"]["timezone"] == "Europe/Oslo" and shown["now"]["text"].startswith("现在 "), view
        assert shown["now"]["at"] >= before[:10], view


def test_the_day_view_writes_the_replan_line_once(anchored):
    """B's day view writes the replan line from the version's state; the service no longer adds a second one."""
    turn = anchored.turn("alice", "Today I am alone.", "alone", nature="user_verbatim", native_ref="fixture:alone")
    fact = dict(key="companions", value=[], text="Today p1 is alone", subject=["p1"], scope={"start": "2030-06-10", "end": "2030-06-10"},
                true_from=now(), invalidated_by="Day ends", source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    draft = anchored.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="alone", changes={"facts": [fact]})
    version = anchored.commit("alice", draft["draft_id"], "alone")
    assert version["needs_replan"]
    day = anchored.state("alice", "day", "2030-06-10")
    assert day["needs_replan"] and day["warnings"] == version["warnings"]
    assert day["text"].count("需重排") == 1 and "需重排：这一版计划和后来记下的情况冲突，先重排再照它走。" in day["text"]
