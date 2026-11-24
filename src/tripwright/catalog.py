"""The catalog: one short row for every entry the user's repository holds, so the host reads the whole list and picks
the entries a message needs, even when the message shares no word with them (route F of the 2026-11-24 comparison:
the lexical top six missed entries a person would pick at once, e.g. the ticket rule for "can I take the earlier
train"). Finding what a sentence *means* is the host model's work; the service only lists, cuts and counts. No model
runs here, and nothing is stored: the catalog is computed from `recall.corpus` on every turn and is the same for the
same repository.

A row is `id|kind|text`: the text whole when it has at most WHOLE characters, else its first CUT characters and an
ellipsis. Rows of one kind whose first MERGE characters agree are one row marked ` +n`. An entry that is no longer in
force is listed, marked, only when it ended in the last EXPIRED_HOURS hours (it tells the host that something was
looked up before and where to look again); older ones are only counted.

The budget is MAX_LINES rows and MAX_CHARS characters, counting the lines of `omitted`. Over it, the rows are packed in
this order: the places, the plan and the bookings of today and the two days either side of it, and what the user
said (fact, item, decision, message); then the rest by how well it matches the message, then newest first. What does
not fit is counted per kind and day in `omitted` (`list`: the argument that expands it, `n`: how many are not
listed); over OMIT_GROUPS groups it is counted per kind.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KIND_ORDER = ["place", "plan", "booking", "fact", "item", "decision", "check", "web", "lookup", "said"]
MAX_LINES, MAX_CHARS = 150, 9000
WHOLE, CUT, MERGE = 90, 56, 24
EXPIRED_HOURS = 48
NEAR_DAYS = 2  # the days either side of today whose plan and bookings are packed first
OMIT_GROUPS = 30  # the most lines `omitted` takes; the rows get what is left of the budget
OMIT_CHARS = 1200
FIRST_SHARE = 1.0  # the share of the room the first group (plan near today, places, what the user said) may take: 1.0 is D-06 as designed
EXPAND_LIMIT = 60  # rows `query(kind="saved", arguments={list})` returns
FULL_TEXT = 4000  # the longest full text `ids` returns for one entry
TAGS = dict(zh="过期，要重查 ", en="expired, look again ")
SAID_KINDS = ("fact", "item", "decision", "said")  # what the user said


def normal(text) -> str:
    return " ".join(str(text or "").split())


def day_of(moment, zone: str | None) -> str | None:
    """The calendar day (YYYY-MM-DD, in the trip's zone) of a stored moment, or None."""
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    except ValueError:
        return str(moment)[:10] if len(str(moment)) >= 10 else None
    if parsed.tzinfo is not None and zone:
        parsed = parsed.astimezone(ZoneInfo(zone))
    return parsed.strftime("%Y-%m-%d")


def mmdd(entry: dict) -> str:
    """The day of an entry as the group key writes it (MM-DD), or "" when it has none."""
    return str(entry.get("day") or "")[5:10]


def group_of(entry: dict) -> str:
    """The `list` argument of the group an entry belongs to: `<kind> <MM-DD>`, or just `<kind>` when it has no day."""
    return f"{entry['found']} {mmdd(entry)}".strip()


def ended(entry: dict, at: datetime, language: str) -> str | None:
    """"" for an entry in force; the mark for one that ended inside the last EXPIRED_HOURS hours; None for an older one
    (or one with no end time to tell)."""
    if entry["in_force"]:
        return ""
    until = entry.get("valid_until")
    if not until:
        return None
    try:
        moment = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return TAGS["en" if language == "en" else "zh"] if at - moment <= timedelta(hours=EXPIRED_HOURS) else None


def shown(entry: dict) -> str:
    text = normal(entry["text"])
    return text if len(text) <= WHOLE else text[:CUT] + "…"


def merged_key(entry: dict) -> tuple[str, str]:
    return entry["found"], normal(entry["text"])[:MERGE]


def render(row: dict) -> str:
    extra = f" +{len(row['entries']) - 1}" if len(row["entries"]) > 1 else ""
    return f"{row['entries'][0]['id']}|{row['kind']}|{row['text']}{extra}"


def order(kind: str) -> int:
    return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)


def near(entry: dict, today: date) -> int | None:
    """How many days a plan or booking entry is from today, or None for an entry with no day."""
    try:
        return abs((date.fromisoformat(str(entry.get("day"))[:10]) - today).days)
    except ValueError:
        return None


def ordinal(entry: dict) -> int:
    try:
        return date.fromisoformat(str(entry.get("day"))[:10]).toordinal()
    except ValueError:
        return 0


