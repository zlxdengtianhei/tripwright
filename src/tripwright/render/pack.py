"""Machine-readable pack → reading digest (CONTRACTS 3.1—3.3).

Pure: the output depends only on the pack (and, for v1 text-only full messages, on the optional message sources),
never on the clock or a database. Every required ID appears at least once. When the digest does not fit the budget, records
outside the hard and principle sections drop to ID-only lines section by section and their group becomes `partial`; hard
conditions, principles, hard nodes, full messages and dispositions never drop, so a pack that is still too large says
so in its header instead of losing them.
"""
from __future__ import annotations

import copy
import json
import re

from tripwright.render.labels import abbreviation, short_day, stamp_day_clock, stamp_with_zone, words
from tripwright.render.tokens import estimate
from tripwright.render.writing import writing_rules

GROUPS = ("items", "facts", "decisions", "hard_nodes", "checks_due", "questions")
GROUP_OF = {"item": "items", "fact": "facts", "decision": "decisions", "hard_node": "hard_nodes", "check": "checks_due",
            "question": "questions"}
# Least harmful to drop first. Decisions drop before facts because the committer still enforces their locks; a
# dropped fact has no such guard.
DROP_ORDER = ("goal", "rule", "preference", "check", "question", "decision", "fact")
CONTENT = {"item": "quote", "fact": "text", "decision": "choice", "hard_node": "text", "check": "question", "question": "question"}


def category(record: dict) -> str:
    return record["section"] if record["type"] == "item" else record["type"]


def pack_meta(pack: dict, required: dict | None = None) -> dict:
    """What the host receives next to the Markdown: the pack without records and full messages (CONTRACTS 3.1)."""
    meta = {key: value for key, value in pack.items() if key not in ("records", "full_quotes")}
    if required is not None:
        meta["required"] = required
    return meta


def render_pack(pack: dict, *, sources: dict | None = None) -> dict:
    """Return `{markdown, tokens, required}`.

    A wide pack's `full_quotes` are v1.1 objects `{event, text, said_at, speaker, nature}` (CONTRACTS 3.1). v1 packs
    carry only the text; then `sources` maps message IDs to `{received_at, speaker, nature}`, and without sources those
    lines name the speaker found on the records and leave out the time and the verbatim label rather than guess them.
    A field the message lacks is written as unknown, never guessed.
    """
    budget = pack["digest"]["budget_tokens"]
    dropped: list[str] = []
    markdown = compose(pack, dropped, sources, over=None)
    tokens = count(pack, markdown, degraded_required(pack, dropped))
    for batch in drop_queue(pack):
        if tokens <= budget:
            break
        trial = compose(pack, dropped + batch, sources, over=None)
        smaller = count(pack, trial, degraded_required(pack, dropped + batch))
        # An ID-only line can cost as much as short records; dropping them then loses text and saves nothing.
        if smaller < tokens:
            dropped += batch
            markdown, tokens = trial, smaller
    required = degraded_required(pack, dropped)
    if tokens > budget:
        kept = estimate("\n".join(line for line in markdown.splitlines() if is_kept(line, pack)))
        reason = "hard" if tokens - kept <= budget else "fixed"
        # The over-budget line is part of what the host reads, so the figure it states includes itself.
        for _ in range(3):
            markdown = compose(pack, dropped, sources, over=(tokens, budget, reason, tokens - kept))
            again = count(pack, markdown, required)
            if again == tokens:
                break
            tokens = again
    return {"markdown": markdown, "tokens": tokens, "required": required}


def is_kept(line: str, pack: dict) -> bool:
    """Lines that never drop: hard conditions, principles, hard nodes, full messages and dispositions."""
    records = pack["records"]
    match = re.match(r"^[^\[]*：\[([^\]]+)\] |^[^\[]*: \[([^\]]+)\] ", line)
    if match:
        record = records.get(match.group(1) or match.group(2))
        return bool(record) and category(record) in ("principle", "hard", "hard_node")
    return line.startswith(("- [E-", "原句全文", "Full messages", "逐条处置", "Disposition"))


