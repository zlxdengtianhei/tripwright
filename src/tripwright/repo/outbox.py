"""Outbox: writes acknowledged to the host and not yet in the canonical branch (CONTRACTS 2).

An operation is recorded in the same database transaction as the receipt or commit it belongs to, so the host never
gets an acknowledgement for a write the outbox does not hold. It carries what replay needs to redo it on another head
(the operation, never a text diff). States only move forward: received → pending_sync → effective, received →
effective when the canonical branch is local, received or pending_sync → conflict; effective and conflict are final.
"""
from __future__ import annotations

from tripwright.storage import Store, encode, identifier, now

NEXT = {"received": {"pending_sync", "effective", "conflict"}, "pending_sync": {"pending_sync", "effective", "conflict"},
        "effective": set(), "conflict": set()}
UNSYNCED = ("received", "pending_sync")


class Outbox:
    def __init__(self, store: Store):
        self.store = store

    def find(self, owner: str, tool: str, key: str) -> dict | None:
        return next((op for op in self.store.where("outbox", owner, "request.key", [key])
                     if op["request"] == dict(tool=tool, key=key)), None)

    def add(self, owner: str, *, kind: str, tool: str, key: str, replay: dict, records: list[str] | None = None,
            state: str = "received", **links) -> dict:
        """Record one write operation. Call inside the transaction that makes the write; the same key with the same
        replay data returns the existing operation, with other data it is refused."""
        existing = self.find(owner, tool, key)
        if existing is not None:
            if encode(existing["replay"]) != encode(replay):
                raise ValueError(f"{tool} key {key!r} was reused with different input")
            return existing
        at = now()
        op = dict(id=identifier("OP"), kind=kind, request=dict(tool=tool, key=key), records=list(records or []),
                  replay=replay, state=state, transitions=[dict(state=state, at=at)], attempts=0, next_attempt=0.0,
                  error=None, created_at=at, **{name: value for name, value in links.items() if value is not None})
        self.store.put("outbox", owner, op, insert=True)
        return op

    def move(self, owner: str, op_id: str, state: str, **details) -> dict:
        op = self.store.get("outbox", owner, op_id)
        if state not in NEXT[op["state"]]:
            raise ValueError(f"{op_id}: {op['state']} cannot become {state}")
        op["state"] = state
        op["transitions"].append(dict(state=state, at=now(), **{key: value for key, value in details.items() if value is not None}))
        self.store.put("outbox", owner, op)
        return op

    def update(self, owner: str, op: dict):
        self.store.put("outbox", owner, op)

    def open(self, owner: str, states=UNSYNCED) -> list[dict]:
        return self.store.where("outbox", owner, "state", states)

    def ref(self, owner: str, op: dict | None) -> dict:
        """What turn and commit return: the operation, its state and how many writes are not in the canonical branch."""
        unsynced = len(self.open(owner))
        if op is None:
            return dict(op_id=None, state="effective", unsynced=unsynced)
        current = self.store.get("outbox", owner, op["id"])
        ref = dict(op_id=current["id"], state=current["state"], unsynced=unsynced)
        blocked = self.blocked(owner) if current["state"] in UNSYNCED else None
        return ref | ({"blocked": blocked} if blocked else {})

    def blocked(self, owner: str) -> dict | None:
        """Why writes are stuck on this server ({kind, since, hint}), kept by the committer until a push or commit works."""
        try:
            record = self.store.get("repo", owner, "blocked")
        except ValueError:
            return None
        return {key: record[key] for key in ("kind", "since", "hint")} if record.get("kind") else None

    def oldest(self, owner: str) -> str | None:
        """When the oldest write not yet in the canonical branch was received: the window a lost database would lose
        is from there, not a fixed few seconds."""
        pending = self.open(owner)
        return min((op["created_at"] for op in pending), default=None)
