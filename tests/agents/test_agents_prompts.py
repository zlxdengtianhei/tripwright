"""MCP prompts for the four roles, and the server instructions, come from the generated files."""
import asyncio

import pytest
from mcp.server.mcpserver import MCPServer

from tripwright.adapters.prompts import register_prompts
from tripwright.agents import ROLES, prompts, server_instructions
from tripwright.agents.assemble import GENERATED, ROOT


def prompt_texts(server):
    return {prompt.name: asyncio.run(server.get_prompt(prompt.name)).messages[0].content.text for prompt in asyncio.run(server.list_prompts())}


def test_four_role_prompts_are_registered_with_titles_and_no_arguments():
    server = MCPServer("t")
    assert sorted(register_prompts(server)) == sorted(ROLES)
    listed = {prompt.name: prompt for prompt in asyncio.run(server.list_prompts())}
    assert set(listed) == set(ROLES)
    assert all(prompt.title and prompt.description and not prompt.arguments for prompt in listed.values())
    texts = prompt_texts(server)
    for role in ROLES:
        generated = (ROOT / GENERATED / f"mcp/{role}.md").read_text(encoding="utf-8")
        assert texts[role] == generated.split("\n", 1)[1]
        assert "不要手改" not in texts[role]


def test_an_older_single_prompt_is_replaced_rather_than_kept():
    server = MCPServer("t", warn_on_duplicate_prompts=False)

    @server.prompt(name="tripwright-main")
    def old() -> str:
        return "old skill text"

    register_prompts(server)
    assert prompt_texts(server)["tripwright-main"].startswith("# Answer on the road from the user's trip")
    register_prompts(server)
    assert len(asyncio.run(server.list_prompts())) == 4


def test_the_real_server_gains_all_four_prompts(tmp_path):
    from tripwright.adapters.mcp import create_server
    from tripwright.service import Tripwright
    from tripwright.storage import Store

    store = Store(tmp_path / "trip.sqlite3")
    try:
        server = create_server(Tripwright(store), manage_jobs=False)
        register_prompts(server)
        assert {prompt.name for prompt in asyncio.run(server.list_prompts())} == set(ROLES)
        assert "Check the draft from independent input" in prompt_texts(server)["tripwright-verifier"]
    finally:
        store.close()


def test_server_instructions_are_the_generated_text_without_the_notice():
    text = server_instructions()
    assert text.startswith("For each trip message: call turn")
    assert "\n1. Call `turn` first for every trip message" in text
    assert "second_part_title" in text and "second_part_due" not in text
    assert "不要手改" not in text


def test_prompt_index_and_files_agree():
    entries = prompts()
    assert [entry["name"] for entry in entries] == sorted(ROLES)
    assert all(entry["text"].startswith("# ") for entry in entries)
