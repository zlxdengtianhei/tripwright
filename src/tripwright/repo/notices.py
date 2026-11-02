"""Notices to whoever already saw a result that replay or a hand edit later overturned (CONTRACTS 2.3).

A conflict notice is written in the transaction that decides the conflict and comes back with every turn until a plan
commit based on the new head is effective; there is no "read" call because the service cannot know whether the user
read it. Hand-edit notices say what the committer did with an edit made outside the tool and stay until it is resolved.
"""
from __future__ import annotations

from tripwright.storage import Store, identifier, now

USER_LINE = {
    "plan_base_changed": "上次的安排已在别处改动，需要重排。",
    "replay_refused": "上次的安排没存进去：别处记了新的情况，需要重排。",
    "record_status_race": "同一条记录在两处被改成了不同结果，要你定用哪个。",
}
UNTIL = "a plan commit based on the new head reaches effective"


def conflict(store: Store, owner: str, op: dict, reason: dict, canonical: str) -> dict:
    draft = (op.get("replay") or {}).get("draft") or {}
    shown = dict(draft_id=draft.get("id") if op["kind"] == "commit" else None, job_id=draft.get("job_id"), retrieved_at=None)
    if draft.get("job_id"):
        retrieved = [message.get("retrieved_at") for message in store.all("inbox", owner) if message["job_id"] == draft["job_id"]]
        shown["retrieved_at"] = next((stamp for stamp in reversed(retrieved) if stamp), None)
    replan = None
    if draft.get("plan") and not draft.get("ledger_only"):
        dates = sorted({day["date"] for day in draft["plan"].get("days", [])})
        people = sorted({person for day in draft["plan"].get("days", []) for plan in day.get("plans", [])
                         for track in plan.get("tracks", []) for person in track.get("subject", [])})
        replan = dict(dates=dates, people=people)
    notice = dict(id=identifier("N"), notice_id=None, kind="write_conflict", op_id=op["id"], created_at=now(), shown=shown,
                  reason=reason["reason"], replan=replan, user_line=reason.get("user_line") or USER_LINE[reason["reason"]], until=UNTIL,
                  detail=reason["detail"], remote_head=canonical[:7], status="open")
    notice["notice_id"] = notice["id"]
    store.put("notices", owner, notice, insert=True)
    return notice


def manual(store: Store, owner: str, *, key: str, user_line: str, detail: str, paths: list[str], once: bool = False) -> dict:
    """A hand-edit notice, at most one open per key; `once` notices close after the next commit shows them."""
    for notice in store.all("notices", owner):
        if notice.get("key") == key and notice["status"] == "open":
            return notice
    notice = dict(id=identifier("N"), notice_id=None, kind="manual_edit", key=key, created_at=now(), user_line=user_line,
                  detail=detail, paths=paths, once=once, status="open")
    notice["notice_id"] = notice["id"]
    store.put("notices", owner, notice, insert=True)
    return notice


def close(store: Store, owner: str, *, key: str | None = None, kind: str | None = None):
    for notice in store.all("notices", owner):
        if notice["status"] == "open" and (key is None or notice.get("key") == key) and (kind is None or notice["kind"] == kind):
            notice.update(status="closed", closed_at=now())
            store.put("notices", owner, notice)


def open_notices(store: Store, owner: str) -> list[dict]:
    """Open notices in the contract shape (`$defs/notice` for conflicts), oldest first."""
    shown = []
    for notice in store.all("notices", owner):
        if notice["status"] != "open":
            continue
        if notice["kind"] == "write_conflict":
            shown.append({key: notice[key] for key in ("notice_id", "kind", "op_id", "created_at", "shown", "reason", "replan",
                                                        "user_line", "until")})
        else:
            shown.append({key: notice[key] for key in ("notice_id", "kind", "created_at", "user_line", "detail", "paths")})
    return shown


def settle(store: Store, owner: str, op: dict):
    """An effective plan commit based on the new head closes the conflict notices; `once` hand-edit notices close after
    the commit that showed them."""
    if op["state"] != "effective":
        return
    draft = (op.get("replay") or {}).get("draft") or {}
    plan_commit = op["kind"] in ("commit", "manual") and not draft.get("ledger_only")
    for notice in store.all("notices", owner):
        if notice["status"] != "open":
            continue
        if (notice["kind"] == "write_conflict" and plan_commit) or (notice["kind"] == "manual_edit" and notice.get("once")):
            notice.update(status="closed", closed_at=now(), closed_by=op["id"])
            store.put("notices", owner, notice)
