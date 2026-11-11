"""The English and Chinese README files stay in step, and what they point to exists."""
from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = {"en": ROOT / "README.md", "zh": ROOT / "README.zh-CN.md"}
MAX_LINES = 150


def text_of(language: str) -> str:
    return FILES[language].read_text(encoding="utf-8")


def outside_code(markdown: str) -> str:
    """The markdown without its fenced code blocks (a `# comment` in a bash block is not a heading)."""
    return re.sub(r"^```.*?^```", "", markdown, flags=re.S | re.M)


def headings(markdown: str, level: str = "##") -> list[str]:
    return [line for line in outside_code(markdown).splitlines() if re.match(rf"{level} ", line)]


def local_targets(markdown: str) -> list[str]:
    body = outside_code(markdown)
    found = re.findall(r"\]\(([^)\s]+)\)", body) + re.findall(r'<img[^>]*\ssrc="([^"]+)"', body)
    return [target for target in found if not re.match(r"[a-z]+:|#", target)]


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def test_both_files_have_the_same_number_of_sections() -> None:
    assert len(headings(text_of("en"))) == len(headings(text_of("zh")))
    assert len(headings(text_of("en"))) >= 6


def test_each_file_has_one_title_and_the_same_number_of_diagrams() -> None:
    assert text_of("en").count("```mermaid") == text_of("zh").count("```mermaid") >= 1
    for language in FILES:
        assert len(headings(text_of(language), "#")) == 0  # the title is the logo line, not a markdown heading


@pytest.mark.parametrize("language, other", [("en", "README.zh-CN.md"), ("zh", "README.md")])
def test_language_row_points_to_the_other_file_near_the_top(language: str, other: str) -> None:
    top = "\n".join(text_of(language).splitlines()[:8])
    assert f"]({other})" in top


@pytest.mark.parametrize("language", ["en", "zh"])
def test_relative_links_point_to_existing_files(language: str) -> None:
    targets = local_targets(text_of(language))
    assert targets
    missing = [target for target in targets if not (ROOT / target.split("#")[0]).exists()]
    assert not missing


def test_both_files_link_to_the_same_files() -> None:
    def files(language: str) -> set[str]:
        return {target.split("#")[0] for target in local_targets(text_of(language))}

    assert files("en") ^ files("zh") <= {"README.md", "README.zh-CN.md"}


@pytest.mark.parametrize("language", ["en", "zh"])
def test_readme_stays_short(language: str) -> None:
    assert len(text_of(language).splitlines()) <= MAX_LINES


def test_pictures_have_their_sizes_and_the_homepage_copy_is_the_same_file() -> None:
    hero, social = ROOT / "docs/assets/hero.png", ROOT / "docs/assets/social-preview.png"
    assert png_size(social) == (1280, 640) and social.stat().st_size <= 300_000
    assert png_size(hero)[0] == 1600 and hero.stat().st_size <= 500_000
    for name in ("hero.png", "social-preview.png"):
        assert (ROOT / "homepage/assets" / name).read_bytes() == (ROOT / "docs/assets" / name).read_bytes()
