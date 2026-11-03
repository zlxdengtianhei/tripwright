"""Checks that the contract sample agrees with itself and with docs/CONTRACTS.md.

Each function returns a list of problems so tests can assert an empty list for the sample
and a non-empty list for a deliberately broken copy. Nothing here implements block A, B or C.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from tripwright.checks import departure_clock
from tripwright.ledger import Scope, overlaps, people_overlap

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLE = ROOT / "tests/fixtures/contract_sample"
SCHEMA = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))
ID = re.compile(r"\b(?:E|I|F|D|P|Q|L|V|CTX|DR|J|OP|N)-\d{6}-[a-z0-9]{3,6}\b")
UNSYNCED = {"received", "pending_sync"}
NEXT = {None: {"received"}, "received": {"pending_sync", "conflict"}, "pending_sync": {"pending_sync", "effective", "conflict"},
        "effective": set(), "conflict": set()}


def load(name: str):
    return json.loads((SAMPLE / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (SAMPLE / name).read_text(encoding="utf-8")


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def schema_errors(instance, definition: str) -> list[str]:
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"})
    return [f"{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in validator.iter_errors(instance)]


def candidate_trip(trip: dict, candidate: dict) -> dict:
    replaced = {day["date"] for day in candidate["replace_days"]}
    return dict(trip, days=[day for day in trip["days"] if day["date"] not in replaced] + candidate["replace_days"])


def blocks(trip: dict) -> dict[str, str]:
    """Block ID → the plan it sits in, by plan ID (the name only for plans stored without one): two alternatives with the
    same display name stay two plans."""
    return {block["id"]: plan.get("id") or plan["name"] for day in trip["days"] for plan in day.get("plans", [])
            for track in plan["tracks"] for block in track["blocks"]}


def anchors(trip: dict) -> dict[str, dict]:
    return {anchor["id"]: dict(anchor, date=day["date"]) for day in trip["days"] for anchor in day.get("anchors", [])}


def digest(journal_ids: list[str]) -> str:
    return "sha256:" + hashlib.sha256("\n".join(journal_ids).encode()).hexdigest()[:16]


def state_at(op: dict, moment: datetime) -> str | None:
    state = None
    for transition in op["transitions"]:
        if at(transition["at"]) <= moment:
            state = transition["state"]
    return state


# --- 1 有效快照 -------------------------------------------------------------

def derived_snapshot(moment: datetime, ledger: dict, writes: dict) -> dict:
    """The snapshot a reader taking one generation at `moment` must see (CONTRACTS 1.1, 1.3)."""
    versions = [version for version in ledger["versions"] if at(version["in_index_from"]) <= moment
                and (version["in_index_until"] is None or moment < at(version["in_index_until"]))]
    journal = [entry["id"] for entry in ledger["journal"] if at(entry["at"]) <= moment]
    events = [event for event in ledger["events"] if at(event["received_at"]) <= moment]
    heads = [head["commit"] for head in writes["heads"] if at(head["from"]) <= moment]
    return dict(plan_version=versions[-1]["number"] if versions else 0, plan_version_id=versions[-1]["id"] if versions else None,
                ledger_revision=len(journal), ledger_digest=digest(journal),
                raw_watermark=dict(count=len(events), last_event=events[-1]["id"] if events else None,
                                   last_received_at=events[-1]["received_at"] if events else None),
                head=heads[-1] if heads else None,
                unsynced_ops=[op["op_id"] for op in writes["ops"] if state_at(op, moment) in UNSYNCED])


def snapshot_problems(name: str, snapshot: dict, ledger: dict, writes: dict) -> list[str]:
    expected = derived_snapshot(at(snapshot["taken_at"]), ledger, writes)
    actual = {key: snapshot[key] for key in ("plan_version", "plan_version_id", "ledger_revision", "ledger_digest", "raw_watermark")}
    actual.update(head=snapshot["repo"]["head"], unsynced_ops=snapshot["repo"]["unsynced_ops"])
    problems = [f"{name}.{key}: {actual[key]!r} != derived {expected[key]!r}" for key in expected if actual[key] != expected[key]]
    if snapshot["repo"]["head_checked_at"] and at(snapshot["repo"]["head_checked_at"]) > at(snapshot["taken_at"]):
        problems.append(f"{name}: head_checked_at is after taken_at")
    return problems


def content_key(snapshot: dict) -> tuple:
    return snapshot["plan_version_id"], snapshot["ledger_digest"]


def freshness(job: dict, current: dict, acked: list[str], ledger: dict) -> tuple[str, list[str], list[str]]:
    """The single rule of CONTRACTS 1.2/1.4: stale on a changed content key, unsettled on unclaimed newer messages."""
    changed = [name for name, key in (("plan", "plan_version_id"), ("ledger", "ledger_digest")) if job[key] != current[key]]
    if changed:
        return "stale", changed, []
    newer = ledger["events"][job["raw_watermark"]["count"]:current["raw_watermark"]["count"]]
    claimed = {entry["source_event"] for entry in ledger["journal"] if at(entry["at"]) <= at(current["taken_at"])}
    unsettled = [event["id"] for event in newer if event["id"] not in claimed and event["id"] not in acked]
    return ("unsettled", [], unsettled) if unsettled else ("completed", [], [])


def sequence_problems(sequence: dict, snapshots: dict, ledger: dict, writes: dict) -> list[str]:
    problems = []
    ops = {op["op_id"]: op for op in writes["ops"]}
    last = None
    for step in sequence["steps"]:
        moment = at(step["at"])
        if last and moment < last:
            problems.append(f"step {step['n']}: time goes backwards")
        last = moment
        if "snapshot" in step and sequence["contexts"].get(step.get("ctx_id")) != step["snapshot"]:
            problems.append(f"step {step['n']}: ctx {step.get('ctx_id')} is not bound to {step['snapshot']}")
        if step["step"] == "turn":
            snapshot = snapshots[step["snapshot"]]
            if snapshot["raw_watermark"]["last_event"] != step["event"]:
                problems.append(f"step {step['n']}: the turn's own message is not in its snapshot")
            open_notices = [notice["notice_id"] for notice in writes["notices"] if at(notice["created_at"]) <= moment]
            if step.get("notices", []) != open_notices:
                problems.append(f"step {step['n']}: turn must return every open conflict notice {open_notices}")
        if "write" in step:
            write = step["write"]
            op = ops.get(write["op_id"])
            if op is None or state_at(op, moment) != write["state"]:
                problems.append(f"step {step['n']}: write state {write['state']} differs from the op sequence")
            unsynced = sum(state_at(candidate, moment) in UNSYNCED for candidate in writes["ops"])
            if write["unsynced"] != unsynced:
                problems.append(f"step {step['n']}: unsynced {write['unsynced']} != {unsynced}")
        if step["step"] == "result":
            status, changed, unsettled = freshness(snapshots[step["job_snapshot"]], snapshots[step["current"]], step["ack_events"], ledger)
            response = step["response"]
            if (response["status"], response["changed"], response["unsettled_events"]) != (status, changed, unsettled):
                problems.append(f"step {step['n']}: result {response['status']} but the rule gives {status} {changed} {unsettled}")
            if (response["result"] is None) != (status != "completed"):
                problems.append(f"step {step['n']}: only a completed result may carry a body")
        if step["step"] == "commit":
            status, _, unsettled = freshness(snapshots[step["draft_snapshot"]], snapshots[step["current"]], step["acked_events"], ledger)
            if step["response"]["accepted"] != (status == "completed"):
                problems.append(f"step {step['n']}: plan commit accepted={step['response']['accepted']} but the rule gives {status} {unsettled}")
        if step["step"] == "sync":
            if state_at(ops[step["op_id"]], moment) != step["state"]:
                problems.append(f"step {step['n']}: sync state differs from the op sequence")
    return problems


# --- 2 写入结果 -------------------------------------------------------------

def transition_problems(op: dict, writes: dict) -> list[str]:
    problems = []
    local = "/" not in writes["canonical"]
    heads = {head["commit"] for head in writes["heads"]}
    notices = {notice["notice_id"]: notice for notice in writes["notices"]}
    state, moment, commit = None, None, None
    for transition in op["transitions"]:
        allowed = NEXT[state] | ({"effective"} if local and state == "received" else set())
        if transition["state"] not in allowed:
            problems.append(f"{op['op_id']}: {state} -> {transition['state']} is not allowed")
        if moment and at(transition["at"]) < moment:
            problems.append(f"{op['op_id']}: transition time goes backwards")
        if state == transition["state"] == "pending_sync" and transition.get("commit") == commit:
            problems.append(f"{op['op_id']}: a repeated pending_sync must name the new local commit")
        if transition["state"] == "effective" and transition.get("commit") not in heads:
            problems.append(f"{op['op_id']}: effective commit {transition.get('commit')} never became the canonical head")
        if transition["state"] == "conflict":
            notice = notices.get(transition.get("notice_id"))
            if notice is None or notice["op_id"] != op["op_id"] or notice["created_at"] != transition["at"]:
                problems.append(f"{op['op_id']}: a conflict needs its notice written in the same step")
        state, moment, commit = transition["state"], at(transition["at"]), transition.get("commit", commit)
    return problems


# --- 3 包与回答 -------------------------------------------------------------

GROUP_TYPES = {"items": "item", "facts": "fact", "decisions": "decision", "hard_nodes": "hard_node", "checks_due": "check", "questions": "question"}
DERIVED = {"section", "speaker", "overdue", "latest_departure", "type"}


def pack_scope(pack: dict) -> tuple[list[str], Scope]:
    window = pack["header"]["window"]
    return pack["header"]["people"], Scope(start=window["date"], end=window["date"], time_from=window["from"], time_to=window["to"],
                                           place=pack["header"]["place"], channel=pack["header"]["channel"])


def window_end(pack: dict) -> datetime:
    """`to` on the window's date in its timezone, or the end of that day when `to` is open."""
    window = pack["header"]["window"]
    day, zone = date.fromisoformat(window["date"]), ZoneInfo(window["timezone"])
    if window["to"]:
        return datetime.combine(day, time.fromisoformat(window["to"]), tzinfo=zone)
    return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)


