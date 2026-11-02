"""Source fragments that the pack renderer and C's skill assembler both copy verbatim.

The text lives in `fragments/<name>.<language>.md`; changing a rule means editing that file, and both the packs and
the generated skills pick it up without another edit.
"""
from __future__ import annotations

from importlib.resources import files

NAMES = ("writing", "skeleton", "transit", "triggers", "rules")
LANGUAGES = ("zh", "en")


def fragment(name: str, language: str = "zh") -> str:
    if name not in NAMES:
        raise ValueError(f"Unknown fragment {name!r}; known: {', '.join(NAMES)}")
    language = language if language in LANGUAGES else "zh"
    return (files(__package__) / "fragments" / f"{name}.{language}.md").read_text(encoding="utf-8").rstrip("\n")


def writing_rules(language: str = "zh") -> str:
    """The text that replaces the `{{WRITING_RULES}}` line in a rendered pack."""
    return fragment("writing", language)
