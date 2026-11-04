#!/usr/bin/env python3
"""Mechanical checks on the answers of one host replay: a regression signal, not a judge.

Reads `turn<N>.stream.jsonl` in a run directory written by `host_replay.py` and checks what the user saw:
- two_parts: the turn shows exactly two texts, a first part and a second part titled "# 第二段". A first part that ends
  with turn's first_part.later (4a) is the two-reply shape: the replay sent “继续” and the second part is in that reply
  (host_replay's `{"type": "user", "replay": "continue"}` line); the later line is left out of the first-part checks.
- clock_in_first_part: the first two lines of the first part name a time within 5 minutes of the clock when it was sent.
- title_time_not_later: no time in the second part's title, and no "HH:MM 查", is later than the moment it was sent.
- no_tags: no text contains markup tags such as `<thinking>` or `<br>`, or internal wording.
- first_line_times_checked: every time in the first part's first line, except the clock time and a check stamp
  ("HH:MM 查的", "MM-DD HH:MM 在 … 查的"),
  appears again in the second part on a line that says 核过, 网上说法, 搜索摘要, 没核, 和第一段不同 or 不变, or under such a heading.
- checked_needs_tool: "核过" appears only in a turn where a Tripwright lookup ran (the `query` tool, or the tripwright
  command line from Bash); what a web page says is 网上说法, not 核过.
- fallback_later: on a line with "误了", the fallback named after it does not leave before the choice that was missed
  (the time right after "误了", else the last time in the same clause, else the first time on the line).
- only_inside_window: a line that says 唯一, 只有这一班, 最后一班 or 之前没有 about a time (and is not marked 没核 or
  网上说法) needs a lookup in this turn whose `window.to` reaches that time.
- denied_needs_coverage: a denial must fall within the looked-up spans, with no contrary connection found.
- count_needs_continuous: a remaining-service count needs continuous coverage, with all found departures included.
- direct_needs_one_ride: "直达" or "不用换车" must match a connection with exactly one ride; window checks also
  use the connections the lookups found, including later departures and the next train after a claimed last one.
- checked_has_evidence: every line that says 核过 matches this turn's evidence: an evidence ID it names, a time that a
  transit or latest_departure result holds, or words of a `web_evidence` claim or quote that counts as checked.

A7 adds three under the report's "more" (11-04: first_part_lines and plan_heading are gone, as in check_answer):
internal_words (外部截止, 计划目标, evidence IDs, field names), unreachable_needs_lookup (赶不上 and the like need a
latest_departure result, and a plain one must not name a departure the tool lists as reachable) and meals_named (a
hungry question gets two named places to eat with their hours and a source: the trip's places or a recorded page).

A10 (D-67) adds two more under "more": frame_matches_server (the second part's first-line 最晚/末班 names a time of the
frame the service computed for the question; a replay sees only compact results, so the frame is rebuilt from the
latest_departure result the host got, with the package's `frame_of`) and first_part_copied (the first part is the
`first_part.text` that turn returned, sent as it is: only a form of address before line 1, 您 for 你 and punctuation may
differ; in another language, the same lines with the same times; a stream without a turn call has nothing to compare).

Times are compared in the trip's time zone (`timezone` in the workspace's `tripwright.yaml`, else Europe/Vienna). A replay
run with a pinned clock (`run.json` `now`, A7) is checked against that clock: a text's moment is the one pinned for its
message plus the seconds from sending it until the text appeared (`text_at`); otherwise the record's own time (D-47).
Writes `answer_checks.json` into the run directory and prints one line per turn and check.

The checks themselves are the package's (`tripwright.render.check_answer`, A6), the same ones `query(kind="check_answer")`
runs on a draft before it is sent; this script only reads the replay's streams: what the user saw, when, and what this
conversation's Tripwright lookups returned.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
try:
    from tripwright.render import check_answer as checks
except ImportError:  # run from a checkout without the package installed: use its source, or its uv environment
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from tripwright.render import check_answer as checks
    except ImportError:
        if os.environ.get("ANSWER_CHECKS_REEXEC"):
            raise
        os.environ["ANSWER_CHECKS_REEXEC"] = "1"
        os.execvp("uv", ["uv", "run", "--project", str(ROOT), "python", str(Path(__file__).resolve()), *sys.argv[1:]])

TIME, CHECKED_WORD, ahead, clock_times = checks.CLOCK, checks.CHECKED_WORD, checks.ahead, checks.clock_times
# turn's first_part.later (4a, D-88): a first part may end with it, and the second part then comes in the reply to “继续”
try:
    from tripwright.service import LATER
except ImportError:
    LATER = {}
LATER = dict({"zh": "要核过的第二段，回我一句“继续”。", "en": "Reply “go on” for the checked second part."}, **LATER)
LOOKUP_COMMAND = re.compile(r"\btripwright\b[^\n|;&]*\bquery\b")
EVIDENCE_ID = checks.EVIDENCE_ID
# first_part_copied: what may differ between turn's first_part.text and the first part sent
ADDRESS_CHARS = 12  # a form of address before line 1 ("Lin，", "Hi Lin, ")
LOOSE = re.compile(r"[\s，,；;：:（）()。.、！!？?“”\"'‘’·]")
# S5b (s5_mcponly_r1 question 1): a text of zero-width characters (and spaces) shows the user nothing: it is no part, only
# a text too many (no_extra_text)
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


def invisible(text: str) -> bool:
    return not text.translate(ZERO_WIDTH).strip()


def shown_as(text: str) -> str:
    """A text as no_extra_text names it: its first line, or the code points of an invisible one."""
    return " ".join(f"U+{ord(char):04X}" for char in text.strip()) if invisible(text) else text.strip().splitlines()[0][:50]


def texts(stream: Path, zone: ZoneInfo, pinned: list[datetime] | None = None) -> list[tuple[int, str]]:
    """The main agent's visible texts, each with the local clock minute of the message that carried it: the replay's
    pinned clock at each text (`pinned`, from `pinned_moments`) when it has one for every text, else the record's time."""
    found = []
    for line in stream.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") != "assistant" or event.get("parent_tool_use_id"):
            continue
        stamp = event.get("timestamp")
        for block in event["message"].get("content", []):
            if block.get("type") == "text" and block["text"].strip():
                found.append((datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None, block["text"]))
    if pinned is not None and len(pinned) == len(found):  # host_replay lists the same texts in the same order
        found = [(moment, text) for moment, (_, text) in zip(pinned, found)]
    return [((at := moment.astimezone(zone)).hour * 60 + at.minute, text) for moment, text in found if moment is not None]


