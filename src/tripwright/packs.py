"""Constraint packs (CONTRACTS 3.1, 3.2): which ledger records one read carries, and in what shape.

A narrow pack covers the asker's people and window; a wide pack covers every person, place and channel on the days a
draft changes, for the deep check. Records narrow by people, then date, time, place and channel, never by kind;
principles come along whatever their scope. Everything in scope is required except three kinds, which may be left out
with `over_budget` when the rendered pack does not fit: open checks due after the window ends, goals in a quick or
status pack, and the agent's own soft entries (S5, `ledger.offered`; in a wide pack they only need no disposition).
Pure with respect to the clock: the moment of the read is an argument.

A live pack (a turn's) carries the moment of the read as `header.now` in the trip's time zone; a pack written into the
repository does not, because it is read later. Every hard node says whether its time is an external deadline or the
plan's own target, which plan version has stored it since when, which source last checked it and when, and whether
that is older than the refresh time of its kind (A2).
"""
from __future__ import annotations

import copy
import hashlib
import re
from difflib import SequenceMatcher
from datetime import date as Date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from tripwright.checks import departure_clock
from tripwright.consider import SETTING
from tripwright.evidence import uncounted
from tripwright.ledger import Ledger, Scope, offered, overlaps, people_overlap
from tripwright.locks import unescape
from tripwright.render.pack import pack_meta, render_pack
from tripwright.storage import Store
from tripwright.trip import aware

SCHEMA = "tripwright.pack/1"
KINDS = ("narrow", "wide")
SCENARIOS = ("quick", "status", "replan", "plan", "compare", "requirements", "system")
BUDGETS = {"quick": 1800, "status": 500, "replan": 3000, "first": 3000, "plan": 3000, "compare": 3000, "requirements": 3000,
           "system": 3000, "wide": 8000}
SECTIONS = ["header", "principles", "hard", "facts", "hard_nodes", "decisions", "preferences", "checks", "rules", "omitted", "details"]
GROUPS = ("items", "facts", "decisions", "hard_nodes", "checks_due", "questions")
ITEM_ORDER = ("principle", "hard", "preference", "goal", "rule")
REASONS = ("people", "time", "date", "place", "channel")  # listing order of omitted entries (the schema's enum order)
LIVE = {"items": ("active",), "facts": ("active",), "decisions": ("active", "review"), "pending_checks": ("open",)}
GROUP_OF = {"items": "items", "facts": "facts", "decisions": "decisions", "pending_checks": "checks_due"}
OPTIONAL_GOALS = ("quick", "status")
# How long a stored time is trusted before a pack says it may have changed (A2, recommended defaults): departures and
# other timetable-like times 6 hours, opening hours 7 days. A hard node is a timetable-like time.
REFRESH_HOURS = {"hard_node": 6, "timetable": 6, "opening": 24 * 7}
# An anchor's time is an external deadline (the counter closes, the last train leaves) or the plan's own target with
# margin (be at the counter by then). Anchors that do not say so are "unmarked" unless a source checked their time.
DEADLINES = ("external", "plan")
WORDS = {
    "zh": {"contradiction": "「{first}」和「{second}」出自同一条消息，可能是同一意思的两种读法：两条都算，还是只算一条？",
           "contradiction_default": "两条都照用，直到用户说明", "both": "两条都成立：{first}；{second}",
           "one": "只算一条：{first}，或 {second}", "memory": "长期条目待确认（没答前照用）：{ids}", "memory_default": "照用",
           "join": "、"},
    "en": {"contradiction": "“{first}” and “{second}” come from one message and may be two readings of one "
                            "intention: do both hold, or only one?",
           "contradiction_default": "Keep both until the user says otherwise", "both": "Both hold: {first}; {second}",
           "one": "Only one is meant: {first}, or {second}",
           "memory": "Long-term entries awaiting confirmation (applied until answered): {ids}", "memory_default": "apply",
           "join": ", "},
}


def section(item: dict) -> str:
    if item.get("always"):
        return "principle"
    if item["kind"] == "rule":
        return "rule"
    if item["strength"] in ("must", "forbid") or item["kind"] in ("constraint", "authorization", "stop"):
        return "hard"
    return "goal" if item["kind"] == "goal" else "preference"


def bare_scope(scope: dict | None) -> dict:
    """A record's scope without its open defaults (null bounds, `*` place and channel)."""
    full = Scope.model_validate(scope or {}).model_dump(mode="json")
    return {key: value for key, value in full.items() if value not in (None, "*")}


def out_of_scope(record: dict, people: list[str], window: Scope) -> str | None:
    """The first reason a record lies outside the pack: people, then date, time, place and channel, each on its own."""
    if not people_overlap(record["subject"], people):
        return "out_of_scope:people"
    own = Scope.model_validate(record.get("scope") or {})
    for reason, fields in (("date", ("start", "end")), ("time", ("time_from", "time_to")), ("place", ("place",)),
                           ("channel", ("channel",))):
        if not overlaps(own, Scope(**{field: getattr(window, field) for field in fields})):
            return f"out_of_scope:{reason}"
    return None


def local_day(at: str, zone: str) -> str:
    return aware(at).astimezone(ZoneInfo(zone)).date().isoformat()


def window_end(day: str, to: str | None, zone: str) -> datetime:
    """The window's last instant: `to` on its last day, or the end of that day when `to` is open."""
    if to:
        return datetime.combine(Date.fromisoformat(day), time.fromisoformat(to), tzinfo=ZoneInfo(zone))
    return datetime.combine(Date.fromisoformat(day) + timedelta(days=1), time(0, 0), tzinfo=ZoneInfo(zone))


def optional(record: dict, *, scenario: str, end: datetime | None) -> bool:
    """Not required, so it may leave with `over_budget`: an open check due after the window ends, or a goal in a quick
    or status pack. A window without an end (and every wide pack) has no late checks."""
    if record["type"] == "check":
        return end is not None and aware(record["due_at"]) > end
    return record["type"] == "item" and record["section"] == "goal" and scenario in OPTIONAL_GOALS


def non_required(pack: dict) -> list[str]:
    """The records marked non-required when the pack was compiled, in pack order. They are marked then because the
    header names one date while a whole-trip window spans several, so the window's end cannot be read back from it."""
    return [record_id for group in GROUPS for record_id in pack["required"][group]["ids"]
            if pack["records"].get(record_id, {}).get("non_required")]


def bound_plan(store: Store, owner: str, snapshot: dict) -> dict | None:
    """The committed plan of the snapshot's version: a pack reads the same generation as the rest of its call."""
    version_id = snapshot.get("plan_version_id")
    if version_id is None:
        return None
    for version in store.all("versions", owner):
        if version["id"] == version_id:
            return version["plan"]
    raise ValueError(f"Snapshot plan version {version_id} is not in the store")


def changed_days(before: dict | None, after: dict) -> list[str]:
    old = {str(day["date"]): day for day in (before or {}).get("days", [])}
    new = {str(day["date"]): day for day in after.get("days", [])}
    return sorted(day for day in old.keys() | new.keys() if old.get(day) != new.get(day))


def anchors(plan: dict | None, dates: list[str], people: list[str]) -> list[tuple[str, dict]]:
    found = [(str(day["date"]), anchor) for day in (plan or {}).get("days", []) if str(day["date"]) in dates
             for anchor in day.get("anchors", []) if people_overlap(anchor["subject"], people)]
    return sorted(found, key=lambda entry: (entry[0], entry[1]["time"]))


