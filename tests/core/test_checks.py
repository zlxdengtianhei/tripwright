from copy import deepcopy

import pytest

from tripwright.checks import check_plan, departure_clock, fare_totals
from tripwright.demo import example
from tripwright.storage import now
from tripwright.trip import Trip


def test_adjacent_items_need_enough_time(service, plan):
    blocks = plan["days"][0]["plans"][0]["tracks"][0]["blocks"]
    blocks[1].update(start="11:05", transfer_minutes=15)
    report = check_plan(service.store, "alice", plan)
    assert len(report["conflicts"]) == 3
    blocks[1]["start"] = "11:15"
    assert check_plan(service.store, "alice", plan)["conflicts"] == []
    blocks[1]["transfer_minutes"] = None
    assert len([check for check in check_plan(service.store, "alice", plan)["unverified"] if check["id"].startswith("chain:")]) == 3


def test_overlapping_person_tracks_are_caught(service, plan):
    tracks = plan["days"][0]["plans"][0]["tracks"]
    tracks.append(dict(subject=["p1"], blocks=[dict(id="overlap", start="10:30", end="11:30", kind="visit", place="oslo-park")]))
    assert check_plan(service.store, "alice", plan)["conflicts"]


def test_hard_node_margin_and_missing_evidence(service):
    plan = deepcopy(example()["trip"])
    report = check_plan(service.store, "alice", plan)
    margins = [check for check in report["checks"] if check["id"].startswith("anchor:")]
    assert len(margins) == 3 and all(check["status"] == "passed" for check in margins)
    assert all(check["details"]["margin_minutes"] == 20 for check in margins)
    assert all(check["details"]["latest_departure"] == "15:50" for check in margins)
    assert any(check["id"].startswith("anchor-evidence:") for check in report["unverified"])
    plan["days"][1]["plans"][0]["tracks"][0]["blocks"][1]["end"] = "16:20"
    assert any(check["id"].startswith("anchor:") for check in check_plan(service.store, "alice", plan)["conflicts"])


def test_scoped_solo_fact_is_enforced_per_person(service, source, plan):
    service.ledger.apply("alice", {"facts": [dict(key="companions", value=[], text="Alone in the morning", subject=["p1"],
        scope={"start": "2030-06-10", "end": "2030-06-10", "time_from": "08:00", "time_to": "12:00"},
        true_from=now(), invalidated_by="Noon", source=source)]})
    conflicts = check_plan(service.store, "alice", plan)["conflicts"]
    assert len(conflicts) == 1 and conflicts[0]["id"].startswith("person:")
    plan["days"][0]["plans"][0]["tracks"][0]["subject"] = ["p2", "p3"]
    assert check_plan(service.store, "alice", plan)["conflicts"] == []


def test_numeric_item_predicate_really_checks_limit(service, item, plan):
    maximum = dict(item, key="visits", kind="constraint", strength="must", quote="Maximum 2 visits.", text="Maximum 2 visits.",
                   check={"metric": "visit_count", "op": "max", "value": 2})
    service.ledger.apply("alice", {"items": [maximum]})
    assert not check_plan(service.store, "alice", plan)["conflicts"]
    blocks = plan["days"][0]["plans"][0]["tracks"][0]["blocks"]
    blocks[1]["kind"] = "visit"
    blocks[2]["kind"] = "visit"
    report = check_plan(service.store, "alice", plan)
    assert any(check["id"].startswith("item:") for check in report["conflicts"])


