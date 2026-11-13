from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from tripwright.checks import check_plan, conclusion
from tripwright.jobs import Jobs
from tripwright.ledger import Changes, Ledger, Revision, Scope, lock_receipt, offered, refusals
from tripwright.locks import (binding, changed_items, claimed, covers, decides, field_name, held, identify, lock_changes, plan_changes,
                              plan_objects, pointer, protects, recorded_snapshot, required, snapshot, stable_path, user_part)
from tripwright.repo import snapshot as snapshots
from tripwright.storage import Store, encode, identifier, now
from tripwright.trip import Trip, aware
from tripwright.usage import account_usages


def ledger_only(draft: dict) -> bool:
    """Drafts without a plan candidate; drafts stored before B3c carry the narrower facts_only flag."""
    return draft.get("ledger_only", draft.get("facts_only", False))


def sourced(operations: dict) -> set[tuple[str, str]]:
    """The fields this proposal's admitted user reports and confirmed corrections change, as (target, field)."""
    return {(entry["target"], entry["field"]) for part in ("reports", "confirmations") for entry in operations.get(part, [])
            if part == "reports" or entry["answer"] == "confirmed"}


def unsourced(record: dict, target: str, fields: set[tuple[str, str]]) -> dict:
    return {name: value for name, value in record.items() if (target, name) not in fields}


def lock_reason(check: dict) -> str:
    changed = check["details"].get("changed")
    if not changed:
        return ""
    return " (locked part missing)" if changed == ["missing"] else f" ({', '.join(changed)} changed)"


REPLY_NATURES = ("user_verbatim", "host_restatement")
# The unverified facts a change waits for the user about: facts of the outside world that a lookup could settle
# (evidence: opening hours, a timetable, the weather, whether a ticket applies, a block's own critical facts;
# anchor-evidence: a hard node). The other critical checks describe the plan itself: a travel time between two blocks
# that was not given (chain, one line per person), a must the service has no test for (item, one line per day), fares
# not known yet (budget). They stay in checks.unverified for the answer and hold no change back (2026-11-13: adding a
# walk asked the user about three "chain" lines, and a must recorded with a plan change about four "item" lines).
OUTSIDE_FACTS = ("evidence:", "anchor-evidence:")
SHOWN_IN_REFUSAL = 5


def unknown_key(check: dict) -> str:
    """One unverified fact as a saved plan and a draft are compared by: the check, what it says and what it is about
    (an evidence check carries the block's place and times, so a block moved to another hour is another unknown)."""
    return encode([check["id"], check["text"], check["details"]])


def brought_in(known: set[str], checks: dict) -> list[dict]:
    """The unverified outside facts a candidate brings in: those the saved plan does not already carry."""
    return [check for check in checks["unverified"] if check["critical"] and check["id"].startswith(OUTSIDE_FACTS)
            and unknown_key(check) not in known]


def not_saved(facts: list[dict], *, asks: bool = True, answered: bool = False) -> str:
    """The refusal for a change that brings in unverified facts: what they are and what to do. `asks`: the user's
    answer is required (versions.commit's ask_first); otherwise only the caller's accept_unverified is missing.
    `answered`: a message has arrived since the facts were returned, and only the agent's statement that the user
    agreed is missing."""
    def line(check):
        details = check["details"]
        hours = "–".join(str(details[key]) for key in ("start", "end") if details.get(key)) or details.get("time")
        about = " ".join(str(part) for part in (details.get("place"), hours) if part)
        return f"{check['id']}{f' ({about})' if about else ''}: {check['text']} {check['consequence']} {check['alternative']}"

    more = len(facts) - SHOWN_IN_REFUSAL
    listed = " | ".join(line(check) for check in facts[:SHOWN_IN_REFUSAL]) + (
        f" | and {more} more in the draft's checks.unverified." if more > 0 else "")
    if not asks:
        return (f"Not saved yet: this change brings in {len(facts)} fact(s) that remain unverified. {listed} Commit with "
                "accept_unverified=true to save it with them, and tell the user which facts are unknown and what follows "
                "from each.")
    if answered:
        return (f"Not saved yet: this change brings in {len(facts)} fact(s) that remain unverified. {listed} A message has "
                "arrived since they were returned: if it agrees to save the change as it is, commit this draft with "
                "accept_unverified=true; otherwise propose what the user asked for.")
    return (f"Not saved yet: this change brings in {len(facts)} fact(s) that remain unverified, and no message of the user "
            f"has arrived since they were returned. {listed} Tell the user that the change is not saved yet, which facts "
            "are unknown and what follows from each, and ask whether to save it as it is. After the user's answer call "
            "turn with it; if the user agrees, commit this draft with accept_unverified=true.")



def proposal_key(arguments: dict) -> str:
    """The idempotency key of a proposal sent without one (2026-11-13, G16: a weak client failed on the required
    bookkeeping fields): this context and a digest of the whole input, so the same call sent again finds its draft
    and any other input is a new one."""
    content = {name: arguments.get(name) for name in ("ctx_id", "base_version", "plan", "days", "places", "bookings", "changes", "usages")}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
    return f"auto:{arguments.get('ctx_id')}:{digest}"


def commit_key(draft_id: str, accept_unverified: bool) -> str:
    """The idempotency key of a commit sent without one: the draft and whether it accepts unverified facts. A refused
    commit stores nothing, so the same keyless call after the user's answer is not taken for the refused one; a
    retry of a saved commit returns its version."""
    return f"auto:{draft_id}:{'accept_unverified' if accept_unverified else 'plain'}"