def non_required(record: dict, pack: dict) -> bool:
    """The only records a pack may leave out with `over_budget` (CONTRACTS 3.1, 3.10): in a narrow pack, open checks due
    after the window ends, goals in a quick or status pack, and the agent's own soft entries (basis neither user_explicit
    nor direction_set, strength prefer or optional). A wide pack leaves nothing out over budget."""
    if pack["kind"] == "wide":
        return False
    if record["type"] == "check":
        return at(record["due_at"]) > window_end(pack)
    basis = record.get("basis") or (record.get("source") or {}).get("basis")
    if record["type"] == "item" and basis not in ("user_explicit", "direction_set") and record["strength"] in ("prefer", "optional"):
        return True
    goal = record["type"] == "item" and record["kind"] == "goal" and not record.get("always") and record["strength"] not in ("must", "forbid")
    return goal and pack["scenario"] in ("quick", "status")


def out_of_scope(record: dict, people: list[str], scope: Scope) -> str | None:
    record_scope = Scope.model_validate(record.get("scope", {}))
    if not people_overlap(record["subject"], people):
        return "out_of_scope:people"
    for reason, fields in (("date", ("start", "end")), ("time", ("time_from", "time_to")), ("place", ("place",)), ("channel", ("channel",))):
        partial = Scope(**{field: getattr(scope, field) for field in fields})
        if not overlaps(record_scope, partial):
            return f"out_of_scope:{reason}"
    return None


