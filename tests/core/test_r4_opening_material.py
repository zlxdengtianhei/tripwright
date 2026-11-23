"""Round 4 review 4 (2026-11-19): on the road, the service hands the host the stored material for its first message.

The third replay (`AFTER3.md`) made the first message useful but slow: the model had nothing stored to say, so it looked
up for 431 s (median) and three runs sent no first message at all. `turn` now returns `opening_material` on a road turn
with no `first_part`: the nearest stored hard nodes, bought or booked tickets, the next place with its stored address,
and one stored last-service or latest sentence, each marked archived and unchecked. Data only, no model, no lookup.
"""
from __future__ import annotations

import time

ROAD_ZH = "先不调任何工具，用 `opening_material` 和常识写一两句能照着走的话发出去"
ROAD_EN = "First call no tool: write one or two lines you can act on from `opening_material`"


def seeded_with(service, plan, monkeypatch, now="2030-06-10T09:00:00+02:00"):
    monkeypatch.setenv("TRIPWRIGHT_NOW", now)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def test_the_near_hard_nodes_come_first_and_stay_archived(service, plan, monkeypatch):
    plan["days"][0]["anchors"] = [
        dict(id="dinner", time="18:00", text="晚饭订位", subject=["p1", "p2", "p3"], place="oslo-cafe"),
        dict(id="train", time="22:00", text="回车站坐车", subject=["p1", "p2", "p3"], place="oslo-station")]
    seeded_with(service, plan, monkeypatch)
    turn = service.turn("alice", "我饿了，现在去哪吃？", "opening-node", scenario="quick")

    assert turn["first_part"] is None
    items = turn["opening_material"]
    assert 0 < len(items) <= 6
    nodes = [item for item in items if item["kind"] == "hard_node"]
    assert [node["time"] for node in nodes] == ["18:00", "22:00"]
    assert nodes[0]["text"] == "18:00 晚饭订位（存档，没核）"


def test_a_bought_or_booked_ticket_is_material_and_the_next_place_carries_its_address(service, plan, monkeypatch):
    plan["bookings"] = [dict(id="bk-ticket", date="2030-06-10", kind="ticket", what="24 小时交通票",
                             subject=["p1", "p2", "p3"], status="bought")]
    next(place for place in plan["places"] if place["id"] == "oslo-art")["notes"] = "地址 Example Street 1, 0150 Oslo；虚构"
    seeded_with(service, plan, monkeypatch)
    turn = service.turn("alice", "我饿了，现在去哪吃？", "opening-ticket", scenario="quick")

    items = turn["opening_material"]
    ticket = next(item for item in items if item["kind"] == "ticket")
    assert "24 小时交通票" in ticket["text"] and "存档，没核" in ticket["text"]
    place = next(item for item in items if item["kind"] == "place")
    assert "Example Street 1" in place["text"] and "存档，没核" in place["text"]
    assert "Example Street 1" in place["address"]


def test_an_empty_plan_gives_no_plan_lines(service, plan, monkeypatch):
    """No day plan, anchor or booking: nothing of the plan to open with. Lines may still come from what the repository
    already holds (Q19, 2026-11-23: a stored place is the database, `recall`), and each says it is archived and unchecked."""
    plan["days"][0]["plans"] = []
    plan["days"][0]["anchors"] = []
    plan["bookings"] = []
    seeded_with(service, plan, monkeypatch)
    turn = service.turn("alice", "我饿了，现在去哪吃？", "opening-empty", scenario="quick")

    assert not [item for item in turn["opening_material"] if item["kind"] != "recall"]
    assert all("存档，没核" in item["text"] for item in turn["opening_material"])


def test_the_road_next_opens_with_the_one_sentence_in_both_languages(service, plan, monkeypatch):
    seeded_with(service, plan, monkeypatch)
    chinese = service.turn("alice", "我饿了，现在去哪吃？", "opening-next-zh", scenario="quick")
    english = service.turn("alice", "I am hungry, where can I eat now?", "opening-next-en", scenario="quick")

    assert chinese["first_part"] is None and english["first_part"] is None
    # trip60: the one sentence is now the first message's, for every trip message
    assert chinese["next"].startswith("先写第一条消息，再调别的工具") and "存档写 X，还没核" in chinese["next"]
    assert "Write the first message before any other tool" in english["next"] and "the plan stores X, not checked yet" in english["next"]
    assert not chinese["next"].startswith(ROAD_ZH) and ROAD_EN not in english["next"]
    assert len(chinese["next"]) <= 1100 and len(english["next"]) <= 1100
