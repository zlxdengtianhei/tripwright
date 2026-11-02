"""The contract sample as store records at a given moment, for the pack tests (a uniquely named helper module)."""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests/fixtures/contract_sample"
if str(ROOT / "tests/contracts") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests/contracts"))

import contract_checks  # noqa: E402

from tripwright.ledger import Decision, Fact, Item, PendingCheck  # noqa: E402
from tripwright.trip import Trip  # noqa: E402

OWNER = "sample"
FOLDER = "trips/2030-07-seenland"
S1_AT = "2030-07-15T13:00:13+02:00"
WIDE_AT = "2030-07-15T13:10:01+02:00"


def load(name: str):
    return json.loads((SAMPLE / name).read_text(encoding="utf-8"))


def snapshot(name: str) -> dict:
    return load("snapshots.json")["snapshots"][name]


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def store_record(record_id: str, record: dict, nature: str) -> tuple[str, dict]:
    """A sample ledger record in the shape the store keeps it."""
    fields = {key: value for key, value in record.items() if key not in ("type", "basis", "source_event")}
    fields["id"] = record_id
    fields["source"] = dict(event=record["source_event"], nature=nature, basis=record["basis"])
    if record["type"] == "item":
        return "items", Item.model_validate(fields).model_dump(mode="json")
    if record["type"] == "fact":
        return "facts", Fact.model_validate(dict(fields, invalidated_by="a later statement about the same key")).model_dump(mode="json")
    if record["type"] == "decision":
        return "decisions", Decision.model_validate(dict(fields, options=[record["choice"]])).model_dump(mode="json")
    return "pending_checks", PendingCheck.model_validate(fields).model_dump(mode="json")


def plan(document: dict) -> dict:
    return Trip.model_validate(document).model_dump(mode="json")


def load_store(store, moment: str, *, owner: str = OWNER) -> None:
    """Messages received and records journaled by `moment`, the version in the index then, and draft DR-300715-b8d."""
    ledger = load("ledger.json")
    trip, candidate = load("trip.json"), load("candidate.json")
    when = at(moment)
    natures = {}
    with store.transaction():
        for event in ledger["events"]:
            natures[event["id"]] = event["nature"]
            if at(event["received_at"]) > when:
                continue
            received = dict(text=event["text"], nature=event["nature"], channel=event["channel"], native_ref=event["native_ref"],
                            speaker=event["speaker"], suspicious_words=[])
            receipt = dict(event_id=event["id"], received_text=event["text"], nature=event["nature"],
                           verbatim=event["nature"] == "user_verbatim", received_at=event["received_at"], native_ref=event["native_ref"])
            store.put("events", owner, dict(id=event["id"], request_id=event["request_id"], received=received, receipt=receipt), insert=True)
        for entry in ledger["journal"]:
            if at(entry["at"]) > when:
                continue
            table, record = store_record(entry["record_id"], ledger["records"][entry["record_id"]], natures[entry["source_event"]])
            store.put(table, owner, record, insert=True)
            store.put("journal", owner, dict(id=entry["id"], event=None, source_event=entry["source_event"], action=entry["action"],
                                             record_id=entry["record_id"], details={}, at=entry["at"]), insert=True)
        documents = {"trip.json": trip, "candidate.json": contract_checks.candidate_trip(trip, candidate)}
        for version in ledger["versions"]:
            # The sample gives no plan for V-300715-x4w (an outside edit); only the versions with a plan file load.
            if version["plan"] in documents and at(version["in_index_from"]) <= when and (
                    version["in_index_until"] is None or when < at(version["in_index_until"])):
                store.put("versions", owner, dict(id=version["id"], number=version["number"], plan=plan(documents[version["plan"]]),
                                                  committed_at=version["in_index_from"]), insert=True)
        store.put("drafts", owner, dict(id=candidate["draft_id"], base_version=3, plan=plan(contract_checks.candidate_trip(trip, candidate)),
                                        status="proposed"), insert=True)


def text_quotes(pack: dict) -> dict:
    """The pack with v1 `full_quotes` (text only), the form the schema and `pack_problems` still read."""
    if not pack.get("full_quotes"):
        return pack
    return dict(copy.deepcopy(pack), full_quotes={event_id: quote["text"] for event_id, quote in pack["full_quotes"].items()})
