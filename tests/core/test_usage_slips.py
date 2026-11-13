"""A host's slip in propose.usages no longer refuses the plan (2026-11-13).

Three acceptance runs on 2026-11-12/13 (GPT-6 Luna twice, GLM once) lost a saved change to the `usage:` check while the
plan itself was fine: one named the peanut allergy by the key it had just given it ("mira-peanut-allergy") instead of
its record ID; two listed one condition and left out the others. The service cannot tell whether a dinner keeps to a
peanut allergy anyway, so a refusal there only turned a bookkeeping slip into "not saved". Now a key counts as its
record's ID, a condition left out is filled by the service like an omitted list, and a used claim the service cannot
confirm is host_reported and unverified. A conflict stays where the service itself refutes the claim or a must is
traded off."""
import time
from copy import deepcopy

import pytest

from tripwright.demo import example

FIRST = "我是 Mira。把 6 月 10 日在奥斯陆虚构港边咖啡馆的晚饭从 17:00 挪到 19:00。另外记一下：我对花生过敏，以后别给我安排含花生的菜。"
SECOND = "可以，照这样存。另外饭后那个咖啡取消吧，太晚了。"


@pytest.fixture
def trip(service, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T16:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "Create this fictional example trip.", "seed", scenario="status")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=deepcopy(example()["trip"]), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def day(service, *, coffee=True):
    """Day one with dinner at 19:00, the coffee after it or gone."""
    found = deepcopy(service.state("alice", "day", "2030-06-10")["json"])
    for candidate in found["plans"]:
        for track in candidate["tracks"]:
            for block in track["blocks"]:
                if block["id"] == "d1-dinner":
                    block.update(start="19:00", end="20:00", note="为 Mira 安排的菜不得含花生")
                if block["id"] == "d1-coffee":
                    block.update(start="20:00", end="21:00")
            if not coffee:
                track["blocks"] = [block for block in track["blocks"] if block["id"] != "d1-coffee"]
    return found


def peanut(event):
    # as GPT-6 Luna sent it (head3_acc codex_gpt-6-luna b_change_zh_acc-2, round 4)
    return dict(key="mira-peanut-allergy", kind="constraint", quote="我对花生过敏，以后别给我安排含花生的菜。",
                source=dict(basis="user_explicit", event=event), strength="forbid", subject=["Mira"], text="为 Mira 安排的菜不得含花生")


def pace(event):
    return dict(key="mira-evening", kind="preference", quote="晚饭从 17:00 挪到 19:00", source=dict(basis="user_explicit", event=event),
                strength="prefer", subject=["Mira"], text="Mira 想晚一点吃晚饭")


def conflicts(draft):
    return [check["id"] for check in draft["checks"]["conflicts"]]


def recorded(trip):
    """The first message's change saved: the allergy and an evening preference on record, dinner at 19:00."""
    turn = trip.turn("alice", FIRST, "first", scenario="replan", speaker="Mira")
    event = turn["receipt"]["event_id"]
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day(trip)],
                         changes=dict(items=[peanut(event), pace(event)]), idempotency_key="first")
    assert draft["status"] == "proposed", conflicts(draft)
    trip.commit("alice", draft["draft_id"], "first", accept_unverified=True)
    return {item["key"]: item["id"] for item in trip.store.all("items", "alice") if item["status"] == "active"}


def test_a_key_in_usages_is_taken_as_its_record_id(trip):
    turn = trip.turn("alice", FIRST, "first", scenario="replan", speaker="Mira")
    event = turn["receipt"]["event_id"]
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day(trip)],
                         changes=dict(items=[peanut(event)]), idempotency_key="key-as-id",
                         usages=[dict(item_id="mira-peanut-allergy", status="used", reason="已用于晚餐备注，要求菜品不含花生")])
    assert draft["status"] == "proposed", conflicts(draft)
    assert not [check for check in conflicts(draft) if check.startswith("usage")]
    [usage] = [usage for usage in draft["usages"] if usage.get("reported_status") == "used" and not usage.get("filled_by")]
    assert usage["item_id"].startswith("I-") and usage["status"] == "host_reported" and usage["verification"] == "unverified"
    assert any(check["id"] == f"usage-proof:{usage['item_id']}" for check in draft["checks"]["unverified"])


@pytest.mark.parametrize("sent", [
    # GLM (head2_acc group_glm c_change_more_zh_acc-3): one condition marked not applicable, the others left out
    lambda ids: [dict(item_id=ids["mira-evening"], status="not_applicable", reason="饭后咖啡已按用户要求取消，顺延偏好不再适用")],
    # GPT-6 Luna (head3_acc codex_gpt-6-luna c_change_more_zh_acc-3): one condition marked used without blocks
    lambda ids: [dict(item_id=ids["mira-evening"], status="used", block_ids=[], reason="用户要求取消饭后咖啡，安排中已移除该项。")],
])
def test_conditions_left_out_of_usages_are_filled_by_the_service(trip, sent):
    ids = recorded(trip)
    turn = trip.turn("alice", SECOND, "second", scenario="replan", speaker="Mira")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day(trip, coffee=False)],
                         idempotency_key="partial", usages=sent(ids))
    assert draft["status"] == "proposed", conflicts(draft)
    filled = {usage["item_id"]: usage for usage in draft["usages"] if usage.get("filled_by") == "service"}
    assert ids["mira-peanut-allergy"] in filled
    assert filled[ids["mira-peanut-allergy"]]["status"] == "host_reported" and filled[ids["mira-peanut-allergy"]]["verification"] == "unverified"
    version = trip.commit("alice", draft["draft_id"], "partial")
    assert version["number"] == max(row["number"] for row in trip.store.all("versions", "alice"))


def test_a_used_claim_on_blocks_that_do_not_fit_is_unverified_not_refused(trip):
    ids = recorded(trip)
    turn = trip.turn("alice", "晚饭照旧。", "same", scenario="replan", speaker="Mira")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day(trip)], idempotency_key="absent",
                         usages=[dict(item_id=ids["mira-evening"], status="used", block_ids=["no-such-block"])])
    assert draft["status"] == "proposed", conflicts(draft)
    usage = next(usage for usage in draft["usages"] if usage["item_id"] == ids["mira-evening"])
    assert usage["status"] == "host_reported" and usage["verification"] == "unverified" and "no-such-block" in usage["reason"]


def test_a_must_traded_off_is_still_a_conflict(trip):
    ids = recorded(trip)
    turn = trip.turn("alice", "晚饭照旧。", "same", scenario="replan", speaker="Mira")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day(trip)], idempotency_key="traded",
                         usages=[dict(item_id="mira-peanut-allergy", status="traded_off", reason="没有别的餐馆")])
    assert draft["status"] == "rejected"
    assert f"usage:{ids['mira-peanut-allergy']}" in conflicts(draft)


def test_a_usage_for_no_applicable_condition_is_noted_not_refused(trip):
    recorded(trip)
    turn = trip.turn("alice", "晚饭照旧。", "same", scenario="replan", speaker="Mira")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day(trip)], idempotency_key="unknown",
                         usages=[dict(item_id="I-nothing", status="used")])
    assert draft["status"] == "proposed", conflicts(draft)
    assert any(check["id"] == "usage:I-nothing" for check in draft["checks"]["unverified"])