def message(record: dict, events: dict) -> dict | None:
    """The message a record comes from. An entry imported from an earlier trip names a message that is not in this
    store; its origin carries the text, time, speaker and nature."""
    origin = record.get("origin")
    event_id = (record.get("source") or {}).get("event") or record.get("source_event")
    if origin:
        return dict(event=event_id, text=origin.get("message"), said_at=origin.get("said_at"), speaker=origin.get("speaker"),
                    nature=origin.get("nature"))
    event = events.get(event_id)
    if event is None:
        return None
    return dict(event=event_id, text=event["received"]["text"], said_at=event["receipt"]["received_at"],
                speaker=event["received"].get("speaker"), nature=event["received"]["nature"])


def speaker(record: dict, events: dict) -> str | None:
    found = message(record, events)
    return found["speaker"] if found else None


def pack_record(table: str, record: dict, events: dict, at: str) -> dict:
    source = record["source"]
    common = dict(basis=source["basis"], source_event=source["event"], speaker=speaker(record, events), status=record["status"])
    scope = bare_scope(record.get("scope"))
    if table == "items":
        return dict(type="item", section=section(record), kind=record["kind"], key=record["key"], strength=record["strength"],
                    subject=record["subject"], scope=scope, quote=record["quote"], text=record["text"], reason=record.get("reason"),
                    **common)
    if table == "facts":
        return dict(type="fact", key=record["key"], value=record["value"], text=record["text"], subject=record["subject"],
                    scope=scope, true_from=record["true_from"], **common)
    if table == "decisions":
        # must (D-53): only a must locks its paths; the others are the user's choices the agent may change with a reason.
        return dict(type="decision", question=record["question"], choice=record["choice"], decided_by=record["decided_by"],
                    must=bool(record.get("must")), subject=record["subject"], scope=scope, locked_paths=record["locked_paths"], **common)
    return dict(type="check", question=record["question"], due_at=record["due_at"], overdue=aware(record["due_at"]) <= aware(at),
                responsible=record["responsible"], subject=record["subject"], scope=scope, consequence=record["consequence"],
                alternative=record["alternative"], **common)


def hard_node(day: str, anchor: dict, zone: str, timing: dict | None = None) -> dict:
    buffer = anchor.get("buffer_minutes", 0)
    return dict(type="hard_node", date=day, time=anchor["time"], text=anchor["text"], subject=anchor["subject"],
                place=anchor.get("place"), buffer_minutes=buffer, travel_minutes=anchor.get("travel_minutes"),
                latest_departure=departure_clock(anchor["time"], buffer, anchor.get("travel_minutes"), day=day, zone=zone),
                consequence=anchor.get("consequence"), alternative=anchor.get("alternative"),
                **({"where": anchor["where"]} if anchor.get("where") else {}),
                **({"archived_claims": anchor["archived_claims"]} if anchor.get("archived_claims") else {}), **(timing or {}))


def clock(at: str, zone: str) -> dict:
    """The moment of a read in the trip's time zone: what "now" is for the answer (`weekday` 1 = Monday … 7 = Sunday)."""
    local = aware(at).astimezone(ZoneInfo(zone))
    return dict(at=local.isoformat(timespec="seconds"), date=local.date().isoformat(), time=local.strftime("%H:%M"),
                weekday=local.isoweekday(), timezone=zone)


def committed(versions: list[dict], upto: int | None = None) -> list[dict]:
    """Committed versions, newest first, up to version number `upto`."""
    return sorted((version for version in versions if version.get("plan") and (upto is None or version["number"] <= upto)),
                  key=lambda version: version["number"], reverse=True)


def stored_since(versions: list[dict], day: str, anchor: dict) -> dict | None:
    """The oldest version from which the plan has kept this anchor at this time without a break, and when it was
    committed: the time has been stored, not checked, since then."""
    since = None
    for version in versions:
        found = next((candidate for entry in version["plan"].get("days", []) if str(entry["date"]) == day
                      for candidate in entry.get("anchors", []) if candidate["id"] == anchor["id"]), None)
        if found is None or found["time"] != anchor["time"]:
            break
        since = dict(version=version["number"], at=version.get("committed_at"))
    return since


def last_check(store: Store, owner: str, evidence_ids: list[str]) -> dict | None:
    """The newest source record behind a time (synthetic demo data never counts), whatever its validity."""
    found = []
    for evidence_id in evidence_ids or []:
        try:
            evidence = store.get("evidence", owner, evidence_id)
        except ValueError:
            continue
        if evidence.get("synthetic") or str(evidence.get("source", "")).startswith("demo:") or not evidence.get("fetched_at"):
            continue
        if uncounted(evidence):  # a search snippet, or a quote not on the page (A5)
            continue
        found.append(evidence)
    if not found:
        return None
    evidence = max(found, key=lambda record: aware(record["fetched_at"]))
    return dict(source=evidence["source"], at=evidence["fetched_at"], evidence_id=evidence["id"],
                valid_until=evidence.get("valid_until"), stale=bool(evidence.get("stale")))


def node_timing(store: Store, owner: str, versions: list[dict], day: str, anchor: dict, at: str) -> dict:
    """Where a hard node's time comes from (A2): `deadline` external, plan or unmarked; `stored` {version, at}; `checked`
    {source, at, evidence_id, valid_until, stale} or null; `refresh_hours`; and `may_have_changed` when the last check,
    or without one the storing, is older than the refresh time, or the source itself says it is stale or expired."""
    checked = last_check(store, owner, anchor.get("evidence_ids", []))
    stored = stored_since(versions, day, anchor)
    marked = anchor.get("deadline")
    deadline = marked if marked in DEADLINES else "external" if checked else "unmarked"
    hours = REFRESH_HOURS["hard_node"]
    basis = (checked or {}).get("at") or (stored or {}).get("at")
    changed = basis is None or aware(at) - aware(basis) > timedelta(hours=hours)
    if checked and (checked["stale"] or (checked["valid_until"] and aware(checked["valid_until"]) <= aware(at))):
        changed = True
    return dict(deadline=deadline, stored=stored, checked=checked, refresh_hours=hours, may_have_changed=changed)


# --- A9 (D-64): a stored "末班 / 只剩 / 最晚" sentence is archive, not a current fact -----------------------------------
CLAIM_CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3])[:：]([0-5]\d)(?![\d:])")
ARCHIVE_HOURS = 6  # a lookup this much older than now does not void a stored claim


def claim_minutes(part: str) -> list[int]:
    return [int(match.group(1)) * 60 + int(match.group(2)) for match in CLAIM_CLOCK.finditer(part)]


def place_names(plan: dict | None, place_id: str | None) -> list[str]:
    place = next((place for place in (plan or {}).get("places", []) if place["id"] == place_id), None)
    return [name for name in ((place or {}).get("name"), (place or {}).get("name_local")) if name]


def _iter_ride_legs(value):
    """(origin, destination, departure as ISO or HH:MM) of each ride a transit or latest_departure evidence holds."""
    if isinstance(value, list):  # transit: localized itineraries
        for itinerary in value:
            for leg in (itinerary or {}).get("legs", []):
                if leg.get("mode") != "walk":
                    yield str(leg.get("origin") or ""), str(leg.get("destination") or ""), str(leg.get("depart") or "")
    elif isinstance(value, dict):  # latest_departure: the slim departure rows
        for row in value.get("departures") or []:
            for ride in row.get("rides", []):
                yield str(ride.get("from") or ""), str(ride.get("to") or ""), str(ride.get("depart") or "")


