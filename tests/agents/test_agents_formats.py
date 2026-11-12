"""Host agent files follow the formats the hosts document (Claude Code subagents, Codex custom agents, skills).

Formats were read from code.claude.com/docs/en/sub-agents and learn.chatgpt.com/docs/agent-configuration/subagents
on 2026-10-31.
"""
import tomllib

import yaml

from tripwright.agents.assemble import CLAUDE_FIELDS, GENERATED

SUBAGENTS = ("tripwright-requirements", "tripwright-verifier", "tripwright-researcher")
# Codex: name, description and developer_instructions are required; model settings, features and skills are optional.
# sandbox_mode, approval_policy, model_provider and mcp_servers do not override the parent, so a file must not rely on them.
CODEX_REQUIRED = {"name", "description", "developer_instructions"}
CODEX_OPTIONAL = {"model", "model_reasoning_effort", "model_reasoning_summary", "model_verbosity", "features", "skills"}


def frontmatter(text: str) -> tuple[dict, str]:
    assert text.startswith("---\n"), "frontmatter must open on line 1, or the host skips the file"
    head, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(head), body


def claude_agent_problems(text: str) -> list[str]:
    try:
        fields, body = frontmatter(text)
    except AssertionError as error:
        return [str(error)]
    problems = [f"unknown field {key}" for key in fields if key not in CLAUDE_FIELDS | {"name", "description"}]
    if not fields.get("name") or ":" in fields["name"] or not fields.get("description"):
        problems.append("name and description are required; name has no ':'")
    denied = {entry.strip() for entry in str(fields.get("disallowedTools", "")).split(",")}
    if not {"mcp__tripwright__turn", "mcp__tripwright__commit"} <= denied:
        problems.append("a subagent must not be able to call turn or commit")
    if not body.strip():
        problems.append("empty system prompt")
    return problems


def codex_agent_problems(text: str) -> list[str]:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return [f"not TOML: {error}"]
    problems = [f"missing {key}" for key in sorted(CODEX_REQUIRED - set(document))]
    problems += [f"key {key} is not a documented agent key" for key in sorted(set(document) - CODEX_REQUIRED - CODEX_OPTIONAL)]
    if "-" in document.get("name", ""):
        problems.append("name uses a hyphen; the documented examples use underscores")
    return problems


def test_claude_subagents_use_only_documented_fields_and_cannot_write(outputs):
    for name in SUBAGENTS:
        assert claude_agent_problems(outputs[f"{GENERATED}/claude/{name}.md"]) == [], name


def test_claude_format_problems_are_caught(outputs):
    good = outputs[f"{GENERATED}/claude/tripwright-verifier.md"]
    assert claude_agent_problems("<!-- notice -->\n" + good)
    assert any("unknown field" in problem for problem in claude_agent_problems(good.replace("model: inherit", "modle: inherit")))
    assert any("turn or commit" in problem for problem in claude_agent_problems(good.replace("mcp__tripwright__commit", "mcp__tripwright__state")))


def test_codex_agents_parse_and_use_documented_keys(outputs):
    for name in SUBAGENTS:
        text = outputs[f"{GENERATED}/codex/{name.replace('-', '_')}.toml"]
        assert codex_agent_problems(text) == [], name
        document = tomllib.loads(text)
        assert document["name"] == name.replace("-", "_")
        assert document["developer_instructions"] == frontmatter(outputs[f"{GENERATED}/claude/{name}.md"])[1].lstrip("\n")


def test_codex_format_problems_are_caught(outputs):
    good = outputs[f"{GENERATED}/codex/tripwright_verifier.toml"]
    assert codex_agent_problems(good + 'sandbox_mode = "read-only"\n') == ["key sandbox_mode is not a documented agent key"]
    assert codex_agent_problems(good.replace("developer_instructions =", "instructions ="))
    assert codex_agent_problems(good.replace('name = "tripwright_verifier"', 'name = "tripwright-verifier"'))
    assert codex_agent_problems(good + "name = \n")


def test_every_skill_has_frontmatter_on_line_one_with_matching_name_and_metadata(outputs):
    skills = {path: text for path, text in outputs.items() if path.endswith("/SKILL.md")}
    assert {path.split("/")[1] for path in skills} == {"tripwright-main", *SUBAGENTS}
    for path, text in skills.items():
        fields, body = frontmatter(text)
        assert fields["name"] == path.split("/")[1]
        assert 0 < len(fields["description"]) <= 1024
        assert set(fields["metadata"]) == {"trigger", "owns", "not", "layer"}
        assert body.strip()


def test_main_skill_still_satisfies_the_core_adapter_check(outputs):
    # tests/core/test_adapters.py reads the skill with text.split("---", 2)[1]; the notice comment must not break that.
    text = outputs["skills/tripwright-main/SKILL.md"]
    assert yaml.safe_load(text.split("---", 2)[1])["name"] == "tripwright-main"
    assert all(name in text for name in ("turn", "query", "propose", "commit", "result", "state", "explain"))
    assert "中文说明" in text
