"""Redo one outbox operation on another head's generation (design 5.3 step 4): by operation, never by text merge.

A new message never conflicts. Recorded ledger changes run through the ledger again, so supersession is recomputed
on the new head. A plan commit whose base version changed is redone on the new plan (D-75, repo.rebase): what it changed
against its own base is applied object by object; only a field that both sides changed differently is a conflict
(`plan_base_changed`, with a user line naming the item and both values). A plan commit whose ledger moved under it is
checked again on the new ledger. Either way the commit's full checks (hard constraints, must locks, the user's
decisions) run on the new head and refuse as always (`replay_refused`); a ledger-only commit is applied on top. When both
writers closed the same pending check with different results, the later one becomes an open question
(`record_status_race`). Returns None when the operation applied, else `{reason, detail[, question_id]}`.
"""
from __future__ import annotations

from copy import deepcopy

from tripwright.jobs import Jobs
from tripwright.ledger import Ledger
from tripwright.repo import rebase
from tripwright.repo.snapshot import take
from tripwright.storage import identifier, now
from tripwright.locks import identify
from tripwright.versions import Versions, ledger_only


def replay(service, scratch, owner: str, op: dict) -> dict | None:
    data = op.get("replay") or {}
    if "recovered" in data:
        return dict(reason="replay_refused", detail=f"Rebuilt from commit {data['recovered'][:7]} after the database was lost; "
                                                    "its operation can only be redone by asking again on the new head")
    if op["kind"] == "turn":
        return _turn(scratch, owner, data)
    if op["kind"] == "job":
        present = {record["id"] for record in scratch.all("evidence", owner)}
        with scratch.transaction():
            for record in data.get("evidence", []):
                if record["id"] not in present:
                    scratch.put("evidence", owner, record, insert=True)
        return None
    if op["kind"] == "commit":
        return _commit(scratch, owner, op, data)
    if op["kind"] == "manual":
        return _manual(scratch, owner, data)
    return dict(reason="replay_refused", detail=f"Unknown operation kind {op['kind']}")


def _manual(scratch, owner: str, data: dict) -> dict | None:
    """A hand edit taken in here, on a head whose plan changed elsewhere: redone on the new plan like a plan commit (A15,
    D-75), so it is not lost; a field both changed differently, or a failed check, is a conflict that names it."""
    versions = Versions(scratch, Jobs(scratch))
    current, plan = versions.current(owner), data["plan"]
    if current["id"] != data["base_version_id"]:
        try:
            base = scratch.get("versions", owner, data["base_version_id"])["plan"]
        except ValueError:
            base = None
        if base is None or current.get("plan") is None:
            return dict(reason="plan_base_changed", detail=f"The hand-edited plan was based on {data['base_version_id']}; "
                                                           f"the canonical branch has {current['id']} and no earlier plan to redo it on")
        plan, conflicts = rebase.onto(base, plan, current["plan"])
        if conflicts:
            named = "; ".join("/".join(part if isinstance(part, str) else part[1] for part in conflict["path"]) for conflict in conflicts)
            return dict(reason="plan_base_changed", detail=f"The hand-edited plan and the canonical branch both changed {named} differently",
                        user_line="手改的计划和别处的改动冲突：" + rebase.user_line(conflicts, current["plan"]))
    result = versions.adopt(owner, plan, author=data["author"], base_version_id=current["id"], version_id=data["version_id"])
    if result["adopted"]:
        return None
    return dict(reason="replay_refused", detail="The hand-edited plan does not apply on the new head: " + "; ".join(result["conflicts"]))


def _turn(scratch, owner: str, data: dict) -> dict | None:
    event = data["event"]
    ledger = Ledger(scratch)
    with scratch.transaction():
        if not any(existing["id"] == event["id"] for existing in scratch.all("events", owner)):
            scratch.put("events", owner, event, insert=True)
        imports = data.get("imports") or {}
        for table in ("items", "carry"):
            present = {record["id"] for record in scratch.all(table, owner)}
            for record in imports.get(table, []):
                if record["id"] not in present:
                    scratch.put(table, owner, record, insert=True)
                    if table == "carry":
                        ledger._audit(owner, event["id"], "import_memory", record["id"], {"trip": data.get("new_trip")})
        ledger.ensure_profile(owner)
    if not data.get("changes"):
        return None
    race = _race(scratch, owner, data["changes"], event["id"])
    if race:
        return race
    try:
        with scratch.transaction():
            ledger.apply(owner, data["changes"])
    except ValueError as error:
        return dict(reason="replay_refused", detail=f"The message was kept; what it recorded no longer applies on the new head: {error}")
    return None


