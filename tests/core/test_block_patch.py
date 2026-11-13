"""Change one block without sending the whole day back (analysis 5.21, 2026-11-13). WorkBuddy's MCP client failed on
propose's long arguments and a weak model took about 10 minutes for one dinner move; propose(blocks=[...]) changes or
deletes single blocks by ID and gives the same draft as the whole-day patch."""
import json

import pytest
from test_ask_before_saving import MOVE, day_with, trip  # noqa: F401  (trip is a fixture)


def proposed(service, key, **patch):
    turn = service.turn("alice", MOVE, key, scenario="replan")
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key=key, **patch)


def blocks_of(service, draft, date="2030-06-10"):
    plan = service.store.get("drafts", "alice", draft["draft_id"])["plan"]
    day = next(day for day in plan["days"] if str(day["date"]) == date)
    return {block["id"]: block for option in day["plans"] for track in option["tracks"] for block in track["blocks"]}


@pytest.mark.parametrize("name, whole, small", [
    ("move dinner", dict(d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30")),
     [dict(id="d1-dinner", start="19:00", end="20:00"), dict(id="d1-coffee", start="20:30", end="21:30")]),
    ("cancel coffee", None, [dict(id="d1-coffee", delete=True)]),
])
def test_a_block_patch_gives_the_same_draft_as_the_whole_day_and_is_much_shorter(trip, name, whole, small):
    day = day_with(trip, **(whole or {}))
    if whole is None:  # the whole-day way to cancel the coffee: the day without that block
        for option in day["plans"]:
            for track in option["tracks"]:
                track["blocks"] = [block for block in track["blocks"] if block["id"] != "d1-coffee"]
    by_day = proposed(trip, f"{name}-day", days=[day])
    by_block = proposed(trip, f"{name}-block", blocks=small)
    assert blocks_of(trip, by_block) == blocks_of(trip, by_day)
    assert by_block.get("ask_user", {}).get("unverified") == by_day.get("ask_user", {}).get("unverified")
    assert len(json.dumps(small)) * 10 < len(json.dumps([day]))


def test_null_removes_a_field_and_the_result_is_checked_like_any_plan(trip):
    draft = proposed(trip, "note", blocks=[dict(id="d1-coffee", app=None, text="Harbour walk")])
    block = blocks_of(trip, draft)["d1-coffee"]
    assert block["text"] == "Harbour walk" and block.get("app") is None
    with pytest.raises(ValueError, match="Invalid plan"):
        proposed(trip, "bad", blocks=[dict(id="d1-dinner", kind="party")])


@pytest.mark.parametrize("patch, message", [
    ([dict(id="d9-nowhere", start="10:00")], "no block 'd9-nowhere'; state\\(view='day'\\) lists the block IDs"),
    ([dict(id="d1-coffee", colour="red")], "unknown fields colour"),
    ([dict(id="d1-coffee", delete=True, start="10:00")], "delete takes no fields"),
    ([dict(start="10:00")], "blocks.0.id is required"),
])
def test_a_wrong_block_patch_says_what_to_send(trip, patch, message):
    with pytest.raises(ValueError, match=message):
        proposed(trip, "wrong", blocks=patch)


def test_blocks_with_a_whole_plan_or_with_restore_are_refused(trip):
    with pytest.raises(ValueError, match="not both"):
        proposed(trip, "both", blocks=[dict(id="d1-coffee", delete=True)], plan=trip.state("alice")["plan"])
    with pytest.raises(ValueError, match="send no plan"):
        proposed(trip, "undo", blocks=[dict(id="d1-coffee", delete=True)], restore_version=1)


def test_the_propose_tool_describes_blocks(service):
    from test_iface_tools import listing

    propose = next(tool for tool in listing(service) if tool["name"] == "propose")
    assert "whole day" in propose["inputSchema"]["properties"]["blocks"]["description"]
