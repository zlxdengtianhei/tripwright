import pytest
from render_samples import load


@pytest.fixture(scope="session")
def ledger():
    return load("ledger.json")


@pytest.fixture(scope="session")
def sources(ledger):
    return {event["id"]: event for event in ledger["events"]}


@pytest.fixture
def state(ledger):
    """The sample trip as the committer would pass it at snapshot S2 (all records journaled by then)."""
    return {"path": "trips/2030-07-seenland", "trip": load("trip.json"), "records": ledger["records"],
            "snapshot": load("snapshots.json")["snapshots"]["S2"], "events": {event["id"]: event for event in ledger["events"]}}
