"""Long-term entries carried into a new trip, and the memory data the views show (D-32, CONTRACTS 3.5).

Opening a trip imports every active long-term entry of `profile/` (items said as lasting, the people of earlier
trips, registered materials) with the status "pending": it applies and the package marks it until the user answers in
the conversation; `turn(record={"carry": [{"id", "status"}]})` records "confirmed" (keep for this trip) or "not_used".
An imported item is a copy in this trip's ledger that keeps its original source and an `origin` naming the earlier
trip and message, so later status changes on this trip never rewrite the profile.
"""
from __future__ import annotations

from tripwright.ledger import Ledger
from tripwright.repo import index, layout
from tripwright.storage import Store, now

RULE_KINDS = ("rule", "constraint", "authorization", "stop")


def entry_kind(item: dict) -> str:
    return "rule" if item["kind"] in RULE_KINDS else "preference"


def prepare_new_trip(service, owner: str, trip: str) -> None:
    """Before a trip opens: every earlier write must be in the canonical branch, so the old trip's generation never
    needs replaying after the index switches to the new one."""
    if service.committer is None:
        raise ValueError("Opening a new trip needs a trip repository (tripwright --repo <folder>)")
    layout.trip_folder(trip)
    if trip == service.current_trip(owner):
        raise ValueError(f"{trip} is already the current trip")
    committer = service.committer
    committer.sync() if service.repository.remote() else committer.flush(push=False)
    waiting = service.outbox.open(owner)
    if waiting:
        raise ValueError(f"{len(waiting)} writes are not in the canonical branch yet; run `tripwright sync` (the remote must be "
                         "reachable) before opening another trip")


def switch(service, owner: str, trip: str) -> dict:
    """Replace the index with the trip's folder (empty for a new trip); return what to import into a new one."""
    repository = service.repository
    working = repository.working()
    exists = f"{layout.trip_folder(trip)}/trip.yaml" in working
    generation = repository.load("HEAD", trip=trip) if exists else None
    tables = generation["tables"] if generation else {name: [] for name in index.TRIP_TABLES}
    tables = dict(tables, profiles=service.store.all("profiles", owner))
    service.store.replace(owner, tables)
    head = service.committer.head_record() or {}
    service.committer.set_head(head.get("commit") or repository.git.rev("HEAD"), trip=trip)
    return dict(new=not exists, profile=profile_entries(working))


def profile_entries(working: dict[str, str]) -> dict:
    items = []
    for path, text in sorted(working.items()):
        if path.startswith(f"{layout.PROFILE}/items/") and path.endswith(".yaml"):
            try:
                item = layout.record_from("items", path, text)
            except ValueError:
                continue
            if item.get("status", "active") == "active":
                items.append(item)
    people = layout.parse("profile/people.yaml", working["profile/people.yaml"]) if "profile/people.yaml" in working else []
    materials = layout.parse("profile/materials.yaml", working["profile/materials.yaml"]) if "profile/materials.yaml" in working else []
    return dict(items=items, people=people or [], materials=materials or [], used=last_uses(working))


def last_uses(working: dict[str, str]) -> dict[str, dict]:
    """Entry ID → where it was last used `{trip, date, version_id}`, from every trip's version ledger."""
    found: dict[str, tuple] = {}
    trips: dict[str, dict[str, str]] = {}
    for path, text in working.items():
        parts = path.split("/", 2)
        if len(parts) == 3 and parts[0] == layout.TRIPS:
            trips.setdefault(parts[1], {})[parts[2]] = text
    for trip, files in trips.items():
        if "plan/versions.jsonl" not in files:
            continue
        try:
            plan = layout.plan_from(files) or {}
            lines = layout.parse("plan/versions.jsonl", files["plan/versions.jsonl"])
        except ValueError:
            continue
        dates = {block["id"]: day["date"] for day in plan.get("days", []) for option in day.get("plans", [])
                 for track in option.get("tracks", []) for block in track.get("blocks", [])}
        for version in lines:
            for usage in version.get("usages", []):
                if usage.get("status") not in ("used", "host_reported"):
                    continue
                day = next((dates[block] for block in usage.get("block_ids", []) if block in dates), None)
                stamp = version.get("committed_at") or ""
                if usage["item_id"] not in found or stamp > found[usage["item_id"]][0]:
                    found[usage["item_id"]] = (stamp, dict(trip=trip, date=day, version_id=version["id"]))
    return {key: value for key, (_, value) in found.items()}