def count(pack: dict, markdown: str, required: dict) -> int:
    meta = json.dumps(pack_meta(pack, required), ensure_ascii=False, separators=(",", ":"))
    return estimate(markdown) + estimate(meta)


def drop_queue(pack: dict) -> list[list[str]]:
    """One batch per section, least harmful first. A whole section drops at once: within a section the shared ID-only
    line makes a single dropped record cost as much as it saves, which would let a fact drop before a preference."""
    records = pack["records"]
    order = [record_id for group in GROUPS for record_id in pack["required"][group]["ids"]]
    batches = [[record_id for record_id in order
                if record_id in records and category(records[record_id]) == name and has_content(records[record_id])]
               for name in DROP_ORDER]
    return [batch for batch in batches if batch]


def has_content(record: dict) -> bool:
    return bool(record.get(CONTENT[record["type"]]))


def degraded_required(pack: dict, dropped: list[str]) -> dict:
    required = copy.deepcopy(pack["required"])
    thin = set(dropped) | {record_id for record_id, record in pack["records"].items() if not has_content(record)}
    for name, group in required.items():
        if group["status"] == "complete" and thin & set(group["ids"]):
            group["status"] = "partial"
    return required


def material_lines(pack: dict) -> list[str]:
    """The pack as a person reads it on the result page: header, completeness, one line per record, orders and what
    was left out. The writing rules, file pointers, full messages and dispositions are for the host, and a page has no
    budget, so nothing is shortened."""
    return compose(pack, [], None, over=None, reader=True).splitlines()


def compose(pack: dict, dropped: list[str], sources: dict | None, over: tuple | None, *, reader: bool = False) -> str:
    w = words(pack["digest"]["writing_rules"])
    zone = pack["header"]["window"]["timezone"]
    records = pack["records"]
    thin = set(dropped) | {record_id for record_id, record in records.items() if not has_content(record)}
    required = degraded_required(pack, dropped)
    lines = [title(pack, w), base(pack, w, over)]
    lines += integrity(required, w)
    order = [record_id for group in GROUPS for record_id in pack["required"][group]["ids"]]

    def section(names: tuple[str, ...]) -> list[str]:
        full, ids = [], []
        for record_id in order:
            record = records.get(record_id)
            if record is None or category(record) not in names:
                continue
            if record_id in thin:
                ids.append(record_id)
            else:
                full.append(record_line(record_id, record, pack, w))
        if ids:
            label = w["section"][category(records[ids[0]])]
            full.append(w["degraded"].format(section=label, ids=w["join"].join(f"[{record_id}]" for record_id in ids)))
        return full

    for name in pack["digest"]["sections"]:
        if name == "principles":
            lines += section(("principle",))
        elif name == "hard":
            lines += section(("hard",))
        elif name == "facts":
            lines += section(("fact",))
        elif name == "hard_nodes":
            lines += section(("hard_node",))
        elif name == "decisions":
            lines += section(("decision",))
        elif name == "preferences":
            lines += section(("preference",)) + section(("goal",)) + [order_line(order_, w) for order_ in pack["orders"]]
        elif name == "checks":
            lines += section(("check",)) + section(("question",))
        elif name == "rules":
            lines += section(("rule",))
            if not reader:
                lines.append(writing_rules(pack["digest"]["writing_rules"]))
        elif name == "omitted":
            lines.append(omitted_line(pack["omitted"], w))
        elif reader:
            continue
        elif name == "details":
            if pack["details"]:
                lines.append(details_line(pack["details"], w))
            if pack.get("candidate"):
                lines.append(w["candidate"].format(draft=pack["candidate"]["draft_id"]))
        elif name == "full_quotes" and pack.get("full_quotes"):
            lines.append(w["full_quotes"])
            lines += quote_lines(pack, sources, zone, w)
        elif name == "dispositions" and pack.get("dispositions") is not None:
            lines.append(dispositions_line(pack, w))
    return "\n".join(lines) + "\n"


