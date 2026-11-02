from copy import deepcopy

import pytest


def test_same_object_replaces_and_other_object_coexists(service, item):
    service.ledger.apply("alice", {"items": [item]})
    new_source = service.events.receive("alice", "Prefer parks.", "parks")
    replacement = dict(item, quote="Prefer parks.", text="Prefer parks.", source={"event": new_source["event_id"]})
    other = dict(item, key="art", quote="Prefer art.", text="Prefer art.")
    service.ledger.apply("alice", {"items": [replacement, other]})
    active = [record for record in service.ledger.current("alice")["items"] if record["status"] == "active"]
    assert len(active) == 2
    assert {record["key"] for record in active} == {"pace", "art"}
    assert service.store.all("items", "alice")[0]["status"] == "superseded"
    assert len(service.store.all("journal", "alice")) == 3


def test_narrower_exception_only_applies_in_its_scope(service, item):
    service.ledger.apply("alice", {"items": [item]})
    receipt = service.events.receive("alice", "Prefer parks.", "exception")
    exception = dict(item, quote="Prefer parks.", text="Prefer parks.",
                     scope={"start": "2030-06-11", "end": "2030-06-11", "channel": "conversation"},
                     source={"event": receipt["event_id"]})
    service.ledger.apply("alice", {"items": [exception]})
    inside = service.ledger.compile("alice", subject=["p1"], scope={"start": "2030-06-11", "end": "2030-06-11", "channel": "conversation"})
    outside = service.ledger.compile("alice", subject=["p1"], scope={"start": "2030-06-10", "end": "2030-06-10", "channel": "conversation"})
    assert [record["text"] for record in inside["items"]] == ["Prefer parks."]
    assert [record["text"] for record in outside["items"]] == ["Prefer a relaxed pace."]
    assert len(service.ledger.compile("alice", subject=["p1"])["items"]) == 2


def test_same_message_contradiction_is_pending(service, item):
    opposite = dict(item, quote="Prefer art.", text="Prefer art.")
    service.ledger.apply("alice", {"items": [item, opposite]})
    assert all(record["status"] == "pending" for record in service.store.all("items", "alice"))
    assert service.store.all("undecided", "alice")
    assert service.ledger.compile("alice")["items"] == []


def test_contradictory_new_message_does_not_supersede_previous_preference(service, item):
    service.ledger.apply("alice", {"items": [item]})
    receipt = service.events.receive("alice", "Prefer parks. Prefer art.", "contradiction")
    first = dict(item, quote="Prefer parks.", text="Prefer parks.", source={"event": receipt["event_id"]})
    second = dict(first, quote="Prefer art.", text="Prefer art.")
    service.ledger.apply("alice", {"items": [first, second]})
    active = service.ledger.compile("alice")["items"]
    assert len(active) == 1 and active[0]["text"] == item["text"]


def test_hard_conflict_does_not_silently_replace(service, item):
    service.ledger.apply("alice", {"items": [dict(item, strength="must")]})
    received = service.events.receive("alice", "Prefer parks.", "new-hard")
    new = dict(item, quote="Prefer parks.", text="Prefer parks.", source={"event": received["event_id"]})
    service.ledger.apply("alice", {"items": [new]})
    assert service.store.all("items", "alice")[0]["status"] == "active"
    assert service.store.all("items", "alice")[1]["status"] == "pending"


def test_applicability_people_window_channel_and_small_quick_package(service, item):
    records = [item, dict(item, key="food", subject=["p2"]),
               dict(item, key="web", scope={"channel": "website"}),
               dict(item, key="morning", scope={"start": "2030-06-10", "end": "2030-06-10", "time_from": "08:00", "time_to": "10:00"}),
               dict(item, key="limit", kind="constraint", strength="must")]
    service.ledger.apply("alice", {"items": records})
    scope = {"start": "2030-06-10", "end": "2030-06-10", "time_from": "12:00", "time_to": "13:00", "channel": "conversation"}
    normal = service.ledger.compile("alice", subject=["p1"], scope=scope)
    quick = service.ledger.compile("alice", subject=["p1"], scope=scope, scenario="quick")
    assert {record["key"] for record in normal["items"]} == {"pace", "limit"}
    # CONTRACTS 3.2 (model change, implementation A): quick narrows by people, time, place and channel like every
    # scenario, never by kind; the applicable preference stays and the out-of-scope ones stay out.
    assert {record["key"] for record in quick["items"]} == {"pace", "limit"}
    assert len(normal["conversation_rules"]) == 7
    assert service.ledger.compile("alice", scope={"channel": "website"})["conversation_rules"] == []
    excluded = service.ledger.compile("alice", subject=["p1"], scope=scope, exclude_keys=["pace", "limit"])
    assert [record["key"] for record in excluded["items"]] == ["limit"]


def test_numeric_invention_and_query_items_are_rejected(service, item):
    with pytest.raises(ValueError, match="numeric limit"):
        service.ledger.apply("alice", {"items": [dict(item, check={"metric": "walk_minutes", "op": "max", "value": 35})]})
    with pytest.raises(ValueError):
        service.ledger.apply("alice", {"items": [dict(item, kind="query")]})
    assert service.store.all("items", "alice") == []


def test_expiry_scan_and_overdue_pending_are_not_auto_passed(service, item, source):
    service.ledger.apply("alice", {"items": [dict(item, valid_until="2020-01-01T00:00:00+00:00")],
        "pending_checks": [dict(question="Exact branch?", subject=["p1"], source=source, due_at="2020-01-01T00:00:00+00:00",
                                responsible="host", consequence="Wrong branch", alternative="Confirm locally")]})
    scan = service.ledger.scan_due("alice")
    assert len(scan["expired"]) == 1
    assert len(scan["overdue"]) == 1
    assert scan["overdue"][0]["status"] == "open"
    assert len(service.ledger.compile("alice")["overdue"]) == 1
    pending = scan["overdue"][0]
    with pytest.raises(ValueError, match="requires evidence"):
        service.ledger.apply("alice", {"resolve_pending": [dict(id=pending["id"], status="verified", result="I think so")]})
    service.ledger.apply("alice", {"resolve_pending": [dict(id=pending["id"], status="unverified", result="Branch cannot be confirmed; use fallback.")]})
    assert service.ledger.compile("alice")["pending_checks"] == []


def test_retracting_wrong_interpretation_retracts_derived_items(service, item):
    root = dict(item, source=dict(item["source"], basis="agent_inference"), id="I-root")
    derived = dict(item, key="derived", id="I-derived", relations={"derived_from": ["I-root"]})
    service.ledger.apply("alice", {"items": [root, derived]})
    service.ledger.apply("alice", {"retract_interpretation": ["I-root"]})
    assert service.ledger.compile("alice")["items"] == []