def later_departure(store: Store, owner: str, claim: int, names: list[str], at: str, zone: str) -> str | None:
    """A departure this session's evidence found later than a stored claim's time, toward the anchor's place (A9: the
    stored "last one" is then void). Minutes compare on the evening's dial: up to 12 hours after the claim counts."""
    if not names:
        return None
    since = aware(at) - timedelta(hours=ARCHIVE_HOURS)
    for record in store.all("evidence", owner):
        if record.get("kind") not in ("transit", "latest_departure") or record.get("synthetic") or record.get("stale"):
            continue
        try:
            if aware(record["fetched_at"]) < since:
                continue
        except (KeyError, ValueError):
            continue
        for origin, destination, depart in _iter_ride_legs(record.get("value")):
            if not destination or not any(name.casefold() in destination.casefold() or destination.casefold() in name.casefold()
                                          for name in names):
                continue
            moment = aware(depart) if "T" in depart else None
            if moment is not None:
                local_moment = moment.astimezone(ZoneInfo(zone))
                minute = local_moment.hour * 60 + local_moment.minute
            elif re.match(r"^[0-2]\d:[0-5]\d$", depart):
                minute = int(depart[:2]) * 60 + int(depart[3:5])
            else:
                continue
            if 0 < (minute - claim) % 1440 <= 720:
                return f"{minute // 60:02d}:{minute % 60:02d}"
    return None


def archived_claims(store: Store, owner: str, plan: dict | None, day: str, anchor: dict, timing: dict, at: str, *,
                    language: str = "zh") -> dict:
    """A hard node's stored last/only/late sentences marked as archive (A9, D-64): not current facts. Each claim is
    rendered “计划里写过…（存档，写于 …，没核）” — with “这轮查到更晚的 HH:MM，这条作废” when this session's evidence found a
    later departure toward the anchor's place. Returns the anchor fields to override (text, consequence) plus
    `archived_claims` listing each claim, when it was stored, its source and whether it is void."""
    from tripwright.render.labels import claim_parts, split_claims, stamp_day_clock

    zone = (plan or {}).get("timezone", "UTC")
    written = (timing.get("stored") or {}).get("at") or (timing.get("checked") or {}).get("at")
    source = (timing.get("checked") or {}).get("source")
    names = place_names(plan, anchor.get("place"))
    claims, updates = [], {}
    for field in ("text", "consequence"):
        text = anchor.get(field)
        marked_parts = claim_parts(text or "")
        if not marked_parts:
            continue
        parts = []
        for part in split_claims(text):
            if part not in marked_parts:
                parts.append(part)
                continue
            claim = part.strip("。； （）")
            times = claim_minutes(claim)
            later = later_departure(store, owner, max(times), names, at, zone) if times else None
            claims.append(dict(field=field, claim=claim, written_at=written, source=source, superseded=later))
            when = f"，写于 {stamp_day_clock(written, zone)}" if written else ""
            void = f"；这轮查到更晚的 {later}，这条作废" if later else ""
            marked = (f"计划里写过“{claim}”（存档{when}，没核{void}）" if language == "zh"
                      else f"The plan stored \"{claim}\" (archived{', written ' + stamp_day_clock(written, zone) if written else ''}"
                           f", not checked{'; a later one at ' + later + ' was found this session: void' if later else ''})")
            parts.append(marked + ("。" if part.endswith("。") else ""))
        # an English sentence after a part with no closing space (the stored text is often Chinese, “…回 Bad Ischl”)
        updates[field] = "".join(parts) if language == "zh" else " ".join(part.strip() for part in parts if part.strip())
    if claims:
        updates["archived_claims"] = claims
    return updates


def rank(subject: list[str], people: list[str]) -> tuple:
    """Records of the header's first person first, then shared ones, then the next person's."""
    if "*" in subject:
        return tuple(range(len(people)))
    return tuple(sorted(people.index(person) if person in people else len(people) for person in subject))


def orders(items: list[tuple[str, dict]], profile: dict) -> list[dict]:
    """Pairs of required preferences of the same people whose scopes overlap. Without a user order the planner must
    give options when they compete (CONTRACTS 3.1)."""
    stated = [entry for entry in profile.get("concession_order") or [] if isinstance(entry, dict) and entry.get("order")]
    found = []
    for index, (first_id, first) in enumerate(items):
        for second_id, second in items[index + 1:]:
            if set(first["subject"]) != set(second["subject"]):
                continue
            if not overlaps(Scope.model_validate(first["scope"]), Scope.model_validate(second["scope"])):
                continue
            user = next((entry for entry in stated if {first_id, second_id} <= set(entry["order"])), None)
            if user:
                order = [record_id for record_id in user["order"] if record_id in (first_id, second_id)]
                found.append(dict(between=[first_id, second_id], status="user", order=order, source_event=user.get("source_event")))
            else:
                found.append(dict(between=[first_id, second_id], status="none", order=None, source_event=None))
    return found


def contradiction(notice: dict, items: dict, w: dict) -> tuple[str, dict]:
    first, second = (items[item_id] for item_id in notice["item_ids"])
    record = dict(type="question", kind="possible_contradiction",
                  question=w["contradiction"].format(first=first["quote"], second=second["quote"]),
                  default=w["contradiction_default"], item_ids=list(notice["item_ids"]),
                  readings=[w["both"].format(first=first["text"], second=second["text"]),
                            w["one"].format(first=first["text"], second=second["text"])],
                  status="open")
    return f"PC-{notice['item_ids'][0]}-{notice['item_ids'][1]}", record


def undecided(question: dict, events: dict) -> dict:
    return dict(type="question", kind="undecided", question=question["question"], default=question.get("default"),
                options=question.get("options", []), item_ids=question.get("item_ids", []), subject=question["subject"],
                scope=bare_scope(question.get("scope")), source_event=question.get("source_event"),
                speaker=speaker(question, events), status=question["status"])


def details(plan: dict | None, folder: str | None, dates: list[str], nodes: list[tuple[str, dict]], decisions: list[dict]) -> list[dict]:
    prefix = f"{folder}/plan" if folder else "plan"
    where = {block["id"]: (str(day["date"]), block) for day in (plan or {}).get("days", []) for candidate in day.get("plans", [])
             for track in candidate.get("tracks", []) for block in track.get("blocks", [])}
    locked = []  # blocks a must holds (D-53); the user's other decisions lock nothing
    for decision in decisions:
        if not decision.get("must"):
            continue
        for path in decision.get("locked_paths") or {}:
            parts = path.split("/")
            if len(parts) > 2 and parts[1] == "blocks" and unescape(parts[2]) in where and unescape(parts[2]) not in locked:
                locked.append(unescape(parts[2]))
    by_day: dict[str, list[str]] = {}
    for day, anchor in nodes:
        by_day.setdefault(day, []).append(anchor["id"])
    for block_id in locked:
        by_day.setdefault(where[block_id][0], []).append(block_id)
    entries = [dict(path=f"{prefix}/days/{day}.yaml", ids=ids) for day, ids in sorted(by_day.items(), key=lambda entry: (
        entry[0] not in dates, entry[0]))]
    places = []
    for place in [anchor.get("place") for _, anchor in nodes] + [where[block_id][1].get("place") for block_id in locked]:
        if place and place not in places:
            places.append(place)
    if places:
        entries.append(dict(path=f"{prefix}/places.yaml", ids=places))
    return entries


