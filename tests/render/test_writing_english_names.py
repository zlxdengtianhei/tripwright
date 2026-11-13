"""Analysis item 7 (2026-11-13, coverage page tw-1031-g06): an English answer gives a place its English name next to
the local spelling. The main role says so (B, 8c86cc9); the writing fragment the packs carry says it too."""
from tripwright.render.writing import fragment


def test_the_english_writing_fragment_gives_a_place_its_english_name():
    text = fragment("writing", "en")
    assert "with its English name when the local name is not English (Wien Hauptbahnhof: Vienna's main station)" in text
