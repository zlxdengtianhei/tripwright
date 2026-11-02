"""The effective snapshot one read is bound to, and the single freshness rule (CONTRACTS 1.1, 1.2, 1.4).

A snapshot is a pure function of the index: the committed version, the journal, the received messages and, in
repository mode, the head the index was built from and the operations not yet in the canonical branch. Every caller
(turn, propose, deep jobs, result, commit) takes it the same way, so no reader keeps a parallel notion of "current".
"""
from __future__ import annotations

from tripwright.ledger import Ledger
from tripwright.storage import Store, now

UNSYNCED = ("received", "pending_sync")


def received_events(store: Store, owner: str) -> list[dict]:
    """Messages in the order they were received (change records share the events table and are skipped)."""
    return [event for event in store.all("events", owner) if "received" in event]


def repo_head(store: Store, owner: str) -> dict | None:
    """The canonical branch and head the index was built from; None in single-database mode."""
    try:
        return store.get("repo", owner, "head")
    except ValueError:
        return None


def unsynced_ops(store: Store, owner: str) -> list[dict]:
    return [op for op in store.all("outbox", owner) if op["state"] in UNSYNCED]


def take(store: Store, owner: str, *, at: str | None = None) -> dict:
    with store.transaction():
        versions = store.all("versions", owner)
        current = max(versions, key=lambda version: version["number"], default=None)
        ledger = Ledger(store)
        events = received_events(store, owner)
        head = repo_head(store, owner)
        repo = None
        if head is not None:
            repo = dict(canonical=head["canonical"], head=head["head"], head_checked_at=head.get("head_checked_at"),
                        unsynced_ops=[op["id"] for op in unsynced_ops(store, owner)])
        return dict(taken_at=at or now(), plan_version=current["number"] if current else 0,
                    plan_version_id=current["id"] if current else None,
                    ledger_revision=ledger.revision(owner), ledger_digest=ledger.digest(owner),
                    raw_watermark=dict(count=len(events), last_event=events[-1]["id"] if events else None,
                                       last_received_at=events[-1]["receipt"]["received_at"] if events else None),
                    repo=repo)


def content_key(snapshot: dict) -> tuple:
    return snapshot["plan_version_id"], snapshot["ledger_digest"]


def changed(bound: dict, current: dict) -> list[str]:
    return [name for name, key in (("plan", "plan_version_id"), ("ledger", "ledger_digest")) if bound[key] != current[key]]


def unsettled(store: Store, owner: str, bound: dict, current: dict, acked=()) -> list[str]:
    """Messages received after the bound watermark that no journal entry cites and the host did not acknowledge."""
    events = received_events(store, owner)[bound["raw_watermark"]["count"]:current["raw_watermark"]["count"]]
    claimed = {entry["source_event"] for entry in store.all("journal", owner)}
    return [event["id"] for event in events if event["id"] not in claimed and event["id"] not in set(acked)]


def freshness(store: Store, owner: str, bound: dict, current: dict, acked=()) -> tuple[str, list[str], list[str]]:
    """CONTRACTS 1.2: a changed content key is stale; otherwise newer unclaimed, unacknowledged messages are unsettled.
    The repository part never makes a result stale: a newer head that changed plan or ledger changed the content key."""
    moved = changed(bound, current)
    if moved:
        return "stale", moved, []
    open_events = unsettled(store, owner, bound, current, acked)
    return ("unsettled", [], open_events) if open_events else ("fresh", [], [])
