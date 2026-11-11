"""The traveller's memory: long-term content a new trip imports, shown so the user can see and correct it.

Long-term entries are preferences, companions, standing rules and materials. Each shows the user's words (or the
gist when there are none), which trip and message it came from, its status in the current trip (confirmed and carried
over, waiting for confirmation, or not used on this trip) and where it was last used. One row model feeds both the
repository page (`trips/<trip>/views/memory.md`) and the website page, so the two never disagree.

Input shape (proposed for the seam contract; A produces it): see `MEMORY_FIELDS` and `memory_problems`.
"""
from __future__ import annotations

from tripwright.render.labels import short_day

KINDS = ("preference", "companion", "rule", "material")
STATUSES = ("pending", "confirmed", "not_used")
NATURES = ("user_verbatim", "host_restatement", "screenshot_extraction", "agent_inference")
MEMORY_FIELDS = {"id", "kind", "text", "quote", "subject", "strength", "source", "status", "status_event", "last_used"}
WORDS = {
    "zh": {"title": "记忆（长期内容）", "kind": {"preference": "偏好", "companion": "同行人", "rule": "常驻规矩", "material": "资料"},
           "status": {"confirmed": "已确认沿用", "pending": "待确认", "not_used": "这趟不用"},
           "nature": {"user_verbatim": "逐字", "host_restatement": "转述", "screenshot_extraction": "截图转写", "agent_inference": "推断"},
           "summary": "这趟（{trip}）：待确认 {pending} 条｜已确认沿用 {confirmed} 条｜这趟不用 {not_used} 条",
           "ask": "待确认的：回“沿用”或“这趟不用”，记进这一趟。", "from": "来自 {where}", "trip_event": "{trip} 的 {event}",
           "used": "最近用在 {trip} {day}", "never": "还没用过", "by": "依据 {event}", "none": "还没有长期内容。",
           "file": "文件 {path}", "join": "、", "sep": "｜"},
    "en": {"title": "Memory (long-term content)", "kind": {"preference": "Preferences", "companion": "Companions",
                                                         "rule": "Standing rules", "material": "Materials"},
           "status": {"confirmed": "confirmed, carried over", "pending": "waiting for confirmation", "not_used": "not used on this trip"},
           "nature": {"user_verbatim": "verbatim", "host_restatement": "restated", "screenshot_extraction": "from screenshot",
                      "agent_inference": "inferred"},
           "summary": "This trip ({trip}): {pending} waiting | {confirmed} confirmed | {not_used} not used",
           "ask": "For each waiting entry, reply \"keep\" or \"not this trip\".", "from": "from {where}",
           "trip_event": "{trip}, message {event}", "used": "last used {trip} {day}", "never": "not used yet", "by": "per {event}",
           "none": "No long-term content yet.", "file": "file {path}", "join": ", ", "sep": " | "},
}


def memory_problems(memory: dict) -> list[str]:
    """What A's data must hold for the page to be honest (no invented source, no hidden status)."""
    problems = []
    if memory.get("schema") != "tripwright.memory/1":
        problems.append("schema must be tripwright.memory/1")
    for key in ("trip", "as_of", "entries"):
        if key not in memory:
            problems.append(f"memory needs {key}")
    seen = set()
    for entry in memory.get("entries", []):
        name = entry.get("id", "?")
        if name in seen:
            problems.append(f"{name}: listed twice")
        seen.add(name)
        if set(entry) - MEMORY_FIELDS or not {"id", "kind", "text", "status", "source"} <= set(entry):
            problems.append(f"{name}: fields must be {sorted(MEMORY_FIELDS)}")
            continue
        if entry["kind"] not in KINDS:
            problems.append(f"{name}: kind must be one of {KINDS}")
        if entry["status"] not in STATUSES:
            problems.append(f"{name}: status must be one of {STATUSES}")
        source = entry["source"] or {}
        if entry.get("quote") and source.get("nature") not in NATURES:
            problems.append(f"{name}: a quote needs the nature of its message, so a restatement is never shown as verbatim")
        if not (source.get("event") or source.get("path")):
            problems.append(f"{name}: say where it came from (a message or a file)")
    return problems


def rows(memory: dict, language: str = "zh") -> list[dict]:
    """Sections in a fixed order (preferences, companions, standing rules, materials); in each, waiting entries
    first because they need the user."""
    problems = memory_problems(memory)
    if problems:
        raise ValueError("; ".join(problems))
    w = WORDS["en" if language == "en" else "zh"]
    sections = []
    for kind in KINDS:
        entries = sorted((entry for entry in memory["entries"] if entry["kind"] == kind),
                         key=lambda entry: (STATUSES.index(entry["status"]), entry["id"]))
        if not entries:
            continue
        items = []
        for entry in entries:
            source = entry["source"]
            words = f"「{entry['quote']}」" if entry.get("quote") else entry["text"]
            origin = []
            if source.get("event"):
                where = w["trip_event"].format(trip=source["trip"], event=source["event"]) if source.get("trip") else source["event"]
                # The date is read in the message's own offset: the day the user said it, where they were.
                said = source["said_at"][5:10] if source.get("said_at") else None
                detail = [part for part in (source.get("speaker"), said, w["nature"].get(source.get("nature"))) if part]
                origin.append(w["from"].format(where=where) + (f"（{'，'.join(detail)}）" if language != "en" and detail else
                                                               f" ({', '.join(detail)})" if detail else ""))
            if source.get("path"):
                origin.append(w["file"].format(path=source["path"]))
            used = entry.get("last_used")
            items.append(dict(id=entry["id"], status=entry["status"], status_label=w["status"][entry["status"]],
                              subject=w["join"].join(entry.get("subject") or []), subject_ids=list(entry.get("subject") or []),
                              words=words,
                              gist=entry["text"] if entry.get("quote") else None, origin=w["sep"].join(origin),
                              # a use recorded for the whole trip has no day
                              used=w["used"].format(trip=used["trip"], day=short_day(used.get("date") or "")).rstrip() if used else w["never"],
                              basis=w["by"].format(event=entry["status_event"]) if entry.get("status_event") else None))
        sections.append(dict(kind=kind, title=w["kind"][kind], rows=items))
    return sections


def counts(memory: dict) -> dict:
    return {status: sum(entry["status"] == status for entry in memory["entries"]) for status in STATUSES}


def summary_line(memory: dict, language: str = "zh") -> str:
    return WORDS["en" if language == "en" else "zh"]["summary"].format(trip=memory["trip"], **counts(memory))


def row_text(row: dict, language: str = "zh") -> str:
    w = WORDS["en" if language == "en" else "zh"]
    parts = [row["status_label"], row["subject"], row["words"], row["origin"], row["used"], row["basis"]]
    return f"- [{row['id']}] " + w["sep"].join(part for part in parts if part)


def memory_lines(memory: dict, language: str = "zh") -> list[str]:
    w = WORDS["en" if language == "en" else "zh"]
    lines = [summary_line(memory, language)]
    if counts(memory)["pending"]:
        lines.append(w["ask"])
    sections = rows(memory, language)
    if not sections:
        lines.append(w["none"])
    for section in sections:
        lines += ["", f"## {section['title']}", *[row_text(row, language) for row in section["rows"]]]
    return lines
