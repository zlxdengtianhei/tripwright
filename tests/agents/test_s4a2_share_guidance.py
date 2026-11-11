"""S4a2 (D-89): the share page is published again after an effective commit, and the first public repository waits for
the user. The rule lives with step 6 (the skill's single-agent reference and the MCP prompt); the core steps only say
that publishing to others is a stop."""
from __future__ import annotations

from tripwright.agents import server_instructions
from tripwright.agents.assemble import GENERATED
from tripwright.agents.promises import PROMISES, promise_problems

SINGLE = "skills/tripwright-main/references/single-agent.md"
MCP_MAIN = f"{GENERATED}/mcp/tripwright-main.md"


def test_after_a_commit_the_share_page_is_published_and_the_first_public_repository_waits_for_the_user(outputs):
    for path in (SINGLE, MCP_MAIN):
        text = outputs[path]
        step = text[text.index("6. Update the site"):]
        assert "`.tripwright/share/config.json`" in step and "after the write is `effective`, run `tripwright share publish`" in step
        assert "Exit 2: the privacy scan blocked this version and nothing was pushed" in step
        assert "`share_blocked` notice with the categories and pages" in step and "never write the value it found" in step
        assert "`tripwright share publish --create`" in step and "stop and ask first" in step
        assert "GitHub Free serves Pages only from a public repository" in step and "`trip-<trip id>`" in step
    # Publishing detail stays in repository-host documentation, not the six chat rules.
    assert "anything sent or published to others" not in server_instructions()
    assert "the share page after a commit" not in server_instructions()
    assert "the share page after a commit | `references/single-agent.md`" in outputs["skills/tripwright-main/SKILL.md"]
    assert len(outputs["skills/tripwright-main/SKILL.md"].encode()) <= 12225  # S4h-b: step 1's wait-for-the-tools sentence
    # bad sample: step 6 without the stop before the first public repository is caught on every build
    promise = next(promise for promise in PROMISES if promise.id == "share-page-after-commit")
    broken = dict(outputs, **{SINGLE: outputs[SINGLE].replace("stop and ask first", "go ahead")})
    assert promise_problems(broken, (promise,)) == [f"share-page-after-commit: {SINGLE} lacks /stop and ask first/"]
    assert promise_problems(outputs, (promise,)) == []
