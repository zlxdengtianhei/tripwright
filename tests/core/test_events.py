import sqlite3

import pytest


def test_paraphrase_is_not_verbatim(service):
    receipt = service.events.receive("alice", "The user wants an easy morning", "message")
    assert receipt["nature"] == "host_restatement"
    assert receipt["verbatim"] is False
    with pytest.raises(ValueError, match="native"):
        service.events.receive("alice", "Easy morning", "other", nature="user_verbatim")


def test_duplicate_request_does_not_append_and_reuse_is_refused(service):
    first = service.events.receive("alice", "hello", "message")
    assert service.events.receive("alice", "hello", "message") == first
    assert len(service.store.all("events", "alice")) == 1
    with pytest.raises(ValueError, match="different"):
        service.events.receive("alice", "changed", "message")
    service.events.receive("bob", "another user's message", "message")
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
