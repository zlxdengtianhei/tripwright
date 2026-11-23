"""A rule meant for hosts has to be in what hosts read, not only in a source file nobody includes.

11-23: "quote the matched line, not a summary" went into the `rules` and `skeleton` fragments, which no role includes,
so it reached no host. These tests read the generated outputs and the role sources themselves.
"""
import re
from pathlib import Path

import pytest

from tripwright.agents.assemble import ROOT
from tripwright.render.writing import NAMES, SHIPPED

SKILL = ROOT / "skills/tripwright-main/SKILL.md"
INSTRUCTIONS = ROOT / "src/tripwright/agents/generated/server/instructions.md"
AGENTS_INIT = ROOT / "src/tripwright/agents/generated/repo/AGENTS.init.md"
SOURCES = [*sorted((ROOT / "src/tripwright/agents/roles").glob("*.md")), ROOT / "src/tripwright/agents/single-agent.md"]
INCLUDE = re.compile(r"<!--\s*include:\s*fragment\s+(\w+)\s+\w+\s*-->")


def test_the_main_skill_says_to_copy_the_snippet_line_word_for_word():
    text = SKILL.read_text(encoding="utf-8")
    assert "仓里的命中照抄 `snippet` 那一行原话，不写摘要" in text
    assert "`snippet_exact` 为假时那行是兜底" in text


@pytest.mark.parametrize("path", [INSTRUCTIONS, AGENTS_INIT], ids=lambda p: p.name)
def test_chat_apps_that_read_only_the_server_instructions_get_the_same_rule(path):
    assert "`snippet` line, word for word, with its source, never a summary" in path.read_text(encoding="utf-8")


def test_only_the_fragments_named_shipped_reach_a_host():
    included = {name for source in SOURCES for name in INCLUDE.findall(source.read_text(encoding="utf-8"))}
    pack = (ROOT / "src/tripwright/render/pack.py").read_text(encoding="utf-8")
    inlined = {"writing"} if "writing_rules" in pack else set()
    assert set(SHIPPED) == (included | inlined) & set(NAMES), (
        "a fragment started or stopped reaching hosts: update SHIPPED in render/writing.py, and if a fragment "
        "no longer reaches a host, move any rule meant for hosts into a role or core.yaml")
