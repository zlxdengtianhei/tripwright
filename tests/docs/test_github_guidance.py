"""What to tell the user about GitHub is written once, in the setup guide; the service's note and both READMEs agree with it."""
from __future__ import annotations

import re
from pathlib import Path

from tripwright.repo.committer import LOCAL_NOTE

ROOT = Path(__file__).resolve().parents[2]
SETUP = (ROOT / "deploy" / "SETUP.md").read_text(encoding="utf-8")


def section(number: str) -> tuple[str, str]:
    found = re.search(rf"^## {number}\. ([^\n]*)\n(.*?)(?=^## )", SETUP, flags=re.S | re.M)
    assert found, f"deploy/SETUP.md has no section {number}"
    return found.group(1), found.group(2)


def test_the_setup_guide_recommends_the_private_repository_and_says_where_trips_stay_without_it() -> None:
    title, body = section("3")
    assert title.startswith("Recommended")
    assert "GitHub is optional" not in SETUP
    assert "kept only on the server" in body and "Nothing is uploaded to GitHub" in body


def test_the_setup_guide_has_the_agent_look_at_where_it_runs_before_recommending() -> None:
    _, body = section("3")
    assert "gh auth status" in body  # an agent with a shell checks, then recommends and runs the commands
    assert "Claude Code or Codex" in body  # an agent without one tells the user where to do it


def test_the_setup_guide_says_public_pages_need_github() -> None:
    title, body = section("3A")
    assert title.startswith("Optional")
    assert body.lstrip().startswith("Public trip pages need GitHub")


def test_the_service_note_for_a_local_repository_points_to_setup_sections_that_exist() -> None:
    assert "Back it up yourself" in LOCAL_NOTE
    assert "deploy/SETUP.md, sections 3 and 3A" in LOCAL_NOTE
    section("3"), section("3A")


def test_both_readmes_call_the_github_step_recommended_and_say_the_same_two_things() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "**Recommended: connect GitHub.**" in english and "**推荐：连 GitHub。**" in chinese
    assert "kept only on the server" in english and "只存在服务器上" in chinese
    assert "public pages need it" in english and "要公开页就得连它" in chinese


def test_the_setup_guide_turns_on_pages_after_the_first_push() -> None:
    """GitHub answers 422 "The main branch must exist before GitHub Pages can be built" for an empty repository
    (2026-11-12, a one-off public test repository): Pages is turned on after `share connect` pushed main."""
    _, body = section("3A")
    connect = body.index("tripwright share connect git@github.com:")
    pages = body.index("/pages -f 'source[branch]=main'")
    assert connect < pages
