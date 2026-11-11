"""Upgrade removes retired Tripwright host files without touching the repository owner's files."""
from __future__ import annotations

from fnmatch import fnmatch

from repo_fixtures import BY_HAND, git
from tripwright.agents import user_repo_files
from tripwright.cli import main
from tripwright.repo import workspace

OLD_FILES = (
    ".agents/skills/tripwright-requirements/SKILL.md",
    ".agents/skills/tripwright-verifier/SKILL.md",
    ".claude/agents/tripwright-requirements.md",
    ".claude/agents/tripwright-verifier.md",
    ".claude/skills/tripwright-requirements/SKILL.md",
    ".claude/skills/tripwright-verifier/SKILL.md",
    ".codex/agents/tripwright_requirements.toml",
    ".codex/agents/tripwright_verifier.toml",
)
USER_FILES = {".claude/agents/my-own.md": "my own agent\n", ".claude/skills/other/SKILL.md": "my own skill\n"}


def test_upgrade_removes_retired_tool_files_in_the_same_commit_and_keeps_user_files(tmp_path, capsys):
    root = workspace.init(tmp_path / "repo").root
    for path, text in dict.fromkeys(OLD_FILES + tuple(USER_FILES)).items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(USER_FILES.get(path, "retired\n"), encoding="utf-8")
    git(root, "add", "--", *OLD_FILES, *USER_FILES)
    git(root, *BY_HAND, "commit", "-qm", "old host files")

    assert main(["init", str(root), "--upgrade"]) == 0
    output = capsys.readouterr().out
    assert all(path in output for path in OLD_FILES)
    assert all(not (root / path).exists() for path in OLD_FILES)
    assert all(not (root / path).parent.exists() for path in OLD_FILES if "/skills/" in path)
    assert all((root / path).read_bytes() == data for path, data in user_repo_files().items())
    assert len(user_repo_files()) == 22
    assert all((root / path).read_text(encoding="utf-8") == text for path, text in USER_FILES.items())
    deleted = {line.split("\t", 1)[1] for line in git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD").splitlines()
               if line.startswith("D\t")}
    assert deleted == set(OLD_FILES)
    assert git(root, "status", "--porcelain") == ""


def test_every_generated_host_role_or_skill_path_matches_a_tool_owned_pattern():
    from tripwright.agents import TOOL_FILE_PATTERNS

    prefixes = (".claude/agents/", ".codex/agents/", ".claude/skills/", ".agents/skills/")
    paths = [path for path in user_repo_files() if path.startswith(prefixes)]
    assert paths and all(any(fnmatch(path, pattern) for pattern in TOOL_FILE_PATTERNS) for path in paths)


def test_upgrade_without_retired_files_keeps_the_existing_output_and_files(tmp_path, capsys):
    root = workspace.init(tmp_path / "repo").root
    assert main(["init", str(root), "--upgrade"]) == 0
    output = capsys.readouterr().out
    assert "Removed old tool file:" not in output
    assert output.startswith(f"Trip repository ready: {root}\nUse it with:")
    assert all((root / path).read_bytes() == data for path, data in user_repo_files().items())
    assert git(root, "log", "-1", "--format=%s") == "tripwright: refresh the tool's files\n"
    assert git(root, "status", "--porcelain") == ""