def pack_problems(pack: dict, ledger: dict, trip: dict) -> list[str]:
    problems = []
    compiled = at(pack["header"]["compiled_at"])
    events = {event["id"]: event for event in ledger["events"]}
    required = [record_id for group in pack["required"].values() for record_id in group["ids"]]
    if set(pack["records"]) != set(required) or len(required) != len(set(required)):
        problems.append("records must hold exactly the required ids, each once")
    for name, group in pack["required"].items():
        for record_id in group["ids"]:
            record = pack["records"].get(record_id, {})
            if record.get("type") != GROUP_TYPES[name]:
                problems.append(f"{record_id}: listed in {name} but typed {record.get('type')}")
    known = {**ledger["records"], **anchors(trip)}
    for record_id, record in pack["records"].items():
        source = known.get(record_id)
        if source is None:
            problems.append(f"{record_id}: not in the sample ledger or plan")
            continue
        if record["type"] == "hard_node":
            if record["latest_departure"] != departure_clock(source["time"], source["buffer_minutes"], source["travel_minutes"],
                                                            day=source["date"], zone=trip["timezone"]):
                problems.append(f"{record_id}: latest_departure differs from the core calculation")
            source = {key: value for key, value in source.items() if key in record}
        mismatched = [key for key in record if key not in DERIVED and key in source and record[key] != source[key]]
        if mismatched:
            problems.append(f"{record_id}: differs from its source in {mismatched}")
        if "source_event" in record and record.get("speaker") != events[record["source_event"]]["speaker"]:
            problems.append(f"{record_id}: speaker differs from its message")
        if record["type"] == "check" and record["overdue"] != (at(record["due_at"]) <= compiled):
            problems.append(f"{record_id}: overdue flag is wrong at compile time")
    people, scope = pack_scope(pack)
    recorded = {entry["record_id"] for entry in ledger["journal"] if at(entry["at"]) <= compiled}
    omitted = {entry["id"]: entry["reason"] for entry in pack["omitted"]}
    for record_id in sorted(recorded):
        record = ledger["records"][record_id]
        if record["status"] not in ("active", "open"):
            continue
        # A principle (always) comes along whatever its people, time, place or channel.
        reason = None if record.get("always") else out_of_scope(record, people, scope)
        left_out = omitted.get(record_id) == "over_budget" and non_required(record, pack)
        if reason is None and record_id not in required and not left_out:
            problems.append(f"{record_id}: applicable but missing from the required set")
        if reason is not None and omitted.get(record_id) != reason:
            problems.append(f"{record_id}: out of scope ({reason}) but omitted as {omitted.get(record_id)}")
    for record_id, reason in omitted.items():
        if record_id in required or record_id not in recorded:
            problems.append(f"{record_id}: omitted entry is required or unknown at compile time")
        elif reason == "over_budget" and not non_required(ledger["records"][record_id], pack):
            problems.append(f"{record_id}: only a non-required record may be left out over budget")
    preferences = {record_id for record_id in pack["required"]["items"]["ids"] if pack["records"][record_id]["section"] == "preference"}
    for order in pack["orders"]:
        if not set(order["between"]) <= preferences:
            problems.append(f"order {order['between']} names records that are not required preferences")
    if pack["kind"] == "wide":
        sources = {record["source_event"] for record in pack["records"].values() if "source_event" in record}
        if pack["full_quotes"] != {event_id: events[event_id]["text"] for event_id in sorted(sources)}:
            problems.append("full_quotes must hold the full text of every required record's message")
        if [entry["item_id"] for entry in pack["dispositions"]] != pack["required"]["items"]["ids"]:
            problems.append("dispositions must list every required item once, in order")
        if any(entry["status"] != "unaddressed" for entry in pack["dispositions"]):
            problems.append("a pack's dispositions start unaddressed")
    return problems


