"""Rendering and output (block B): packs, answers, transport lines, query triggers, writing fragments, checks, views.

Entry points other blocks call:
- `render_pack(pack, *, sources=None) -> {markdown, tokens, required}` and `pack_meta(pack, required)` (A's service);
  `material_lines(pack)` is the reader's version the result page shows;
- `render_answer(answer, *, orders=None) -> str` (A's service);
- `repo_files(states, *, current, snapshot, packs=None, sources=None, memory=None) -> {path: text}` and the single-file
  functions in `screens` (A's committer);
- `fragment(name, language)` (C's skill assembler).
"""
from tripwright.render.answer import render_answer
from tripwright.render.lines import leg_text, place_line, place_lines, transit_leg
from tripwright.render.options import build_options, unauthorized_choices
from tripwright.render.pack import material_lines, pack_meta, render_pack
from tripwright.render.regions import choose, tool_hint
from tripwright.render.memory import memory_problems, rows as memory_rows
from tripwright.render.screens import (day_view, decisions_view, index_view, memory_view, pack_view, repo_files, repo_readme,
                                       today_view, trip_readme)
from tripwright.render.triggers import evaluate, markers_from_text
from tripwright.render.writing import fragment, writing_rules

__all__ = ["render_pack", "pack_meta", "material_lines", "render_answer", "leg_text", "place_line", "place_lines", "transit_leg",
           "build_options", "unauthorized_choices", "choose", "tool_hint", "evaluate", "markers_from_text", "fragment",
           "writing_rules", "repo_files", "repo_readme", "trip_readme", "memory_view", "memory_rows", "memory_problems", "today_view", "day_view", "pack_view", "index_view",
           "decisions_view"]
