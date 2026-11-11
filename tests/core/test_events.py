import sqlite3

import pytest


def test_paraphrase_is_not_verbatim(service):
    receipt = service.events.receive("alice", "The user wants an easy morning", "message")
    assert receipt["nature"] == "host_restatement"
    assert receipt["verbatim"] is False
    downgraded = service.events.receive("alice", "Easy morning", "other", nature="user_verbatim")
    assert downgraded["nature"] == "host_restatement" and downgraded["note"]


def test_duplicate_request_does_not_append_and_reuse_with_new_text_appends(service):
    first = service.events.receive("alice", "hello", "message")
    assert service.events.receive("alice", "hello", "message") == first
    assert len(service.store.all("events", "alice")) == 1
    changed = service.events.receive("alice", "changed", "message")
    assert changed["event_id"] != first["event_id"] and changed["request_id"] != first["request_id"]
    service.events.receive("bob", "another user's message", "message")
    assert len(service.store.all("events", "alice")) == 2
    assert len(service.store.all("events", "bob")) == 1


def test_events_are_append_only_even_through_sql(service):
    service.events.receive("alice", "hello", "message")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        service.store.connection.execute("DELETE FROM events")


def test_source_nature_cannot_be_upgraded(service, item):
    received = service.events.receive("alice", item["quote"], "paraphrase")
    item["source"] = {"event": received["event_id"], "nature": "user_verbatim"}
    with pytest.raises(ValueError, match="nature"):
        service.ledger.apply("alice", {"items": [item]})
    assert service.store.all("items", "alice") == []
