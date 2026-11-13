"""The repository's files for one commit: A's canonical records and B's front screens and views (CONTRACTS 4).

Everything a generated file depends on is recoverable from the commit itself: the records (its tree), the moment
(its commit date), the base commit (its parent), the operations it carries and the notices it showed (its trailers).
So `check` rebuilds the index from the commit, regenerates and compares: "regenerated = committed".
"""
from __future__ import annotations

from datetime import datetime

from tripwright.repo import index, layout, memory
from tripwright.repo.snapshot import take

MANAGED = ("plan/days/", "views/days/", "views/packs/")


def flat_events(tables: dict) -> dict[str, dict]:
    """Messages in the flat shape the views quote from: `{id, speaker, nature, received_at, text, ...}`."""
    return {event["id"]: dict(id=event["id"], request_id=event["request_id"], speaker=event["received"].get("speaker"),
                              nature=event["received"]["nature"], native_ref=event["received"].get("native_ref"),
                              channel=event["received"]["channel"], received_at=event["receipt"]["received_at"],
                              text=event["received"]["text"])
            for event in tables["events"] if "received" in event}


def other_states(trip_files: dict[str, dict[str, str]], snapshot: dict) -> list[dict]:
    """Trips other than the current one, for the repository's front screen: their committed plan only."""
    states = []
    for trip_id, files in sorted(trip_files.items()):
        try:
            plan = layout.plan_from(files)
        except ValueError:
            plan = None
        if plan is not None and plan.get("id") == trip_id:
            states.append(dict(path=layout.trip_folder(trip_id), trip=plan, records={}, snapshot=snapshot))
    return states


def generated(tables: dict, owner: str, *, trip: str | None, snapshot: dict, notices: list[dict], others: list[dict],
              language: str = "zh", zone: str | None = None) -> dict[str, str]:
    """B's bytes for the repository front screen and the current trip's screen and views. A trip opened but not yet
    planned already shows what it imported (`views/memory.md`), so the user sees the memory before the first plan."""
    from tripwright import packs
    from tripwright.render import memory_view, repo_files as views, repo_readme

    scratch = index.scratch(tables, owner)
    try:
        versions = tables["versions"]
        plan = max(versions, key=lambda version: version["number"])["plan"] if versions else None
        if not trip or plan is None:
            files = {"README.md": repo_readme(others, current=None, snapshot=snapshot)}
            remembered = memory.memory_data(scratch, owner, trip=trip, at=snapshot["taken_at"]) if trip else None
            if remembered and remembered["entries"]:
                folder = layout.trip_folder(trip)
                state = dict(path=folder, trip=dict(id=trip, timezone=zone or "UTC"), records={}, snapshot=snapshot)
                files[f"{folder}/views/memory.md"] = memory_view(remembered, state=state)
            return files
        at = snapshot["taken_at"]
        folder = layout.trip_folder(trip)
        events = flat_events(tables)
        latest = max(versions, key=lambda version: version["number"])
        state = dict(path=folder, trip=plan, records=packs.view_records(scratch, owner, at=at), snapshot=snapshot,
                     notices=notices, events=events, needs_replan=latest.get("needs_replan", False), warnings=latest.get("warnings", []))
        sources = {event_id: dict(received_at=event["received_at"], speaker=event["speaker"], nature=event["nature"])
                   for event_id, event in events.items()}
        day = packs.day_packs(scratch, owner, snapshot=snapshot, at=at, trip_folder=folder, language=language)
        remembered = memory.memory_data(scratch, owner, trip=trip, at=at)
        files = views([state], current=trip, snapshot=snapshot, packs={trip: day}, sources=sources, memory={trip: remembered})
        files["README.md"] = repo_readme([state, *others], current=trip, snapshot=snapshot, memory=remembered)
        return files
    finally:
        scratch.close()