def test_budget_totals_do_not_double_count_included_fares_or_convert_currencies(service):
    plan = deepcopy(example()["trip"])
    booking = plan["bookings"][0]
    booking["fares"].extend([
        dict(id="f-included", segment="included", ticket="included", subject=["p1"], amount_per_person=1000, currency="NOK", included_in=booking["id"]),
        dict(id="f-alt", segment="alternative", ticket="alternative", subject=["p1"], amount_per_person=1000, currency="NOK", alternative=True),
        dict(id="f-euro", segment="other", ticket="other", subject=["p1"], amount_per_person=5.1, currency="EUR"),
        dict(id="f-euro2", segment="other2", ticket="other2", subject=["p1"], amount_per_person=0.2, currency="EUR"),
    ])
    totals = fare_totals(Trip.model_validate(plan))
    assert totals["by_person"]["p1"] == {"NOK": 400, "EUR": 5.3}
    assert totals["by_person"]["p2"] == {"NOK": 400}
    plan["budgets"]["NOK"] = 1100
    assert any(check["id"] == "budget:NOK" for check in check_plan(service.store, "alice", plan)["conflicts"])
    booking["fares"][0]["amount_per_person"] = None
    assert "f-example-rail" in fare_totals(Trip.model_validate(plan))["unknown"]


def test_booking_and_fare_cannot_be_assigned_to_the_wrong_person(service):
    plan = deepcopy(example()["trip"])
    plan["bookings"][0]["subject"] = ["p2", "p3"]
    plan["bookings"][0]["fares"][0]["subject"] = ["p2", "p3"]
    block = plan["days"][1]["plans"][0]["tracks"][0]["blocks"][1]
    block.update(booking="bk-example-rail", fare="f-example-rail")
    plan["days"][1]["anchors"][0]["booking"] = "bk-example-rail"
    report = check_plan(service.store, "alice", plan)
    assert any(check["id"].startswith("fare-holder:") for check in report["conflicts"])
    assert any(check["id"].startswith("booking-holder:") for check in report["conflicts"])
    assert any(check["id"].startswith("anchor-booking:") for check in report["conflicts"])


def test_absent_plan_does_not_pass_a_hard_condition(service, item, plan):
    service.ledger.apply("alice", {"items": [dict(item, kind="constraint", strength="must")]})
    plan["days"][0]["plans"] = []
    report = check_plan(service.store, "alice", plan)
    assert report["verified"] is False
    assert any("missing-plan" in check["id"] for check in report["unverified"])


def test_checker_discovers_opening_obligations_without_author_pending_list(service, plan):
    assert not plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0].get("critical_facts")
    report = check_plan(service.store, "alice", plan)
    assert any(check["id"].endswith(":opening_hours") for check in report["unverified"])
    assert not report["verified"]


def test_daylight_saving_uses_elapsed_time_and_does_not_verify_ambiguous_clocks(service, plan):
    plan.update(start="2030-03-31", end="2030-03-31")
    plan["days"][0]["date"] = "2030-03-31"
    blocks = plan["days"][0]["plans"][0]["tracks"][0]["blocks"]
    blocks[0].update(start="01:00", end="01:30")
    blocks[1].update(start="03:15", end="04:00", transfer_minutes=60)
    report = check_plan(service.store, "alice", plan)
    assert any(check["id"].startswith("chain:") for check in report["conflicts"])
    blocks[1].update(start="02:30")
    report = check_plan(service.store, "alice", plan)
    assert any(check["id"].startswith("local-time:") for check in report["unverified"])
    assert not report["verified"]
    assert departure_clock("03:15", 0, 60, day="2030-03-31", zone="Europe/Oslo") == "01:15"


@pytest.mark.parametrize("mutation", [
    lambda plan: plan.update(timezone="Not/AZone"),
    lambda plan: plan["days"][0].update(date="2000-01-01"),
    lambda plan: plan["days"][0]["plans"][0]["tracks"][0].update(subject=["unknown"]),
    lambda plan: plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0].update(place="unknown"),
    lambda plan: plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0].update(end=None),
    lambda plan: plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0].update(start="25:00"),
])
def test_trip_format_refuses_invalid_references_and_times(plan, mutation):
    mutation(plan)
    with pytest.raises((ValueError, KeyError)):
        Trip.model_validate(plan)
