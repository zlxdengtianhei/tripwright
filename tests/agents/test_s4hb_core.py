"""S4h-b (quality judge 9, premise C): in a new session the first tool searches answered "still connecting", the host
switched to the command line and wrote "Fallback to CLI." / "MCP tools are up now; switching to them." — the first
part came six minutes late and both runs had a status line. Step 1 now says to keep looking for the tools for a while
before using the command line, and to write nothing about tool connections either way; the sentence reaches every
entry through the build."""
from __future__ import annotations

from tripwright.agents import core_steps, server_instructions, user_repo_files
from tripwright.agents.assemble import GENERATED, INSTRUCTIONS_LIMIT

STEP = ("While Tripwright's tools are still connecting, retry every few seconds (up to about 30) before using the "
        "command line; never write about connections.")


def test_step_1_waits_for_the_tools_and_says_nothing_about_connections():
    steps = core_steps()["steps"]
    assert steps[0]["step"] == 1 and STEP in steps[0]["do"]
    # the wait is bounded and the command line stays the fallback, not the first choice
    assert steps[0]["do"].index("still connecting") < steps[0]["do"].index("command line")


def test_the_sentence_reaches_every_entry_within_the_limit():
    text = server_instructions()
    assert STEP in text and len(text.encode()) <= INSTRUCTIONS_LIMIT
    assert STEP in user_repo_files()["AGENTS.md"].decode()
    assert STEP in open(f"{GENERATED}/server/steps.json", encoding="utf-8").read()
    assert STEP in open(f"{GENERATED}/mcp/tripwright-main.md", encoding="utf-8").read()