def rendered_pack_problems(pack: dict, markdown: str, ledger: dict) -> list[str]:
    problems = []
    lines = markdown.splitlines()
    snapshot = pack["snapshot"]
    header = lines[1] if len(lines) > 1 else ""
    for needle in (f"基于 V{snapshot['plan_version']}", f"提交 {snapshot['repo']['head']}", f"未入仓 {len(snapshot['repo']['unsynced_ops'])} 条"):
        if needle not in header:
            problems.append(f"pack header misses {needle!r}")
    if pack["pack_id"] not in lines[0]:
        problems.append("pack title misses the pack id")
    if markdown.count("{{WRITING_RULES}}") != 1:
        problems.append("the writing-rules marker must appear once")
    for record_id in pack["records"]:
        if f"[{record_id}]" not in markdown:
            problems.append(f"required {record_id} is not rendered")
    id_only = " ".join(line for line in lines if "只列编号" in line or "IDs only" in line)
    for record_id, record in pack["records"].items():
        degradable = record.get("section") not in ("hard", "principle") and record["type"] != "hard_node"
        if degradable and f"[{record_id}]" in id_only:
            continue  # dropped to an ID-only line by the budget (CONTRACTS 3.3); its group must then be partial
        if "quote" in record and f"「{record['quote']}」" not in markdown:
            problems.append(f"{record_id}: quote is not rendered verbatim")
    omitted_line = next((line for line in lines if line.startswith("没放进来：")), "")
    if not omitted_line.startswith(f"没放进来：{len(pack['omitted'])} 条") or any(entry["id"] not in omitted_line for entry in pack["omitted"]):
        problems.append("omitted line must count and name every omitted id")
    allowed = set(pack["records"]) | {entry["id"] for entry in pack["omitted"]} | {event["id"] for event in ledger["events"]}
    allowed |= {pack["pack_id"]} | ({pack["candidate"]["draft_id"]} if pack["candidate"] else set())
    for token in ID.findall(markdown):
        if token not in allowed:
            problems.append(f"rendered pack mentions {token}, which the pack does not carry")
    for event_id, quote in (pack["full_quotes"] or {}).items():
        if f"[{event_id}]" not in markdown or f"「{quote}」" not in markdown:
            problems.append(f"full quote of {event_id} is not rendered")
    for entry in pack["dispositions"] or []:
        disposition_line = next((line for line in lines if line.startswith("逐条处置：")), "")
        if entry["item_id"] not in disposition_line:
            problems.append(f"disposition of {entry['item_id']} is not rendered")
    return problems


