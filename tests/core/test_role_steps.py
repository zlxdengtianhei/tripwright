"""The service hands out a role's steps, and every named role carries a minimal task packet (2026-11-13, G1).

The user: "单 agent 和多 agent 都是基于 skill 运作，单 agent 就按 skill 一步步操作，不需要做过多额外适配". A chat app has no
skill files; `state(view="role")` gives it the same text the skill ships, and `flow.dispatch` says which call that is,
which IDs to hand over and what comes back."""
import asyncio
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_b3d_mcp import environment
from test_flow_dispatch import SHOTS, seeded

from tripwright.agents import ROLES, _skill_dir
from tripwright.flow import FULL


def skill_body(role: str) -> str:
    text = _skill_dir(role).joinpath("SKILL.md").read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1].strip()


@pytest.mark.parametrize("role", ROLES)
def test_every_role_comes_from_the_service_as_its_skill_holds_it(service, role):
    steps = service.state("alice", view="role", role=role)
    assert steps["role"] == role and steps["text"] == skill_body(role)
    assert steps["description"].startswith("Use when") and "不要手改" not in steps["text"]
    assert "flow" in steps  # a role read is a tool result like any other


def test_the_main_role_lists_its_references_and_each_one_is_readable(service):
    main = service.state("alice", view="role", role="tripwright-main")
    assert set(main["references"]) >= {"records.md", "tools.md", "writing.md"}
    records = service.state("alice", view="role", role="tripwright-main/records.md")
    assert records["file"] == "records.md" and records["text"] and not records["text"].startswith("<!--")


@pytest.mark.parametrize("role, message", [(None, "needs role"), ("tripwright-planner", "Unknown role"),
                                           ("tripwright-main/secret.md", "Unknown file")])
def test_a_missing_or_unknown_role_is_refused_with_the_choices(service, role, message):
    with pytest.raises(ValueError, match=message):
        service.state("alice", view="role", role=role)


def test_a_named_role_carries_its_ids_what_it_returns_and_where_its_steps_are(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", SHOTS, "shots", nature="screenshot_extraction", scenario="requirements")
    [entry] = turn["flow"]["dispatch"]
    assert entry["role"] == "tripwright-requirements"
    assert entry["read"] == dict(event_id=turn["receipt"]["event_id"], ctx_id=turn["ctx_id"], base_version=turn["base_version"])
    assert entry["steps"] == 'state(view="role", role="tripwright-requirements")' and entry["returns"]


def test_the_verifier_named_after_a_draft_gets_the_draft_and_its_job(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "把第一天的博物馆换到下午。", "edit", scenario="replan")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="edit")
    verifier = next(entry for entry in draft["flow"]["dispatch"] if entry["role"] == "tripwright-verifier")
    assert verifier["read"]["draft_id"] == draft["draft_id"] and verifier["read"]["ctx_id"] == turn["ctx_id"]
    assert verifier["read"].get("job_id") == draft.get("job_id")
    assert "passed" in verifier["returns"] and verifier["steps"].endswith('role="tripwright-verifier")')


def test_the_whole_flow_says_to_read_the_steps_first_when_the_host_cannot_dispatch():
    for language in ("zh", "en"):
        for mode in ("quick", "plan"):
            assert "steps" in FULL[language][mode]
    assert "自己依次照它的步骤做" not in FULL["zh"]["plan"]


async def role_over_stdio(directory):
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "r.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            response = await session.call_tool("state", dict(view="role", role="tripwright-verifier"))
            text = " ".join(getattr(content, "text", "") for content in response.content)
            assert not response.is_error, text
            return response.structured_content or json.loads(text)


def test_a_chat_host_reads_a_role_through_the_state_tool(tmp_path):
    steps = asyncio.run(role_over_stdio(tmp_path))
    assert steps["role"] == "tripwright-verifier" and steps["text"] == skill_body("tripwright-verifier")
