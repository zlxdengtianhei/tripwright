"""The page friends read, as friends see it (home page, day page, the example), and the relaxed privacy rules: a name, a
stay's name or a flight number found by the scan is swapped, not blocked; the example file builds and a stay's name or an
order number put into it does not get through. All names, stays and numbers here are invented."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from test_share import NOW, PEOPLE, PRIVATE, block, build, sample_plan, with_text  # noqa: F401
from tripwright.site.example import build_example, load_example
from tripwright.site.share import ShareBlocked, share_site

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/trip-2026-10-central-europe.yaml"


def test_home_page_has_title_intro_route_and_a_row_per_day(tmp_path):
    extras = dict(intro="一趟虚构的两天行程。", route=[
        dict(name="维也纳", note="7 月 14—15 日 · 住 1 晚", leg="火车，约 1 小时", lat=48.2, lon=16.37, day="2030-07-14"),
        dict(name="奥斯陆", note="7 月 15 日", day="2030-07-15", lat=59.9, lon=10.75)])
    result = share_site(sample_plan(), tmp_path / "site", people=PEOPLE, now=NOW, extras=extras,
                        english={"一趟虚构的两天行程。": "A made-up two-day trip.", "维也纳": "Vienna", "火车，约 1 小时": "train, about 1 h",
                                 "奥斯陆": "Oslo", "7 月 14—15 日 · 住 1 晚": "14–15 Jul · 1 night", "7 月 15 日": "15 Jul"})
    home = (result["out"] / "index.html").read_text()
    assert "A made-up two-day trip." in home and "Vienna" in home and "train, about 1 h" in home and "14–15 Jul · 1 night" in home
    assert home.count('class="stop"') == 2 and 'id="map-all"' in home and '"lat": 48.2' in home
    assert home.index("The route") < home.index("Day by day")
    assert 'href="2030-07-14.html"' in home and "Day 1 · Sun" in home and "第 1 天 · 周日" in home
    assert "a hotel in 1070 Wien" in home  # a row says where the night is: district and type only
    assert 'class="days"' in home and ">7/14<" in home  # the strip of days under the header


def test_a_home_page_without_extras_derives_its_route_from_the_days(tmp_path):
    plan = sample_plan()
    assert 'class="stop"' not in (build(tmp_path, plan)["out"] / "index.html").read_text()  # one area, one stop: no route list
    plan["days"][1]["area"] = "Oslo"
    home = (build(tmp_path, plan, name="two")["out"] / "index.html").read_text()
    assert home.count('class="stop"') == 2 and "14 Jul · 1 night" in home and "15 Jul" in home  # a night at the stay on day 1 only


def test_day_page_starts_with_key_times_and_places_then_getting_around_and_folds_the_rest(tmp_path):
    html = (build(tmp_path)["out"] / "2030-07-14.html").read_text()
    order = [html.index(marker) for marker in ("Key times", "Places to go", "Getting around", "Notes and rain", "hour by hour")]
    assert order == sorted(order)
    assert 'data-at="2030-07-14T10:00:00+02:00"' in html and 'data-date="2030-07-14"' in html  # the key time knows its zone
    assert html.count("<details") == 2 and "<details open" not in html
    assert '<a href="2030-07-15.html" data-lang-link>' in html and "daynavs" in html


def test_places_to_go_carry_a_map_link_each_and_getting_around_says_how(tmp_path):
    html = (build(tmp_path)["out"] / "2030-07-14.html").read_text()
    section = html[html.index("Places to go"):html.index("Getting around")]
    assert section.count("google.com/maps/search") == 2  # the museum and the café, not the stay
    assert "Musterweg" not in html
    legs = html[html.index("Getting around"):html.index("Notes and rain")]
    assert "U6 → Westbahnhof，换 18 路电车，€2.40" in legs


def test_language_buttons_and_the_untranslated_note(tmp_path):
    html = (build(tmp_path)["out"] / "index.html").read_text()
    assert html.index('data-lang-button="en"') < html.index('data-lang-button="zh"') and ">EN</button>" in html and ">中文</button>" in html
    assert "tw-lang" in html and 'location.search).get("lang")' in html  # remembered; a link can say it


# --- the scan swaps names, stays and flight numbers instead of blocking, and still blocks the rest -------------------

def leaking(field: str, text: str) -> dict:
    plan = sample_plan()
    plan["days"][0][field] = text  # a field the page prints as it is: where a name could still slip through
    return plan


@pytest.mark.parametrize("leak, category, generic", [
    ("Fiona Example schaut zu", "person name", "Traveler A"),
    ("zurück zu Hotel Fictiva Rosengarten", "lodging", "our stay"),
    ("Abflug QX 4821", "flight number", "flight"),
])
def test_a_name_a_stay_or_a_flight_number_the_scan_finds_is_swapped_and_the_page_goes_out(tmp_path, leak, category, generic):
    result = build(tmp_path, leaking("sunset", leak))
    html = (result["out"] / "2030-07-14.html").read_text()
    assert result["swapped"] == {category: ["2030-07-14.html"]}
    assert generic in html
    for private in PRIVATE:
        assert private not in html, private


def test_the_swap_leaves_public_stations_lines_and_times_alone(tmp_path):
    result = build(tmp_path, leaking("sunset", "Fiona Example, U6 → Westbahnhof 09:31, REX 7, RJX 19959"))
    html = (result["out"] / "2030-07-14.html").read_text()
    assert "U6 → Westbahnhof 09:31, REX 7, RJX 19959" in html and "Traveler A" in html
    assert result["swapped"] == {"person name": ["2030-07-14.html"]}


def test_a_swap_that_does_not_clean_the_page_still_blocks(tmp_path):
    with pytest.raises(ShareBlocked) as caught:
        build(tmp_path, leaking("sunset", "Fiona Example mailt fiction.person@example.test"))
    assert list(caught.value.findings) == ["email"] and "example.test" not in str(caught.value)


# --- the example --------------------------------------------------------------------------------------------------

def test_the_example_is_a_valid_trip_of_eight_days_that_builds_with_all_english(tmp_path):
    plan, english, extras = load_example(EXAMPLE)
    assert len(plan["days"]) == 8 and plan["start"] == "2026-10-25" and plan["end"] == "2026-11-01"
    result = build_example(EXAMPLE, tmp_path / "example", now=NOW)
    assert result["pages"] == ["2026-10-25.html", "2026-10-26.html", "2026-10-27.html", "2026-10-28.html", "2026-10-29.html",
                               "2026-10-30.html", "2026-10-31.html", "2026-11-01.html", "index.html"]
    assert result["untranslated"] == [] and result["swapped"] == {}


def test_the_day_everyone_missed_the_bus_answers_where_when_which_line_where_to_change_and_the_latest_time(tmp_path):
    html = (build_example(EXAMPLE, tmp_path / "example", now=NOW)["out"] / "2026-10-29.html").read_text()
    assert "Gosausee (front lake to back lake)" in html                      # where to
    assert "10:31" in html and "our stop in Bad Ischl → Hallstatt Gosaumühle" in html and "541 (the one that ran about 9′ late)" in html  # when and from where, which line
    assert "Hallstatt Gosaumühle → Hintertal b.Gosau Zwieselalmbahn" in html and "542 (timetable 10:45 → 11:09" in html  # where to change
    assert "14:35" in html and "Latest time to start back from the back lake" in html  # the latest time to turn back
    assert "An apartment" not in html and "an apartment in 4820 Bad Ischl" in html  # the night is a district and a type
    for gone in ("Taxi Egger", "Gosauer Taxi"):  # the taxi plan is in the alternatives, with its note, not dropped
        assert gone in html


def test_an_invented_stay_name_is_swapped_and_an_invented_order_number_stops_the_example(tmp_path):
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    stay = next(place for place in document["trip"]["places"] if place["id"] == "stay-wien")
    stay.update(name="Hotel Fiktiv Sonnenhof", name_local="Hotel Fiktiv Sonnenhof, Beispielgasse 5, 1100 Wien")
    day = next(day for day in document["trip"]["days"] if day["date"] == "2026-10-30")
    day["notes"] = "晚上回 Hotel Fiktiv Sonnenhof 休息"
    document["english"][day["notes"]] = "Back to Hotel Fiktiv Sonnenhof for the night"
    path = tmp_path / "example.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    result = build_example(path, tmp_path / "ok", now=NOW)
    html = (result["out"] / "2026-10-30.html").read_text(encoding="utf-8")
    assert "Sonnenhof" not in html and "Beispielgasse" not in html and "a hostel in 1100 Wien" in html
    assert "Back to our stay for the night" in html

    day["notes"] = "订单号 FX20261030A，晚上回去"
    document["english"][day["notes"]] = "Order number FX20261030A, back at night"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ShareBlocked) as caught:
        build_example(path, tmp_path / "blocked", now=NOW)
    assert caught.value.findings == {"order-like identifier": ["2026-10-30.html"]}
    assert "FX20261030A" not in str(caught.value) and not (tmp_path / "blocked").exists()


def test_the_example_has_no_private_data_in_it():
    from tripwright.site.privacy import scan_text

    text = EXAMPLE.read_text(encoding="utf-8")
    assert [finding for finding in scan_text(text) if finding[1] != "flight number"] == []
