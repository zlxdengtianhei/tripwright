"""The delivery-level guide: who each level serves, setup, where multi-agent comes from, and L1 recipes."""
import re

from tripwright.agents.assemble import ROOT

LEVELS = ROOT / "skills/tripwright-main/references/levels.md"


def level_problems(text: str) -> list[str]:
    problems = []
    sections = dict(re.findall(r"^## (L\d) [^\n]*\n([\s\S]*?)(?=^## |\Z)", text, re.M))
    if sorted(sections) != ["L1", "L2"]:
        problems.append(f"levels found: {sorted(sections)}")
    for level in ("L1", "L2"):
        body = sections.get(level, "")
        for field in ("- For:", "- Set up:", "- Multi-agent:", "- Lacks, compared with"):
            if field not in body:
                problems.append(f"{level} lacks {field}")
    l1 = sections.get("L1", "")
    for recipe in ("Claude Code on your computer", "Codex on your computer", "The Code page of the Claude phone app (文档说可用，未实测)"):
        if recipe not in l1:
            problems.append(f"L1 lacks the recipe {recipe}")
    if "one folder per trip under `trips/`" not in text:
        problems.append("must say one repository holds the trips, one folder per trip under trips/")
    return problems


def test_levels_guide_covers_every_level_and_the_l1_details():
    assert level_problems(LEVELS.read_text(encoding="utf-8")) == []


def test_levels_guide_problems_are_caught():
    text = LEVELS.read_text(encoding="utf-8")
    assert any("文档说可用，未实测" in problem for problem in level_problems(text.replace("(文档说可用，未实测)", "")))
    assert any("L2 lacks - Multi-agent:" == problem for problem in level_problems(text.replace("- Multi-agent: none on the server", "- Agents: none on the server")))
    assert any("one folder per trip" in problem for problem in level_problems(text.replace("one folder per trip under `trips/`", "one repository per trip")))
