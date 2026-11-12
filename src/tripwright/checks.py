from __future__ import annotations

import re
from decimal import Decimal
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from tripwright.ledger import Ledger, Scope, overlaps, people_overlap
from tripwright.evidence import outdoor, snapshot
from tripwright.storage import Store, now
from tripwright.trip import Trip, aware, local_instant, minutes


def departure_clock(time: str, buffer_minutes: int, travel_minutes: int | None, *, day: str | None = None, zone: str | None = None) -> str | None:
    if travel_minutes is None:
        return None
    if day and zone:
        instant = local_instant(day, time, zone)
        if instant is None:
            return None
        departure = (instant - timedelta(minutes=buffer_minutes + travel_minutes)).astimezone(ZoneInfo(zone))
        offset = (departure.date() - date.fromisoformat(day)).days
        clock = departure.strftime("%H:%M")
        return clock if offset == 0 else f"{clock} ({offset:+d} day)"
    offset, clock_minutes = divmod(minutes(time) - buffer_minutes - travel_minutes, 24 * 60)
    clock = f"{clock_minutes // 60:02d}:{clock_minutes % 60:02d}"
    return clock if offset == 0 else f"{clock} ({offset:+d} day)"


ACTIVITIES = {
    "climbing": ("climbing", "bouldering", "抱石", "攀岩"),
    "museum": ("museum", "博物馆"),
    "gallery": ("gallery", "art", "画廊"),
    "view": ("viewpoint", "view", "观景"),
    "park": ("park", "公园"),
    "hiking": ("hiking", "hike", "爬山", "徒步"),
}
SYNONYMS = {"view": {"view", "viewpoint"}}
SATISFIES = {"gallery": {"gallery", "museum"}}
ACTIVITY_BLOCKS = ("visit", "flex", "reserve")


def mentions(text: str | None, kind: str) -> bool:
    words = (text or "").casefold()
    return any(re.search(r"(?<![a-z])" + re.escape(alias) + r"s?(?![a-z])", words) if alias.isascii() else alias in words
               for alias in ACTIVITIES[kind])


def place_kind(item: dict) -> str | None:
    return next((kind for kind in ACTIVITIES if mentions(item["key"] + " " + item["quote"], kind)), None)


def place_kinds(kind: str, *, satisfying: bool) -> set[str]:
    """Place kinds that count as this activity; a wish is also satisfied by close substitutes, a ban covers synonyms only."""
    return (SATISFIES if satisfying else {}).get(kind) or SYNONYMS.get(kind, {kind})


def block_activity(block, places: dict, activity: str, kinds: set[str]) -> bool | None:
    """True when a block's place or option has one of these kinds; None when only unclassified text or an
    unclassified place name mentions the activity, so the service cannot decide; otherwise False."""
    named = [places[place_id] for place_id in [block.place, *block.options] if place_id in places]
    if any(place["kind"] in kinds for place in named):
        return True
    if any(place["kind"] == "other" and mentions(place["name"] + " " + place["name_local"], activity) for place in named):
        return None
    return None if not named and block.kind in ACTIVITY_BLOCKS and mentions(block.text, activity) else False


def activity_coverage(trip: Trip, entry: dict) -> dict:
    """Whether every person of a wanted activity is covered. Alternatives of a day are choices, so a day covers a person
    only when each of its plans does; a person is covered when some day in scope does. Statuses per person: covered,
    missing (every planned alternative is definitely without it) or unknown (unplanned days, or text only mentions it)."""
    scope = Scope.model_validate(entry["item"]["scope"])
    first, last = max(scope.start or trip.start, trip.start), min(scope.end or trip.end, trip.end)
    dates = [str(first + timedelta(days=offset)) for offset in range((last - first).days + 1)]
    people = {}
    for person in entry["people"]:
        days = []
        for day_id in dates:
            alternatives = [plan[person]["status"] for plan in entry["days"].get(day_id, [])]
            days.append(None if not alternatives else True if all(status is True for status in alternatives)
                        else False if False in alternatives else None)
        status = "covered" if True in days else "unknown" if None in days else "missing"
        people[person] = dict(status=status, blocks=list(dict.fromkeys(block_id for day_id in dates for plan in entry["days"].get(day_id, [])
                                                                       for block_id in plan[person]["blocks"])))
    statuses = {result["status"] for result in people.values()}
    return dict(status="passed" if statuses <= {"covered"} else "conflict" if "missing" in statuses else "unverified",
                people=people, blocks=list(dict.fromkeys(block_id for result in people.values() for block_id in result["blocks"])))