WORDS = {
    "zh": {"stop": "。", "unchecked": "没核：", "unchecked_second": "没核到：", "or": " 或 ", "decide": "{before} {labels}：",
           "colon": "：", "recommend": "推荐 ", "second": "# 第二段 · ", "checked": "核过的安排", "unfinished": "安排（没核完）",
           "draft": "（草稿）", "schedule": "# 今天的安排（从 {time} 起）"},
    "en": {"stop": ".", "unchecked": "Not checked: ", "unchecked_second": "Not checked: ", "or": " or ", "decide": "{before}: {labels}",
           "colon": ": ", "recommend": "Recommended ", "second": "# Second reply · ", "checked": "Checked plan",
           "unfinished": "Plan (not fully checked)", "draft": " (draft)", "schedule": "# Today's plan (from {time})"},
}


def downgrade_problems(pack: dict, markdown: str, required: dict, tokens: int) -> list[str]:
    """CONTRACTS 3.3 on a pack the renderer had to shrink: what it dropped to ID-only lines and how it reported that."""
    problems = []
    lines = markdown.splitlines()
    id_only = [line for line in lines if "只列编号" in line or "IDs only" in line]
    dropped = {token.strip("[]") for line in id_only for token in re.findall(r"\[[^\]]+\]", line)}
    for record_id in dropped:
        record = pack["records"].get(record_id, {})
        if record.get("section") in ("hard", "principle") or record.get("type") == "hard_node":
            problems.append(f"{record_id}: a hard condition, principle or hard node cannot drop to an ID-only line")
    for name, group in required.items():
        source = pack["required"][name]
        if set(group["ids"]) != set(source["ids"]):
            problems.append(f"{name}: a downgrade must keep every ID")
        if source["status"] == "unknown":
            if group["status"] != "unknown" or not any(line.startswith("适用集没定") and source["reason"] in line for line in lines):
                problems.append(f"{name}: an unknown group keeps its status and its reason is shown")
        elif dropped & set(group["ids"]) and group["status"] != "partial":
            problems.append(f"{name}: a group with ID-only records must be partial")
        elif not dropped & set(group["ids"]) and group["status"] != source["status"]:
            problems.append(f"{name}: status changed without a dropped record")
    if any(group["status"] == "partial" for group in required.values()) and not any(line.startswith("只列了编号") for line in lines):
        problems.append("a partial pack must say which groups list IDs only")
    over = re.search(r"约 (\d+) token，预算 (\d+)", lines[1] if len(lines) > 1 else "")
    if (tokens > pack["digest"]["budget_tokens"]) != bool(over):
        problems.append("the header must report an overrun exactly when the rendered pack exceeds its budget")
    return problems


