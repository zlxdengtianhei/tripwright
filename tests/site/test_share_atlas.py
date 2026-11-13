"""The share pages in the scheme "atlas" (web_ui_spec D-14, D-16): the structure of a day page (key times before the
figure on a phone, the figure next to the list it numbers), one numbering for the map and the lists, the marks on what
really changed, the sketch when there are no coordinates, and the scheme switch. Names and places are invented except
in the shipped example."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from test_share import NOW, PEOPLE, build, sample_plan  # noqa: F401
from tripwright.site import theme
from tripwright.site.example import build_example
from tripwright.site.share import share_site

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/trip-2026-10-central-europe.yaml"


@pytest.fixture(scope="module")
def example(tmp_path_factory):
    return build_example(EXAMPLE, tmp_path_factory.mktemp("atlas") / "example", now=NOW)["out"]


def day_of(folder: Path, date: str) -> str:
    return (folder / f"{date}.html").read_text(encoding="utf-8")


def section(html: str, start: str, end: str) -> str:
    return html[html.index(start):html.index(end)]


def test_key_times_come_before_the_figure_and_the_figure_sits_right_before_the_places_it_numbers(example):
    html = day_of(example, "2026-10-27")
    order = [html.index(marker) for marker in ('class="blk keytimes"', 'class="figcol"', 'class="listcol"', "Places to go", "Getting around")]
    assert order == sorted(order)
    # nothing between the figure and the list it numbers
    between = html[html.index('class="figcol"'):html.index("Places to go")]
    assert "<section" not in between.replace('<section class="blk">', "") and "Key times" not in between


def test_the_map_and_the_lists_use_the_same_pins(example):
    html = day_of(example, "2026-10-27")
    places = section(html, "Places to go", "Getting around")
    listed = re.findall(r'<span class="pin (fixed|visit|opt)">([0-9A-Z]+)</span>', places)
    assert listed == [("fixed", "1"), ("visit", "2"), ("visit", "3"), ("visit", "4"), ("opt", "5A"), ("opt", "5B"), ("opt", "5C")]
    drawn = re.findall(r'<path class="m-pin (fixed|visit|opt)"[^>]*/><text class="m-num [a-z]+"[^>]*>([0-9A-Z]+)</text>', html)
    assert sorted(drawn) == sorted(listed)  # every place with coordinates on the sketch, with its own number and shape
    data = json.loads(re.search(r'<script type="application/json" id="map-day">(.*?)</script>', html, re.S).group(1))
    assert sorted((point["shape"], point["label"]) for point in data["points"]) == sorted(listed)  # the real map: the same pins
    assert len(data["branches"]) == 3 and len(data["path"]) == 4  # 1→2→3→4 in a line, 4 to each of 5A–5C dashed


def test_a_leg_names_the_pins_at_its_two_ends(example):
    legs = section(day_of(example, "2026-10-27"), "Getting around", "Notes and rain")
    rows = {re.search(r'class="t">([0-9:]+)', row).group(1): re.findall(r'class="pin ([a-z]+) mini">([0-9A-Z]+)<', row)
            for row in legs.split("</li>")[:-1]}
    assert rows["13:30"] == [("fixed", "1"), ("visit", "2")]
    assert rows["14:44"] == [("visit", "4"), ("opt", "5")]  # to one of several: the number alone
    assert rows["11:40"] == [("fixed", "1")]  # from the station: no pin, nothing invented


def test_what_really_changed_is_struck_out_and_marked_new_only_on_its_day(example):
    html = day_of(example, "2026-10-27")
    assert "<del>16:57 IC 742</del>" in html and "<ins>IC 744</ins>" in html
    assert 'class="revkey"' in html
    keytimes = section(html, "Key times", 'class="figcol"')
    assert keytimes.count("<del>") == 3 and keytimes.count("<ins>") == 3  # two languages each, and the legend has one of each
    for other in ("2026-10-26.html", "2026-10-28.html", "index.html"):
        assert "<del>" not in (example / other).read_text(encoding="utf-8"), other


def test_a_change_whose_words_are_not_on_the_page_marks_nothing_and_adds_nothing(tmp_path):
    extras = dict(changes=[dict(date="2030-07-14", was="Westbahnhof 08:12", now="Erfundener Zug 99")])
    html = (share_site(sample_plan(), tmp_path / "site", people=PEOPLE, now=NOW, extras=extras)["out"] / "2030-07-14.html").read_text()
    assert "<del>" not in html and "<ins>" not in html and 'class="revkey"' not in html
    assert "Erfundener Zug 99" not in html and "Westbahnhof 08:12" not in html


def test_without_coordinates_the_figure_falls_back_to_pins_in_order_and_no_map_is_loaded(tmp_path):
    html = (build(tmp_path)["out"] / "2030-07-14.html").read_text()  # the museum and the café have no coordinates
    figure = section(html, 'class="figcol"', 'class="listcol"')
    assert "No coordinates: the pins stand in order only" in figure and "没有坐标：钉只按先后排" in figure
    assert "2 places have no coordinates and are not drawn." in figure
    assert 'type="application/json"' not in html  # nothing for a real map to draw
    places = section(html, "Places to go", "Getting around")
    assert re.findall(r'<span class="pin [a-z]+">([0-9A-Z]+)</span>', places) == ["1", "2"]  # the list still numbered
    assert "48.20" not in html and "16.35" not in html  # the stay's coordinates are never drawn


def test_with_some_coordinates_the_sketch_says_what_it_left_out(tmp_path):
    plan = sample_plan()
    next(place for place in plan["places"] if place["id"] == "kunsthalle")["coords"] = [48.2035, 16.3590]
    html = (build(tmp_path, plan)["out"] / "2030-07-14.html").read_text()
    figure = section(html, 'class="figcol"', 'class="listcol"')
    assert "Position sketch from coordinates, no base map" in figure and "1 place has no coordinates and is not drawn." in figure
    data = json.loads(re.search(r'id="map-day">(.*?)</script>', html, re.S).group(1))
    assert [point["label"] for point in data["points"]] == ["1"]
    assert re.search(r'<text class="m-num [a-z]+"[^>]*>1</text>', figure) and not re.search(r'<text class="m-num [a-z]+"[^>]*>2</text>', figure)


def test_the_page_takes_its_values_from_the_scheme_and_every_scheme_gives_what_the_components_read(tmp_path):
    design = (Path(theme.THEMES) / "atlas" / "DESIGN.md").read_text(encoding="utf-8")
    table = yaml.safe_load(design.split("---")[1])
    html = (build(tmp_path)["out"] / "index.html").read_text()
    assert 'data-theme="atlas"' in html
    for name, value in table["colors"].items():
        assert f"--{name}:{value.lower()}" in html  # exported from DESIGN.md, not copied by hand
    shared = theme.SHARED.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", shared)  # the shared components name no colour of their own
    for name in theme.names():
        assert theme.used(shared) <= set(theme.load(name).variables), name


def test_an_unknown_scheme_or_one_missing_a_value_is_refused(tmp_path, monkeypatch):
    with pytest.raises(theme.UnknownTheme):
        share_site(sample_plan(), tmp_path / "x", people=PEOPLE, now=NOW, theme="route-map")
    folder = tmp_path / "themes" / "thin"
    folder.mkdir(parents=True)
    folder.joinpath("DESIGN.md").write_text("---\ncolors:\n  ink: \"#000000\"\n---\n# thin\n", encoding="utf-8")
    monkeypatch.setattr(theme, "THEMES", tmp_path / "themes")
    theme.load.cache_clear()
    try:
        with pytest.raises(ValueError, match="does not give"):
            theme.style("thin")
    finally:
        theme.load.cache_clear()


def test_the_script_keeps_the_sketch_when_the_map_or_its_tiles_do_not_load():
    script = theme.SHARED.with_name("share.js").read_text(encoding="utf-8")
    assert 'layer.on("tileload"' in script and "if (!loaded) back(item)" in script  # no tile in time: back to the sketch
    assert "catch (e) { back(item); }" in script  # the map library failing to draw: back to the sketch
    assert "script.onerror" not in script  # the library not loading leaves the sketch untouched: nothing to undo


def test_leg_ends_allow_a_wait_between_but_not_another_leg():
    from tripwright.site.figure import leg_ends, number_stops
    stops = [dict(start="10:00", end="11:00", certainty="fixed", spots=[{}]), dict(start="11:40", end="12:00", certainty="", spots=[{}, {}])]
    labels = number_stops(stops)
    walk = dict(start="11:05", end="11:30")
    assert leg_ends(walk, stops, labels, [walk]) == (dict(label="1", shape="fixed"), dict(label="2", shape="opt"))
    tram = dict(start="11:32", end="11:38")  # a second leg after the walk: the walk no longer reaches stop 2 itself
    assert leg_ends(walk, stops, labels, [walk, tram]) == (dict(label="1", shape="fixed"), None)
    assert leg_ends(tram, stops, labels, [walk, tram]) == (None, dict(label="2", shape="opt"))
