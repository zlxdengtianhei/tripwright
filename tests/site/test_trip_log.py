"""The optional example trip log: bilingual field notes, then and now, with the same privacy boundary as every page."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from test_share import NOW, sample_plan
from tripwright.site.example import build_example
from tripwright.site.share import ShareBlocked, share_site

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/trip-2026-10-central-europe.yaml"


def log_data() -> dict:
    return dict(
        intro=dict(zh="这一页记下路上发生的事。", en="This page records what happened on the road."),
        origin=dict(zh="当时的处理来自纸质时刻表。", en="The handling then came from a paper timetable."),
        pieces=[dict(
            id="missed-ferry",
            title=dict(zh="误了一班船", en="Missed the ferry"),
            when=dict(zh="2030-06-11 周二 10:24，卑尔根码头", en="Tue 11 Jun 2030, 10:24, Bergen pier"),
            said=[
                dict(time="10:24", zh="我们好像误了一班船，现在怎么走", en="It looks like we just missed a ferry. How do we go now?"),
                dict(time="10:40", retold=True, zh="之后又问了下一班的时刻", en="Then asked for the time of the next one"),
            ],
            then=dict(zh="它让我们去问码头工作人员。", en="It told us to ask the pier staff."),
            became=dict(zh="现在先查下一班，再给出备选。", en="Now it checks the next sailing before alternatives."),
            match=dict(zh="对上", en="Matches"),
            replay=dict(
                setup=dict(zh="2030-06-20 用虚构行程跑的", en="Run on 20 Jun 2030 with an invented trip"),
                asked=dict(zh="我们好像误了一班船，现在怎么走", en="It looks like we just missed a ferry. How do we go now?"),
                parts=[dict(
                    label=dict(zh="第一条回复（14 秒）", en="First reply (14 s)"),
                    text=dict(zh="第一段原文", en="The first part"),
                )],
                note=dict(zh="这次没列出码头位置。", en="It omitted the pier location this time."),
            ),
        )],
    )


def example_with_log(tmp_path, log: dict | None) -> Path:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    if log is not None:
        document["log"] = log
    path = tmp_path / "example.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def build_log(tmp_path, log: dict | None = None):
    return build_example(example_with_log(tmp_path, log_data() if log is None else log), tmp_path / "site", now=NOW)


def test_an_example_without_a_log_has_no_log_page_or_links(tmp_path):
    result = build_example(EXAMPLE, tmp_path / "site", now=NOW)
    assert "log.html" not in result["pages"] and not (result["out"] / "log.html").exists()
    for name in result["pages"]:
        assert 'href="log.html"' not in (result["out"] / name).read_text(encoding="utf-8")


def test_a_log_builds_a_page_with_ordered_sections_and_navigation_on_every_page(tmp_path):
    result = build_log(tmp_path)
    html = (result["out"] / "log.html").read_text(encoding="utf-8")
    assert "log.html" in result["pages"] and "<title>Trip log · " in html
    for title in ("误了一班船", "Missed the ferry"):
        assert title in html
    headings = [html.index(text) for text in ("What I said", "How it was handled then", "tripwright now")]
    assert headings == sorted(headings)
    for name in result["pages"]:
        page = (result["out"] / name).read_text(encoding="utf-8")
        assert 'href="log.html" data-lang-link' in page and "Trip log" in page
    assert 'href="log.html" data-lang-link aria-current="page"' in html
    home = (result["out"] / "index.html").read_text(encoding="utf-8")
    assert "路上的实录 →" in home and "Trip log →" in home
    assert 'href="#missed-ferry"' in html and 'class="card log-origin"' in html


def test_an_original_saying_is_a_timed_quote_but_a_retelling_is_not(tmp_path):
    html = (build_log(tmp_path)["out"] / "log.html").read_text(encoding="utf-8")
    quote = html[html.index("<blockquote>"):html.index("</blockquote>")]
    assert '<span class="time">10:24</span>' in quote and "It looks like we just missed a ferry" in quote
    start = html.index('<p class="retold">')
    retold = html[start:html.index("</p>", start)]
    assert "Retold" in retold and "转述" in retold and "Then asked for the time" in retold
    assert "<blockquote" not in retold


def test_a_replay_keeps_literal_escaped_text_and_newlines_in_a_pre_wrap_element(tmp_path):
    log = log_data()
    log["pieces"][0]["replay"]["parts"][0]["text"] = dict(
        zh="<script>alert(1)</script>\n\n**粗体**",
        en="<script>alert(1)</script>\n\n**bold**",
    )
    html = (build_log(tmp_path, log)["out"] / "log.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\n\n**bold**" in html
    assert "**粗体**" in html and "<strong>" not in html
    assert 'class="log-reply"' in html and "white-space:pre-wrap" in html


def test_names_in_then_and_replay_text_are_rewritten_to_traveler_codes(tmp_path):
    log = log_data()
    log["pieces"][0]["then"] = dict(zh="Fiona Example 让我们去问工作人员。", en="Fiona Example told us to ask the staff.")
    log["pieces"][0]["replay"]["parts"][0]["text"] = dict(zh="Fiona Example 可以先问下一班。",
                                                               en="Fiona Example can ask about the next sailing first.")
    result = share_site(sample_plan(), tmp_path / "site", extras={"log": log}, now=NOW)
    html = (result["out"] / "log.html").read_text(encoding="utf-8")
    assert "Fiona" not in html and "旅伴 A" in html and "Traveler A" in html


def test_a_phone_in_a_log_blocks_the_build_and_preserves_the_previous_output(tmp_path):
    result = share_site(sample_plan(), tmp_path / "site", extras={"log": log_data()}, now=NOW)
    before = {path.name: path.read_bytes() for path in result["out"].iterdir()}
    log = log_data()
    log["pieces"][0]["then"] = dict(zh="打 +43 660 1234567 联系", en="Call +43 660 1234567")
    with pytest.raises(ShareBlocked) as caught:
        share_site(sample_plan(), tmp_path / "site", extras={"log": log}, now=NOW)
    assert caught.value.findings["phone number"] == ["log.html"]
    assert "1234567" not in str(caught.value)
    assert {path.name: path.read_bytes() for path in result["out"].iterdir()} == before


@pytest.mark.parametrize("change, fragment, field", [
    (lambda log: log["pieces"][0].pop("then"), "missed-ferry", "then"),
    (lambda log: log["pieces"].append(deepcopy(log["pieces"][0])), "missed-ferry", "id"),
    (lambda log: log["pieces"][0].update(said=[]), "missed-ferry", "said"),
    (lambda log: log["pieces"][0].update(id="Missed Ferry"), "Missed Ferry", "id"),
    (lambda log: log["pieces"][0]["replay"].update(parts=[]), "missed-ferry", "parts"),
])
def test_a_malformed_log_names_the_fragment_and_the_invalid_field(tmp_path, change, fragment, field):
    log = log_data()
    change(log)
    with pytest.raises(ValueError) as caught:
        build_log(tmp_path, log)
    assert fragment in str(caught.value) and field in str(caught.value)
