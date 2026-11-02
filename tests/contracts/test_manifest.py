"""Write domains do not overlap, and generated files belong to their generator (CONTRACTS section 4)."""
import copy
import json
import subprocess

import contract_checks as cc


def repository_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "."], cwd=cc.ROOT, check=True,
                            capture_output=True, text=True).stdout.splitlines()
    return sorted(path for path in listed if (cc.ROOT / path).is_file())


def manifest() -> dict:
    return json.loads((cc.HERE / "manifest.json").read_text(encoding="utf-8"))


def contracts() -> str:
    return (cc.ROOT / "docs/CONTRACTS.md").read_text(encoding="utf-8")


def test_every_file_has_exactly_one_writer_and_generated_files_sit_with_their_generator():
    files = repository_files()
    assert "docs/CONTRACTS.md" in files and "src/tripwright/versions.py" in files
    assert cc.manifest_problems(manifest(), files, contracts()) == []


def test_overlapping_domains_are_detected():
    broken = manifest()
    broken["domains"]["B"]["paths"].append("skills/**")
    problems = cc.manifest_problems(broken, repository_files(), contracts())
    assert any("skills/tripwright-main/SKILL.md: owned by ['B', 'C']" in problem for problem in problems)


def test_an_unowned_file_and_a_template_outside_c_are_detected():
    problems = cc.manifest_problems(manifest(), ["notes/todo.md", "src/tripwright/render/AGENTS.md.tmpl"], contracts())
    assert any("notes/todo.md: owned by nobody" in problem for problem in problems)
    assert any("templates are written by C only" in problem for problem in problems)


def test_a_generated_file_outside_its_generator_and_an_undocumented_glob_are_detected():
    broken = manifest()
    broken["generated_in_repo"][0] = dict(broken["generated_in_repo"][0], generator="B")
    broken["domains"]["A"]["paths"].append("src/tripwright/sync.py")
    problems = cc.manifest_problems(broken, [], contracts())
    assert any("not its generator B" in problem for problem in problems)
    assert any("src/tripwright/sync.py is not listed" in problem for problem in problems)


def test_user_repo_paths_name_one_generator_whose_domain_holds_the_source():
    entries = manifest()["user_repo"]
    assert len({entry["path"] for entry in entries}) == len(entries)
    broken = copy.deepcopy(manifest())
    broken["user_repo"][0]["generator"] = "C"
    assert cc.manifest_problems(broken, [], contracts())
