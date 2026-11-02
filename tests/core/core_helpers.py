"""Helpers for core tests after implementation A: turn returns a rendered package instead of the full bundle, and
propose returns per-item usages instead of the old answer skeleton (CONTRACTS 3.1: the full data is not sent twice)."""


def usage_of(draft: dict, item_id: str) -> dict:
    return next(usage for usage in draft["usages"] if usage["item_id"] == item_id)


def package(service, turn: dict, owner: str = "alice") -> dict:
    """The bundle the turn's package was compiled from (stored with its context)."""
    return service.store.get("contexts", owner, turn["ctx_id"])["bundle"]


def decide(service, decision: dict, key: str, *, text: str = "We decided this.", speaker: str = "p1", plan: dict | None = None) -> dict:
    """The user's message deciding it, and the proposal recording the decision from that message: a user decision is
    admitted only from the message its proposal answers, written by a person of the trip (D-49)."""
    turn = service.turn("alice", text, f"turn-{key}", nature="user_verbatim", native_ref=f"fixture:{key}", speaker=speaker)
    decision = dict(decision, source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                           changes={"decisions": [decision]}, idempotency_key=key)
