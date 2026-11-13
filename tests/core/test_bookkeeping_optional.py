"""propose and commit fill in their own bookkeeping (2026-11-13, G16).

WorkBuddy's MCP client refused a long propose call locally ("must have required property 'idempotency_key'") and the
model went round through files. The service knows the version it gave with a ctx_id, and a key made from the call
itself recognises a retry, so base_version and both idempotency keys are optional. Given values work as before."""
import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tripwright.adapters.mcp import create_server
from tripwright.versions import commit_key, proposal_key

from test_ask_before_saving import DINNER, MOVE, YES, day_with, trip  # noqa: F401  (trip is a fixture)


def call(server, tool, **arguments):
    return asyncio.run(server.call_tool(tool, arguments)).structured_content


def moved_day(service):
    return day_with(service, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))


def test_propose_with_only_ctx_id_and_days_and_the_same_call_again_gives_the_same_draft(trip):
    turn = trip.turn("alice", MOVE, "move", scenario="replan")
    drafts = len(trip.store.all("drafts", "alice"))
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], days=[moved_day(trip)])
    assert draft["status"] == "proposed" and draft["base_version"] == turn["base_version"] == 1
    again = trip.propose("alice", ctx_id=turn["ctx_id"], days=[moved_day(trip)])
    assert again["draft_id"] == draft["draft_id"] and len(trip.store.all("drafts", "alice")) == drafts + 1
    other = trip.propose("alice", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("18:30", "19:30"))])
    assert other["draft_id"] != draft["draft_id"]


def test_a_keyless_commit_refused_before_the_answer_saves_after_it_and_a_retry_adds_no_version(trip):
    turn = trip.turn("alice", MOVE, "move", scenario="replan")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], days=[moved_day(trip)])
    assert [check["id"] for check in draft["ask_user"]["unverified"]] == [DINNER]
    with pytest.raises(ValueError, match="Not saved yet"):
        trip.commit("alice", draft["draft_id"], accept_unverified=True, ask_first=True)
    trip.turn("alice", YES, "yes", scenario="plan")
    version = trip.commit("alice", draft["draft_id"], accept_unverified=True, ask_first=True)
    assert version["number"] == 2 and version["idempotency_key"] == commit_key(draft["draft_id"], True)
    assert trip.commit("alice", draft["draft_id"], accept_unverified=True, ask_first=True)["id"] == version["id"]
    assert len(trip.store.all("versions", "alice")) == 2


def test_given_keys_still_work_and_a_reused_key_with_other_input_is_refused(trip):
    turn = trip.turn("alice", MOVE, "move", scenario="replan")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="mine", days=[moved_day(trip)])
    with pytest.raises(ValueError, match="reused with different input"):
        trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="mine",
                     days=[day_with(trip, d1_dinner=("18:30", "19:30"))])
    assert trip.commit("alice", draft["draft_id"], "mine", accept_unverified=True)["idempotency_key"] == "mine"


def test_the_derived_proposal_key_follows_the_input_not_its_order():
    first = proposal_key(dict(ctx_id="C-1", days=[{"date": "2030-06-10", "title": "a"}], usages=None))
    assert first == proposal_key(dict(usages=None, days=[{"title": "a", "date": "2030-06-10"}], ctx_id="C-1"))
    assert first.startswith("auto:C-1:") and first != proposal_key(dict(ctx_id="C-1", days=[{"date": "2030-06-10", "title": "b"}]))


def test_the_tools_take_only_ctx_id_and_draft_id(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    schemas = {tool.name: tool.input_schema for tool in asyncio.run(server.list_tools())}
    assert schemas["propose"]["required"] == ["ctx_id"] and schemas["commit"]["required"] == ["draft_id"]
    turn = call(server, "turn", text=MOVE, scenario="replan")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], days=[moved_day(trip)])
    with pytest.raises(ToolError, match="Not saved yet"):
        call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)
    call(server, "turn", text=YES, scenario="plan")
    version = call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)
    assert version["accepted"] is True and version["number"] == 2
    assert call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)["number"] == 2