class Versions:
    def __init__(self, store: Store, jobs: Jobs):
        self.store = store
        self.ledger = Ledger(store)
        self.jobs = jobs
        self.requery = None

    def current(self, owner: str) -> dict:
        return max(self.store.all("versions", owner), key=lambda version: version["number"],
                   default=dict(number=0, plan=None, id=None, committed_at=None))

    def _previous_plan(self, owner: str) -> dict | None:
        previous = self.current(owner)["plan"]
        return identify(deepcopy(previous)) if previous is not None else None

    def _stabilize_locks(self, owner: str):
        """Locks stored before stable block paths or before B3d snapshots take their identities from the last committed
        plan, where they last passed the comparator of their time. An old placement that cannot be rebound stays as it
        was: it never matches a current snapshot, so the lock fails closed until the user decides again."""
        previous = self._previous_plan(owner)
        if previous is None:
            return
        for decision in self.store.all("decisions", owner):
            if decision["status"] == "superseded":
                continue
            paths = {stable_path(previous, path): expected for path, expected in decision["locked_paths"].items()}
            placements = dict(decision.get("placement") or {})
            for path in paths:
                if recorded_snapshot(placements.get(path)):
                    continue
                try:
                    where = snapshot(previous, path)
                except ValueError:
                    where = None
                if where:
                    placements[path] = where
            if paths != decision["locked_paths"] or placements != (decision.get("placement") or {}):
                decision.update(locked_paths=paths, placement=placements)
                self.store.put("decisions", owner, decision)

    def context(self, owner: str, source_event: str, *, subject: list[str] | None = None,
                scope: dict | None = None, scenario: str = "plan", focus_keys: list[str] | None = None,
                exclude_keys: list[str] | None = None, context_id: str | None = None, at: str | None = None) -> dict:
        """A read bound to one effective snapshot (CONTRACTS 1.1); drafts and their jobs inherit it."""
        self.store.get("events", owner, source_event)
        with self.store.transaction():
            current = self.current(owner)
            taken = snapshots.take(self.store, owner, at=at)
            bundle = self.ledger.compile(owner, subject=subject, scope=scope, scenario=scenario, at=at,
                                         focus_keys=focus_keys, exclude_keys=exclude_keys)
            context = dict(id=context_id or identifier("CTX"), source_event=source_event, base_version=current["number"],
                           base_version_id=current["id"], revision=taken["ledger_revision"], snapshot=taken, bundle=bundle,
                           created_at=taken["taken_at"])
            self.store.put("contexts", owner, context, insert=True)
            return context

    def _only_own_ledger_commits(self, owner: str, context: dict, current: dict) -> bool:
        """Every version since the context's is a ledger-only commit proposed against this same context."""
        if current["number"] <= context["base_version"]:
            return False
        later = [version for version in self.store.all("versions", owner) if version["number"] > context["base_version"]]
        for version in later:
            if version.get("ctx_id") != context["id"] or not version.get("draft_id"):
                return False
            try:
                draft = self.store.get("drafts", owner, version["draft_id"])
            except ValueError:
                return False
            if not ledger_only(draft):
                return False
        return True

    def fresh(self, owner: str, bound: dict | None) -> list[str]:
        """What changed since a stored snapshot: plan and/or ledger by content key; [] when still current."""
        return snapshots.changed(bound, snapshots.take(self.store, owner)) if bound else []

    def known_unknowns(self, owner: str) -> set[str]:
        """The critical facts the saved plan already leaves unverified, checked now (evidence expires, and versions
        saved before 2026-11-12 stored these checks without place and times). A change that leaves them as they are
        brings in nothing new, so it does not ask the user about them again."""
        plan = self.current(owner)["plan"]
        if plan is None:
            return set()
        return {unknown_key(check) for check in check_plan(self.store, owner, Trip.model_validate(plan))["unverified"] if check["critical"]}

    def messages(self, owner: str) -> list[dict]:
        """What the user said, in the order received: turns that only transcribe screenshots or carry the agent's
        inference are not the user speaking."""
        return [event for event in snapshots.received_events(self.store, owner) if event["received"]["nature"] in REPLY_NATURES]

    def unanswered(self, owner: str, brought: list[dict]) -> tuple[list[dict], dict | None]:
        """Of the unverified facts a change brings in: those not answered yet, and the message that answered the
        others (the latest one, when different messages answered different facts).

        2026-11-12, Claude and ChatGPT on the web: both committed such a change with accept_unverified in the turn
        that proposed it and told the user afterwards. What the service checks is narrow: after a draft returned the
        fact, turn received another message with different words. It cannot read what the agent told the user, whether
        that message agrees, or whether the user wrote it (the agent passes the text). An agent that commits in the
        turn of its proposal cannot show such a message without calling turn with words of its own.

        The answer to a fact is the first such message: what comes after it is a new message like any other and is
        not covered by it (commit). A fact is answered once: a draft proposed again after the answer (the old one
        went stale, or the user changed something else) does not ask about it a second time. The same words sent
        again (a turn repeated to record the message) are not an answer."""
        if not brought:
            return [], None
        events = self.messages(owner)
        answered: dict[str, dict] = {}
        for draft in self.store.all("drafts", owner):
            shown = {unknown_key(check) for check in draft.get("ask") or []}
            if not shown:
                continue
            said = next((event["received"]["text"] for event in events if event["id"] == draft["source_event"]), None)
            first = next((event for event in events if aware(event["receipt"]["received_at"]) > aware(draft["created_at"])
                          and event["received"]["text"] != said), None)
            for key in shown if first else ():
                earlier = answered.get(key)
                if earlier is None or aware(first["receipt"]["received_at"]) < aware(earlier["receipt"]["received_at"]):
                    answered[key] = first
        waiting = [check for check in brought if unknown_key(check) not in answered]
        used = [answered[unknown_key(check)] for check in brought if unknown_key(check) in answered]
        return waiting, max(used, key=lambda event: aware(event["receipt"]["received_at"]), default=None)

    def _check(self, owner: str, plan: Trip, *, usages: list[dict], require_usage: bool, sourced: set[tuple[str, str]] = frozenset()) -> dict:
        """All checks of a candidate. Facts stay as committed (D-53): a bought or booked booking and a hard node change
        only in the fields `sourced` names, the ones this proposal's admitted user reports and confirmed corrections
        change; a user lock is a must."""
        report = check_plan(self.store, owner, plan)
        serialized = plan.model_dump(mode="json")
        previous = self._previous_plan(owner)
        if previous:
            bookings = {booking["id"]: booking for booking in serialized["bookings"]}

            def kept(booking):
                fares = [unsourced(fare, f"fare:{fare['id']}", sourced) for fare in booking.get("fares", [])]
                return dict(unsourced(booking, f"booking:{booking['id']}", sourced), fares=fares)

            for booking in previous["bookings"]:
                if booking["status"] in ("bought", "booked"):
                    matches = booking["id"] in bookings and kept(booking) == kept(bookings[booking["id"]])
                    report["checks"].append(conclusion(f"paid-booking:{booking['id']}", "passed" if matches else "conflict",
                                                       "A bought or booked arrangement cannot be silently changed or removed; it requires user review.", date=booking["date"]))
            anchors = {anchor["id"]: (day["date"], anchor) for day in serialized["days"] for anchor in day["anchors"]}
            for day in previous["days"]:
                for anchor in day["anchors"]:
                    actual = anchors.get(anchor["id"])
                    protected = [key for key in ("time", "subject", "place", "buffer_minutes", "destination", "line")
                                 if (f"anchor:{anchor['id']}", key) not in sourced]
                    matches = actual is not None and actual[0] == day["date"] and all(actual[1].get(key) == anchor.get(key) for key in protected)
                    report["checks"].append(conclusion(f"preserved-anchor:{anchor['id']}", "passed" if matches else "conflict",
                                                       "An existing hard node must remain in the candidate with its people, date, time and buffer.", date=day["date"]))
        for decision in self.ledger.current(owner)["decisions"]:
            for path, expected in decision["locked_paths"].items():
                if not binding(decision, path):
                    continue
                try:
                    stable = stable_path(previous or serialized, path)
                except ValueError:
                    stable, changed = path, ["missing"]
                else:
                    changed = lock_changes(serialized, stable, expected, (decision.get("placement") or {}).get(stable))
                recorded = (decision.get("placement") or {}).get(stable)
                report["checks"].append(conclusion(f"locked:{decision['id']}:{path}", "conflict" if changed else "passed",
                                                   "A user-decided plan part must keep its value, date, plan and the plan's position, "
                                                   "its people and the identity of the places, fares, bookings and people it refers to.",
                                                   changed=changed, decided_placement=recorded))
        required, optional = {}, {}
        if require_usage:
            for day in plan.days:
                bundle = self.ledger.compile(owner, scope=Scope(start=day.date, end=day.date, channel="conversation"))
                required.update({item["id"]: item for item in bundle["items"] if not offered(item)})
                optional.update({item["id"]: item for item in bundle["items"] if offered(item)})
        report["usages"] = account_usages(plan, required, usages, report["checks"], report["activity_coverage"], optional=optional)
        report["applicable_items"] = list(required.values())
        report["undecided"] = self.ledger.compile(owner)["undecided"]
        report["conflicts"] = [check for check in report["checks"] if check["status"] == "conflict" and check["critical"]]
        report["unverified"] = [check for check in report["checks"] if check["status"] == "unverified"]
        report["verified"] = bool(report["checks"]) and all(check["status"] == "passed" for check in report["checks"])
        return report

    def _preview(self, owner: str, candidate: Trip, changes: Changes, usages: list[dict] | None, *, require_usage: bool,
                 answered: str, at: str, auto_revisions: bool = False) -> tuple[dict, dict]:
        """The checks of a candidate with the changes applied in a preview, and the lock operations they admitted; the
        report's `guard` holds the commit's own refusals (D-52)."""
        with self.store.preview():
            self._stabilize_locks(owner)
            base, flexible, before = self._user_locks(owner), self._flexible(owner), self.current(owner)["plan"]
            plan = candidate.model_dump(mode="json")
            filled = []
            if auto_revisions and before is not None:
                for decision, path in flexible:
                    try:
                        value, recorded = pointer(before, path), snapshot(before, path)
                    except ValueError:
                        continue
                    if changed_items(path, lock_changes(plan, path, value, recorded)):
                        filled.append(Revision(decision=decision["id"], filled_by="service",
                                               reason="Service-filled contact: the candidate touches this decision; no preference precedence inferred."))
                changes = changes.model_copy(update=dict(revisions=[*changes.revisions, *filled]))
            applied = self.ledger.apply(owner, changes, answered=answered, at=at, plan=plan)
            guard, _ = self._guard(owner, event=answered, base=base, before=before, plan=plan,
                                   operations=applied["locks"], changes=changes.model_dump(mode="json"))
            changelog, unexplained = self._changelog(owner, event=answered, flexible=flexible, before=before, plan=plan,
                                                     operations=applied["locks"], changes=changes)
            return dict(self._check(owner, candidate, usages=usages, require_usage=require_usage, sourced=sourced(applied["locks"])),
                        crowding=applied["crowding"], possible_contradictions=applied["possible_contradictions"], guard=guard,
                        changelog=changelog, unexplained=unexplained, filled_revisions=filled), applied["locks"]

    def _user_locks(self, owner: str) -> list[tuple[dict, str]]:
        """Each current user lock (a must, D-53) with each path it holds for the user."""
        return [(decision, path) for decision in self.ledger.current(owner)["decisions"] for path in decision["locked_paths"]
                if binding(decision, path)]

    def _flexible(self, owner: str) -> list[tuple[dict, str]]:
        """Each current decision of the user not marked a must, with each path the user decided (D-53)."""
        return [(decision, path) for decision in self.ledger.current(owner)["decisions"] for path in decision["locked_paths"]
                if user_part(decision, path) and not binding(decision, path)]

    def _changelog(self, owner: str, *, event: str | None, flexible: list[tuple[dict, str]], before: dict | None, plan: dict,
                   operations: dict, changes) -> tuple[list[dict], list[str]]:
        """The change log of a proposal (D-53) and what it refuses. Each changed field of the plan (`plan_changes`) is
        attributed: to the user when this proposal's user decision, report or confirmed correction holds it (basis: the
        message and that record); otherwise to the agent (basis: the message the proposal answers and the object's
        evidence), with the reason of the revision it belongs to or the proposal's `why`. A change to what the user
        decided earlier without marking it a must (`flexible`, judged on the base plan with the comparator) that no user
        record of this proposal holds needs a revision with a reason for that decision; without one it is refused."""
        changes = Changes.model_validate(changes or {})
        said = []
        for part in ("reports", "confirmations"):
            for entry in operations.get(part, []):
                if part == "confirmations" and entry["answer"] != "confirmed":
                    continue
                if part == "reports":
                    basis, why = dict(event=entry["event"], report=entry["id"]), f"the user said: {entry['quote']}"
                else:
                    correction = self.store.get("undecided", owner, entry["id"])["correction"]
                    basis = dict(event=entry["event"], correction=entry["id"], **(dict(evidence_ids=correction["evidence_ids"])
                                                                                 if correction.get("evidence_ids") else {}))
                    why = f"the user confirmed: {correction['basis']} ({entry['quote']})"
                said.append(({field_name(plan, entry["target"], entry["field"])}, dict(by="user", basis=basis, why=why)))
        for line in operations.get("decisions", []):
            if line.get("refused"):
                continue
            record = self.store.get("decisions", owner, line["id"])
            names = set().union(*(decides(path, record["locked_paths"][path], before)
                                  | held(path, record["locked_paths"][path], (record.get("placement") or {}).get(path)) for path in line["paths"]))
            said.append((names, dict(by="user", basis=dict(event=line["event"], decision=line["id"]),
                                     why=record.get("reason") or f"{record['question']}: {record['choice']}")))
        reasons = {revision.decision: revision.reason for revision in changes.revisions}
        for decision_id in reasons:
            if not any(decision["id"] == decision_id for decision, _ in flexible):
                # an item's ID or key (2026-11-12: a host named both): say how an item is replaced
                item = next((entry["id"] for entry in self.store.all("items", owner)
                             if entry["status"] == "active" and decision_id in (entry["id"], entry["key"])), None)
                item = (f"; {decision_id} is a recorded condition, not a decision: to replace or narrow it, record the new "
                        f"statement in items with exclusive_with naming {item}, and the user is asked which one stays"
                        if item else
                        f"; {decision_id} is not an active recorded condition; revisions take decision IDs (D-…); to replace "
                        "or narrow a recorded condition, record the new statement in items with exclusive_with naming that "
                        "item's ID or key" if decision_id.startswith("I-") else "")
                raise ValueError(f"changes.revisions: {decision_id} is not a current decision of the user without must; a must "
                                 f"changes only by the user's new decision, report or confirmed correction{item}")
        refused, revised = [], []
        for decision, path in flexible if before is not None else []:
            try:
                value, recorded = pointer(before, path), snapshot(before, path)
            except ValueError:
                continue  # already gone from the plan this proposal is based on
            items = changed_items(path, lock_changes(plan, path, value, recorded))
            unexplained = {item for item in items if not any(protects(names, item) for names, _ in said)}
            if not unexplained:
                continue
            if decision["id"] in reasons:
                revised.append((unexplained, decision["id"], reasons[decision["id"]]))
            else:
                refused.append(f"not recorded (reason): the plan changes what the user decided in {decision['id']} at {path} "
                               f"({', '.join(sorted(unexplained))}) and gives no reason; fix: add changes.revisions "
                               f"{{decision: \"{decision['id']}\", reason: \"<why>\"}} (it is not a must, so the agent may change it, "
                               "saying why), or record the user's new decision")
        objects, entries = plan_objects(plan), []
        for line in plan_changes(before, plan):
            name = line.pop("name")
            source = next((attributes for names, attributes in said if protects(names, name)), None)
            if source is not None:
                entries.append(dict(line, **source))
                continue
            revision = next(((decision_id, reason) for items, decision_id, reason in revised
                             if any(protects({item}, name) or protects({name}, item) for item in items)), None)
            evidence = (objects.get(line["target"]) or (None, {}))[1].get("evidence_ids") or []
            entries.append(dict(line, by="agent", basis=dict(event=event, **(dict(evidence_ids=evidence) if evidence else {})),
                                why=revision[1] if revision else changes.why, **(dict(revises=revision[0]) if revision else {})))
        return entries, refused

    def _guard(self, owner: str, *, event: str | None, base: list[tuple[dict, str]], before: dict | None, plan: dict,
               operations: dict, changes: dict) -> tuple[list[str], dict | None]:
        """The commit's own check of user locks (D-52), anchored on the version the proposal is based on and run after the
        ledger recorded the changes, whatever it recorded: `base` holds the user locks before, `plan` is the plan to be
        stored, the ledger's current locks are those after. Refused, as one line each:
        - conservation: a part a base lock protects changed, without a user decision this proposal admitted that
          decides all the lock decided and holds the part, or the user's report or confirmed correction of that field;
          or a part it protects, still in the plan, is protected by no lock after (a released part needs the user's
          change of it: a block the user's new day no longer has goes with that day);
        - used: the message this proposal answers already changed one of the locks in an earlier version (its own drafts
          commit once; the same draft committed again returns that version before this runs);
        - order: the message is older than the one that last changed a lock (`Ledger.in_order`), or a confirmation's
          message came before the correction was asked.
        Returns the refusals and what the version records: the message and the locks it changed or created."""
        after = self._user_locks(owner)
        after_held = [held(path, decision["locked_paths"][path], (decision.get("placement") or {}).get(path)) for decision, path in after]
        covering = []
        for line in operations.get("decisions", []):
            if line.get("refused"):
                continue
            record = self.store.get("decisions", owner, line["id"])
            claims = set().union(*(decides(path, record["locked_paths"][path], before) for path in line["paths"]))
            covering.append((claims, claims.union(*(held(path, record["locked_paths"][path], (record.get("placement") or {}).get(path))
                                                     for path in line["paths"])), bool(record.get("must"))))
        fields = {field_name(plan, entry["target"], entry["field"]) for part in ("reports", "confirmations")
                  for entry in operations.get(part, []) if part == "reports" or entry["answer"] == "confirmed"}
        refused, changed = [], set()
        after_ids = {decision["id"] for decision, _ in after}
        for lock, path in base:
            value, recorded = lock["locked_paths"][path], (lock.get("placement") or {}).get(path)
            items = changed_items(path, lock_changes(plan, path, value, recorded))
            if items or lock["id"] not in after_ids:
                changed.add(lock["id"])
            whole = claimed(path, value)
            for item in sorted(items):
                if not (protects(fields, item) or any(covers(claims, whole) and protects(holds, item) for claims, holds, _ in covering)):
                    refused.append(f"not admitted (conservation): lock {lock['id']} at {path}: {item} changed without the user's authority "
                                   f"for it; the user decides all of {path} again, or reports or confirms that field")
            protected = held(path, value, recorded)
            # The user's new decision on all of it without a must releases everything the lock protected (D-53).
            released = any(not must and covers(claims, whole) for claims, _, must in covering)
            for item in sorted(required(plan, path)) if not released else []:
                if protects(protected, item) and not any(protects(names, item) for names in after_held):
                    refused.append(f"not admitted (conservation): lock {lock['id']} at {path}: {item} would be protected by no lock after "
                                   f"this commit; the user decides {path} (or the day holding it) again as a whole")
        if event is not None and changed:
            authority = self.ledger._authority(owner, event)
            for version in self.store.all("versions", owner):
                used = version.get("authorized") or {}
                if used.get("event") == event and changed & set(used.get("locks", [])):
                    refused.append(f"not admitted (used): message {event} already changed lock {', '.join(sorted(changed & set(used['locks'])))} "
                                   f"in version {version['number']}; only a new message of the user changes it again")
            for lock_id in sorted(changed):
                try:
                    self.ledger.in_order(owner, next(decision for decision, _ in base if decision["id"] == lock_id), authority,
                                         f"the change of lock {lock_id}")
                except ValueError as error:
                    refused.append(str(error))
        for entry in operations.get("confirmations", []):
            question = self.store.get("undecided", owner, entry["id"])
            asked = (question.get("asked") or {}).get("at") or question["since"]
            if entry["event"] and aware(self.store.get("events", owner, entry["event"])["receipt"]["received_at"]) <= aware(asked):
                refused.append(f"not admitted (order): the confirmation of {entry['id']} comes from message {entry['event']}, received "
                               f"before the correction was asked at {asked}")
        created = after_ids - {decision["id"] for decision, _ in base}
        record = dict(event=event, locks=sorted(changed | created)) if event is not None and (changed or created) else None
        return refused, record

    def propose(self, owner: str, *, ctx_id: str, base_version: int | None = None, plan: dict | None = None,
                changes: dict | None = None, usages: list[dict] | None = None, idempotency_key: str | None = None) -> dict:
        """`base_version` and `idempotency_key` may be left out (G16): the version is then the one the context was
        issued on, the key `proposal_key` of the input."""
        request = dict(ctx_id=ctx_id, base_version=base_version, plan=plan, changes=changes or {}, usages=usages)
        idempotency_key = idempotency_key or proposal_key(request)
        # When the proposal was made: a correction it opens is asked at this time, also when committed or replayed (D-50).
        made = now()
        with self.store.transaction():
            for previous in self.store.all("drafts", owner):
                if previous["idempotency_key"] == idempotency_key:
                    if encode(previous["request"]) != encode(request):
                        raise ValueError("Proposal idempotency key was reused with different input")
                    return previous
            context = self.store.get("contexts", owner, ctx_id)
            current = self.current(owner)
            if base_version is None:
                base_version = context.get("moved_from", context["base_version"])  # the version turn gave with this ctx_id
            if base_version == context.get("moved_from", context["base_version"]) and context["base_version"] != current["number"] \
                    and self._only_own_ledger_commits(owner, context, current):
                # 2026-11-12: this message's requirements were committed first as their own ledger-only version, so
                # that a plan draft held back by the verifier never takes them with it; the plan is then proposed
                # against the same context, moved onto that version (nothing else changed since the turn).
                context = dict(context, moved_from=context.get("moved_from", context["base_version"]),
                               base_version=current["number"], base_version_id=current["id"],
                               revision=self.ledger.revision(owner), snapshot=snapshots.take(self.store, owner))
                self.store.put("contexts", owner, context)
                base_version = current["number"]
            if base_version == context.get("moved_from") and context["base_version"] == current["number"]:
                base_version = current["number"]  # the turn's version, for a context already moved onto its own ledger commit
            if current["number"] != base_version or context["base_version"] != base_version:
                raise ValueError("Stale version; call turn again")
            if context["revision"] != self.ledger.revision(owner) or "ledger" in self.fresh(owner, context.get("snapshot")):
                raise ValueError("Ledger changed; call turn again")
            if "plan" in self.fresh(owner, context.get("snapshot")):
                raise ValueError("Stale version; call turn again")
            # Plans get stable IDs before anything compares them: locks and later versions identify plans by ID and
            # position, never by display name.
            serialized = identify(Trip.model_validate(plan if plan is not None else current["plan"]).model_dump(mode="json"),
                                  self._previous_plan(owner))
            candidate = Trip.model_validate(serialized)
            changes_model = Changes.model_validate(changes or {}).model_copy(deep=True)
            for decision in changes_model.decisions:
                decision.locked_paths = {stable_path(serialized, path): expected for path, expected in decision.locked_paths.items()}
                decision.parts = {stable_path(serialized, path): who for path, who in decision.parts.items()}
                decision.placement = {}
                for path in decision.locked_paths:
                    try:
                        where = snapshot(serialized, path)
                    except ValueError:
                        where = None
                    if where:
                        decision.placement[path] = where
            people = {person.id for person in candidate.people}
            for record in [*changes_model.items, *changes_model.facts, *changes_model.decisions, *changes_model.pending_checks]:
                if not set(record.subject) <= people | {"*"}:
                    raise ValueError("Ledger subject is outside trip people")
            # A ledger-only proposal cannot change the plan, so plan conflicts it reveals mark the unchanged plan
            # needs_replan instead of refusing what the user said.
            without_plan = plan is None
            if without_plan and usages:
                raise ValueError("Usages describe a plan candidate; omit usages for ledger-only changes")
            others = [field for field in Changes.model_fields if field != "facts" and getattr(changes_model, field)]
            not_recorded = []
            known = set() if without_plan else self.known_unknowns(owner)
            try:
                report, operations = self._preview(owner, candidate, changes_model, usages, require_usage=not without_plan,
                                                   answered=context["source_event"], at=made,
                                                   auto_revisions="revisions" not in (changes or {}))
            except ValueError as error:
                if not (without_plan and changes_model.facts and others):
                    raise
                # Facts are first-hand and never traded off against preferences: an invalid part of the same message
                # does not hold them back. Record them alone and say what was left out and why.
                not_recorded = [dict(part=field, records=len(getattr(changes_model, field)), reason=str(error)) for field in others]
                changes_model = Changes(facts=changes_model.facts)
                report, operations = self._preview(owner, candidate, changes_model, [], require_usage=False, answered=context["source_event"],
                                                   at=made)
            guard, changelog, unexplained = report.pop("guard"), report.pop("changelog"), report.pop("unexplained")
            filled = report.pop("filled_revisions")
            if filled:
                changes_model = changes_model.model_copy(update=dict(revisions=[*changes_model.revisions, *filled]))
            refused = refusals(operations) + guard + unexplained
            draft_id = identifier("DR")
            draft = dict(id=draft_id, ctx_id=ctx_id, base_version=base_version, base_version_id=current["id"], revision=context["revision"],
                         snapshot=context.get("snapshot"),
                         source_event=context["source_event"], plan=candidate.model_dump(mode="json"),
                         changes=changes_model.model_dump(mode="json"), usages=report["usages"], checks=report, ledger_only=without_plan,
                         ask=[] if without_plan else brought_in(known, report),
                         not_recorded=not_recorded, require_usage=not without_plan, idempotency_key=idempotency_key, request=deepcopy(request),
                         lock_changes=dict(lock_receipt(operations, committed=False), **(dict(refused=guard) if guard else {})),
                         refused=refused, status="rejected" if (report["conflicts"] and not without_plan) or refused else "proposed",
                         changelog=changelog, created_at=made)
            self.store.put("drafts", owner, draft, insert=True)
            job = self.jobs.enqueue(owner, "deep_check", {"draft_id": draft_id}, base_version, f"deep:{draft_id}",
                                    snapshot=context.get("snapshot"))
            draft["job_id"] = job["id"]
            self.store.put("drafts", owner, draft)
            return draft

    async def deep_check(self, owner: str, payload: dict) -> dict:
        draft = self.store.get("drafts", owner, payload["draft_id"])
        if self.current(owner)["number"] != draft["base_version"] or self.ledger.revision(owner) != draft["revision"] or self.fresh(owner, draft.get("snapshot")):
            raise ValueError("Draft version or ledger changed")
        candidate, notes = await self.requery(owner, draft["plan"]) if self.requery else (draft["plan"], [])
        with self.store.transaction():
            if self.current(owner)["number"] != draft["base_version"] or self.ledger.revision(owner) != draft["revision"] or self.fresh(owner, draft.get("snapshot")):
                raise ValueError("Draft version or ledger changed")
            with self.store.preview():
                self._stabilize_locks(owner)
                base, flexible, before = self._user_locks(owner), self._flexible(owner), self.current(owner)["plan"]
                operations = self.ledger.apply(owner, draft["changes"], answered=draft["source_event"], at=draft["created_at"], plan=candidate)["locks"]
                guard, _ = self._guard(owner, event=draft["source_event"], base=base, before=before, plan=candidate, operations=operations,
                                       changes=draft["changes"])
                changelog, unexplained = self._changelog(owner, event=draft["source_event"], flexible=flexible, before=before, plan=candidate,
                                                         operations=operations, changes=draft["changes"])
                refused = refusals(operations) + guard + unexplained
                report = self._check(owner, Trip.model_validate(candidate), usages=draft["request"]["usages"], require_usage=draft["require_usage"],
                                     sourced=sourced(operations))
            draft.update(plan=candidate, checks=report, usages=report["usages"], changelog=changelog,
                         status="rejected" if (report["conflicts"] and not ledger_only(draft)) or refused else "proposed")
            self.store.put("drafts", owner, draft)
            return dict(draft_id=draft["id"], base_version=draft["base_version"], checks=report,
                        differences="Checks rerun against the complete candidate, scoped facts and all hard conditions.",
                        external_requery=notes)

    def unsettled(self, owner: str, draft: dict) -> list[str]:
        """Messages after the draft's watermark that no journal entry cites and its deep job did not acknowledge."""
        if not draft.get("snapshot"):
            return []
        acked = []
        if draft.get("job_id"):
            acked = [ack["event"] for ack in self.store.get("jobs", owner, draft["job_id"]).get("acked", [])]
        return snapshots.unsettled(self.store, owner, draft["snapshot"], snapshots.take(self.store, owner), acked)

    def adopt(self, owner: str, plan: dict, *, author: str, base_version_id: str | None = None,
              version_id: str | None = None) -> dict:
        """A plan edited outside the tool, taken as a proposal (design 5.6): it becomes a version only when every check
        passes, which includes every user lock and preserved hard node, and only on the version it was edited from.
        Unknown facts stay unverified in its checks; nobody acknowledged them, so nothing is labelled passed."""
        with self.store.transaction():
            current = self.current(owner)
            if base_version_id is not None and current["id"] != base_version_id:
                return dict(adopted=False, conflicts=[f"based on {base_version_id}, current is {current['id']}"])
            serialized = identify(Trip.model_validate(plan).model_dump(mode="json"), self._previous_plan(owner))
            candidate = Trip.model_validate(serialized)
            self._stabilize_locks(owner)
            checks = self._check(owner, candidate, usages=[], require_usage=False)
            if checks["conflicts"]:
                return dict(adopted=False, conflicts=[check["id"] + lock_reason(check) for check in checks["conflicts"]])
            # A hand edit is the person's own change (D-53): every line is theirs.
            changelog = [dict({key: value for key, value in line.items() if key != "name"}, by="user",
                              basis=dict(edit="external", author=author), why=None)
                         for line in plan_changes(current["plan"], candidate.model_dump(mode="json"))]
            version = dict(id=version_id or identifier("V"), number=current["number"] + 1, base_version=current["number"],
                           base_version_id=current["id"], source_event=None, plan=candidate.model_dump(mode="json"), checks=checks,
                           ctx_id=None, usages=[], draft_id=None, needs_replan=False, planning_status="current", warnings=[],
                           ledger_revision=self.ledger.revision(owner), idempotency_key=f"manual:{version_id or identifier('M')}",
                           accept_unverified=False, committed_at=now(), submitted_by=author, origin="external_edit", changelog=changelog)
            self.store.put("versions", owner, version, insert=True)
            self.jobs.invalidate(owner)
            return dict(adopted=True, version=version)

    def commit(self, owner: str, draft_id: str, idempotency_key: str | None = None, *, accept_unverified: bool = False,
               ask_first: bool = False, version_id: str | None = None, replaying: bool = False,
               answered: dict | None = None) -> dict:
        """A change that brings in unverified outside facts (`brought_in`) is saved only with `accept_unverified`.
        `ask_first` is how a host agent's call is treated (the MCP adapter sets it): the acceptance then counts only
        when turn has received another message since those facts were returned (`unanswered`), and the version
        records that message in `answered`. Messages that came after it still have to be settled. Without
        `ask_first` the caller vouches for the user itself (seeds, tests, replay, an operator's
        TRIPWRIGHT_SAVE_UNVERIFIED=agent). Unverified facts the saved plan already carries never hold a change back,
        in either mode, and neither do the checks on the plan itself (OUTSIDE_FACTS).

        `version_id`, `replaying` and `answered` are for replay on a new head (repo.replay): the version keeps its ID
        and its acceptance record, and the checks on later messages, which held when the user saw the draft, are not
        repeated."""
        idempotency_key = idempotency_key or commit_key(draft_id, accept_unverified)
        with self.store.transaction():
            for version in self.store.all("versions", owner):
                if version["idempotency_key"] == idempotency_key:
                    if version["draft_id"] != draft_id or version["accept_unverified"] != accept_unverified:
                        raise ValueError("Commit idempotency key was reused with different input")
                    return version
            draft = self.store.get("drafts", owner, draft_id)
            if draft.get("options_pending"):
                raise ValueError("Plan options are waiting for the user's choice; commit preferences with a ledger-only draft, "
                                 "then change the plan after the user chooses")
            if draft["status"] == "committed":
                return self.store.get("versions", owner, draft["version_id"])
            if self.current(owner)["number"] != draft["base_version"]:
                raise ValueError("Stale draft cannot overwrite a newer version")
            if self.ledger.revision(owner) != draft["revision"] or "ledger" in self.fresh(owner, draft.get("snapshot")):
                raise ValueError("Ledger changed; propose again")
            if "plan" in self.fresh(owner, draft.get("snapshot")):
                raise ValueError("Stale draft cannot overwrite a newer version")
            self._stabilize_locks(owner)
            base, flexible, before = self._user_locks(owner), self._flexible(owner), self.current(owner)["plan"]
            known = set() if ledger_only(draft) else self.known_unknowns(owner)
            applied = self.ledger.apply(owner, draft["changes"], answered=draft["source_event"], at=draft["created_at"], plan=draft["plan"])
            if refusals(applied["locks"]):
                # D-51: a decision that only touches an old lock refuses the whole proposal, also a ledger-only one.
                raise ValueError("Commit refused: " + "; ".join(refusals(applied["locks"])))
            # Drafts stored before plan IDs existed get them the way propose assigns them now.
            plan = Trip.model_validate(identify(deepcopy(draft["plan"]), self._previous_plan(owner)))
            checks = self._check(owner, plan, usages=draft["request"]["usages"], require_usage=draft["require_usage"],
                                 sourced=sourced(applied["locks"]))
            unchanged_plan = ledger_only(draft)
            # D-52: the commit's own check against the version it is based on, whatever the ledger recorded; it refuses
            # what the current locks no longer see, also in a ledger-only proposal. Its reasons follow the comparator's.
            guard, authorized = self._guard(owner, event=draft["source_event"], base=base, before=before, plan=plan.model_dump(mode="json"),
                                            operations=applied["locks"], changes=draft["changes"])
            # D-53: what the user decided without a must changes only with the user's record or the agent's reason.
            changelog, unexplained = self._changelog(owner, event=draft["source_event"], flexible=flexible, before=before,
                                                     plan=plan.model_dump(mode="json"), operations=applied["locks"], changes=draft["changes"])
            conflicts = [check["id"] + lock_reason(check) for check in checks["conflicts"]] if not unchanged_plan else []
            if conflicts or guard or unexplained:
                raise ValueError("Commit refused: " + "; ".join(conflicts + guard + unexplained))
            brought = [] if unchanged_plan else brought_in(known, checks)
            asking = bool(brought) and ask_first and not replaying
            waiting, reply = self.unanswered(owner, brought) if asking else ([], None)
            if waiting or (brought and not accept_unverified):
                raise ValueError(not_saved(waiting or brought, asks=ask_first, answered=asking and not waiting))
            if asking:
                answered = dict(event=reply["id"], unverified=[check["id"] for check in brought])
            if not unchanged_plan and not replaying:
                later = self.unsettled(owner, draft)
                if asking:
                    # the answer settles the messages up to it; one that came after it is a new message (2026-11-13:
                    # "yes, save it", then "and drop the coffee", must not save the draft without the second)
                    until = aware(reply["receipt"]["received_at"])
                    later = [event for event in later
                             if aware(self.store.get("events", owner, event)["receipt"]["received_at"]) > until]
                if later:
                    raise ValueError("Unsettled messages after the draft: " + ", ".join(later) + "; record what they change and "
                                     "propose again, or declare they change nothing with result(job_id, ack_events=[...])")
            version = dict(id=version_id or identifier("V"), number=draft["base_version"] + 1, base_version=draft["base_version"],
                           base_version_id=self.current(owner)["id"],
                           source_event=draft["source_event"], plan=plan.model_dump(mode="json"), checks=checks,
                           ctx_id=draft["ctx_id"], usages=checks["usages"], draft_id=draft_id,
                           needs_replan=bool(checks["conflicts"]), planning_status="needs_replan" if checks["conflicts"] else "current",
                           warnings=checks["conflicts"] if unchanged_plan else [],
                           ledger_revision=self.ledger.revision(owner), idempotency_key=idempotency_key,
                           accept_unverified=accept_unverified, committed_at=now(), submitted_by=owner, changelog=changelog,
                           **(dict(authorized=authorized) if authorized else {}),
                           **(dict(answered=answered) if answered else {}))
            self.store.put("versions", owner, version, insert=True)
            for decision_id in sorted({entry["revises"] for entry in changelog if entry.get("revises")}):
                lines = [entry for entry in changelog if entry.get("revises") == decision_id]
                self.ledger._audit(owner, draft["source_event"], "revise_decision", decision_id, dict(
                    version=version["number"], reason=lines[0]["why"], fields=[f"{entry['target']}/{entry['field']}" for entry in lines]))
            draft.update(status="committed", version_id=version["id"], committed_lock_changes=lock_receipt(applied["locks"], committed=True))
            self.store.put("drafts", owner, draft)
            self.jobs.invalidate(owner)
            return version