def title(pack: dict, w: dict) -> str:
    """The first line. A live pack (one with `header.now`) starts with the moment of the read in the trip's zone, so
    the host answers from now, not from the plan's times; a pack written into the repository has no now."""
    header = pack["header"]
    window = header["window"]
    zone_name = abbreviation(window["timezone"], window["date"], window["from"])
    if window["from"] is None and window["to"] is None:
        span = w["all_day"]
    else:
        span = f"{window['from'] or ''}—{window['to'] or ''}"
    label = w["wide"] if pack["kind"] == "wide" else w["scenario"].get(pack["scenario"], pack["scenario"])
    place = w["all_places"] if header["place"] == "*" else header["place"]
    channel = w["channel"].get(header["channel"], header["channel"])
    line = w["header_title"].format(pack_id=pack["pack_id"], title=label, people=w["join"].join(header["people"]),
                                    window=f"{short_day(window['date'])} {span} {zone_name}", place=place, channel=channel)
    if header.get("now"):
        line = "# " + now_text(header["now"], w) + w["sep"] + line[2:]
    return line


def now_text(now: dict, w: dict) -> str:
    """“现在 00:25（Europe/Vienna，11-01 周日）” from a `now` record {at, date, time, weekday, timezone}."""
    return w["now"].format(time=now["time"], zone=now["timezone"], day=short_day(now["date"]), weekday=w["weekdays"][now["weekday"] - 1])


def now_line(now: dict, language: str = "zh") -> str:
    """The same sentence for a tool's result outside a pack (turn, state)."""
    return now_text(now, words(language))


def timing_parts(record: dict, zone: str, w: dict) -> list[str]:
    """Where a hard node's time comes from (A2): external deadline or plan target, the plan's margin, the last check or
    since when the plan has stored it, and whether that is older than the refresh time. Records without these fields
    (packs before A2) render as before."""
    if not any(key in record for key in ("deadline", "stored", "checked")):
        return []
    parts = [w["deadline"].get(record.get("deadline") or "unmarked", w["deadline"]["unmarked"])]
    if record.get("buffer_minutes"):
        parts.append(w["margin"].format(minutes=record["buffer_minutes"]))
    checked, stored = record.get("checked"), record.get("stored")
    if checked:
        source = w["checked_by"].format(source=checked["source"], when=stamp_day_clock(checked["at"], zone))
    elif stored and stored.get("at"):
        source = w["stored_only"].format(version=stored["version"], when=stamp_day_clock(stored["at"], zone))
    else:
        source = w["stored_unknown"]
    if record.get("may_have_changed"):
        source += ("，" if w["join"] == "、" else ", ") + w["may_have_changed"].format(hours=record.get("refresh_hours"))
    return parts + [source]


def base(pack: dict, w: dict, over: tuple | None) -> str:
    snapshot = pack["snapshot"]
    window = pack["header"]["window"]
    zone = window["timezone"]
    parts = [w["header_base"].format(version=snapshot["plan_version"])]
    repo = snapshot["repo"]
    if repo is None:
        parts.append(w["header_single"])
    else:
        parts.append(w["header_commit"].format(head=repo["head"]) if repo["head"] else w["header_no_commit"])
    received = snapshot["raw_watermark"]["last_received_at"]
    parts.append(w["header_raw"].format(when=stamp_with_zone(received, zone)) if received else w["header_no_raw"])
    compiled = pack["header"]["compiled_at"]
    same_day = stamp_day_clock(compiled, zone)[:5] == short_day(window["date"])
    parts.append(w["header_compiled"].format(when=stamp_with_zone(compiled, zone, with_day=not same_day)))
    if repo is not None:
        parts.append(w["header_unsynced"].format(count=len(repo["unsynced_ops"])))
    if over:
        tokens, budget, reason, fixed = over
        parts.append(w["over" if reason == "hard" else "over_fixed"].format(tokens=tokens, budget=budget, fixed=fixed))
    return w["sep"].join(parts)