def pinned_moments(run: Path, turn: int) -> list[datetime] | None:
    """The pinned clock (`--now`, A7) at each text the user saw in turn `turn`: host_replay pins message N to the Nth
    moment of `run.json`'s `now` (after the last moment the clock runs on from it), and `text_at` gives each text's
    seconds since its message was sent. None when the replay ran on the real clock."""
    meta = json.loads((run / "run.json").read_text(encoding="utf-8")) if (run / "run.json").is_file() else {}
    moments = [datetime.fromisoformat(moment.strip().replace("Z", "+00:00")) for moment in str(meta.get("now") or "").split(",") if moment.strip()]
    turns = {entry.get("turn"): entry for entry in meta.get("turns") or [] if isinstance(entry, dict)}
    if not moments or turn not in turns:
        return None
    pinned = min(turn, len(moments))
    sent = moments[pinned - 1] + timedelta(seconds=sum(turns.get(n, {}).get("seconds") or 0 for n in range(pinned, turn)))
    return [sent + timedelta(seconds=at) for at in turns[turn].get("text_at") or []]


def continued_at(stream: Path) -> int | None:
    """How many of the main agent's texts came before the replay's “继续” line (host_replay), or None."""
    count = 0
    for line in stream.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") == "user" and event.get("replay") == "continue":
            return count
        if event.get("type") == "assistant" and not event.get("parent_tool_use_id"):
            count += sum(1 for block in event["message"].get("content", []) if block.get("type") == "text" and block["text"].strip())
    return None