def conclusion(check_id: str, status: str, text: str, *, critical: bool = True,
               consequence: str | None = None, alternative: str | None = None, **details) -> dict:
    return dict(id=check_id, status=status, text=text, critical=critical, details=details,
                consequence=consequence if status == "passed" else consequence or "This part of the plan may be infeasible.",
                alternative=alternative if status == "passed" else alternative or "Confirm before acting and retain a simpler fallback.")


def evidence_status(store: Store, owner: str, evidence_ids: list[str], *, day: str, place: str | None,
                    fact: str, at: str, binding: dict | None = None) -> bool:
    for evidence_id in evidence_ids:
        try:
            evidence = store.get("evidence", owner, evidence_id)
        except ValueError:
            continue
        if evidence.get("stale") or evidence.get("synthetic") or evidence["source"].startswith("demo:") or not evidence.get("valid_until"):
            continue
        if aware(evidence["fetched_at"]) > aware(at) or aware(evidence["valid_until"]) <= aware(at):
            continue
        if evidence.get("date") != day or evidence.get("place_id") != place or fact not in evidence.get("facts", []):
            continue
        if evidence.get("binding") is not None and evidence["binding"] != binding:
            continue
        return True
    return False


def fare_totals(trip: Trip, *, day: str | None = None) -> dict:
    totals: dict[str, dict[str, Decimal]] = {person.id: {} for person in trip.people}
    unknown = []
    estimated = []
    for booking in trip.bookings:
        if booking.status == "cancelled":
            continue
        for fare in booking.fares:
            if fare.included_in or fare.alternative or (day and str(fare.date or booking.date) != day):
                continue
            if fare.amount_per_person is None:
                unknown.append(fare.id)
                continue
            if fare.estimated:
                estimated.append(fare.id)
            for person in fare.subject:
                currency_totals = totals[person]
                currency_totals[fare.currency] = currency_totals.get(fare.currency, Decimal(0)) + Decimal(str(fare.amount_per_person))
    return dict(by_person={person: {currency: float(amount) for currency, amount in values.items()} for person, values in totals.items()},
                unknown=unknown, estimated=estimated)


