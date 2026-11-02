"""Writing fragments that C copies into the skills verbatim and that every pack inlines."""
import re

import pytest

from tripwright.render.writing import LANGUAGES, NAMES, fragment, writing_rules

# Names from the private replay set and the old trip; examples must use fictional places only.
REAL = ("Hallstatt", "St. Wolfgang", "Wien Hbf", "Salzburg", "Bergen", "Oslo", "Gosau", "541", "542", "546", "抱石", "后湖")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("language", LANGUAGES)
def test_every_fragment_exists_in_both_languages_without_a_heading(name, language):
    text = fragment(name, language)
    assert text and not text.startswith("#") and "{{" not in text


def test_the_writing_fragment_has_nine_rules_and_one_before_after_pair():
    for language, before, after in (("zh", "改前：", "改后："), ("en", "Before:", "After:")):
        lines = writing_rules(language).splitlines()
        assert sum(line.startswith("- ") for line in lines) == 9
        assert [line for line in lines if line.startswith((before, after))] == lines[-2:]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("language", LANGUAGES)
def test_examples_use_fictional_places_only(name, language):
    text = fragment(name, language)
    assert [word for word in REAL if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", text)] == []


def test_rules_reference_has_all_ten_rules_and_says_to_write_the_search_out():
    for language, word in (("zh", "规则 "), ("en", "Rule ")):
        text = fragment("rules", language)
        assert [int(number) for number in re.findall(rf"^{word}(\d+)", text, flags=re.M)] == list(range(1, 11))
    assert "在哪个 App 搜什么" in fragment("rules", "zh") and "在哪个 App 搜什么" in writing_rules("zh")


def test_unknown_fragment_is_refused():
    with pytest.raises(ValueError, match="Unknown fragment"):
        fragment("tone")