def view_records(store: Store, owner: str, *, at: str) -> dict[str, dict]:
    """Every ledger record of the owner, whatever its status, in the pack-record shape, by ID: what the front screens
    and views read as `state["records"]` (they filter by status themselves)."""
    events = {event["id"]: event for event in store.all("events", owner) if "received" in event}
    carried = Ledger(store).carried(owner)
    records = {}
    for table in LIVE:
        for record in store.all(table, owner):
            if table == "items" and record["kind"] == SETTING:
                continue  # this trip's sources and notes: consider.sources shows them
            records[record["id"]] = pack_record(table, record, events, at)
            if table == "items" and (carried.get(record["id"]) or {}).get("status") == "pending":
                records[record["id"]]["memory"] = "pending"
    for question in store.all("undecided", owner):
        records[question["id"]] = undecided(question, events)
    return records


def build(store: Store, owner: str, *, kind: str, scenario: str, pack_id: str, snapshot: dict, at: str,
          subject: list[str] | None = None, scope: dict | None = None, trip_folder: str | None = None,
          candidate: str | None = None, language: str = "zh", budget: int | None = None, live: bool = False) -> dict:
    """A `tripwright.pack/1` pack (CONTRACTS 3.1). A wide pack's `full_quotes` are v1.1 objects
    `{event, text, said_at, speaker, nature}`, which the renderer reads as they are. `live` marks a read answered now:
    its header carries `now`."""
    return compile_pack(store, owner, kind=kind, scenario=scenario, pack_id=pack_id, snapshot=snapshot, at=at, subject=subject,
                        scope=scope, trip_folder=trip_folder, candidate=candidate, language=language, budget=budget, live=live)


def compile_pack(store: Store, owner: str, *, kind: str, scenario: str, pack_id: str, snapshot: dict, at: str,
                 subject: list[str] | None = None, scope: dict | None = None, trip_folder: str | None = None,
                 candidate: str | None = None, language: str = "zh", budget: int | None = None, whole_day: bool = False,
                 live: bool = False) -> dict:
    if kind not in KINDS:
        raise ValueError(f"Unknown pack kind {kind!r}; use narrow or wide")
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown pack scenario {scenario!r}; known: {', '.join(SCENARIOS)}")
    language = "en" if language == "en" else "zh"
    w = WORDS[language]
    ledger = Ledger(store)
    plan = bound_plan(store, owner, snapshot)
    zone = plan["timezone"] if plan else "UTC"
    everyone = [person["id"] for person in plan["people"]] if plan else ["*"]
    today = local_day(at, zone)
    if kind == "wide":
        if not candidate:
            raise ValueError("A wide pack needs the candidate draft it checks")
        dates = changed_days(plan, store.get("drafts", owner, candidate)["plan"]) or [today]
        day = dates[0]
        people, place, channel = everyone, "*", "*"
        start = end = close = None  # a wide pack leaves nothing out over budget
        window = Scope(start=dates[0], end=dates[-1])
    else:
        given = Scope.model_validate(scope or {})
        trip_days = sorted(str(entry["date"]) for entry in (plan or {}).get("days", []))
        people = everyone if not subject or "*" in subject else list(subject)
        place = (scope or {}).get("place") or "*"
        channel = (scope or {}).get("channel") or "conversation"
        start, end = given.time_from, given.time_to
        if given.start or today in trip_days:
            # One day: the day asked about, or today while the trip runs.
            day = first = last = given.start.isoformat() if given.start else today
            dates = [day]
            if start is None and end is None and not whole_day and day == today:
                start = aware(at).astimezone(ZoneInfo(zone)).strftime("%H:%M")
                latest = max((anchor["time"] for _, anchor in anchors(plan, dates, people)), default=None)
                end = latest if latest and latest > start else None
        elif plan:
            # Before or after the trip, or on a day it does not plan: today's window would hide every dated record.
            day, first, last = str(plan["start"]), str(plan["start"]), str(plan["end"])
            dates = [entry for entry in trip_days if first <= entry <= last]
        else:
            day, first, last, dates = today, None, None, [today]
        window = Scope(start=first, end=last, time_from=start, time_to=end, place=place, channel=channel)
        close = window_end(last, end, zone) if last else None

    view = ledger.current(owner, at=at)
    events = {event["id"]: event for event in store.all("events", owner) if "received" in event}
    carried = ledger.carried(owner)
    kept: dict[str, list] = {group: [] for group in GROUPS}
    omitted = []
    for table_rank, table in enumerate(LIVE):
        for index, record in enumerate(view[table]):
            if record.get("status") not in LIVE[table] or (table == "items" and record["kind"] == SETTING):
                continue  # a setting (this trip's sources and notes, M1) constrains no plan: consider lists it
            principle = table == "items" and record.get("always")
            reason = None if principle else out_of_scope(record, people, window)
            if reason:
                omitted.append(((REASONS.index(reason.split(":")[1]), table_rank, index), dict(id=record["id"], reason=reason)))
                continue
            if table == "items":
                key = (ITEM_ORDER.index(section(record)), rank(record["subject"], people), index)
            elif table == "pending_checks":
                key = (aware(record["due_at"]), index)
            else:
                key = (rank(record["subject"], people), index)
            kept[GROUP_OF[table]].append((key, record))
    stored = {group: [record for _, record in sorted(entries, key=lambda entry: entry[0])] for group, entries in kept.items()}

    records: dict[str, dict] = {}
    origins: dict[str, dict] = {}  # store record behind each pack record that cites a message
    required = {group: [] for group in GROUPS}
    for table, group in GROUP_OF.items():
        for record in stored[group]:
            records[record["id"]] = pack_record(table, record, events, at)
            origins[record["id"]] = record
            required[group].append(record["id"])
    pending = [item["id"] for item in stored["items"] if (carried.get(item["id"]) or {}).get("status") == "pending"]
    for item_id in pending:
        records[item_id]["memory"] = "pending"
    nodes = anchors(plan, dates, people)
    versions = committed(store.all("versions", owner), snapshot.get("plan_version"))
    for node_day, anchor in nodes:
        timing = node_timing(store, owner, versions, node_day, anchor, at)
        anchor = dict(anchor, **archived_claims(store, owner, plan, node_day, anchor, timing, at, language=language))
        records[anchor["id"]] = hard_node(node_day, anchor, zone, timing)
        required["hard_nodes"].append(anchor["id"])
    for question in view["undecided"]:
        if question.get("status") == "open" and out_of_scope(question, people, window) is None:
            records[question["id"]] = undecided(question, events)
            origins[question["id"]] = question
            required["questions"].append(question["id"])
    current = {item["id"]: item for item in view["items"]}
    for notice in ledger.possible_contradictions(owner, stored["items"]):
        if all(item_id in current for item_id in notice["item_ids"]):
            question_id, record = contradiction(notice, current, w)
            records[question_id] = record
            required["questions"].append(question_id)
    if pending:
        question_id = f"MEM-{plan['id'] if plan else 'trip'}"
        records[question_id] = dict(type="question", kind="memory_pending", question=w["memory"].format(ids=w["join"].join(pending)),
                                    default=w["memory_default"], item_ids=pending, status="open")
        required["questions"].append(question_id)
    # Records in group order, as the reader meets them.
    records = {record_id: records[record_id] for group in GROUPS for record_id in required[group]}
    # S5 (METHOD 3.2): the agent's own soft entries (ledger.offered) are not required: in a narrow pack they leave
    # first when it does not fit, before the user's are cut to ID lines; in a wide pack they need no disposition.
    offers = [record_id for record_id in required["items"] if offered(origins[record_id])]
    if kind == "narrow":
        for record_id in required["items"] + required["checks_due"]:
            if optional(records[record_id], scenario=scenario, end=close) or record_id in offers:
                records[record_id]["non_required"] = True
        for record_id in required["questions"]:  # a possible contradiction between two of the agent's own readings
            if records[record_id].get("item_ids") and set(records[record_id]["item_ids"]) <= set(offers):
                records[record_id]["non_required"] = True
    else:
        for record_id in offers:
            records[record_id]["non_required"] = True

    # the order between preferences is the user's to give (METHOD 4 three): the agent's own entries make no pair (S5)
    preferences = [(item["id"], item) for item in stored["items"] if section(item) == "preference" and item["id"] not in offers]
    wide = kind == "wide"
    quotes = None
    if wide:
        found = {}
        for record_id in records:
            said = message(origins[record_id], events) if record_id in origins else None
            if said and said["text"] and said["event"] not in found:
                found[said["event"]] = said
        earliest = datetime.min.replace(tzinfo=timezone.utc)
        quotes = dict(sorted(found.items(), key=lambda entry: (aware(entry[1]["said_at"]) if entry[1]["said_at"] else earliest, entry[0])))
    return dict(
        schema=SCHEMA, pack_id=pack_id, kind=kind, scenario=scenario,
        header=dict(people=people, window={"date": day, "from": start, "to": end, "timezone": zone}, place=place,
                    channel=channel, compiled_at=at, **(dict(now=clock(at, zone)) if live else {})),
        snapshot=copy.deepcopy(snapshot),
        required={group: dict(status="complete", ids=ids, reason=None) for group, ids in required.items()},
        records=records,
        orders=orders(preferences, view["profile"]),
        omitted=[entry for _, entry in sorted(omitted, key=lambda entry: entry[0])],
        details=details(plan, trip_folder, dates, nodes, [records[record_id] for record_id in required["decisions"]]),
        candidate=dict(draft_id=candidate, budget="excluded") if candidate else None,
        full_quotes=quotes,
        dispositions=[dict(item_id=item_id, status="unaddressed") for item_id in required["items"] if item_id not in offers] if wide else None,
        digest=dict(sections=SECTIONS + (["full_quotes", "dispositions"] if wide else []),
                    budget_tokens=budget or BUDGETS["wide" if wide else scenario], writing_rules=language))