def check_plan(store: Store, owner: str, plan: Trip | dict, *, at: str | None = None) -> dict:
    from tripwright.query import anchor_meaning

    trip = Trip.model_validate(plan)
    serialized = trip.model_dump(mode="json")
    places = {place["id"]: place for place in serialized["places"]}
    ledger = Ledger(store)
    at = at or now()
    checks = []
    fares = {fare.id: fare for booking in trip.bookings for fare in booking.fares}
    bookings = {booking.id: booking for booking in trip.bookings}
    totals = fare_totals(trip)
    checks.append(conclusion("budget:sum", "unverified" if totals["unknown"] or totals["estimated"] else "passed",
                             "Fares are summed per person and currency; included and alternative fares are excluded.", totals=totals))
    for currency, limit in trip.budgets.items():
        group_total = sum(values.get(currency, 0) for values in totals["by_person"].values())
        status = "conflict" if group_total > limit else "unverified" if totals["unknown"] or totals["estimated"] else "passed"
        checks.append(conclusion(f"budget:{currency}", status, f"Group {currency} {group_total:g}; limit {limit:g}"))
    wanted = {}
    for day in trip.days:
        first_day_check = len(checks)
        day_id = str(day.date)
        bundle = ledger.compile(owner, scope=Scope(start=day.date, end=day.date), at=at)
        if not day.plans:
            checks.append(conclusion(f"empty-day:{day_id}", "unverified", "This day has no concrete candidate to check."))
        for candidate in day.plans:
            per_person = {person.id: [] for person in trip.people}
            for track in candidate.tracks:
                for person in track.subject:
                    per_person[person].extend(track.blocks)
                for block in track.blocks:
                    if block.baggage_holder is not None:
                        checks.append(conclusion(f"person:task:{block.id}", "passed" if block.baggage_holder in track.subject else "conflict",
                                                 "A baggage task belongs only in a track containing its assigned holder."))
                    if local_instant(day_id, block.start, trip.timezone) is None or (block.end and local_instant(day_id, block.end, trip.timezone) is None):
                        checks.append(conclusion(f"local-time:{block.id}", "unverified", "This local clock time is ambiguous or nonexistent at a timezone transition."))
                    if block.fare:
                        fare = fares[block.fare]
                        booking = next(booking for booking in trip.bookings if any(row.id == fare.id for row in booking.fares))
                        applicable = (set(track.subject) <= set(fare.subject) and str(fare.date or booking.date) == day_id
                                      and not fare.alternative and booking.status != "cancelled")
                        checks.append(conclusion(f"fare-holder:{block.id}", "passed" if applicable else "conflict",
                                                 "A fare must cover every assigned holder and the planned date."))
                    if block.booking:
                        booking = bookings[block.booking]
                        applicable = set(track.subject) <= set(booking.subject) and str(booking.date) == day_id and booking.status != "cancelled"
                        checks.append(conclusion(f"booking-holder:{block.id}", "passed" if applicable else "conflict",
                                                 "The booking must apply to this date and these people."))
                    obligations = set(block.critical_facts)
                    if block.kind in ("visit", "meal"):
                        obligations.add("opening_hours")
                    if block.kind == "travel" and block.certainty == "fixed" and block.mode != "walk":
                        obligations.add("timetable")
                    if block.booking:
                        obligations.add("ticket_applicability")
                    if outdoor(block.model_dump(mode="json"), places.get(block.place)):
                        obligations.add("weather")
                    for fact in sorted(obligations):
                        passed = evidence_status(store, owner, block.evidence_ids, day=day_id, place=block.place, fact=fact, at=at,
                                                 binding=snapshot(serialized, dict(block_id=block.id, date=day_id)))
                        # where and when the fact is about: a block moved to another time or place is another unknown
                        about = {key: value for key in ("place", "start", "end", "origin", "destination")
                                 if (value := getattr(block, key)) is not None}
                        checks.append(conclusion(f"evidence:{day_id}:{block.id}:{fact}", "passed" if passed else "unverified",
                                                 f"{fact} at the planned branch and date needs current first-hand evidence.", **about))
                    for fact in bundle["facts"]:
                        if not people_overlap(fact["subject"], track.subject) or not overlaps(Scope.model_validate(fact["scope"]), Scope(start=day.date, end=day.date, time_from=block.start, time_to=block.end)):
                            continue
                        key, value = fact["key"], fact["value"]
                        conflict = False
                        if key == "companions" and value == []:
                            conflict = len(track.subject) > 1
                        elif key == "visited" and block.kind == "visit":
                            conflict = block.place in (value if isinstance(value, list) else [value])
                        elif key == "baggage_holder":
                            conflict = block.baggage_holder is not None and block.baggage_holder != value
                        elif key == "equipment" and block.equipment:
                            conflict = not set(block.equipment) <= set(value)
                        if conflict:
                            checks.append(conclusion(f"person:{fact['id']}:{block.id}", "conflict", "Plan contradicts a scoped per-person fact."))
            for person, blocks in per_person.items():
                blocks.sort(key=lambda block: minutes(block.start))
                for previous, following in zip(blocks, blocks[1:]):
                    previous_instant = local_instant(day_id, previous.end or previous.start, trip.timezone)
                    next_instant = local_instant(day_id, following.start, trip.timezone)
                    gap = (next_instant - previous_instant).total_seconds() / 60 if previous_instant and next_instant else None
                    transfer = following.transfer_minutes
                    same_place = previous.place is not None and previous.place == following.place
                    if gap is None:
                        status = "unverified"
                    elif gap < 0:
                        status = "conflict"
                    elif transfer is None and not same_place and previous.kind != "travel" and following.kind != "travel":
                        status = "unverified"
                    else:
                        status = "passed" if gap >= (transfer or 0) else "conflict"
                    checks.append(conclusion(f"chain:{day_id}:{candidate.name}:{person}:{following.id}", status,
                                             f"{person}: {previous.id} → {following.id}; {gap} minutes available.", transfer_minutes=transfer))
            for anchor in day.anchors:
                if anchor.booking:
                    booking = bookings[anchor.booking]
                    applicable = set(anchor.subject) <= set(booking.subject) and str(booking.date) == day_id and booking.status != "cancelled"
                    checks.append(conclusion(f"anchor-booking:{anchor.id}", "passed" if applicable else "conflict",
                                             "The hard-node booking must cover its people and date."))
                # A11: a node done online has no place to reach (query.ANCHOR_MEANINGS)
                for person in [] if anchor_meaning(anchor.model_dump()) == "online_deadline" else anchor.subject:
                    preceding = [block for block in per_person[person] if minutes(block.start) < minutes(anchor.time)]
                    previous = max(preceding, key=lambda block: minutes(block.start), default=None)
                    if previous is None:
                        status, margin = "unverified", None
                    else:
                        travel = 0 if previous.place == anchor.place and anchor.place is not None else anchor.travel_minutes
                        anchor_instant = local_instant(day_id, anchor.time, trip.timezone)
                        previous_instant = local_instant(day_id, previous.end or previous.start, trip.timezone)
                        margin = ((anchor_instant - previous_instant).total_seconds() / 60 - anchor.buffer_minutes - (travel or 0)
                                  if anchor_instant and previous_instant else None)
                        status = "unverified" if margin is None else "conflict" if margin < 0 else "unverified" if travel is None else "passed"
                    checks.append(conclusion(f"anchor:{day_id}:{candidate.name}:{anchor.id}:{person}", status,
                                             f"Hard node {anchor.text}; buffer {anchor.buffer_minutes} minutes.", margin_minutes=margin,
                                             latest_departure=departure_clock(anchor.time, anchor.buffer_minutes, anchor.travel_minutes, day=day_id, zone=trip.timezone),
                                             consequence=anchor.consequence, alternative=anchor.alternative))
                passed = evidence_status(store, owner, anchor.evidence_ids, day=day_id, place=anchor.place, fact="hard_node", at=at,
                                         binding=snapshot(serialized, dict(anchor_id=anchor.id, date=day_id)))
                checks.append(conclusion(f"anchor-evidence:{day_id}:{anchor.id}", "passed" if passed else "unverified",
                                         "Hard-node timetable, branch and date need current evidence.",
                                         consequence=anchor.consequence, alternative=anchor.alternative,
                                         time=anchor.time, **(dict(place=anchor.place) if anchor.place else {})))
            for item in bundle["items"]:
                specification = item.get("check")
                relevant_people = set(per_person) if "*" in item["subject"] else set(item["subject"])
                item_scope = Scope.model_validate(item["scope"])
                in_scope = {person: [block for block in per_person.get(person, [])
                                     if overlaps(item_scope, Scope(start=day.date, end=day.date, time_from=block.start, time_to=block.end, place=block.place or "*"))]
                            for person in relevant_people}
                relevant_blocks = list({id(block): block for blocks in in_scope.values() for block in blocks}.values())
                activity = place_kind(item) if item["kind"] != "rule" and not item.get("condition") else None
                if not specification and activity and item["strength"] == "forbid":
                    found = [block_activity(block, places, activity, place_kinds(activity, satisfying=False)) for block in relevant_blocks]
                    status = "conflict" if True in found else "unverified" if None in found else "passed"
                    checks.append(conclusion(f"item:{item['id']}:{day_id}:{candidate.name}", status,
                                             f"Banned activity '{activity}': no block for these people in this scope may be at a {activity} place; text that only mentions it stays unverified.",
                                             activity=activity, blocks=list(dict.fromkeys(block.id for block, hit in zip(relevant_blocks, found) if hit))))
                    continue
                if not specification and activity:
                    # Coverage is recorded per person and per alternative plan; it is judged once over the scope below.
                    kinds = place_kinds(activity, satisfying=True)
                    covered = {}
                    for person, blocks in in_scope.items():
                        hits = [(block.id, block_activity(block, places, activity, kinds)) for block in blocks]
                        covered[person] = dict(status=True if any(hit is True for _, hit in hits) else None if any(hit is None for _, hit in hits) else False,
                                               blocks=[block_id for block_id, hit in hits if hit is True])
                    entry = wanted.setdefault(item["id"], dict(item=item, activity=activity, people=sorted(relevant_people), days={}))
                    entry["days"].setdefault(day_id, []).append(covered)
                    if item["strength"] == "must":
                        continue
                if not specification:
                    if item["strength"] in ("must", "forbid") and item["kind"] != "rule":
                        checks.append(conclusion(f"item:{item['id']}:{day_id}:{candidate.name}", "unverified", "Hard condition has no deterministic predicate."))
                    continue
                metric = specification.get("metric")
                values = None
                if metric == "walk_minutes":
                    values = [sum(block.walk_minutes or 0 for block in per_person.get(person, []) if block in relevant_blocks) for person in relevant_people]
                    if any(block.kind == "travel" and block.mode == "walk" and block.walk_minutes is None for block in relevant_blocks):
                        values = None
                elif metric == "visit_count":
                    values = [sum(block.kind == "visit" for block in per_person.get(person, []) if block in relevant_blocks) for person in relevant_people]
                elif metric == "budget":
                    values = [totals["by_person"].get(person, {}).get(specification.get("currency"), 0) for person in relevant_people]
                    if totals["unknown"] or totals["estimated"]:
                        values = None
                operations = {"max": lambda actual, expected: actual <= expected,
                              "min": lambda actual, expected: actual >= expected,
                              "equal": lambda actual, expected: actual == expected}
                operation = operations.get(specification.get("op"))
                if values is None or operation is None or item.get("condition") or not isinstance(specification.get("value"), (int, float)):
                    status = "unverified"
                else:
                    status = "passed" if all(operation(value, specification.get("value")) for value in values) else "conflict"
                checks.append(conclusion(f"item:{item['id']}:{day_id}:{candidate.name}", status, item["text"],
                                         critical=item["strength"] in ("must", "forbid"), values=values))
        for pending in bundle["pending_checks"]:
            checks.append(conclusion(f"pending:{pending['id']}", "unverified", pending["question"],
                                     consequence=pending["consequence"], alternative=pending["alternative"], due_at=pending["due_at"]))
        for check in checks[first_day_check:]:
            check["details"]["date"] = day_id
    coverage = {item_id: activity_coverage(trip, entry) for item_id, entry in wanted.items()}
    for item_id, entry in wanted.items():
        if entry["item"]["strength"] != "must":
            continue
        result = coverage[item_id]
        checks.append(conclusion(f"item:{item_id}:activity", result["status"],
                                 f"Required activity '{entry['activity']}': every person of this condition needs a block at a {entry['activity']} place "
                                 "in its scope, in every alternative plan of some day; text that only mentions it stays unverified.",
                                 activity=entry["activity"], blocks=result["blocks"], people=result["people"]))
    for item in ledger.compile(owner, scope=Scope(start=trip.start, end=trip.end), at=at)["items"]:
        if item["strength"] in ("must", "forbid") and item["kind"] != "rule" and not any(check["id"].startswith(f"item:{item['id']}:") for check in checks):
            checks.append(conclusion(f"item:{item['id']}:missing-plan", "unverified", "No candidate covers this hard condition's scope."))
    if not trip.days:
        checks.append(conclusion("empty-trip", "unverified", "No days have been planned yet."))
    return dict(checks=checks, conflicts=[check for check in checks if check["status"] == "conflict" and check["critical"]],
                unverified=[check for check in checks if check["status"] == "unverified"],
                verified=bool(checks) and all(check["status"] == "passed" for check in checks), checked_at=at,
                coverage="Deterministic checks only; external facts without current evidence remain unverified.",
                activity_coverage=coverage)