def unchecked(line: dict, language: str = "zh") -> str:
    return f"{line['what']} → {line['consequence']} → {line['onsite']}{WORDS[language]['stop']}"


def search(row: dict, language: str = "zh") -> str:
    app, query = row["search"]["app"], row["search"]["query"]
    return f"{app}: search {query}" if language == "en" else f"{app}{' ' if app[-1].isascii() else ''}搜 {query}"


def leg(row: dict, language: str = "zh") -> str:
    """Transport line (CONTRACTS 3.4): the app and exact search are in the text; the link is only a shortcut."""
    route = f"{row['from']} → {row['to']}"
    middle = [search(row, language)] if row["search"]["query"] == route else [route, search(row, language)]
    parts = [row["verb"], *middle, row["timing"], row["note"], f"[{row['link']['label']}]({row['link']['url']})"]
    return " · ".join(part for part in parts if part)


def answer_problems(answer: dict, markdown: str) -> list[str]:
    problems = []
    lines = markdown.splitlines()
    language = answer["language"]
    w = WORDS[language]
    for index, notice in enumerate(answer["notices"]):
        if index >= len(lines) or lines[index] != notice["line"]:
            problems.append(f"notice {notice['notice_id']} must be line {index + 1}")
    for place in answer.get("places", []):
        if place["name_local"] not in lines:
            problems.append(f"place {place['place_id']} needs its own name line")
        if f"{place['line']} · {search(place, language)} · [{place['link']['label']}]({place['link']['url']})" not in lines:
            problems.append(f"place {place['place_id']} needs one explanation line with its search and link")
    for row in answer.get("legs", []):
        if leg(row, language) not in lines:
            problems.append(f"leg {row['from']} → {row['to']} is not rendered as one line")
    checks = answer.get("checks") or answer.get("unchecked") or ([answer["check"]] if answer.get("check") else [])
    prefix = w["unchecked_second"] if answer["skeleton"] == "second" else w["unchecked"]
    for line in checks:
        if not any(candidate.endswith(unchecked(line, language)) and candidate.startswith(prefix) for candidate in lines):
            problems.append(f"unchecked {line['what']} is not rendered as what → consequence → onsite")
    options = answer.get("options")
    if options:
        labels = w["or"].join(v["label"].split()[0] for v in options["variants"])
        if options["decide_before"] and w["decide"].format(before=options["decide_before"], labels=labels) not in lines:
            problems.append("options need their decide-before line")
        for variant in options["variants"]:
            first = variant["label"] + (f"{w['colon']}{variant['diff']}" if variant["diff"] else "")
            if first not in lines or variant["how"] not in lines:
                problems.append(f"variant {variant['label']} needs two lines: label and difference, then how")
        if options["recommend"] and f"{w['recommend']}{options['recommend']['label']}{w['colon']}{options['recommend']['reason']}" not in lines:
            problems.append("recommendation must be rendered with its factual reason")
    if answer["skeleton"] == "second":
        checked = answer["result"]["verdict"] == "passed" and answer["commit"]["accepted"]
        draft = answer["write"] is None or answer["write"]["state"] != "effective"
        title = w["second"] + (w["checked"] if checked else w["unfinished"]) + (w["draft"] if draft else "")
        if lines[len(answer["notices"])] != title:
            problems.append(f"second response title must be {title!r}")
        for entry in answer["schedule"]:
            body = leg(entry["leg"], language) if "leg" in entry else entry["text"]
            if f"{entry['time']} {body}" not in lines:
                problems.append(f"schedule entry {entry['time']} is not one line starting with its time")
        if w["schedule"].format(time=answer["schedule_from"]) not in lines:
            problems.append("second response needs the schedule heading")
    return problems