def rendered(pack: dict) -> dict:
    """The renderer reads v1.1 `full_quotes` objects directly; a field a message lacks is written as unknown."""
    return render_pack(pack)


def without(pack: dict, record_ids: list[str]) -> dict:
    final = copy.deepcopy(pack)
    for group in final["required"].values():
        group["ids"] = [record_id for record_id in group["ids"] if record_id not in record_ids]
    for record_id in record_ids:
        final["records"].pop(record_id, None)
        final["omitted"].append(dict(id=record_id, reason="over_budget"))
    return final


def shortened(pack: dict, out: dict) -> bool:
    """Whether the renderer could not fit the whole pack: it dropped records to ID-only lines or reported an overrun.
    Its token figure is the one after those drops, so it cannot tell this; the budget changes nothing else in the text."""
    unbounded = rendered(dict(pack, digest=dict(pack["digest"], budget_tokens=10 ** 12)))
    return out["markdown"] != unbounded["markdown"]


def present(pack: dict) -> dict:
    """Render a pack for the host: `{pack, markdown, tokens, required, meta}`. When the whole pack does not fit, the
    non-required records leave first, all at once, listed as `over_budget`; only then does the renderer shorten
    required records to ID-only lines."""
    out = rendered(pack)
    final = pack
    optional = non_required(pack) if pack["kind"] == "narrow" else []  # a wide pack leaves nothing out over budget
    if optional and shortened(pack, out):
        final = without(pack, optional)
        out = rendered(final)
    return dict(pack=final, markdown=out["markdown"], tokens=out["tokens"], required=out["required"],
                meta=pack_meta(final, out["required"]))


def day_pack_id(day: str, snapshot: dict) -> str:
    """Derived from the day and the content key, so regenerating the same commit gives the same ID."""
    digest = hashlib.sha256(f"{day}{snapshot['plan_version_id']}{snapshot['ledger_digest']}".encode()).hexdigest()[:6]
    return f"CTX-{day[2:4]}{day[5:7]}{day[8:10]}-{digest}"


def day_packs(store: Store, owner: str, *, snapshot: dict, at: str, trip_folder: str | None, language: str = "zh") -> dict[str, dict]:
    """`{date: pack}` for every day of the committed plan, for hosts without tools: everyone, the whole day, the
    conversation channel. Each pack is the one `present` settles on, so an over-budget day already lists what it left out."""
    plan = bound_plan(store, owner, snapshot)
    packs = {}
    for day in sorted(str(entry["date"]) for entry in (plan or {}).get("days", [])):
        pack = compile_pack(store, owner, kind="narrow", scenario="plan", pack_id=day_pack_id(day, snapshot), snapshot=snapshot, at=at,
                            scope={"start": day, "channel": "conversation"}, trip_folder=trip_folder, language=language,
                            whole_day=True)
        packs[day] = present(pack)["pack"]
    return packs


# --- A12: the stored facts behind each point of this question ---------------------------------------------------------------
# quality judge 6: the first part was the plan's hard nodes whatever was asked (“饿了、想看万圣节、是不是要赶去机场” got the
# same airport template as “最晚几点去机场”). The host reads the question (D-08) and passes its points; for each point the
# service gives what it stores: an earlier lookup of tonight to that place, the hard nodes there, the stored tickets,
# the places to eat its stored hours have open near the user, the places the host named.
POINT_KM = 2.0  # places to eat within this distance of where the user is
FOOD_KINDS = ("food", "restaurant", "cafe", "café", "bakery", "supermarket", "snack", "fast_food", "imbiss", "meal")


def short_name(place: dict, language: str, plan: dict | None = None) -> str:
    """A place as one line names it: its Chinese name before any “（…）” for a Chinese answer, else its local name. When
    another place of the trip has the same short name (A14 replay: two gyms of one chain), its local name before the
    address."""
    from tripwright.query import name_head

    def short(entry):
        return re.split(r"[（(]", entry.get("name") or "")[0].strip() if language == "zh" else ""

    mine = short(place)
    if mine and not any(other["id"] != place["id"] and short(other) == mine for other in (plan or {}).get("places", [])):
        return mine
    return name_head(place.get("name_local") or "") or place.get("name_local") or place.get("name") or place["id"]


def asker(plan: dict | None, speaker: str | None = None, subject: list[str] | None = None) -> str | None:
    """The person a question is about: the speaker, else the one person of `subject`, else the trip's first person (the
    one the trip is kept for)."""
    people = [person["id"] for person in (plan or {}).get("people", [])]
    if speaker in people:
        return speaker
    if subject and len(subject) == 1 and subject[0] in people:
        return subject[0]
    return people[0] if people else None


def clock_minutes(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:5])