def _race(scratch, owner: str, changes: dict, source_event: str) -> dict | None:
    """Two writers closed the same pending check differently: the first one stands, the later becomes a question."""
    for resolution in changes.get("resolve_pending", []):
        try:
            pending = scratch.get("pending_checks", owner, resolution["id"])
        except ValueError:
            continue
        if pending["status"] != "open" and (pending["status"], pending.get("result")) != (resolution["status"], resolution.get("result")):
            question = dict(id=identifier("Q"), question=f"{pending['question']}：两处给了不同结果", subject=pending["subject"],
                            scope=pending["scope"], item_ids=[], source_event=source_event,
                            options=[f"{pending['status']}: {pending.get('result')}", f"{resolution['status']}: {resolution.get('result')}"],
                            recommendation="Ask which result holds", default="Keep the result recorded first", since=now(), status="open")
            with scratch.transaction():
                scratch.put("undecided", owner, question, insert=True)
            return dict(reason="record_status_race", detail=f"{pending['id']} was closed elsewhere as {pending['status']}",
                        question_id=question["id"])
    return None


def _commit(scratch, owner: str, op: dict, data: dict) -> dict | None:
    draft = deepcopy(data["draft"])
    versions = Versions(scratch, Jobs(scratch))
    current = versions.current(owner)
    snapshot = take(scratch, owner)
    redone = None
    if not ledger_only(draft) and current["id"] != draft.get("base_version_id"):
        clash = _redo(scratch, owner, draft, current)
        if clash:
            return clash
        redone = dict(on_version=current["number"], on_version_id=current["id"], from_version=draft["base_version"], note=rebase.REDONE)
    race = _race(scratch, owner, draft.get("changes") or {}, draft.get("source_event"))
    if race:
        return race
    # A ledger-only commit is redone on top of whatever the new head holds; a plan commit only on its unchanged base.
    draft.update(status="proposed", base_version=current["number"], base_version_id=current["id"],
                 revision=snapshot["ledger_revision"], snapshot=dict(snapshot, taken_at=(draft.get("snapshot") or snapshot)["taken_at"]))
    draft.pop("version_id", None)
    with scratch.transaction():
        scratch.put("drafts", owner, draft)
        if draft.get("ctx_id") and data.get("context"):
            scratch.put("contexts", owner, data["context"])
    if not any(event["id"] == draft.get("source_event") for event in scratch.all("events", owner)) and draft.get("source_event"):
        return dict(reason="replay_refused", detail=f"Its message {draft['source_event']} is not on the new head")
    try:
        with scratch.transaction():
            version = versions.commit(owner, draft["id"], op["request"]["key"], accept_unverified=data.get("accept_unverified", False),
                                      version_id=data.get("version_id"), replaying=True)
            if redone:
                # The receipt and the changelog say the change was redone on top of the changes made elsewhere.
                version = dict(version, redone=redone, changelog=[dict(entry, redone=rebase.REDONE) for entry in version.get("changelog", [])])
                scratch.put("versions", owner, version)
    except ValueError as error:
        return dict(reason="replay_refused", detail=("Redone on the plan changed elsewhere, then refused by its checks: " if redone else "")
                    + str(error))
    return None


def _redo(scratch, owner: str, draft: dict, current: dict) -> dict | None:
    """Put this commit's own change (its plan against its base) on the newer plan, in the draft; a clash is returned."""
    try:
        base = scratch.get("versions", owner, draft["base_version_id"])["plan"] if draft.get("base_version_id") else None
    except ValueError:
        base = None
    stale = dict(reason="plan_base_changed", detail=f"The plan was based on V{draft['base_version']} ({draft.get('base_version_id')}); "
                                                     f"the canonical branch has V{current['number']} ({current['id']})")
    if base is None or current.get("plan") is None:
        return dict(stale, detail=stale["detail"] + "; there is no earlier plan to redo the change on")
    base = identify(deepcopy(base))
    mine, newer = identify(deepcopy(draft["plan"]), base), identify(deepcopy(current["plan"]), base)
    merged, conflicts = rebase.three_way(base, mine, newer)
    if conflicts:
        named = "; ".join("/".join(part if isinstance(part, str) else part[1] for part in conflict["path"]) for conflict in conflicts)
        return dict(stale, detail=stale["detail"] + f"; both changed {named} differently",
                    user_line=rebase.user_line(conflicts, newer))
    draft["plan"] = merged
    return None
