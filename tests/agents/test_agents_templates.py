"""The user trip repository's AGENTS.md and CLAUDE.md templates, and the files init writes from block C."""
import fnmatch
import json
import re
import subprocess
import tomllib

import pytest
import yaml

import tripwright.agents as agents
from tripwright import __version__
from tripwright.agents import ROLES, user_repo_files
from tripwright.agents.assemble import AGENTS, GENERATED, ROOT

TEMPLATES = ROOT / AGENTS / "templates"
MANIFEST = json.loads((ROOT / "tests/contracts/manifest.json").read_text(encoding="utf-8"))


def agents_md() -> str:
    # S4a: what init writes, the template with core.yaml's steps filled in by the build
    return user_repo_files()["AGENTS.md"].decode("utf-8")


def template_problems(text: str) -> list[str]:
    problems = []
    if not text.startswith("<!-- 由 tripwright init 生成，源：src/tripwright/agents/templates/AGENTS.md.tmpl、src/tripwright/agents/core.yaml；不要手改"):
        problems.append("first line must say it is generated, from which source, not to edit")
    if not re.search(r"calls `turn` first", text):
        problems.append("must say the main agent calls turn first")
    if not re.search(r"## Read in this order\n\n1\. [^\n]+\n2\. [^\n]+\n3\. ", text):
        problems.append("must give the reading order")
    for generated in ("`README.md`", "`trips/*/README.md`", "`trips/*/views/**`", "`AGENTS.md`", "`CLAUDE.md`", "`.mcp.json`",
                      "`.claude/agents/*.md`", "`.claude/skills/`", "`.agents/skills/`", "`.codex/agents/*.toml`"):
        if generated not in text:
            problems.append(f"generated list lacks {generated}")
    # One repository holds all of a person's trips; the reading order goes into the current trip's folder.
    if not re.search(r"2\. Without the tool: [^\n]*`trips/<trip>/README\.md`[^\n]*`trips/<trip>/views/packs/<date>\.md`", text):
        problems.append("reading order must go into the current trip's folder trips/<trip>/")
    if "{{" in text:
        problems.append("init writes the bytes as they are; no placeholders")
    # The file holds no preferences: no user quotes and no statements of what someone likes.
    if re.search(r"「|」|\bprefers?\b|\blikes?\b|喜欢|偏好：", re.sub(r"## No preferences here[\s\S]*", "", text)):
        problems.append("holds a preference")
    return problems


def test_agents_template_says_turn_first_the_reading_order_and_what_is_generated():
    assert template_problems(agents_md()) == []


def test_agents_template_problems_are_caught():
    text = agents_md()
    assert any("turn first" in problem for problem in template_problems(text.replace("calls `turn` first", "may call `turn`")))
    assert any("`.codex/agents/*.toml`" in problem for problem in template_problems(text.replace("`.codex/agents/*.toml`", "")))
    assert any("trips/<trip>/" in problem for problem in template_problems(text.replace("`trips/<trip>/views/packs/<date>.md`", "`views/packs/<date>.md`")))
    assert any("preference" in problem for problem in template_problems(text.replace("# Trip repository", "The traveller prefers short walks.\n\n# Trip repository")))
    assert any("placeholders" in problem for problem in template_problems(text + "{{trip}}\n"))


def test_claude_template_is_one_import_line():
    assert (TEMPLATES / "CLAUDE.md.tmpl").read_text(encoding="utf-8") == "@AGENTS.md\n"


def test_templates_are_only_tmpl_files_and_no_agents_or_claude_file_sits_in_this_repository():
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "."], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    assert not [name for name in names if name.rsplit("/", 1)[-1] in ("AGENTS.md", "CLAUDE.md")]
    assert sorted(path.name for path in TEMPLATES.iterdir()) == ["AGENTS.md.tmpl", "CLAUDE.md.tmpl", "mcp.json.tmpl", "settings.json.tmpl"]