def missed_leg(plan: dict | None, day: str, at_clock: str, person: str | None, language: str) -> dict | None:
    """The leg the user has just missed and what the plan stores for it (2026-11-12): in the followed plan (the day's
    first), the person's travel block with an origin and a destination that left in the last 30 minutes; then its stored
    fallback, the alternative its author wrote on the hard node of that departure (the default sentence does not count:
    keeping an earlier departure is no answer once it is missed), else the same leg leaving later in another of the
    day's plans. None when either is missing: a first part that only names the missed train answers nothing."""
    from tripwright.trip import Anchor

    entry = next((entry for entry in (plan or {}).get("days", []) if str(entry["date"]) == day), None)
    if not entry or not entry.get("plans"):
        return None
    now = clock_minutes(at_clock)

    def legs(candidate):
        return [block for track in candidate.get("tracks", []) if not person or person in track.get("subject", [])
                for block in track.get("blocks", []) if block.get("kind") == "travel" and block.get("start")
                and block.get("origin") and block.get("destination")]

    gone = [block for block in legs(entry["plans"][0]) if 0 <= now - clock_minutes(block["start"]) <= 30]
    if not gone:
        return None
    leg = max(gone, key=lambda block: clock_minutes(block["start"]))
    default = Anchor.model_fields["alternative"].default
    written = next((node["alternative"] for node in entry.get("anchors", []) if node.get("time") == leg["start"]
                    and (not person or person in node.get("subject", [])) and node.get("alternative")
                    and node["alternative"] != default), None)
    later = sorted((block for candidate in entry["plans"][1:] for block in legs(candidate)
                    if (block["origin"], block["destination"]) == (leg["origin"], leg["destination"])
                    and clock_minutes(block["start"]) > clock_minutes(leg["start"])), key=lambda block: clock_minutes(block["start"]))
    if written is None and not later:
        return None
    places = {place["id"]: place for place in (plan or {}).get("places", [])}

    def named(block):
        ends = [short_name(places[end], language, plan) if end in places else end for end in (block["origin"], block["destination"])]
        return dict(start=block["start"], line=block.get("line"), origin=ends[0], destination=ends[1])

    return dict(leg=named(leg), later=named(later[0]) if later else None, written=written)


def plan_place(plan: dict | None, day: str, at_clock: str, person: str | None = None) -> str | None:
    """Where the followed plan (the day's first) has `person` at `at_clock`, from that person's own blocks (A14, quality
    judge 7: the group's lunch, 11:40—13:30, was taken at 21:01 while the user's own line had the gym, 17:30—21:45): the
    block covering the moment (a block on the way: where it starts), else the latest block begun before it (a finished
    way: where it ends, the person arrived). None when the plan has no block of that person by then."""
    entry = next((entry for entry in (plan or {}).get("days", []) if str(entry["date"]) == day), None)
    if not entry or not entry.get("plans"):
        return None
    now = clock_minutes(at_clock)
    blocks = []
    for track in entry["plans"][0].get("tracks", []):
        if person and person not in track.get("subject", []):
            continue
        for block in track.get("blocks", []):
            if not block.get("start"):
                continue
            start = clock_minutes(block["start"])
            end = clock_minutes(block["end"]) if block.get("end") else start
            blocks.append((start, end + 1440 if end < start else end, block))
    begun = [entry for entry in blocks if entry[0] <= now]
    covering = [entry for entry in begun if now < entry[1]]
    if covering:
        block = max(covering, key=lambda entry: entry[0])[2]
        return block.get("place") or block.get("origin") or block.get("destination")
    if not begun:
        return None
    block = max(begun, key=lambda entry: (entry[1], entry[0]))[2]
    return block.get("place") or block.get("destination") or block.get("origin")