def lookup_ran(stream: Path) -> bool:
    """Whether the main agent or a subagent ran a Tripwright lookup in this turn."""
    for line in stream.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        content = (json.loads(line).get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") != "tool_use":
                continue
            if block["name"].endswith("__query") or (block["name"] == "Bash" and LOOKUP_COMMAND.search(str(block.get("input", {}).get("command", "")))):
                return True
    return False


def lookups(stream: Path, zone: ZoneInfo = ZoneInfo("Europe/Vienna")) -> dict:
    """What this turn's Tripwright lookups returned (main agent and subagents, MCP or command line)."""
    calls, found = {}, dict(windows=[], times=set(), ids=set(), counted=[], reachable=set(), latest=0, sources=[], lookups=[])
    for line in stream.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                command = str(block.get("input", {}).get("command", "")) if block["name"] == "Bash" else ""
                if block["name"].endswith("__query"):
                    calls[block["id"]] = block.get("input", {}).get("kind", "")
                elif LOOKUP_COMMAND.search(command):
                    kind = re.search(r"\bquery\s+(\w+)", command)
                    calls[block["id"]] = kind.group(1) if kind else ""
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                raw = block.get("content")
                text = "".join(part.get("text", "") for part in raw if isinstance(part, dict)) if isinstance(raw, list) else str(raw or "")
                # A result too large for the host is saved to a file and only its path comes back.
                saved = re.search(r"saved to (\S+\.(?:txt|json))", text)
                if saved and Path(saved.group(1)).is_file():
                    text = Path(saved.group(1)).read_text(encoding="utf-8", errors="replace")
                result = first_json(text) or {}
                kind = calls[block["tool_use_id"]] or next((name for name in ("latest_departure", "transit", "web_evidence")
                                                            if f'"kind": "{name}"' in text or f'"kind":"{name}"' in text), "")
                found["ids"] |= set(EVIDENCE_ID.findall(text))
                if kind in ("transit", "latest_departure"):
                    found["times"] |= clock_times(text, zone)
                    # A window's end may be local HH:MM or an ISO time with an offset.
                    ends = [str(window["to"]) for window in walk(result, "window") if isinstance(window, dict) and window.get("to")]
                    # Output the agent cut short (head, its own summary) is no longer JSON; the window may still be readable.
                    ends = ends or re.findall(r'"window"\s*:\s*\{[^{}]*?"to"\s*:\s*"([^"]+)"', text)
                    found["windows"] += [end for to in ends for end in clock_times(to, zone)]
                    # Full evidence is the service's input to evidence_found. Older/compact results may only
                    # carry connection views; normalize their explicit legs, without inferring any omitted leg.
                    record = dict(result.get("evidence") or {})
                    record.setdefault("kind", kind)
                    record.setdefault("id", "")
                    record.setdefault("fetched_at", event.get("timestamp") or "1970-01-01T00:00:00+00:00")
                    record.setdefault("window", result.get("window") or {})
                    if "value" not in record:
                        connections = result.get("connections") or []
                        if kind == "latest_departure":
                            record["value"] = latest_value(result)
                        elif connections and any("legs" not in connection for connection in connections):
                            # Legacy summaries omit the legs. Keep their window-only check instead of
                            # representing them as an empty lookup (which would assert nothing was found).
                            record["value"] = None
                        else:
                            record["value"] = [
                                {"legs": [{"mode": "walk" if leg.get("walk") else "transit", "line": leg.get("line"),
                                           "depart": leg.get("depart"), "arrive": leg.get("arrive"),
                                           "origin": leg.get("from"), "destination": leg.get("to")}
                                          for leg in connection.get("legs") or []]}
                                for connection in connections]
                    records = [record] + node_records(result, record)
                    parsed = checks.evidence_found(records, zone, datetime.min.replace(tzinfo=zone), datetime.max.replace(tzinfo=zone))
                    found["lookups"] += parsed["lookups"]
                if kind == "latest_departure":
                    found["latest"] += 1
                    taken = [result.get(key) for key in ("primary", "fallback", "later", "candidates")]
                    found["reachable"] |= clock_times(json.dumps(taken), zone) if any(taken) else set()
                if kind == "web_evidence":
                    evidence = result.get("evidence") or {}
                    found["sources"].append(f"{evidence.get('url', '')} {evidence.get('claim', '')} {evidence.get('quote', '')}")
                if kind == "web_evidence" and result.get("counts_as_checked"):
                    evidence = result.get("evidence") or {}
                    found["counted"].append(f"{evidence.get('claim', '')} {evidence.get('quote', '')}")
    return found


def latest_value(result: dict) -> dict:
    """A compact latest_departure result as the evidence value check_answer reads (A10): tonight's last connection
    puts its last ones where a deadline result has its first choice and routes, and both carry the frame the service
    stores in its own evidence (`value.frame`), rebuilt with the package's `frame_of` from what the host saw."""
    from tripwright.query import frame_of

    target = result.get("target") or {}
    meaning = target.get("meaning")
    if not meaning:
        return result

    def rides(view):  # a compact route lists its rides, a compact connection its legs
        return dict(view, legs=view.get("legs") or view.get("rides") or []) if view else None

    if meaning == "last_connection":
        last, regular, rail = (rides(result.get(key)) for key in ("last", "last_without_extra_ticket", "last_train"))
        backed = ([entry for entry in (last, regular) if entry] if result.get("tonight_ends_after_last") else []) + ([rail] if rail else [])
        frame = frame_of(meaning, [last, regular, rail], result.get("first_line"), {},
                         last=[time for entry in backed for time in (entry.get("leave"), (entry.get("boards") or {}).get("time")) if time])
        return dict(result, primary=last, later=[], fallback=None, candidates=[entry for entry in (regular, rail) if entry], frame=frame)
    first = result.get("first_line") or next((sentence for sentence in result.get("sentences") or []
                                              if sentence.startswith(("要 ", "赶 ", "To arrive", "To catch"))), None)
    shown = [rides(view) for view in [result.get("primary"), *(result.get("later") or []), *(result.get("candidates") or [])]]
    return dict(result, frame=frame_of(meaning, shown, first, {}, targets=[target.get("time"), target.get("plan_by")]))


def node_records(result: dict, record: dict) -> list[dict]:
    """The hard node behind a last-connection result as its own latest_departure record (A10 replay, turn 2 L17): the
    service keeps the node's lookup as separate evidence, which a replay never sees; the result carries its first choice,
    target and coverage, so “要 05:45 前到，最晚 04:58 出门” is read against that lookup as the service reads it."""
    latest = (result.get("hard_node") or {}).get("latest") or {}
    if not latest.get("connection"):
        return []
    target = dict(latest.get("target") or {}, meaning="arrival_deadline")
    value = dict(target=target, primary=dict(latest["connection"], within_limit=True), later=[], fallback=None, candidates=[],
                 window=dict(covers_target=bool(latest.get("covers_target"))))
    return [dict(record, id="", value=dict(value, frame=latest_value(value)["frame"]))]


def turn_draft(stream: Path) -> str | None:
    """The first_part.text of this turn's `turn` call (A10), or None when no turn result carries one."""
    calls, draft = set(), None
    for line in stream.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        content = (json.loads(line).get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use" and str(block.get("name", "")).endswith("__turn"):
                calls.add(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                raw = block.get("content")
                text = "".join(part.get("text", "") for part in raw if isinstance(part, dict)) if isinstance(raw, list) else str(raw or "")
                draft = ((first_json(text) or {}).get("first_part") or {}).get("text") or draft
    return draft


def loose(line: str) -> str:
    return LOOSE.sub("", line.replace("您", "你")).casefold()


def copied(sent: str, draft: str) -> str | None:
    """Why `sent` is not `draft` sent as it is, or None (first_part_copied)."""
    said, wanted = lines_of(sent), lines_of(draft)
    if checks.detect_language(sent) != checks.detect_language(draft):
        times = [[sorted(clock_times(line, ZoneInfo("UTC"))) for line in lines] for lines in (said, wanted)]
        return None if times[0] == times[1] else f"in another language, but {len(said)} lines with times {times[0]} for the draft's {len(wanted)} with {times[1]}"
    if said and wanted and loose(said[0]) != loose(wanted[0]) and loose(said[0]).endswith(loose(wanted[0])) \
            and len(loose(said[0])) - len(loose(wanted[0])) <= ADDRESS_CHARS:
        said = [wanted[0], *said[1:]]  # a form of address before line 1
    for number in range(max(len(said), len(wanted))):
        here, there = (said[number] if number < len(said) else ""), (wanted[number] if number < len(wanted) else "")
        if loose(here) != loose(there):
            return f"line {number + 1} is “{here[:50]}”, turn's first_part has “{there[:50]}”"
    return None


def first_json(text: str):
    start = text.find("{")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0] if start >= 0 else None
    except ValueError:
        return None


def walk(value, key: str):
    """Every value stored under `key` anywhere in a JSON value."""
    if isinstance(value, dict):
        for name, inner in value.items():
            if name == key:
                yield inner
            yield from walk(inner, key)
    elif isinstance(value, list):
        for inner in value:
            yield from walk(inner, key)


def lines_of(text: str) -> list[str]:
    return [line for _, line in checks.numbered(text)]


def loose_end(text: str) -> str:
    # quote marks do not count: a host may copy “继续” as "继续" (S4e, dry_cc_ns_synth)
    return "".join(text.split()).translate(str.maketrans("", "", "“”\"「」『』'‘’")).rstrip("。.!！")


def without_later(text: str) -> tuple[str, bool]:
    """The first part without a last line that is turn's first_part.later, and whether it had one."""
    lines = text.rstrip().splitlines()
    if lines and any(loose_end(lines[-1]).endswith(loose_end(sentence)) for sentence in LATER.values()):
        return "\n".join(lines[:-1]).rstrip(), True
    return text, False


def parts(shown: list[tuple[int, str]], draft: str | None = None):
    """split_parts, with the first part's `later` line left out of the checks on the first part (its 8 lines, its clock,
    first_part_copied: turn's first_part.text does not carry it)."""
    first, second = split_parts([(minute, without_later(text)[0]) for minute, text in shown], draft)
    return first, second


def continued_shape(shown: list[tuple[int, str]], continued_at: int | None, draft: str | None = None) -> str | None:
    """What is wrong with a two-reply turn (4a): a first part that ends with first_part.later needs the replay's “继续”
    and the second part after it; a second part before it, or none after it, is wrong. None when right or not this shape."""
    first, second = parts(shown, draft)
    asked = any(without_later(text)[1] for _, text in (shown[:continued_at] if continued_at is not None else shown))
    if not asked:
        return None if continued_at is None else "“继续” was sent but no first part ended with first_part.later"
    if continued_at is None:
        return "the first part asks for “继续”, but the replay did not send it"
    if second is None:
        return "no second part in the reply to “继续”"
    index = [text for _, text in shown].index(second[1])
    return None if index >= continued_at else "the second part came before “继续”, in the same reply as the first part"


def split_parts(shown: list[tuple[int, str]], draft: str | None = None):
    second = next((item for item in shown if checks.SECOND_TITLE.match(lines_of(item[1])[0])), None)
    # The first part is the last answer-sized text before the second part; a stray status line is not it. With turn's
    # first_part (A10): the text that copies it, whatever its length, else the last text before the second part (what
    # the host sent instead of the draft is what first_part_copied compares).
    before = [item for item in (shown[:shown.index(second)] if second else shown) if not invisible(item[1])]
    if draft and before:
        return next((item for item in reversed(before) if copied(item[1], draft) is None), before[-1]), second
    first = next((item for item in reversed(before) if len(lines_of(item[1])) >= 3), None)
    return first, second


def outcome(problems: list[dict]) -> dict:
    return dict(ok=not problems, detail="; ".join(dict.fromkeys(f"L{entry['line']}: {entry['why']}" if entry["line"] else entry["why"]
                                                                for entry in problems)) or "none")


def check_turn(shown: list[tuple[int, str]], lookup: bool = False, found: dict | None = None, draft: str | None = None,
               continued_at: int | None = None, needs_continue: bool = False) -> dict[str, dict]:
    """The checks on one turn: what the user saw (texts with the local minute they were sent), whether a Tripwright
    lookup ran, what this conversation's lookups found so far, and turn's first_part. `continued_at`: how many of the
    texts came before the replay's “继续” (a turn answered in two replies, 4a), None for one reply. `needs_continue`: the
    host cannot run background subagents (Codex, Claude Code without them), so its first part must end with the later
    line and its second part come after “继续” (D-93; S4h, stage 4 judge: codex_r1 sent both parts in one reply)."""
    found = found or dict(windows=[], times=set(), ids=set(), counted=[])
    results = {}
    first, second = parts(shown, draft)
    shape = continued_shape(shown, continued_at, draft)
    # The later line is the service's own sentence (“要核过的第二段，回我一句‘继续’”): its 核过 is not a claim (S4h,
    # stage 4 judge: checked_has_evidence flagged it in ns_on, ns_off, mcponly)
    bare = [(minute, without_later(text)[0]) for minute, text in shown]
    kept = split_parts(bare, draft)
    extra = [text for item in bare for text in [item[1]] if item is not kept[0] and item is not kept[1] and text.strip()]
    results["no_extra_text"] = dict(ok=not extra, detail=(f"{len(extra)} other texts: " + " | ".join(shown_as(text) for text in extra))
                                    if extra else "only the first and the second part")
    asked = any(without_later(text)[1] for _, text in shown)
    results["later_when_no_subagents"] = (dict(ok=asked and continued_at is not None and shape is None,
                                               detail="first part ends with the later line; second part after “继续”" if asked and continued_at is not None and shape is None
                                               else (shape or "the first part does not end with the later line; both parts came in one reply"))
                                          if needs_continue else dict(ok=True, detail="host can run background subagents; either shape"))
    two = "; two replies: first part, then the second part after “继续”" if continued_at is not None and shape is None else ""
    results["two_parts"] = dict(ok=len(shown) == 2 and first is not None and second is not None and shape is None,
                                detail=f"{len(shown)} texts; first part {'found' if first else 'missing'}, second part {'found' if second else 'missing'}"
                                       + two + (f"; {shape}" if shape else ""))
    results["clock_in_first_part"] = outcome(checks.clock_problems(first[1], first[0])) if first else dict(ok=False, detail="no first part")
    results["title_time_not_later"] = outcome(checks.title_problems(second[1], second[0])) if second else dict(ok=False, detail="no second part")
    results["no_tags"] = outcome([entry for _, text in bare for entry in checks.tag_problems(text)])
    results["first_line_times_checked"] = (outcome(checks.first_line_problems(first[1], second[1], first[0])) if first and second
                                           else dict(ok=False, detail="a part is missing"))
    claimed = [line for _, text in bare for line in lines_of(text) if CHECKED_WORD.search(line)]
    results["checked_needs_tool"] = dict(ok=lookup or not claimed,
                                         detail=("a Tripwright lookup ran" if lookup else f"no Tripwright lookup; 核过 on {len(claimed)} lines")
                                                + ("" if lookup or not claimed else f": {claimed[0][:60]}"))
    results["fallback_later"] = outcome([entry for _, text in bare for entry in checks.fallback_problems(text)])
    clock = first[0] if first else (shown[0][0] if shown else 0)
    results["only_inside_window"] = outcome([entry for _, text in bare for entry in checks.window_problems(text, clock, found["windows"], found.get("lookups") or None)])
    coverage = [entry for _, text in bare for entry in checks.coverage_problems(text, clock, found.get("lookups", []))]
    for name in ("denied_needs_coverage", "count_needs_continuous"):
        results[name] = outcome([entry for entry in coverage if entry["check"] == name])
    results["direct_needs_one_ride"] = outcome([entry for _, text in bare for entry in checks.direct_problems(text, found.get("lookups", []))])
    results["checked_has_evidence"] = outcome([entry for _, text in bare for entry in checks.evidence_problems(text, found)])
    return results


def more_checks(shown: list[tuple[int, str]], found: dict, question: str | None, places: list[dict], draft: str | None = None) -> dict[str, dict]:
    """The A7 checks on a turn, kept apart from the ones above so their names stay as they were: no internal words in
    either part, a cannot-reach sentence backed by latest_departure, two named places to eat with
    the source of their hours when the question is hungry. A10: the second part's first line keeps the service's frame,
    and the first part is turn's first_part sent as it is."""
    first, second = parts(shown, draft)
    found = dict(checks.NO_EVIDENCE, **found)
    results = {"internal_words": outcome([entry for _, text in shown for entry in checks.internal_problems(without_later(text)[0])])}
    results["unreachable_needs_lookup"] = outcome(checks.unreachable_problems(second[1], found)) if second else dict(ok=False, detail="no second part")
    results["meals_named"] = outcome(checks.meal_problems(second[1], question, found, places)) if second else dict(ok=False, detail="no second part")
    results["frame_matches_server"] = (outcome(checks.frame_problems(second[1], second[0], found["lookups"])) if second
                                       else dict(ok=False, detail="no second part"))
    if draft is None:
        results["first_part_copied"] = dict(ok=True, detail="no turn result with first_part in this turn's stream")
    elif first is None:
        results["first_part_copied"] = dict(ok=False, detail="turn returned first_part, but no first part was sent")
    else:
        why = copied(first[1], draft)
        results["first_part_copied"] = dict(ok=why is None, detail=why or "none")
    return results


def trip_places(workspace: Path) -> list[dict]:
    """The places of the replayed trip repository (`trips/*/plan/places.yaml`), for the meal check."""
    try:
        import yaml
    except ImportError:
        return []
    places = []
    for path in sorted(workspace.glob("trips/*/plan/places.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        places += [place for place in loaded if isinstance(place, dict)]
    return places


def run_checks(run: Path) -> dict:
    zone_name = "Europe/Vienna"
    config = run / "ws" / "tripwright.yaml"
    if config.is_file():
        found = re.search(r"^timezone:\s*(\S+)", config.read_text(encoding="utf-8"), flags=re.M)
        zone_name = found.group(1) if found else zone_name
    zone = ZoneInfo(zone_name)
    report = {"run": str(run), "timezone": zone_name, "turns": {}, "more": {}}
    places = trip_places(run / "ws")
    settings = json.loads((run / "run.json").read_text(encoding="utf-8")) if (run / "run.json").is_file() else {}
    # D-93: only a host that can run background subagents may send the second part without “继续”
    needs_continue = settings.get("host") == "codex" or bool(settings.get("no_subagents"))
    # Evidence from an earlier turn of the same conversation still backs a later answer.
    seen = dict(windows=[], times=set(), ids=set(), counted=[], reachable=set(), latest=0, sources=[], lookups=[])
    for stream in sorted(run.glob("turn*.stream.jsonl"), key=lambda path: int(re.sub(r"\D", "", path.name.split(".")[0]) or 0)):
        turn = stream.name.split(".")[0]
        found = lookups(stream, zone)
        seen = dict(windows=seen["windows"] + found["windows"], times=seen["times"] | found["times"], ids=seen["ids"] | found["ids"],
                    counted=seen["counted"] + found["counted"], reachable=seen["reachable"] | found["reachable"],
                    latest=seen["latest"] + found["latest"], sources=seen["sources"] + found["sources"],
                    lookups=seen["lookups"] + found["lookups"])
        shown = texts(stream, zone, pinned_moments(run, int(re.sub(r"\D", "", turn) or 0)))
        draft = turn_draft(stream)
        report["turns"][turn] = check_turn(shown, lookup_ran(stream), seen, draft, continued_at(stream), needs_continue)
        summary = run / f"{turn}.json"
        question = json.loads(summary.read_text(encoding="utf-8")).get("message") if summary.is_file() else None
        report["more"][turn] = more_checks(shown, seen, question, places, draft)
    report["passed"] = all(result["ok"] for part in ("turns", "more") for turn in report[part].values() for result in turn.values())
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("run", nargs="+", help="Replay run directories")
    parser.add_argument("--no-write", action="store_true", help="Do not write answer_checks.json")
    arguments = parser.parse_args(argv)
    everything = True
    for run in map(Path, arguments.run):
        report = run_checks(run)
        if not arguments.no_write:
            (run / "answer_checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        for part in ("turns", "more"):
            for turn, results in report[part].items():
                for name, result in results.items():
                    print(f"{run.name} {turn} {name}: {'pass' if result['ok'] else 'FAIL'} — {result['detail']}")
        everything &= report["passed"]
    return 0 if everything else 1


if __name__ == "__main__":
    sys.exit(main())