def test_settings_allow_the_road_tools_read_only_shell_commands_and_only_tripwright_s_own_command_line():
    settings = json.loads(user_repo_files()[".claude/settings.json"])
    # More room than Claude Code's default 30 s, but a hung server delays the first answer by this much (D-40).
    assert settings["env"] == {"MCP_TIMEOUT": "60000"}
    allow = settings["permissions"]["allow"]
    assert {"mcp__tripwright__*", "WebSearch", "WebFetch", "Agent"} <= set(allow)
    shell = [rule[len("Bash("):-len(":*)")] for rule in allow if rule.startswith("Bash(")]
    assert all(rule.startswith("Bash(") and rule.endswith(":*)") for rule in allow if "Bash" in rule)
    # The one writing command: the trip's own bundled tripwright running `query`, which the researcher runs when MCP
    # is missing (D-43). Claude Code matches whole words, so the rule names this package's wheel file.
    query = f"uvx --from ./tools/tripwright-{__version__}-py3-none-any.whl tripwright --repo . query"
    assert set(shell) <= {"cat", "ls", "grep", "head", "tail", "wc", "cd", "date", "git log", "git status", "git diff", query}
    assert query in shell and "{{" not in json.dumps(settings)
    # The two hooks run the same bundled tripwright (D-45): first part before any dispatch or lookup, and the second
    # part sent equal to the draft check_answer passed.
    hooks = {event: entry["hooks"][0]["command"] for event, entries in settings["hooks"].items() for entry in entries}
    wheel = f'"$CLAUDE_PROJECT_DIR/tools/tripwright-{__version__}-py3-none-any.whl"'
    assert hooks == {"PreToolUse": f'uvx --from {wheel} tripwright --repo "$CLAUDE_PROJECT_DIR" hook first-part',
                     "Stop": f'uvx --from {wheel} tripwright --repo "$CLAUDE_PROJECT_DIR" hook stop'}
    assert "Agent" in settings["hooks"]["PreToolUse"][0]["matcher"] and "mcp__tripwright__query" in settings["hooks"]["PreToolUse"][0]["matcher"]
    assert not [rule for rule in allow if re.search(r"\b(?:rm|mv|push|commit|reset|checkout|tee|sed|curl)\b", rule)]


def test_init_files_are_exactly_block_c_user_repo_outputs_and_load_in_a_trip_repository(tmp_path):
    files = user_repo_files()
    patterns = [entry["path"] for entry in MANIFEST["user_repo"] if entry["generator"] == "C"]
    assert all(any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns) for path in files)
    assert all(any(fnmatch.fnmatchcase(path, pattern) for path in files) for pattern in patterns)
    assert files["CLAUDE.md"] == b"@AGENTS.md\n"
    # Live providers: with the default synthetic data the second part would rest on fictional evidence (E3).
    assert json.loads(files[".mcp.json"]) == {"mcpServers": {"tripwright": {"command": "tripwright", "args": ["--repo", ".", "serve", "--stdio"],
                                                                            "env": {"TRIPWRIGHT_PROVIDERS": "live"}}}}
    for path, data in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(data)
    for agent in (tmp_path / ".claude/agents").iterdir():
        assert agent.read_text(encoding="utf-8") == (ROOT / GENERATED / "claude" / agent.name).read_text(encoding="utf-8")
    for agent in (tmp_path / ".codex/agents").iterdir():
        assert tomllib.loads(agent.read_text(encoding="utf-8"))["developer_instructions"]


def test_init_carries_each_role_skill_with_its_references_but_not_its_maintenance_files():
    files = user_repo_files()
    for destination in (".claude/skills", ".agents/skills"):
        for role in ROLES:
            skill = files[f"{destination}/{role}/SKILL.md"]
            assert skill == (ROOT / "skills" / role / "SKILL.md").read_bytes()
            assert yaml.safe_load(skill.decode().split("---", 2)[1])["name"] == role
        references = sorted(path for path in files if path.startswith(f"{destination}/tripwright-main/references/"))
        assert references == sorted(f"{destination}/tripwright-main/references/{path.name}" for path in (ROOT / "skills/tripwright-main/references").iterdir())
    assert {path.removeprefix(".claude/") for path in files if path.startswith(".claude/skills/")} == \
           {path.removeprefix(".agents/") for path in files if path.startswith(".agents/skills/")}
    assert not [path for path in files if path.endswith("MAINTAINING.md") or "/evals/" in path]


def test_a_role_skill_missing_from_package_and_checkout_is_a_loud_error(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_CHECKOUT", tmp_path)
    with pytest.raises(LookupError, match="force-include"):
        user_repo_files()


def test_the_registered_server_name_is_the_one_the_subagents_restrict():
    """Claude Code names MCP tools mcp__<server>__<tool>; the deny list only works if .mcp.json registers `tripwright`."""
    servers = json.loads(user_repo_files()[".mcp.json"])["mcpServers"]
    agents_dir = ROOT / GENERATED / "claude"
    denied = {entry.split("__")[1] for agent in agents_dir.iterdir() for line in agent.read_text(encoding="utf-8").splitlines()
              if line.startswith("disallowedTools:") for entry in line.split(":", 1)[1].split(",") if entry.strip().startswith("mcp__")}
    assert denied == set(servers) == {"tripwright"}
