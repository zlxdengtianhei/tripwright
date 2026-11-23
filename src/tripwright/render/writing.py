"""Source fragments that the pack renderer and C's skill assembler both copy verbatim.

The text lives in `fragments/<name>.<language>.md`; changing a rule means editing that file, and both the packs and
the generated skills pick it up without another edit.
"""
from __future__ import annotations

from importlib.resources import files

NAMES = ("writing", "skeleton", "transit", "triggers", "rules")
# Which fragments reach a host. Only `writing` does: every pack inlines it. The other four are kept for explicit
# `<!-- include: fragment … -->` lines, and no role includes them today, so editing them changes nothing a host reads;
# a rule meant for hosts goes into `agents/roles/*.md` or `agents/core.yaml` (11-23: a rule added to `rules` and
# `skeleton` never reached a host). tests/agents/test_rules_reach_hosts.py keeps this tuple true.
SHIPPED = ("writing",)
LANGUAGES = ("zh", "en")


def fragment(name: str, language: str = "zh") -> str:
    if name not in NAMES:
        raise ValueError(f"Unknown fragment {name!r}; known: {', '.join(NAMES)}")
    language = language if language in LANGUAGES else "zh"
    return (files(__package__) / "fragments" / f"{name}.{language}.md").read_text(encoding="utf-8").rstrip("\n")


def writing_rules(language: str = "zh") -> str:
    """The text that replaces the `{{WRITING_RULES}}` line in a rendered pack."""
    return fragment("writing", language)
