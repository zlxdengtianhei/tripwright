"""Link formats, the region table and the leg/place lines with their search terms (design 9.3; user requirement 10-31)."""
import pytest

from tripwright.render import links
from tripwright.render.lines import leg_text, place_line, place_lines, timetable_place, transit_leg
from tripwright.render.regions import choose, table, tool_hint


def test_link_formats_encode_names_and_keep_clock_times_readable():
    assert links.google_directions("Uferweg Nord, Seeufer", mode="walk") == \
        "https://www.google.com/maps/dir/?api=1&destination=Uferweg%20Nord%2C%20Seeufer&travelmode=walking"
    assert links.scotty_query("Seeufer Süd", "Altmoor Bahnhof", "2030-07-15", "17:40") == \
        "https://fahrplan.oebb.at/bin/query.exe/dn?S=Seeufer+S%C3%BCd&Z=Altmoor+Bahnhof&date=15.07.2030&time=17:40&timesel=depart&start=1"
    assert links.apple_directions("Café Uferblick", mode="bus") == \
        "https://maps.apple.com/directions?destination=Caf%C3%A9%20Uferblick&mode=transit"
    assert links.google_search("Gasthaus Lindengrund") == "https://www.google.com/maps/search/?api=1&query=Gasthaus%20Lindengrund"
    assert "timesel=arrive" in links.scotty_query("A", "B", "2030-07-15", "09:00", arrive=True)


@pytest.mark.parametrize("call", [
    lambda: links.scotty_query("A", "B", "2030-07-15", "9:00"),
    lambda: links.google_directions(" "),
    lambda: links.google_directions("x" * 2100),
])
def test_bad_link_input_is_refused(call):
    with pytest.raises(ValueError):
        call()


def test_every_region_row_says_verified_or_inferred_and_every_tool_says_what_to_type():
    data = table()
    assert {row["status"] for row in data["regions"]} <= {"verified", "inferred"}
    assert all(row["source"] for row in data["regions"])
    assert [row["id"] for row in data["regions"] if row["status"] == "verified"] == ["at-salzkammergut"]
    assert all(tool["usage"]["zh"] and tool["usage"]["en"] and tool["usage_checked"] is False for tool in data["tools"].values())


def test_what_the_user_said_beats_a_screenshot_and_both_beat_the_table():
    said = {"kind": "user_said", "region": "at-vienna", "timetable": "oebb-scotty", "why": "这里 Google 查不到车", "source_event": "E-1"}
    shot = {"kind": "screenshot", "region": None, "timetable": "wienmobil", "source_event": "E-2"}
    assert choose("at-vienna").timetable.id == "google-maps"
    assert choose("at-vienna", reports=[shot]).timetable.id == "wienmobil"
    chosen = choose("at-vienna", reports=[shot, said])
    assert (chosen.timetable.id, chosen.status, chosen.source) == ("oebb-scotty", "user_said", "E-1")
    assert choose("ch", reports=[said]).timetable.id == "sbb-mobile"  # a report about another region does not apply
    assert choose("nowhere").timetable.id == "google-maps"


def test_tool_hint_names_the_app_why_and_what_to_type_only_for_a_less_common_app():
    hint = tool_hint("at-salzkammergut")
    assert hint == {"tool": "oebb-scotty", "line": "ÖBB App（这一带 Google 地图查不到巴士）：起点栏填起点站，终点栏填终点站。改好出发时间再搜索。"}
    assert tool_hint("at-vienna") is None


def test_a_timetable_leg_names_the_app_and_both_stops_once():
    leg = transit_leg("坐 471 路", "Altmoor Bahnhof", "Seeufer Nord", mode="bus", region="at-salzkammergut", day="2030-07-15",
                      clock="13:24")
    assert leg["search"] == {"app": "ÖBB App", "query": "Altmoor Bahnhof → Seeufer Nord"}
    assert leg_text(leg).startswith("坐 471 路 · ÖBB App 搜 Altmoor Bahnhof → Seeufer Nord · [ÖBB / Scotty](")
    assert leg_text(leg).count("Altmoor Bahnhof → Seeufer Nord") == 1


def test_a_map_leg_shows_the_route_and_the_destination_to_search():
    leg = transit_leg("走 12 分钟", "Bootssteg Grauwald", "Gasthaus Lindengrund", mode="walk", region="at-salzkammergut")
    assert leg_text(leg) == ("走 12 分钟 · Bootssteg Grauwald → Gasthaus Lindengrund · Google 地图搜 Gasthaus Lindengrund · "
                             "[Google 地图](https://www.google.com/maps/dir/?api=1&destination=Gasthaus%20Lindengrund&travelmode=walking)")


def test_a_region_without_a_link_format_still_names_the_app_and_the_search():
    leg = transit_leg("Take the train", "Zürich HB", "Luzern", mode="train", region="ch", language="en")
    assert leg_text(leg, "en").startswith("Take the train · SBB Mobile: search Zürich HB → Luzern · [Google Maps](")


def test_place_lines_keep_the_name_alone_and_write_the_search_out():
    place = place_line("uferweg-nord", "Uferweg Nord", "北湖岸步道口 · 湖边路起点", search="Uferweg Nord, Seeufer", mode="walk")
    assert place_lines(place)[0] == "Uferweg Nord"
    assert "Google 地图搜 Uferweg Nord, Seeufer · [Google 地图](" in place_lines(place)[1]
    stop = timetable_place("seeufer-nord", "Seeufer Nord", "北湖岸车站 · 第一个湖", origin="Altmoor Bahnhof",
                           region="at-salzkammergut", day="2030-07-15", clock="13:24")
    assert stop["search"]["query"] == "Altmoor Bahnhof → Seeufer Nord" and "time=13:24" in stop["link"]["url"]
