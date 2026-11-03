"""S4a: the steps of every message come from one source (core.yaml) and read the same in every entry.

An entry that has only the MCP server (豆包工作) reads the server instructions and what turn returns; Claude Code reads
CLAUDE.md (which imports AGENTS.md) and the skill; Codex reads AGENTS.md first. Each step is one action with a check
a tool result can show (td/apps-github.md 4.2, 5.3)."""
from __future__ import annotations

import re

import pytest

from tripwright.agents import core_steps, server_instructions, user_repo_files
from tripwright.agents.__main__ import main
from tripwright.agents.assemble import (CORE, GENERATED, INSTRUCTIONS_LIMIT, AssembleError, build, core_step_lines, load_sources,
                                        parse_core)

MAIN = "skills/tripwright-main/SKILL.md"
PLACES = (MAIN, f"{GENERATED}/mcp/tripwright-main.md", f"{GENERATED}/server/instructions.md", f"{GENERATED}/repo/AGENTS.init.md")


def test_server_instructions_hold_every_step_and_its_check_within_the_limit(sources):
    text = server_instructions()
    core = sources.core
    assert len(core.steps) == 8 and all(do and done for do, done in core.steps)
    for number, (do, done) in enumerate(core.steps, 1):
        assert f"\n{number}. {do} Done when: {done}" in text
    assert all(f"\n- {rule}" in text for rule in core.always)
    # the limit stated in REPLY: 3,600 bytes, against 5,715 for the run of rules before S4a
    assert INSTRUCTIONS_LIMIT == 3600 and len(text.encode()) <= INSTRUCTIONS_LIMIT
    # each check names what a tool returns or what was sent, not a feeling of being done
    assert sum(bool(re.search(r"`turn`|`second_part_due`|`check_answer`|`commit`|`query`|first part|draft|researcher", done))
               for _, done in core.steps) == 8


def test_the_build_refuses_instructions_over_the_limit_and_a_step_without_its_check(sources):
    long = sources.core.__class__(sources.core.intro, sources.core.steps + (("Read every file. " * 30, "all files read."),),
                                  sources.core.always, sources.core.read)
    with pytest.raises(AssembleError, match="over 3600"):
        build(sources.__class__(sources.roles, sources.steps, sources.fragments, long, sources.agents_template))
    with pytest.raises(AssembleError, match="each one action and the check"):
        parse_core("intro: x\nsteps:\n  - do: Call turn.\nalways: []\nread: y\n")


def test_the_four_places_carry_the_same_steps(outputs, sources):
    lines = core_step_lines(sources.core)
    for path in PLACES:
        text = outputs[path]
        found = [text.index(line) for line in lines]
        assert found == sorted(found), path  # every step, in order
        assert all(f"- {rule}" in text for rule in sources.core.always), path
    # what init writes into a trip repository (CLAUDE.md imports it) and what turn returns
    written = user_repo_files()["AGENTS.md"].decode()
    assert written.startswith("<!-- 由 tripwright init 生成") and all(line in written for line in lines)
    assert user_repo_files()["CLAUDE.md"] == b"@AGENTS.md\n"
    returned = core_steps()
    assert [f"{step['step']}. {step['do']} Done when: {step['done']}" for step in returned["steps"]] == lines
    assert returned["always"] == list(sources.core.always)


def test_a_changed_step_reaches_every_place_only_through_the_build(checkout, capsys):
    core = checkout / CORE
    core.write_text(core.read_text(encoding="utf-8").replace('done: "`turn` returned."', 'done: "`turn` returned its result."'),
                    encoding="utf-8")
    # bad sample: the source moved and the four outputs did not
    assert main(["build", "--check", "--root", str(checkout)]) == 1
    stale = capsys.readouterr().err
    assert all(f"{path}: differs from a fresh build" in stale for path in PLACES)
    assert f"{GENERATED}/server/steps.json: differs from a fresh build" in stale
    # control: one build, and every place says the new check
    assert main(["build", "--root", str(checkout)]) == 0
    assert all("Done when: `turn` returned its result." in (checkout / path).read_text(encoding="utf-8") for path in PLACES)
    assert '"done": "`turn` returned its result."' in (checkout / GENERATED / "server/steps.json").read_text(encoding="utf-8")
    assert main(["build", "--check", "--root", str(checkout)]) == 0


def test_codex_is_told_to_spawn_the_researcher_and_no_entry_must_have_subagents(outputs):
    agents = outputs[f"{GENERATED}/repo/AGENTS.init.md"]
    assert "spawn `tripwright_researcher` for step 5" in agents
    for path in PLACES:
        assert "Otherwise skip this step" in outputs[path] and "without one, do them yourself, one after another" in outputs[path]
        assert not re.search(r"(?:searches|lookups) at once|in parallel|请并行", outputs[path]), path
    # the old line of the skill, which assumed searches side by side, is gone
    assert "several searches at once" not in outputs[MAIN]


def test_the_main_skill_did_not_grow(outputs):
    # S4a: 12,130 bytes before; the core steps replaced the skill's own steps and check section
    # S4h-b: 12,225 bytes, step 1's wait-for-the-tools sentence (a deliberate growth, pinned once)
    assert len(outputs[MAIN].encode()) <= 12225


def test_sources_load_the_core_from_the_checkout():
    assert load_sources().core.path == CORE