def disposition_problems(pack: dict, usages: list[dict], plan: dict) -> list[str]:
    problems = []
    plans = blocks(plan)
    by_item = {}
    for usage in usages:
        if usage["item_id"] in by_item:
            problems.append(f"{usage['item_id']}: disposed twice")
        by_item[usage["item_id"]] = usage
    for item_id in pack["required"]["items"]["ids"]:
        usage = by_item.get(item_id)
        if usage is None or usage["status"] == "unaddressed":
            problems.append(f"{item_id}: required item has no disposition")
            continue
        missing = [block for block in usage.get("block_ids", []) if block not in plans]
        if missing:
            problems.append(f"{item_id}: unknown blocks {missing}")
        if pack["records"][item_id]["strength"] in ("must", "forbid") and usage["status"] == "traded_off":
            problems.append(f"{item_id}: a hard condition cannot be traded off")
    for order in pack["orders"]:
        if order["status"] != "none":
            continue
        chosen = []
        for item_id in order["between"]:
            usage = by_item.get(item_id, {})
            if usage.get("status") not in ("used", "host_reported"):
                problems.append(f"{item_id}: two user preferences without a user order cannot be traded off by the agent")
            chosen.append({plans.get(block) for block in usage.get("block_ids", [])})
        if len(chosen) == 2 and chosen[0] & chosen[1]:
            problems.append(f"{order['between']}: squeezed preferences must land in different options")
    return problems


# --- 4 写域与生成文件 --------------------------------------------------------

def glob_regex(pattern: str) -> re.Pattern:
    expression = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            expression += ".*"
            index += 2
        elif pattern[index] == "*":
            expression += "[^/]*"
            index += 1
        else:
            expression += re.escape(pattern[index])
            index += 1
    return re.compile(expression + r"\Z")


def owners(manifest: dict, path: str) -> list[str]:
    return [name for name, domain in manifest["domains"].items() if any(glob_regex(pattern).match(path) for pattern in domain["paths"])]


def manifest_problems(manifest: dict, files: list[str], contracts: str) -> list[str]:
    problems = []
    for path in files:
        found = owners(manifest, path)
        if len(found) != 1:
            problems.append(f"{path}: owned by {found or 'nobody'}")
    for probe in manifest["probes"]:
        found = owners(manifest, probe["path"])
        if found != [probe["owner"]]:
            problems.append(f"probe {probe['path']}: owned by {found}, expected {probe['owner']}")
    for entry in manifest["generated_in_repo"]:
        found = owners(manifest, entry["path"].replace("*", "x"))
        if found != [entry["generator"]]:
            problems.append(f"generated {entry['path']} lies in {found}, not its generator {entry['generator']}")
        for source in entry["sources"]:
            if not owners(manifest, source.replace("*", "x")):
                problems.append(f"source {source} of {entry['path']} has no owner")
    for entry in manifest["user_repo"]:
        for source in entry["sources"]:
            if entry["generator"] != "user" and entry["generator"] not in owners(manifest, source.replace("*", "x")):
                problems.append(f"user-repo {entry['path']}: source {source} is outside generator {entry['generator']}")
    for path in files + [probe["path"] for probe in manifest["probes"]]:
        if path.endswith(".tmpl") and owners(manifest, path) != ["C"]:
            problems.append(f"{path}: templates are written by C only")
    for name, domain in manifest["domains"].items():
        for pattern in domain["paths"]:
            if f"`{pattern}`" not in contracts:
                problems.append(f"{name} glob {pattern} is not listed in docs/CONTRACTS.md")
    return problems


def mutated(value, path: list, new):
    """A deep copy with one nested field replaced, for bad-sample tests."""
    result = copy.deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    if new is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = new
    return result