def import_entries(service, owner: str, opened: dict, trip: str, event_id: str) -> list[str]:
    """Copy the profile's long-term entries into the new trip as pending (照用，待确认), journaled under the message that
    opened the trip."""
    if not opened["new"]:
        return []
    store, ledger = service.store, service.ledger
    profile, at, imported = opened["profile"], now(), []
    for item in profile["items"]:
        origin = dict(item.get("origin") or {}, import_event=event_id, profile=f"{layout.PROFILE}/items/{item['id']}.yaml")
        scope = {key: value for key, value in (item.get("scope") or {}).items() if key not in ("start", "end")}
        copy = dict(item, status="active", relations={}, scope=scope, origin=origin)
        store.put("items", owner, copy, insert=True)
        _carry(store, owner, dict(id=item["id"], kind=entry_kind(item), text=item["text"], path=origin["profile"],
                                  subject=item["subject"], from_trip=origin.get("trip")), profile, at)
        imported.append(item["id"])
    for person in profile["people"]:
        _carry(store, owner, dict(id=person["id"], kind="companion", text=f"{person['id']}：{person.get('name', person['id'])}",
                                  path="profile/people.yaml", subject=[person["id"]], from_trip=(person.get("trips") or [None])[0]), profile, at)
        imported.append(person["id"])
    for material in profile["materials"]:
        _carry(store, owner, dict(id=material["id"], kind="material", text=material.get("text", material["id"]),
                                  path=material.get("path", material["id"]), subject=[], from_trip=material.get("trip")), profile, at)
        imported.append(material["id"])
    for entry_id in imported:
        ledger._audit(owner, event_id, "import_memory", entry_id, {"trip": trip})
    return imported


def _carry(store: Store, owner: str, entry: dict, profile: dict, at: str):
    store.put("carry", owner, dict(entry, status="pending", status_event=None, imported_at=at,
                                   last_used=profile["used"].get(entry["id"])), insert=True)


def pending_entries(store: Store, owner: str) -> list[dict]:
    return [dict(id=entry["id"], kind=entry["kind"], text=entry["text"], from_trip=entry.get("from_trip"))
            for entry in store.all("carry", owner) if entry["status"] == "pending"]


def used_here(store: Store, owner: str, trip: str, entry_id: str) -> dict | None:
    for version in sorted(store.all("versions", owner), key=lambda version: -version["number"]):
        for usage in version.get("usages", []):
            if usage["item_id"] == entry_id and usage.get("status") in ("used", "host_reported"):
                plan = version.get("plan") or {}
                dates = {block["id"]: day["date"] for day in plan.get("days", []) for option in day.get("plans", [])
                         for track in option.get("tracks", []) for block in track.get("blocks", [])}
                day = next((dates[block] for block in usage.get("block_ids", []) if block in dates), None)
                return dict(trip=trip, date=day, version_id=version["id"])
    return None


def memory_data(store: Store, owner: str, *, trip: str | None, at: str) -> dict:
    """`tripwright.memory/1` for the current trip (CONTRACTS 3.5)."""
    items = {item["id"]: item for item in store.all("items", owner)}
    events = {event["id"]: event for event in store.all("events", owner) if "received" in event}
    entries = []
    for entry in Ledger(store).carried(owner).values():
        last = used_here(store, owner, trip, entry["id"]) or entry.get("last_used")
        if last is not None and not last.get("date"):
            last = None
        if entry["kind"] in ("preference", "rule") and entry["id"] in items:
            item = items[entry["id"]]
            origin = item.get("origin") or {}
            source = dict(trip=origin.get("trip"), event=origin.get("event"), said_at=origin.get("said_at"),
                          speaker=origin.get("speaker"), nature=origin.get("nature"), path=entry["path"])
            entries.append(dict(id=entry["id"], kind=entry["kind"], text=item["text"], quote=item["quote"], subject=item["subject"],
                                strength=item["strength"], source=source, status=entry["status"], status_event=entry.get("status_event"),
                                last_used=last))
        else:
            source = dict(trip=entry.get("from_trip"), event=None, said_at=None, speaker=None, nature=None, path=entry["path"])
            entries.append(dict(id=entry["id"], kind=entry["kind"], text=entry["text"], quote=None, subject=entry.get("subject", []),
                                strength=None, source=source, status=entry["status"], status_event=entry.get("status_event"),
                                last_used=last))
    for item in items.values():
        if not item.get("long_term") or item.get("origin") or item["status"] not in ("active", "pending"):
            continue
        event = events.get(item["source"]["event"], {})
        received = event.get("received", {})
        source = dict(trip=trip, event=item["source"]["event"], said_at=event.get("receipt", {}).get("received_at"),
                      speaker=received.get("speaker"), nature=item["source"].get("nature") or received.get("nature"),
                      path=f"{layout.PROFILE}/items/{item['id']}.yaml")
        entries.append(dict(id=item["id"], kind=entry_kind(item), text=item["text"], quote=item["quote"], subject=item["subject"],
                            strength=item["strength"], source=source, status="confirmed", status_event=item["source"]["event"],
                            last_used=used_here(store, owner, trip, item["id"])))
    return dict(schema="tripwright.memory/1", trip=trip, as_of=at, entries=entries)
