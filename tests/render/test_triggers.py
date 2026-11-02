"""Timetable lookup triggers on the four fictional chains of red-team E4, plus the contrasts with a fixed 3-hour window."""
from tripwright.render.triggers import evaluate, markers_from_text


def fixed_three_hours(now: str, node_time: str) -> bool:
    """The rule the design replaced: look up when the hard node is within 3 hours."""
    to = lambda clock: int(clock[:2]) * 60 + int(clock[3:])
    return to(node_time) - to(now) <= 180


def test_frequent_service_without_a_hard_node_gets_links_only():
    legs = [{"id": "tram", "depart": "10:05", "minutes": 12, "headway_minutes": 6},
            {"id": "metro", "depart": "14:00", "minutes": 8, "headway_minutes": 4}]
    result = evaluate("10:00", legs)
    assert result["queries"] == [] and result["fired"] == []
    assert result["link_only"] == [{"leg": "tram", "note": None}, {"leg": "metro", "note": None}] and result["whole_day"]


def test_known_sparse_service_is_looked_up_and_nothing_else():
    legs = [{"id": "bus471", "depart": "13:24", "minutes": 26, "headway_minutes": 60},
            {"id": "walk", "depart": "15:30", "minutes": 50, "headway_minutes": 0}]
    result = evaluate("13:00", legs)
    assert result["queries"] == [{"leg": "bus471", "kinds": ["timetable"], "rules": [5]}]
    assert [entry["leg"] for entry in result["link_only"]] == ["walk"]


def test_unknown_frequency_is_looked_up_only_for_the_next_step_and_the_day_is_not_promised():
    legs = [{"id": "next", "depart": "10:10", "minutes": 20, "headway_minutes": None},
            {"id": "later", "depart": "15:00", "minutes": 30, "headway_minutes": None}]
    result = evaluate("10:00", legs)
    assert result["queries"] == [{"leg": "next", "kinds": ["timetable"], "rules": [5]}]
    assert result["link_only"] == [{"leg": "later", "note": "班次没核"}]
    assert result["whole_day"] is False


def test_a_hard_node_six_hours_away_whose_chain_must_start_now_is_caught():
    legs = [{"id": "bus", "depart": "13:30", "minutes": 90, "headway_minutes": None, "transfer_minutes": 30},
            {"id": "train", "depart": "15:30", "minutes": 120, "headway_minutes": 30},
            {"id": "museum-tram", "depart": "12:10", "minutes": 10, "headway_minutes": 8}]
    flight = {"id": "flight", "time": "18:00", "buffer_minutes": 120, "chain": ["bus", "train"]}
    result = evaluate("12:00", legs, [flight])
    assert not fixed_three_hours("12:00", "18:00")  # the old window would stay silent
    assert result["latest"]["flight"] == {"latest_departure": "12:00", "slack_minutes": 0, "travel_minutes": 240}
    hard = next(entry for entry in result["fired"] if entry["rule"] == 1)
    assert hard["legs"] == ["bus", "train"] and "按现在的安排 13:30 才出发，赶不上" in hard["reason"]
    assert {entry["leg"] for entry in result["queries"]} == {"bus", "train"}  # only the chain, not the tram
    assert [entry["leg"] for entry in result["link_only"]] == ["museum-tram"]


def test_a_hard_node_with_ample_chain_slack_is_not_looked_up_although_a_three_hour_window_would():
    legs = [{"id": "walk", "depart": "14:20", "minutes": 15, "headway_minutes": 0}]
    dinner = {"id": "dinner", "time": "14:50", "buffer_minutes": 10, "chain": ["walk"]}
    result = evaluate("12:00", legs, [dinner])
    assert fixed_three_hours("12:00", "14:50")
    assert result["latest"]["dinner"]["slack_minutes"] == 145 and result["queries"] == []


def test_questions_tickets_and_screenshots_fire_their_own_triggers():
    legs = [{"id": "train", "depart": "18:05", "minutes": 70, "headway_minutes": 30}]
    markers = markers_from_text("WB 79219 那班能不能赶上？我的票能用吗")
    assert markers == {"asks_catch": True, "ticket": True, "named_service": True, "screenshot": False}
    result = evaluate("17:00", legs, [], dict(markers, leg="train"))
    assert sorted(entry["rule"] for entry in result["fired"]) == [2, 3, 4]
    assert result["queries"] == [{"leg": "train", "kinds": ["timetable", "ticket_rules", "compare_first"], "rules": [2, 3, 4]}]
    assert markers_from_text("今天下午去湖边走走") == {"asks_catch": False, "ticket": False, "named_service": False, "screenshot": False}