def integrity(required: dict, w: dict) -> list[str]:
    lines = [w["unknown"].format(group=w["group"][name], reason=group["reason"])
             for name, group in required.items() if group["status"] == "unknown"]
    partial = [w["group"][name] for name, group in required.items() if group["status"] == "partial"]
    if partial:
        lines.append(w["partial"].format(groups=w["join"].join(partial)))
    return lines


def scope_text(scope: dict, window_date: str, w: dict, *, channel: bool) -> list[str]:
    parts = []
    start, end = scope.get("start"), scope.get("end")
    if not (start == end == window_date):
        if start and end:
            parts.append(short_day(start) if start == end else f"{short_day(start)}—{short_day(end)}")
        elif start or end:
            parts.append(f"{short_day(start)}—" if start else f"—{short_day(end)}")
    if scope.get("time_from") or scope.get("time_to"):
        parts.append(f"{scope.get('time_from') or ''}—{scope.get('time_to') or ''}")
    if scope.get("place"):
        parts.append(scope["place"])
    if channel and scope.get("channel"):
        parts.append(w["channel"].get(scope["channel"], scope["channel"]))
    return parts


def relay(record: dict, w: dict) -> str:
    speaker = record.get("speaker")
    return w["relay"].format(speaker=speaker) if speaker and speaker not in record.get("subject", [speaker]) else ""


def record_line(record_id: str, record: dict, pack: dict, w: dict) -> str:
    window_date = pack["header"]["window"]["date"]
    zone = pack["header"]["window"]["timezone"]
    kind = record["type"]
    label = w["section"][category(record)]
    if kind == "item":
        section = record["section"]
        parts = [] if section == "rule" else [w["strength"][record["strength"]], w["join"].join(record["subject"])]
        if record.get("memory") == "pending":
            # Imported from an earlier trip and applied until the user answers (the memory question says what to ask).
            parts.insert(0, w["memory_pending"])
        parts += scope_text(record.get("scope", {}), window_date, w, channel=section != "rule")
        parts.append(f"「{record['quote']}」{relay(record, w)}")
        if record.get("reason"):
            parts.append(w["reason"].format(reason=record["reason"]))
        if section in ("principle", "hard", "rule") or record["basis"] != "user_explicit":
            parts.append(w["basis"][record["basis"]])
    elif kind == "fact":
        parts = [record["text"] + relay(record, w)]
        parts += scope_text(record.get("scope", {}), window_date, w, channel=True)
        parts.append(w["since"].format(when=stamp_day_clock(record["true_from"], zone)))
        parts.append(w["basis"][record["basis"]])
    elif kind == "hard_node":
        latest = record.get("latest_departure")
        parts = [f"{short_day(record['date'])} {record['time']} {record['text']}", w["join"].join(record["subject"]),
                 w["latest"].format(clock=latest) if latest else w["latest_unknown"]]
        parts += timing_parts(record, zone, w)
    elif kind == "decision":
        parts = [f"{record['question']} → {record['choice']}{relay(record, w)}", w["join"].join(record["subject"])]
        if record.get("locked_paths"):
            # Only a must locks (D-53); the user's other decisions are choices the agent may change with a reason. A
            # record without `must` comes from a pack written before packs.py named it and keeps the old wording.
            held = w["locked"] if record.get("must", True) else w["adjustable"]
            parts.append(held.format(paths=w["join"].join(record["locked_paths"])))
        parts.append(w["decided_by"].get(record["decided_by"], record["decided_by"]))
    elif kind == "check":
        parts = [record["question"], w["due"].format(when=stamp_day_clock(record["due_at"], zone))]
        if record.get("overdue"):
            parts.append(w["overdue"])
        parts.append(w["basis"][record["basis"]])
    else:
        parts = question_parts(record, w)
    return f"{label}{w['colon']}[{record_id}] " + w["sep"].join(parts)