def trips_on_disk(files: dict[str, str]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for path, text in files.items():
        parts = path.split("/", 2)
        if len(parts) == 3 and parts[0] == layout.TRIPS:
            found.setdefault(parts[1], {})[parts[2]] = text
    return found


def profile_files(tables: dict, *, trip: str, existing: dict[str, str], raw: dict[str, str]) -> dict[str, str]:
    """`profile/`: the conversation defaults, long-term items first said on this trip, and the people of all trips."""
    files = {}
    if tables["profiles"]:
        files[index.CONVERSATION] = layout.dump(tables["profiles"][0])
    events = flat_events(tables)
    superseding = {}
    for item in tables["items"]:
        for old in item.get("relations", {}).get("supersedes", []):
            superseding[old] = item
    for item in tables["items"]:
        if not item.get("long_term") or item.get("origin"):
            continue
        event = events.get(item["source"]["event"])
        status = item["status"]
        if status == "superseded" and not superseding.get(item["id"], {}).get("long_term"):
            status = "active"  # replaced on this trip only; still the long-term entry
        origin = dict(trip=trip, event=item["source"]["event"], said_at=event["received_at"] if event else None,
                      speaker=event["speaker"] if event else None, nature=item["source"].get("nature"),
                      path=raw.get(item["source"]["event"]), message=event["text"] if event else None)
        files[f"{layout.PROFILE}/items/{item['id']}.yaml"] = layout.dump(dict(item, status=status, origin=origin))
    versions = tables["versions"]
    plan = max(versions, key=lambda version: version["number"])["plan"] if versions else None
    if plan is not None:
        people = {person["id"]: person for person in (layout.parse("profile/people.yaml", existing["profile/people.yaml"]) or [])} \
            if "profile/people.yaml" in existing else {}
        for person in plan["people"]:
            known = people.setdefault(person["id"], dict(id=person["id"], name=person["name"], trips=[]))
            known["name"] = person["name"]
            if trip not in known["trips"]:
                known["trips"] = sorted([*known["trips"], trip])
        files["profile/people.yaml"] = layout.dump([people[key] for key in sorted(people)])
    return files


def repository_files(service, tables: dict, *, trip: str | None, upto: str | None, repo: dict, at: str,
                     notices: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Every file one commit writes, and the generated or plan files it removes (days no longer in the plan)."""
    repository = service.repository
    owner = repository.owner
    if upto:
        number = next(version["number"] for version in tables["versions"] if version["id"] == upto)
        tables = dict(tables, versions=[version for version in tables["versions"] if version["number"] <= number])
    working = repository.working()
    config = index.config_of(working)
    files: dict[str, str] = {}
    if config.get("current_trip") != trip:
        files[index.CONFIG] = layout.dump(dict(config, current_trip=trip))
    versions = tables["versions"]
    plan = max(versions, key=lambda version: version["number"])["plan"] if versions else None
    zone = plan["timezone"] if plan else config.get("timezone")
    raw = {}
    if trip:
        folder = layout.trip_folder(trip)
        existing = layout.raw_paths(path for path in working if path.startswith(folder + "/raw/"))
        raw = {event["id"]: existing[layout.suffix(event["id"])] for event in tables["events"]
               if "received" in event and layout.suffix(event["id"]) in existing}
        files.update(layout.trip_files(tables, trip_id=trip, zone=zone, existing_raw=raw))
        raw = {event["id"]: path for path, text in files.items() if path.startswith(folder + "/raw/")
               for event in [layout.event_record(path, text)]}
        files.update(profile_files(tables, trip=trip, existing=working, raw=raw))
    snapshot = dict(take_tables(tables, owner, at), repo=repo)
    shown = [dict(notice_id=notice["notice_id"], user_line=notice["user_line"]) for notice in notices]
    others = other_states({key: value for key, value in trips_on_disk(working).items() if key != trip}, snapshot)
    files.update(generated(tables, owner, trip=trip, snapshot=snapshot, notices=shown, others=others, language=service.language, zone=zone))
    remove = []
    if trip:
        folder = layout.trip_folder(trip)
        for path in working:
            relative = path[len(folder) + 1:] if path.startswith(folder + "/") else None
            if relative and relative.startswith(MANAGED) and path not in files:
                remove.append(path)
    held = set(getattr(service, "held_paths", ()) or ())
    files = {path: text for path, text in files.items() if path not in held}
    return files, [path for path in remove if path not in held]


def view_repo(canonical: str, parent: str | None, ops: list[str], at: str) -> dict:
    """The repository part of the snapshot that generated files show (front screens, views, day packs).

    A file cannot name the commit it is in, and the writes it reflects are in that very commit. So `head` is a label
    for the reader, "<previous commit> 的下一个，本次带入 N 条写入", rendered by B wherever it writes "提交 {head}", and
    nothing this commit brings counts as not yet in the repository. The snapshots the tools return keep the commit ID."""
    head = None
    if parent:
        head = f"{parent[:7]} 的下一个，本次带入 {len(ops)} 条写入"
    elif ops:
        head = f"第一个，本次带入 {len(ops)} 条写入"
    return dict(canonical=canonical, head=head, head_checked_at=at, unsynced_ops=[])


def take_tables(tables: dict, owner: str, at: str) -> dict:
    scratch = index.scratch(tables, owner)
    try:
        return take(scratch, owner, at=at)
    finally:
        scratch.close()


def notices_from(trailers: dict) -> list[dict]:
    shown = []
    for value in trailers.get("Tripwright-Notice", []):
        notice_id, _, user_line = value.partition(" ")
        shown.append(dict(notice_id=notice_id, user_line=user_line))
    return shown


def check(repository, *, rev: str = "HEAD", language: str = "zh") -> dict:
    """`tripwright check`: the repository's files parse, messages were never edited, generated files equal what the
    tool regenerates from the commit, and the tool's agent files match the installed version (a reminder only)."""
    from tripwright.agents import user_repo_files
    from tripwright.repo.committer import second

    commit = repository.git.rev(rev)
    problems, reminders = [], []
    files = repository.committed(commit)
    generation = repository.load(commit)
    problems += generation["problems"]
    if generation["manual_plan"] is not None:
        problems.append("plan files differ from the last version in plan/versions.jsonl (a hand edit not yet taken in)")
    info = repository.git.log(commit)[0]
    parent = repository.git.rev(f"{commit}^")
    at = second(info["date"])
    snapshot = dict(take_tables(generation["tables"], repository.owner, at),
                    repo=view_repo(repository.canonical(), parent, info["trailers"].get("Tripwright-Op", []), at))
    trip = generation["trip"]
    others = other_states({key: value for key, value in trips_on_disk(files).items() if key != trip}, snapshot)
    versions = generation["tables"]["versions"]
    zone = max(versions, key=lambda version: version["number"])["plan"]["timezone"] if versions and versions[-1].get("plan") else generation["config"].get("timezone")
    expected = generated(generation["tables"], repository.owner, trip=trip, snapshot=snapshot,
                         notices=notices_from(info["trailers"]), others=others, language=language, zone=zone)
    # `tripwright init` writes no front screen: the first write generates it. Until the tool has written anything, a
    # generated file that is missing is not a difference (2026-11-12: a fresh repository's check said "README.md
    # differs", which read as a broken install).
    untouched = not any(generation["tables"].values())
    for path, text in sorted(expected.items()):
        if files.get(path) != text and not (untouched and path not in files):
            problems.append(f"{path}: differs from what the tool generates for this commit")
    for path, data in sorted(user_repo_files().items()):
        if files.get(path, "").encode() != data:
            reminders.append(f"{path}: differs from the installed tool's version (run `tripwright init --upgrade`)")
    edited = repository.git.run("log", "--format=", "--name-only", "--diff-filter=MD", commit, "--", f"{layout.TRIPS}/*/raw/*")
    problems += sorted({f"{path}: a message file was changed or removed after it was written" for path in edited.split() if path})
    return dict(commit=commit, ok=not problems, problems=problems, reminders=reminders, checked_at=datetime.now().astimezone().isoformat())


def day_text(service, owner: str, day: str) -> str:
    """`state(view="day")`: B's day view of the committed plan, as copyable text."""
    from tripwright import packs
    from tripwright.render import day_view

    version = service.versions.current(owner)
    if not version.get("plan"):
        raise ValueError("No committed trip")
    if day not in {entry["date"] for entry in version["plan"]["days"]}:
        raise ValueError("Unknown trip date")
    snapshot = take(service.store, owner)
    folder = service.trip_folder(owner) or ""
    state = dict(path=folder, trip=version["plan"], records=packs.view_records(service.store, owner, at=snapshot["taken_at"]),
                 snapshot=snapshot, notices=service.open_notices(owner), needs_replan=version.get("needs_replan", False),
                 warnings=version.get("warnings", []))
    return day_view(state, day)