# A16 (quality judge 8): one run passed its own English rendering of the stored gym's name as the place and its first part
# said “还不确定你在哪”; the other passed the ID. The service resolves the host's place or the user's words against the
# stored names.
NAME_LIKE = 0.68  # how alike (difflib ratio of the letters) typed words and a stored name must be to be taken as it
NAME_AHEAD = 0.15  # and how far ahead of the next place's name
LATIN_RUN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿß][A-Za-zÀ-ÖØ-öø-ÿß'’.\- ]*[A-Za-zÀ-ÖØ-öø-ÿß]")


def letters(text: str) -> str:
    return re.sub(r"[^0-9a-zà-öø-ÿß]+", "", text.casefold())


def stored_names(place: dict) -> list[str]:
    """A place's names as a user may say them: its Chinese name before any “（…）”, its local name before the address."""
    from tripwright.query import name_head

    return [name for name in dict.fromkeys([re.split(r"[（(]", place.get("name") or "")[0].strip(),
                                            name_head(place.get("name_local") or "")]) if name]


def resolve_place(plan: dict | None, place: str | None, said: str | None = None) -> dict | None:
    """The trip's place a point names: its `place` as an ID or stored name (match_place); else the one place whose stored
    name the place or the words contain, or whose name their Latin words come close to (NAME_LIKE, NAME_AHEAD over the
    next: “Boulder Hall South” for “Boulderhalle Süd”). None when none or several fit: a guess is never taken."""
    from tripwright.query import match_place

    found = match_place(plan, place) if place else None
    if found:
        return found
    # Point resolution accepts every stored place, even one that has no coordinates for a transit lookup yet.
    if place:
        said_name = re.sub(r"\s+", " ", place.casefold()).strip()
        exact = [entry for entry in (plan or {}).get("places", [])
                 if said_name in {re.sub(r"\s+", " ", str(value).casefold()).strip()
                                  for value in (entry["id"], entry.get("name"), entry.get("name_local")) if value}]
        if len(exact) == 1:
            return exact[0]
    places = [entry for entry in (plan or {}).get("places", []) if entry.get("transit_id") or entry.get("coords")]
    texts = [text for text in (place, said) if text]
    held = {entry["id"]: entry for entry in places for name in stored_names(entry) for text in texts
            if (len(letters(name)) >= 4 and letters(name) in letters(text)) or (not name.isascii() and len(name) >= 3 and name in text)}
    if held:
        return next(iter(held.values())) if len(held) == 1 else None
    runs = [letters(run) for text in texts for run in LATIN_RUN.findall(text) if len(letters(run)) >= 5]
    scored = sorted(((max((SequenceMatcher(None, run, letters(name)).ratio() for run in runs for name in stored_names(entry)
                           if letters(name)), default=0), entry["id"]) for entry in places), reverse=True)
    if scored and scored[0][0] >= NAME_LIKE and (len(scored) == 1 or scored[0][0] - scored[1][0] >= NAME_AHEAD):
        return next(entry for entry in places if entry["id"] == scored[0][1])
    return next(filter(None, (aliased(places, text) for text in texts)), None)


# S4h-a (stage 4 exit judge, mcponly_r1 question 1): the host sent the place “airport”, the trip stores “维也纳机场 / Flughafen
# Wien”; nothing matched and the first part had only “几点出发要查”. A word for a kind of place, in any of these languages,
# names the one stored place of that kind (its kind, else a word of its short names).
PLACE_WORDS = {
    "airport": ("airport", "机场", "機場", "空港", "공항", "flughafen", "aéroport", "aeroport", "aeropuerto", "aeroporto", "luchthaven",
                "lufthavn", "flygplats", "lentoasema", "lotnisko", "letiště", "letisko", "repülőtér", "aerodrom", "havalimanı"),
    "station": ("railway station", "train station", "main station", "火车站", "火車站", "中央车站", "hauptbahnhof", "bahnhof",
                "hbf", "gare", "estación", "stazione", "dworzec", "nádraží", "pályaudvar", "駅"),
}


def named_kind(text: str) -> str | None:
    """The one kind of place a text names with a word of PLACE_WORDS (a Latin word on its own, not inside another)."""
    folded = text.casefold()
    kinds = {kind for kind, words in PLACE_WORDS.items() for word in words
             if (re.search(r"(?<![a-zà-öø-ÿß])" + re.escape(word) + r"(?![a-zà-öø-ÿß])", folded) if word.isascii() else word in folded)}
    return next(iter(kinds)) if len(kinds) == 1 else None


def aliased(places: list[dict], text: str) -> dict | None:
    """The stored place a word for its kind names (named_kind): the one place of that kind, else the one whose short
    name (stored_names: not the address, not a note in brackets, so no shop inside the airport) holds such a word."""
    kind = named_kind(text)
    if kind is None:
        return None
    typed = [place for place in places if str(place.get("kind") or "").casefold() == kind]
    named = typed or [place for place in places if any(named_kind(name) == kind for name in stored_names(place))]
    return named[0] if len(named) == 1 else None


def place_conflicts(plan: dict | None, points: list[dict]) -> list[str]:
    """Each point whose `place` is one stored place while its `said` names another (S4h-a, ns_on_r1 question 2: the user
    said “Block Factory”, the host sent the plan's gym, and the answer had the user there): `points.N: …`, with what to
    send instead. Words that name no stored place, or several, agree with any place."""
    found = []
    for index, point in enumerate(points):
        if not point.get("place") or point["kind"] not in ("at", "go", "do"):
            continue
        given, said = resolve_place(plan, point["place"]), resolve_place(plan, None, point["said"])
        if given and said and given["id"] != said["id"]:
            found.append(f"points.{index}: place {point['place']!r} is the stored {given['id']!r} ({given.get('name_local') or given['name']}), "
                         f"but its said “{point['said']}” names the stored {said['id']!r} ({said.get('name_local') or said['name']}): "
                         f"send place {said['id']!r} when that is the place the user means (the user's words decide), else leave "
                         f"place out; never the plan's place in place of what the user said")
    return found


def stored_destination(plan: dict | None, anchors: list[dict], word: str | None) -> dict | None:
    """The place a go point names when its `place` is no place of the trip (a12b replay: “机场”): the one place the
    upcoming hard nodes go to or are at whose stored name holds that word; None when there is none or several."""
    if not word:
        return None
    named = {anchor.get(key) for anchor in anchors for key in ("place", "destination") if anchor.get(key)}
    found = [place for place in (plan or {}).get("places", []) if place["id"] in named
             and word.casefold() in f"{place.get('name', '')} {place.get('name_local', '')}".casefold()]
    return found[0] if len(found) == 1 else None


def planned_train(plan: dict | None, anchors: list[dict], place: dict, now: dict, language: str) -> dict | None:
    """The plan's next stored train to `place` (a boarding node with a line), as the first part quotes it, archived (A14:
    the first line gives what the archive can give before part 2's lookup; A16: with the stop and date, for planned_way)."""
    from tripwright.render.first_part import POINT_WORDS, WORDS, day_word

    found = min((anchor for anchor in anchors if anchor.get("line") and anchor.get("destination") == place["id"]),
                key=lambda anchor: (anchor["date"], anchor["time"]), default=None)
    if found is None:
        return None
    stop = next((entry for entry in (plan or {}).get("places", []) if entry["id"] == found.get("place")), None)
    said = POINT_WORDS.get(language, POINT_WORDS["en"])
    when = said["tonight"] if found["date"] == now["date"] and found["time"] >= "17:00" else \
        day_word(found["date"], found["time"], now["date"], WORDS.get(language, WORDS["en"]))
    return dict(when=when, time=found["time"], line=found["line"], stop=(stop or {}).get("name_local") or found.get("place") or "",
                place=found.get("place"), date=found["date"])


def planned_way(plan: dict | None, train: dict | None, where: dict | None, now: dict, person: str | None,
                language: str) -> dict | None:
    """The plan's own way to the stored train (A16, quality judge 8: “计划里是今晚 HH:MM … 坐 <车次>” gave no time to
    leave and no way to the station, against the user's rule “先在对话里给现在怎么走”): the asker's travel block on the
    train's day that ends at its stop, starts where the user is (said or assumed) and has not begun; when it starts and
    ends, from where, and how (its line, else its stored way up to the first “；”). None when the plan stores none."""
    if not train or not where or not where.get("place"):
        return None
    entry = next((day for day in (plan or {}).get("days", []) if str(day["date"]) == train["date"]), None)
    blocks = [block for option in (entry or {}).get("plans", [])[:1] for track in option.get("tracks", [])
              if not person or person in track.get("subject", []) for block in track.get("blocks", [])]
    found = [block for block in blocks if block.get("kind") == "travel" and block.get("destination") == train["place"]
             and block.get("origin") == where["place"] and block.get("start")
             and (train["date"] != now["date"] or block["start"] >= now["time"])]
    if not found:
        return None
    block = min(found, key=lambda block: block["start"])
    how = block.get("line") or re.split(r"[；;]", block.get("mode") or "")[0].strip() or None
    return dict(leave=block["start"], arrive=block.get("end"), origin=where.get("name") or where["place"], how=how)


def stored_frame(store: Store, owner: str, destination: str, at: str, zone: str) -> dict | None:
    """The newest lookup of tonight's last connection to `destination` whose night is not over and whose named ride
    still lies ahead (latest_departure's frame keeps what it told, A12), as the first part may quote it: when it was
    looked up, from where, all night or not, the last train or tonight's last ride. Synthetic, stale and uncounted
    records never count."""
    now_at, tz, found = aware(at), ZoneInfo(zone), []
    for record in store.all("evidence", owner):
        if record.get("kind") != "latest_departure" or record.get("synthetic") or record.get("stale") or uncounted(record):
            continue
        told = ((record.get("value") or {}).get("frame") or {}).get("told")
        if not told or told.get("destination") != destination or aware(told["until"]) <= now_at or aware(record["fetched_at"]) > now_at:
            continue
        ride = told.get("rail") or told.get("last") or (told.get("gap") or {}).get("before")
        if not ride or aware(ride.get("depart_at") or ride["at"]) <= now_at:
            continue
        found.append((aware(record["fetched_at"]), record["id"], told, ride))
    if not found:
        return None
    fetched, evidence_id, told, ride = max(found, key=lambda entry: entry[0])
    return dict(evidence_id=evidence_id, fetched=fetched.astimezone(tz).strftime("%H:%M"), origin=told.get("origin"),
                all_night=told["all_night"], train=bool(told.get("rail")), gap=told.get("gap"),
                ride=dict(time=ride.get("depart") or ride.get("time"), line=ride.get("line"), stop=ride.get("from") or ride.get("stop"),
                          leave=ride.get("leave"), lines=ride.get("lines"), last_from=ride.get("last_from")))


# Words that put the user at a place now (analysis G7, 2026-11-13). Without them, a stored place the host marked `at` is
# a place the question is about: “机场我进得去吗？” is asked from where the plan has the user, not from the airport.
PRESENT = re.compile(r"(?:我|我们|咱们|咱)(?:现在|已经|刚刚?|正|人)?(?:就)?(?:在|到了|已到)|(?:我|我们|咱们|咱)(?:已经|刚刚?)?到[^，。,.!?！？\s]{1,12}了"
                     r"|刚到|已经到|人在|就在这|刚从|刚出|出来了|从[^，。,.!?！？]{1,15}出来"
                     r"|\b(?:i'?m|i am|we'?re|we are)\s+(?:now\s+|already\s+|still\s+|just\s+)?(?:at|in|inside|outside|here)\b"
                     r"|\bjust (?:arrived|got (?:to|here)|landed|left|came out|walked out)\b"
                     r"|\b(?:i|we) (?:arrived|got here|landed|made it|left|came out)\b", re.IGNORECASE)


def point_facts(store: Store, owner: str, plan: dict | None, points: list[dict], anchors: list[dict], at: str,
                language: str = "zh", person: str | None = None, words: str | None = None) -> dict:
    """What the trip stores for each point of this question (A12): `at` (where the user said they are, else where the
    plan has them now, marked assumed) and one entry per other point with `facts` and the `keys` a first part answering
    it names (its words, the places it names). The service does not read the question; the host's points say what was
    asked."""
    from tripwright.query import coordinates, match_place, place_row, place_rows, stored_tickets

    zone = (plan or {}).get("timezone") or "UTC"
    now, local = clock(at, zone), aware(at).astimezone(ZoneInfo(zone))
    said_at = next((point for point in points if point["kind"] == "at"), None)
    if said_at is not None:
        place = resolve_place(plan, said_at.get("place"), said_at["said"])  # A16: the stored names, not only the host's place
        where = dict(said=said_at["said"], place=(place or {}).get("id"), name=short_name(place, language, plan) if place else None,
                     coords=(tuple(place["coords"]) if place and place.get("coords") else
                             coordinates(plan, said_at["place"]) if said_at.get("place") else None), assumed=False)
        if said_at.get("place") and place is None and where["coords"] is None:
            where["unknown"] = said_at["place"]  # S4h-a: said, never the plan's place in its stead
    planned = plan_place(plan, now["date"], now["time"], person or asker(plan))
    planned = match_place(plan, planned) if planned else None
    # where the plan has the user now, kept even when the host sent an `at`: a first part drafted from the words alone
    # (rule C) does not take the host's `at` (2026-11-12: “机场能不能进去” sent as `at` became “你在维也纳机场”)
    assumed = dict(said=None, place=planned["id"], name=short_name(planned, language, plan), assumed=True,
                   coords=tuple(planned["coords"]) if planned.get("coords") else None) if planned else None
    # an `at` whose words name no stored place and that came without a place of its own is no place the user named
    # (2026-11-12 replay: “说一个最最迟的时间，让我有个底” sent as `at` became “你在‘说一个最最迟的时间……’”): where the plan
    # has the user, as when there is no `at`
    unnamed = said_at is not None and not said_at.get("place") and where["place"] is None and where["coords"] is None
    if said_at is None or (unnamed and assumed is not None):
        where = assumed
    elif (assumed is not None and where["place"] is not None and where["place"] != assumed["place"]
          and not PRESENT.search(said_at["said"]) and not PRESENT.search(words or "")):
        # a stored place the host marked `at`, other than where the plan has the user, with no words that put the
        # user there (G7: “机场我进得去吗？” became “你在机场”): count from the plan's place, as assumed, which asks the
        # user to say so if not; the place the host marked stays in `mentioned`
        where = dict(assumed, mentioned=dict(said=said_at["said"], place=where["place"], name=where["name"]))
    tickets = stored_tickets(plan, now["date"], language)
    upcoming = [anchor for anchor in anchors if not anchor.get("passed")]
    def named(row):  # an English line names a place as its go and at lines do, by the local name
        return row if language == "zh" else dict(row, name=short_name(row, language, plan))

    told = []
    for point in points:
        if point["kind"] == "at":
            continue
        place = resolve_place(plan, point.get("place"), point["said"] if point["kind"] in ("go", "do") else None)
        if place is None and point["kind"] == "go":  # never the user's words as the place's name (a12b)
            place = stored_destination(plan, upcoming, point.get("place"))
        entry = dict(kind=point["kind"], said=point["said"], place=(place or {}).get("id") or point.get("place"),
                     name=short_name(place, language, plan) if place else None)
        if point.get("place") and place is None and coordinates(plan, point["place"]) is None:
            entry["unknown"] = point["place"]  # S4h-a: no stored place and no coordinates; turn's next says so
        keys = [point["said"]] + ([entry["name"], place.get("name_local") or ""] if place else [])
        if point["kind"] == "go":
            train = planned_train(plan, upcoming, place, now, language) if place else None
            facts = dict(frame=stored_frame(store, owner, place["id"], at, zone) if place else None, tickets=tickets,
                         nodes=[anchor["id"] for anchor in upcoming if place and place["id"] in (anchor.get("place"), anchor.get("destination"))],
                         train=train, way=planned_way(plan, train, where, now, person or asker(plan), language))
        elif point["kind"] == "eat":
            here = (coordinates(plan, point["place"]) if point.get("place") else None) or (where or {}).get("coords")
            rows = [row for row in place_rows(plan, here, local, POINT_KM, [], language)
                    if str(row.get("kind") or "").casefold() in FOOD_KINDS] if here else []
            facts = dict(near=here is not None, stored=len(rows), open=[named(row) for row in rows if row["open"] is True])
            keys += [name for row in facts["open"] for name in (short_name(row, language, plan), row["name_local"])]
        elif point["kind"] == "do":
            here = (where or {}).get("coords")
            facts = dict(places=[named(place_row(place, here, local, language))] if place else [])
        elif point["kind"] == "ticket":
            facts = dict(tickets=tickets)
        else:
            facts = {}
        told.append(dict(entry, facts=facts, keys=[key for key in dict.fromkeys(keys) if key]))
    return dict(at=where, planned=assumed, person=person or asker(plan), points=told, next=next_place(plan, where, told, language),
                party=(tickets or {}).get("party"))


def next_place(plan: dict | None, where: dict | None, told: list[dict], language: str) -> dict | None:
    """Where this question has the user go before the place it goes to (A16, quality judge 8: “先去 Hbf 吃饭” and the
    lookups still left from the gym): of the eat and do points in the host's order, the last with a place of the trip
    other than where the user is; a meal without one is at the first open place the first part names. None without."""
    here = (where or {}).get("place")
    steps = []
    for point in told:
        place = point["place"] if point["kind"] in ("eat", "do") and point.get("name") else None
        if point["kind"] == "eat" and (place is None or place == here) and point["facts"].get("open"):
            place = point["facts"]["open"][0]["id"]
        if place and place != here:
            steps.append(dict(kind=point["kind"], said=point["said"], place=place))
    if not steps:
        return None
    step = steps[-1]
    found = next((entry for entry in (plan or {}).get("places", []) if entry["id"] == step["place"]), None)
    return dict(step, name=short_name(found, language, plan) if found else step["place"])


STEP_MINUTES = 30  # a question's next step holds for lookups this long after its turn


def next_step(store: Store, owner: str, plan: dict | None, origin: str, destination: str, at: datetime) -> dict | None:
    """The next place of the latest question (its turn's context, at most STEP_MINUTES old, next_place) for a lookup
    from where that question has the user to a place it goes to; None otherwise, or when the latest turn gave no
    points (A16)."""
    from tripwright.query import match_place

    kept = [context for context in store.all("contexts", owner) if "points" in context and aware(context["created_at"]) <= at + timedelta(minutes=1)]
    latest = max(reversed(kept), key=lambda context: aware(context["created_at"]), default=None)  # of two at one moment, the later written
    if latest is None or aware(latest["created_at"]) < at - timedelta(minutes=STEP_MINUTES):
        return None
    record = latest["points"]
    step, start, end = record.get("next"), match_place(plan, origin), match_place(plan, destination)
    goes = {point.get("place") for point in record.get("points", []) if point["kind"] == "go"}
    if not step or not start or not end or start["id"] != (record.get("at") or {}).get("place") or end["id"] not in goes \
            or step["place"] == start["id"]:
        return None
    return step


def point_record(told: dict | None) -> dict:
    """The points and next place a later lookup in this question uses."""
    if told is None:
        return {}
    return dict(at={key: value for key, value in (told["at"] or {}).items() if key != "coords"} or None,
                points=[dict(kind=point["kind"], said=point["said"], keys=point["keys"], place=point.get("place")) for point in told["points"]],
                next=told.get("next"))
