"""The catalog: one short row for every entry the user's repository holds, so the host reads the whole list and picks
the entries a message needs, even when the message shares no word with them (route F of the 2026-11-24 comparison:
the lexical top six missed entries a person would pick at once, e.g. the ticket rule for "can I take the earlier
train"). Finding what a sentence *means* is the host model's work; the service only lists, cuts and counts. No model
runs here, and nothing is stored: the catalog is computed from `recall.corpus` on every turn and is the same for the
same repository.

A row is `id|kind|text`: the text whole when it has at most WHOLE characters, else its first CUT characters and an
ellipsis (LEVELS: when the rows do not fit, the text is cut shorter, level by level, until they fit or the lines, not
the characters, are what stops the listing). Rows of one kind whose first MERGE characters agree are one row marked ` +n`. An entry that is no longer in
force is listed, marked, only when it ended in the last EXPIRED_HOURS hours (it tells the host that something was
looked up before and where to look again); older ones are only counted. A ledger record (fact, item, decision, check) that is expired has its `valid_until` as the
end time; one that is superseded has none, because the ledger does not store when a record was superseded, so a
superseded record is only counted, never listed (no time is made up).

The budget is MAX_LINES rows and MAX_CHARS characters, counting the lines of `omitted`. Over it (D-07, after the
600-entry reading of 2026-11-24 showed that listing the plan, places and what the user said first left no row for a
web check): first the rows are cut shorter (LEVELS); then the plan and the bookings of today and the two days either
side of it are listed first, all of them; then every other class (places, what the user said [fact, item, decision,
message], checks, web checks, lookups) gets an equal share of what is left, taking inside the class the best match
to the message first, then the newest; then what is left of the room goes to the rest of the rows (including the
plan and bookings of other days) by match, then newest. What does not fit is counted per kind and day in `omitted`
(`list`: the argument that expands it, `n`: how many are not listed); over OMIT_GROUPS groups it is counted per kind.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KIND_ORDER = ["place", "plan", "booking", "fact", "item", "decision", "check", "web", "lookup", "said"]
MAX_LINES, MAX_CHARS = 150, 9000
WHOLE, CUT, MERGE = 90, 56, 24
LEVELS = ((WHOLE, CUT), (40, 40), (32, 32), (24, 24))  # (whole up to, else cut to) at each level of shortening
EXPIRED_HOURS = 48
NEAR_DAYS = 2  # the days either side of today whose plan and bookings are listed first
CLASSES = ("place", "said", "check", "web", "lookup")  # what shares the room left after the plan near today
OMIT_GROUPS = 30  # the most lines `omitted` takes; the rows get what is left of the budget
OMIT_CHARS = 1200
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


def shown(entry: dict, level: int = 0) -> str:
    whole, cut = LEVELS[level]
    text = normal(entry["text"])
    return text if len(text) <= whole else text[:cut] + "…"


def merged_key(entry: dict) -> tuple[str, str]:
    return entry["found"], normal(entry["text"])[:MERGE]


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


def class_of(row: dict) -> str | None:
    """The class of a row that shares the room (CLASSES), or None for a plan or booking row."""
    kind = row["kind"]
    return "said" if kind in SAID_KINDS else kind if kind in CLASSES else None


def is_near(row: dict, today: date) -> bool:
    distance = min((d for d in (near(entry, today) for entry in row["entries"]) if d is not None), default=None)
    return row["kind"] in ("plan", "booking") and distance is not None and distance <= NEAR_DAYS


def best_first(row: dict, scores: dict) -> tuple:
    """Inside a class and in the rest: the best match to the message first, then (the same score) one in force before
    an expired one, then the newest."""
    best = max(scores.get((entry["found"], entry["id"]), 0.0) for entry in row["entries"])
    # at the same score an entry in force comes before one that only says where to look again
    return (-best, bool(row["mark"]), -max(ordinal(entry) for entry in row["entries"]), order(row["kind"]), row["entries"][0]["id"])


def pack(rows: list[dict], today: date, scores: dict, room_lines: int, room_chars: int) -> tuple[list[dict], list[dict]]:
    """The rows that are listed and those that are not, by the order of the module doc."""
    kept, used = [], [0]

    def take(row) -> bool:
        cost = len(row["line"]) + 1
        if len(kept) >= room_lines or used[0] + cost > room_chars:
            return False
        kept.append(row)
        used[0] += cost
        return True

    chosen: set[int] = set()
    for row in sorted((row for row in rows if is_near(row, today)),
                      key=lambda row: (min(near(entry, today) or 0 for entry in row["entries"]), best_first(row, scores)[1:])):
        if take(row):
            chosen.add(row["at"])
    classes: dict[str, list[dict]] = {}
    for row in rows:
        if row["at"] not in chosen and class_of(row):
            classes.setdefault(class_of(row), []).append(row)
    if classes:
        share_lines, share_chars = max(0, room_lines - len(kept)) // len(classes), max(0, room_chars - used[0]) // len(classes)
        for name in sorted(classes, key=CLASSES.index):
            count = chars = 0
            for row in sorted(classes[name], key=lambda row: best_first(row, scores)):
                cost = len(row["line"]) + 1
                if count >= share_lines or chars + cost > share_chars:
                    continue
                if take(row):
                    chosen.add(row["at"])
                    count, chars = count + 1, chars + cost
    for row in sorted((row for row in rows if row["at"] not in chosen), key=lambda row: best_first(row, scores)):
        if take(row):
            chosen.add(row["at"])
    return [row for row in rows if row["at"] in chosen], [row for row in rows if row["at"] not in chosen]


def build(entries: list[dict], *, at: datetime, zone: str | None, language: str = "zh", scores: dict | None = None) -> dict:
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
        seen[key] = row = dict(kind=entry["found"], mark=mark, entries=[entry], at=len(rows))
        rows.append(row)
    stale_groups = groups([entry for entry, _ in older])
    old_entries = [entry for entry, _ in older]
    for level in range(len(LEVELS)):
        for row in rows:
            row["line"] = f"{row['entries'][0]['id']}|{row['kind']}|{row['mark']}{shown(row['entries'][0], level)}" + \
                (f" +{len(row['entries']) - 1}" if len(row["entries"]) > 1 else "")
        lines = [row["line"] for row in sorted(rows, key=listing)]
        if len(lines) + len(stale_groups) <= MAX_LINES and size(lines, stale_groups) <= MAX_CHARS:
            return dict(lines=lines, omitted=stale_groups)
        # shorter text only helps while the characters, not the lines, stop the listing
        room_lines = MAX_LINES - OMIT_GROUPS
        longest = sorted((len(row["line"]) + 1 for row in rows), reverse=True)[:room_lines]
        if len(rows) > room_lines and sum(longest) <= MAX_CHARS - OMIT_CHARS:
            break  # any room_lines rows fit the characters: the lines are what stops the listing
    # the lines of `omitted` take what they need: try with a few, then with what the first try left out
    reserve = min(OMIT_GROUPS, 10)
    for attempt in range(4):
        kept, rest = pack(rows, today, scores, MAX_LINES - reserve, MAX_CHARS - reserve * 40)
        omitted = groups([entry for row in rest for entry in row["entries"]] + old_entries)
        out = [row["line"] for row in sorted(kept, key=listing)]
        if len(out) + len(omitted) <= MAX_LINES and size(out, omitted) <= MAX_CHARS:
            return dict(lines=out, omitted=omitted)
        reserve = min(OMIT_GROUPS, max(reserve + 1, len(omitted)))
    kept, rest = pack(rows, today, scores, MAX_LINES - OMIT_GROUPS, MAX_CHARS - OMIT_CHARS)
    return dict(lines=[row["line"] for row in sorted(kept, key=listing)],
                omitted=groups([entry for row in rest for entry in row["entries"]] + old_entries))


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
