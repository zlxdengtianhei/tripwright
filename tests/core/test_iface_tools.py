"""Small, permissive chat-facing tool contracts (D4/D5)."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from tripwright.adapters.mcp import create_server
from tripwright.trip import Trip


def listing(service):
    async def read():
        tools = await create_server(service).list_tools()
        return [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]
    return asyncio.run(read())


def schema_nodes(schema, path="inputSchema"):
    """Yield actual schema nodes, not the maps that hold named properties."""
    yield path, schema
    if not isinstance(schema, dict):
        return
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else"):
        if isinstance(schema.get(key), dict):
            yield from schema_nodes(schema[key], f"{path}.{key}")
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        for index, child in enumerate(schema.get(key) or []):
            yield from schema_nodes(child, f"{path}.{key}.{index}")
    for key in ("properties", "$defs", "patternProperties"):
        for name, child in (schema.get(key) or {}).items():
            yield from schema_nodes(child, f"{path}.{key}.{name}")


def test_tool_list_is_small_and_every_schema_node_is_typed(service):
    tools = listing(service)
    document = {"tools": tools}
    assert len(json.dumps(document, ensure_ascii=False).encode()) <= 20_000
    for tool in tools:
        limit = 1200 if tool["name"] in ("turn", "query", "propose") else 400
        assert len(tool["description"]) <= limit
        for path, node in schema_nodes(tool["inputSchema"]):
            if not isinstance(node, dict):
                continue
            assert not ("enum" in node and "type" not in node), path
            assert "type" in node or "anyOf" in node or "$ref" in node, (path, node)


def test_turn_generates_request_ids_and_reused_id_with_new_text_is_a_new_message(service):
    first = service.turn("alice", "Same words")
    second = service.turn("alice", "Same words")
    assert first["request_id"] != second["request_id"]
    assert first["receipt"]["event_id"] != second["receipt"]["event_id"]
    third = service.turn("alice", "Different words", request_id=first["request_id"])
    assert third["request_id"] not in (first["request_id"], second["request_id"])
    assert len(service.store.all("events", "alice")) == 3


def test_turn_downgrades_unreferenced_verbatim_and_truncates_point_words(service):
    result = service.turn("alice", "A host quotation", nature="user_verbatim",
                          points=[dict(kind="other", said="x" * 80)])
    assert result["receipt"]["nature"] == "host_restatement"
    assert not result["receipt"]["verbatim"] and "host_restatement" in result["receipt"]["note"]
    assert len(result["receipt"]["received_text"]) == len("A host quotation")


def test_turn_resolves_stored_place_names_without_demanding_another_turn(seeded):
    place = seeded.state("alice", "plan")["plan"]["places"][0]
    result = seeded.turn("alice", "How do I get there?", scenario="quick",
                         points=[dict(kind="go", said=place["name_local"], place=place["name_local"])])
    assert result["points"]["points"][0]["place"] == place["id"]
    assert not result["points"]["points"][0].get("unknown")
    assert "call turn again" not in result["next"] and "再调一次 turn" not in result["next"]


def test_first_part_only_for_a_quick_question_with_a_stored_answer(seeded):
    place = seeded.state("alice", "plan")["plan"]["places"][0]
    point = dict(kind="do", said=place["name_local"], place=place["id"])
    quick = seeded.turn("alice", "What about this place?", scenario="quick", points=[point])
    assert quick["first_part"]["lines"][0] == "From the stored plan first (times not checked yet)"
    assert quick["second_part_title"] == "After checking"
    assert "later" not in quick["first_part"]
    assert len(quick["next"]) <= 600 and quick["next"].startswith("Write first_part.text")
    assert "Write first_part.text as text before any other tool call." in quick["next"]
    assert {"do_first", "steps", "always", "write_back", "answer", "second_part_due"}.isdisjoint(quick)
    assert "写法（对话里的回答）" not in quick["pack"]["markdown"]
    empty = seeded.turn("alice", "Anything else?", scenario="quick", points=[dict(kind="other", said="anything else")])
    assert empty["first_part"] is None and empty["second_part_title"] is None
    planned = seeded.turn("alice", "What about this place?", scenario="plan", points=[point])
    assert planned["first_part"] is None


def test_partial_plan_patches_merge_by_date_and_id(seeded):
    turn = seeded.turn("alice", "Change the day and add records")
    current = seeded.state("alice", "plan")["plan"]
    day = deepcopy(current["days"][0])
    day["title"] = "Changed day"
    place = dict(id="new-place", name="New place", name_local="New place", area=current["areas"][0], kind="other")
    booking = dict(id="new-booking", date=str(day["date"]), kind="ticket", what="New ticket", subject=["p1"],
                   status="on_site", place=current["places"][0]["id"])
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="patch",
                           days=[day], places=[place], bookings=[booking], changes=dict(why="User requested it"))
    seeded.commit("alice", draft["draft_id"], "patch", accept_unverified=True)
    changed = seeded.state("alice", "plan")["plan"]
    assert changed["days"][0]["title"] == "Changed day" and len(changed["days"]) == len(current["days"])
    assert changed["places"][-1]["id"] == "new-place" and changed["bookings"][-1]["id"] == "new-booking"
    with pytest.raises(ValueError, match="days.0.date"):
        seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="bad-day", days=[{}])


def test_partial_day_cannot_replace_a_user_must(seeded):
    current = seeded.state("alice", "plan")["plan"]
    day = current["days"][0]
    track = day["plans"][0]["tracks"][0]
    block = track["blocks"][0]
    locked = seeded.turn("alice", "This block is a must", nature="user_verbatim", native_ref="fixture:must", speaker="p1")
    decision = dict(question="Keep this block", options=["yes"], choice="yes", subject=track["subject"], decided_by="user",
                    must=True, source=dict(event=locked["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={f"/blocks/{block['id']}": block})
    draft = seeded.propose("alice", ctx_id=locked["ctx_id"], base_version=locked["base_version"], idempotency_key="lock",
                           changes=dict(decisions=[decision]))
    seeded.commit("alice", draft["draft_id"], "lock", accept_unverified=True)
    turn = seeded.turn("alice", "Move it", nature="agent_inference")
    changed = deepcopy(seeded.state("alice", "day", date=str(day["date"]))["json"])
    changed["plans"][0]["tracks"][0]["blocks"][0].update(start="23:00", end="23:30")
    refused = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="move-lock",
                             days=[changed], changes=dict(why="Agent chose another time"))
    assert refused["status"] == "rejected" and block["id"] in refused["notice"]
    assert "user decides" in refused["notice"] and "/blocks/" in refused["notice"]


def test_changes_item_schema_and_readable_validation_error(seeded):
    tools = {tool["name"]: tool for tool in listing(seeded)}
    changes = next(branch for branch in tools["propose"]["inputSchema"]["properties"]["changes"]["anyOf"]
                   if branch.get("type") == "object")
    item = changes["properties"]["items"]["items"]
    assert {"kind", "quote", "strength", "source"} <= set(item["required"])
    assert item["properties"]["kind"]["type"] == "string" and "preference" in item["properties"]["kind"]["enum"]
    turn = seeded.turn("alice", "Remember a preference")
    async def call():
        return await create_server(seeded, local_user="alice").call_tool("propose", dict(
            ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="bad-item",
            changes=dict(items=[dict(kind="fact")])))
    with pytest.raises(ToolError) as caught:
        asyncio.run(call())
    text = str(caught.value)
    assert "changes.items.0.kind" in text and "changes.items.0.quote is required" in text
    assert "changes.items.0.strength is required" in text
    assert "For further information" not in text and "input_value=" not in text


def test_state_returns_editable_plan_day_and_a_valid_blank(seeded):
    current = seeded.state("alice", "plan")["plan"]
    date = str(current["days"][0]["date"])
    assert seeded.state("alice", "day", date=date)["json"] == current["days"][0]
    blank = seeded.state("alice", "blank")["plan"]
    assert Trip.model_validate(blank).id == "new-trip"
