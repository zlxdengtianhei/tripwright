from __future__ import annotations

from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError
from pydantic.json_schema import SkipJsonSchema

from tripwright.checks import ACTIVITIES, conclusion, place_kind, place_kinds  # noqa: F401  (registry re-exported for callers)
from tripwright.ledger import Scope, overlaps, people_overlap
from tripwright.trip import Model, input_problems


class Usage(Model):
    """How a candidate used one applicable condition (CONTRACTS 3.1 dispositions use the same shape). The MCP schema of
    propose.usages and the service's check are this one model."""
    item_id: str
    status: Literal["used", "host_reported", "traded_off", "not_applicable"]
    block_ids: list[str] = Field(default_factory=list, description="Blocks that satisfy it; required for used and host_reported.")
    reason: str | None = Field(default=None, description="Required for traded_off and not_applicable.")
    # What the service adds to a disposition; a host may send a version's usages back unchanged.
    verification: SkipJsonSchema[str | None] = None
    reported_status: SkipJsonSchema[str | None] = None
    filled_by: SkipJsonSchema[Literal["service"] | None] = None


USAGES = TypeAdapter(list[Usage])


def checked_usages(usages) -> list[dict]:
    """Usages as sent, refused at the boundary with each problem's path (`usages.0.reason must be a string, got
    object`); values keep the shape the caller sent."""
    try:
        USAGES.validate_python(usages or [])
    except ValidationError as error:
        raise ValueError("Invalid usages: " + "; ".join(input_problems(error, "usages"))) from None
    return list(usages or [])


def applicable(item: dict, day, track, block) -> bool:
    scope = Scope(start=day.date, end=day.date, time_from=block.start, time_to=block.end, place=block.place or "*")
    return people_overlap(item["subject"], track.subject) and overlaps(Scope.model_validate(item["scope"]), scope)


def account_usages(plan, required: dict, usages: list[dict], checks: list[dict], coverage: dict | None = None,
                   optional: dict | None = None) -> list[dict]:
    """`coverage` is the checker's per-person activity coverage (checks.activity_coverage); a used claim for an activity
    is server-checked only when that same result covers every person of the condition. `optional` are applicable
    conditions of the agent's own layer (ledger.offered, S5): a usage sent for one is checked like the others, a missing
    one is no conflict."""
    coverage = coverage or {}
    optional = optional or {}
    blocks = {block.id: (day, track, block) for day in plan.days for candidate in day.plans
              for track in candidate.tracks for block in track.blocks}
    places = {place.id: place.model_dump(mode="json") for place in plan.places}
    if usages is None:
        # Omitted, not an explicit empty list: account for every applicable condition without choosing a winner.
        usages = [dict(item_id=item_id, status="host_reported", block_ids=[], filled_by="service",
                       reason="Service-filled contact only; no preference precedence inferred.") for item_id in required]
    supplied = {}
    for position, usage in enumerate(checked_usages(usages)):
        if usage["item_id"] in supplied:
            raise ValueError(f"usages.{position}.item_id: duplicate usage of {usage['item_id']}")
        if usage["status"] in ("traded_off", "not_applicable") and not usage.get("reason"):
            raise ValueError(f"usages.{position}.reason: a trade-off or exclusion requires its reason")
        supplied[usage["item_id"]] = dict(usage)
    normalized = []
    for item_id in supplied.keys() - required.keys() - optional.keys():
        checks.append(conclusion(f"usage:{item_id}", "conflict", "A reported usage is outside the candidate's applicable conditions."))
        normalized.append(dict(supplied[item_id], status="invalid", reason="Condition is outside this candidate's scope."))
    for item_id, item in [*required.items(), *((item_id, item) for item_id, item in optional.items() if item_id in supplied)]:
        usage = supplied.get(item_id)
        hard = item["strength"] in ("must", "forbid")
        valid = usage is not None and not (hard and usage["status"] == "traded_off")
        predicates = [check for check in checks if check["id"].startswith(f"item:{item_id}:")]
        # The service's own verdict on a condition outranks the host's label: a violated condition is neither used nor
        # inapplicable, whatever the host reports; only an explicit trade-off of a soft condition may stand.
        refuted = [check["id"] for check in predicates if check["status"] == "conflict"]
        if usage and refuted and usage["status"] != "traded_off":
            valid = False
        if usage and usage["status"] in ("used", "host_reported"):
            block_ids = usage.get("block_ids", [])
            matching = []
            for block_id in block_ids:
                location = blocks.get(block_id)
                if location is None:
                    valid = False
                    continue
                day, track, block = location
                if not applicable(item, day, track, block):
                    valid = False
                matching.append(block)
            kind = place_kind(item)
            uncovered = []
            if kind and item["strength"] != "forbid":
                kinds = place_kinds(kind, satisfying=True)
                if matching and not all(block.place and places[block.place]["kind"] in kinds for block in matching):
                    valid = False
                people = (coverage.get(item_id) or {}).get("people", {})
                uncovered = sorted(person for person, result in people.items() if result["status"] != "covered")
                proven = bool(matching) and bool(people) and not uncovered and all(check["status"] == "passed" for check in predicates)
            else:
                proven = bool(predicates) and all(check["status"] == "passed" for check in predicates)
            verified = (valid and bool(block_ids) and not item.get("condition") and proven)
            usage.update(status="used" if verified else "host_reported", verification="server_checked" if verified else "unverified",
                         reported_status="used", block_ids=block_ids)
            if not verified:
                usage["reason"] = usage.get("reason") or (
                    f"宿主自报、未核：no day in scope gives this activity to {', '.join(uncovered)} in every alternative plan." if uncovered else
                    "宿主自报、未核：需要适用的块引用及可判定的活动或条件。")
                checks.append(conclusion(f"usage-proof:{item_id}", "unverified", "宿主自报、未核：the service cannot confirm that this soft condition was satisfied.",
                                         critical=False, uncovered=uncovered))
        if usage and not valid:
            reported = usage.get("reported_status", usage["status"])
            reason = ("The service's own check finds this condition violated: " + ", ".join(refuted) if refuted else
                      "A must or forbid condition cannot be traded off." if usage["status"] == "traded_off" else
                      "Referenced blocks do not satisfy this condition's people, scope or activity kind.")
            usage.update(status="invalid", verification="conflict", reported_status=reported, reason=reason)
        checks.append(conclusion(f"usage:{item_id}", "passed" if valid else "conflict",
                                 "Every applicable condition must be accounted for; used claims need matching blocks and a server-checkable predicate; "
                                 "a host label cannot contradict the service's own check."))
        if usage:
            normalized.append(usage)
    return normalized