def question_parts(record: dict, w: dict) -> list[str]:
    """An open question says what is asked, the default, and who must do what: a possible contradiction lists both
    readings, an open choice its options, and each kind ends with the user's or the host's next step."""
    kind = record.get("kind")
    parts = [record["question"]]
    if kind == "undecided" and record.get("subject"):
        parts.append(w["join"].join(record["subject"]))
    if record.get("default"):
        parts.append(w["default"].format(text=record["default"]))
    if kind == "possible_contradiction" and record.get("readings"):
        parts.append(w["readings"].format(readings=("；" if w["join"] == "、" else "; ").join(record["readings"])))
    if kind == "undecided" and record.get("options"):
        parts.append(w["options"].format(options=w["join"].join(option_text(option) for option in record["options"])))
    if kind == "undecided" and not record.get("default"):
        kind = "undecided_no_default"
    if kind in w["todo"]:
        parts.append(w["todo"][kind])
    return parts


def option_text(option) -> str:
    if isinstance(option, dict):
        return str(option.get("text") or option.get("label") or option.get("choice") or option.get("id") or option)
    return str(option)


def order_line(order: dict, w: dict) -> str:
    if order["status"] == "user":
        return w["order_user"].format(ids=w["order_word"].join(order["order"]), event=order["source_event"])
    return w["order_none"].format(ids=w["join"].join(order["between"]))


def omitted_line(omitted: list[dict], w: dict) -> str:
    groups: dict[str, list[str]] = {}
    for entry in omitted:
        groups.setdefault(entry["reason"], []).append(entry["id"])
    parts = [w["omitted_line"].format(count=len(omitted))]
    parts += [f"{w['omitted'][reason]}{w['colon']}{w['join'].join(ids)}" for reason, ids in groups.items()]
    return w["sep"].join(parts)


def details_line(details: list[dict], w: dict) -> str:
    open_, close, between = ("（", "）", "；") if w["join"] == "、" else (" (", ")", "; ")
    entries = [detail["path"] + (f"{open_}{w['join'].join(detail['ids'])}{close}" if detail["ids"] else "") for detail in details]
    return w["details"] + between.join(entries)


def quote_lines(pack: dict, sources: dict | None, zone: str, w: dict) -> list[str]:
    speakers: dict[str, str] = {}
    for record in pack["records"].values():
        if record.get("source_event") and record.get("speaker"):
            speakers.setdefault(record["source_event"], record["speaker"])
    quotes = pack["full_quotes"]
    events = list(quotes)
    legacy = not any(isinstance(value, dict) for value in quotes.values())
    if sources and legacy:
        events.sort(key=lambda event_id: (sources.get(event_id, {}).get("received_at") or "", event_id))
    lines = []
    for event_id in events:
        value = quotes[event_id]
        if isinstance(value, dict):
            text, source = value.get("text"), dict(received_at=value.get("said_at"), speaker=value.get("speaker"),
                                                   nature=value.get("nature"))
        else:
            text, source = value, (sources or {}).get(event_id)
        if source:
            received, nature = source.get("received_at"), source.get("nature")
            parts = [stamp_with_zone(received, zone) if received else w["unknown_time"],
                     source.get("speaker") or speakers.get(event_id) or w["unknown_speaker"],
                     w["nature"].get(nature, nature) if nature else w["unknown_nature"]]
        else:
            parts = [speakers[event_id]] if event_id in speakers else []
        parts.append(f"「{text}」")
        lines.append(f"- [{event_id}] " + w["sep"].join(parts))
    return lines


def dispositions_line(pack: dict, w: dict) -> str:
    text = w["dispositions"]
    if any(order["status"] == "none" for order in pack["orders"]):
        text += w["dispositions_orders"]
    stop = "。" if w["join"] == "、" else ". "
    return text + stop + w["join"].join(entry["item_id"] for entry in pack["dispositions"])