def priority(row: dict, today: date, scores: dict) -> tuple:
    """Sort key of a row, in the order of the module doc: the plan and bookings of today and the two days either side
    (the nearest day first), the places (as the corpus gives them), what the user said (newest first); then every other
    row by its best score, then newest first."""
    kind, entries = row["kind"], row["entries"]
    newest = -max(ordinal(entry) for entry in entries)
    distance = min((d for d in (near(entry, today) for entry in entries) if d is not None), default=None)
    if kind in ("plan", "booking") and distance is not None and distance <= NEAR_DAYS:
        return (0, 0, distance, newest, order(kind), entries[0]["id"])
    if kind == "place":
        return (0, 1, row["at"], 0, 0, entries[0]["id"])
    if kind in SAID_KINDS:
        return (0, 2, newest, 0, order(kind), entries[0]["id"])
    best = max(scores.get((entry["found"], entry["id"]), 0.0) for entry in entries)
    return (1, -best, newest, 0, order(kind), entries[0]["id"])


def listing(row: dict) -> tuple[int, int]:
    """Where a row stands in the catalog: its kind's place in KIND_ORDER, then the order the corpus gave it."""
    return order(row["kind"]), row["at"]


def groups(entries: list[dict]) -> list[dict]:
    """`omitted`: the entries not listed, counted per kind and day; per kind when there are more groups than lines."""
    counted: dict[str, int] = {}
    for entry in entries:
        counted[group_of(entry)] = counted.get(group_of(entry), 0) + 1
    if len(counted) > OMIT_GROUPS:
        counted = {}
        for entry in entries:
            counted[entry["found"]] = counted.get(entry["found"], 0) + 1
    return [dict(list=key, n=n) for key, n in sorted(counted.items(), key=lambda item: (order(item[0].split()[0]), item[0]))]


def size(lines: list[str], omitted: list[dict]) -> int:
    return sum(len(line) + 1 for line in lines) + sum(len(f"{group['list']} {group['n']}") + 18 for group in omitted)


def build(entries: list[dict], *, at: datetime, zone: str | None, language: str = "zh", scores: dict | None = None,
          first_share: float | None = None) -> dict:
    """The catalog of `entries` (corpus entries, this message's own already left out): {lines, omitted}. `scores` maps
    (kind, id) to how well the entry matches the message (recall.rank's score); an entry not in it counts as 0."""
    scores = scores or {}
    here = at.astimezone(ZoneInfo(zone)) if zone else at
    today = here.date()
    live, older = [], []
    for entry in entries:
        mark = ended(entry, at, language)
        (older if mark is None else live).append((entry, mark))
    rows, seen = [], {}
    for entry, mark in live:
        key = (*merged_key(entry), bool(mark))
        if key in seen:
            seen[key]["entries"].append(entry)
            continue
        seen[key] = row = dict(kind=entry["found"], text=mark + shown(entry), entries=[entry], at=len(rows))
        rows.append(row)
    stale_groups = groups([entry for entry, _ in older])
    lines = [render(row) for row in sorted(rows, key=listing)]
    if len(lines) + len(stale_groups) <= MAX_LINES and size(lines, stale_groups) <= MAX_CHARS:
        return dict(lines=lines, omitted=stale_groups)
    # over the budget: the lines of `omitted` take at most OMIT_GROUPS lines and OMIT_CHARS characters of it
    room_lines, room_chars = MAX_LINES - OMIT_GROUPS, MAX_CHARS - OMIT_CHARS
    share = FIRST_SHARE if first_share is None else first_share
    kept, rest, used, first_lines, first_chars = [], [], 0, 0, 0
    for row in sorted(rows, key=lambda row: priority(row, today, scores)):
        line = render(row)
        first = priority(row, today, scores)[0] == 0
        if len(kept) >= room_lines or used + len(line) + 1 > room_chars or \
                first and (first_lines >= room_lines * share or first_chars + len(line) + 1 > room_chars * share):
            rest.append(row)
            continue
        kept.append(row)
        used += len(line) + 1
        if first:
            first_lines, first_chars = first_lines + 1, first_chars + len(line) + 1
    unlisted = [entry for row in rest for entry in row["entries"]] + [entry for entry, _ in older]
    return dict(lines=[render(row) for row in sorted(kept, key=listing)], omitted=groups(unlisted))


def expand(entries: list[dict], group: str) -> list[dict]:
    """The entries `list` names: a group (`<kind>` or `<kind> <MM-DD>`, as `omitted` writes it) or the id of a catalog
    row that ends in ` +n` (the entries whose first MERGE characters it shares). Newest first. Not capped here."""
    group = normal(group)
    chosen = [entry for entry in entries if entry["id"] == group]
    if chosen:
        head = chosen[0]
        return sorted([entry for entry in entries if merged_key(entry) == merged_key(head)],
                      key=lambda entry: (ordinal(entry), entry["id"]), reverse=True)
    kind, _, day = group.partition(" ")
    return sorted([entry for entry in entries if entry["found"] == kind and (not day or mmdd(entry) == day)],
                  key=lambda entry: (ordinal(entry), entry["id"]), reverse=True)
