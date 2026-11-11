"""S4h-b (quality judge 9, premise C): in a new session the first tool searches answered "still connecting", the host
switched to the command line and wrote "Fallback to CLI." / "MCP tools are up now; switching to them." — the first
part came six minutes late and both runs had a status line. Step 1 now says to keep looking for the tools for a while
before using the command line, and to write nothing about tool connections either way; the sentence reaches every
entry through the build."""
from __future__ import annotations

from tripwright.agents import server_instructions, user_repo_files
from tripwright.agents.assemble import INSTRUCTIONS_LIMIT, load_sources

STEP = ("While Tripwright's tools are still connecting, retry every few seconds (up to about 30) before using the "
        "command line; never write about connections.")


def test_step_1_waits_for_the_tools_and_says_nothing_about_connections():
    do = load_sources().core.steps[0][0]
    assert STEP in do
    # the wait is bounded and the command line stays the fallback, not the first choice
    assert do.index("still connecting") < do.index("command line")


def test_the_sentence_reaches_repository_hosts_and_chat_instructions_stay_short():
    text = server_instructions()
    assert STEP not in text and len(text.encode()) <= INSTRUCTIONS_LIMIT
    assert STEP in user_repo_files()["AGENTS.md"].decode()
    assert STEP in open("skills/tripwright-main/SKILL.md", encoding="utf-8").read()
