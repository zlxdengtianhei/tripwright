"""Upgrade removes retired Tripwright host files without touching the repository owner's files."""
from __future__ import annotations

from fnmatch import fnmatch

from repo_fixtures import BY_HAND, git
from tripwright.agents import user_repo_files
from tripwright.cli import main
from tripwright.repo import workspace

# A role the tool no longer ships (the requirements and verifier roles were this example until they came back, 2026-11-12).
OLD_FILES = (
    ".agents/skills/tripwright-retired/SKILL.md",
    ".claude/agents/tripwright-retired.md",
    ".claude/skills/tripwright-retired/SKILL.md",
    ".codex/agents/tripwright_retired.toml",
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
    assert len(user_repo_files()) == 30
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


def test_check_on_a_fresh_repository_finds_nothing_wrong(tmp_path, capsys):
    # G13 (2026-11-12 pool): right after init, check said "README.md: differs from what the tool generates", which read as
    # a broken install; init writes no front screen and the first write generates it
    for name, trip in (("empty", None), ("with-trip", "2030-06-oslo")):
        root = workspace.init(tmp_path / name, trip=trip).root
        assert main(["--repo", str(root), "check"]) == 0, capsys.readouterr().out
        assert '"problems": []' in capsys.readouterr().out


def test_once_the_tool_has_written_a_missing_front_screen_is_still_a_difference(tmp_path, capsys):
    # control for the test above: the exemption covers only a repository the tool has not written to
    from repo_fixtures import Writer, new_repository, seeded

    writer = Writer(new_repository(tmp_path / "trip"))
    seeded(writer)
    writer.close()
    root = tmp_path / "trip"
    assert main(["--repo", str(root), "check"]) == 0
    capsys.readouterr()
    git(root, "rm", "-q", "README.md")
    git(root, *BY_HAND, "commit", "-qm", "front screen removed by hand")
    assert main(["--repo", str(root), "check"]) == 1
    assert "README.md: differs from what the tool generates" in capsys.readouterr().out
