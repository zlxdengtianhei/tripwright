"""Bytes of the generated files in a user's trip repository (CONTRACTS 4: B decides the bytes, A's committer writes).

Every function is pure: the input is machine-readable data and the moment is the snapshot's `taken_at`, so the same
commit always regenerates the same bytes and `check` can compare "regenerated = committed". Each file starts with an
HTML comment naming the generator, its sources and the base commit.

A trip state is `{"path", "trip", "records", "snapshot", "notices"?, "events"?, "needs_replan"?, "warnings"?}`: the
repo-relative trip folder ("" when the trip sits at the repository root), the committed plan (Trip document), the ledger
records by ID as of the snapshot, the snapshot of this generation, open conflict notices, optionally messages by ID for
quoting the user's words in the decisions view, and the committed version's `needs_replan` with its `warnings` (the
checks the unchanged plan now fails). One repository holds several trips under `trips/<trip>/` (CONTRACTS 4): the
repository's `README.md` points at the current trip, each trip folder has its own front screen. Only the current trip
comes with its full state; any other trip comes with its committed plan alone (`records` empty), so it gets one line in
the repository's front screen and its folder keeps the files generated while it was current.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import re

from tripwright.checks import departure_clock
from tripwright.render.labels import ZH, claim_parts, local, short_day, split_claims, stamp_day_clock, stamp_with_zone
from tripwright.render.links import google_search
from tripwright.render.memory import counts, memory_lines
from tripwright.render.pack import render_pack
from tripwright.trip import Trip

KIND = {"principle": "总带", "hard": "硬约束", "preference": "偏好", "rule": "规矩", "goal": "目标", "fact": "实况",
        "decision": "决定", "check": "待核", "question": "待定"}
STATUS = {"active": "现行", "open": "开着", "pending": "待定", "superseded": "已取代", "retracted": "已撤回", "closed": "已结",
          "resolved": "已结", "expired": "已过期"}
TYPE_ORDER = ("item", "fact", "decision", "check", "question")
FILES = (("views/today.md", "今天"), ("views/days/<日期>.md", "每天"), ("views/packs/<日期>.md", "没有工具时读的约束包"),
         ("views/index.md", "条目索引"), ("views/decisions.md", "决定"), ("views/memory.md", "记忆：从以前带来的长期内容"))


def generated_header(sources: list[str], head: str | None) -> str:
    return f"<!-- 由 tripwright 生成，源：{'、'.join(sources)}；基于提交 {head or '无'}；不要手改 -->"


def head_of(snapshot: dict) -> str | None:
    return snapshot["repo"]["head"] if snapshot.get("repo") else None


def base_line(snapshot: dict, zone: str) -> str:
    parts = [f"V{snapshot['plan_version']}"]
    repo = snapshot.get("repo")
    parts.append(f"提交 {repo['head'] or '无'}" if repo else "单库")
    received = snapshot["raw_watermark"]["last_received_at"]
    parts.append(f"原话到 {stamp_with_zone(received, zone)}" if received else "还没有原话")
    if repo:
        parts.append(f"未入仓 {len(repo['unsynced_ops'])} 条")
    return "基于：" + "｜".join(parts)


def now_of(state: dict) -> datetime:
    return local(state["snapshot"]["taken_at"], state["trip"]["timezone"])


def phase(trip, today: date) -> str:
    """`trip` is a Trip or a raw plan dict (another trip's plan is read as it was committed, not re-validated)."""
    start, end = (date.fromisoformat(str(trip[key] if isinstance(trip, dict) else getattr(trip, key))) for key in ("start", "end"))
    if today < start:
        return "计划中"
    if today > end:
        return "已结束"
    return f"进行中，第 {(today - start).days + 1} 天，共 {(end - start).days + 1} 天"


def names(trip: Trip) -> dict:
    return {place.id: place for place in trip.places}


def latest(trip: Trip, day: str, anchor) -> str | None:
    return departure_clock(anchor.time, anchor.buffer_minutes, anchor.travel_minutes, day=day, zone=trip.timezone)


def hard_line(trip: Trip, day, anchor, *, with_day: bool) -> str:
    leave = latest(trip, str(day.date), anchor)
    when = f"{short_day(str(day.date))} {anchor.time}" if with_day else anchor.time
    return f"{when} {archived_text(anchor.text)}｜{'、'.join(anchor.subject)}｜" + (f"最晚 {leave} 出发" if leave else "最晚出发没核")


def archived_text(text: str | None) -> str | None:
    """A stored last/only/late sentence marked as archive (A9, D-64): these views show the plan as stored, and a host
    reading the files without the tools must not take such a sentence as a checked fact."""
    if not text:
        return text
    parts = []
    for part in split_claims(text):
        if claim_parts(part):
            claim = part.strip("。； （）")
            parts.append(f"计划里写过“{claim}”（存档，没核）" + ("。" if part.endswith("。") else ""))
        else:
            parts.append(part)
    return "".join(parts)


def upcoming_anchors(trip: Trip, now: datetime, hours: int | None = None):
    horizon = now + timedelta(hours=hours) if hours else None
    found = []
    for day in trip.days:
        for anchor in day.anchors:
            moment = datetime.fromisoformat(f"{day.date}T{anchor.time}").replace(tzinfo=now.tzinfo)
            if moment >= now and (horizon is None or moment <= horizon):
                found.append((moment, day, anchor))
    return sorted(found, key=lambda entry: entry[0])


def open_records(records: dict, kind: str) -> list[tuple[str, dict]]:
    return sorted(((record_id, record) for record_id, record in records.items()
                   if record["type"] == kind and record.get("status") in ("open", "pending", "active")), key=lambda entry: entry[0])


def due_checks(records: dict, now: datetime, hours: int = 48) -> list[tuple[str, dict]]:
    limit = now + timedelta(hours=hours)
    return sorted((entry for entry in open_records(records, "check") if datetime.fromisoformat(entry[1]["due_at"]) <= limit),
                  key=lambda entry: entry[1]["due_at"])


def block_text(block, places: dict) -> str:
    text = block.text or " / ".join(places[place_id].name_local for place_id in [block.place, *block.options] if place_id)
    if block.origin and block.destination:
        text += f" · {places[block.origin].name_local} → {places[block.destination].name_local}"
    return text


def next_step(trip: Trip, now: datetime) -> str | None:
    places = names(trip)
    day = next((day for day in trip.days if day.date == now.date()), None)
    if day is None or not day.plans:
        return None
    clock = now.strftime("%H:%M")
    blocks = sorted((block for track in day.plans[0].tracks for block in track.blocks if block.start >= clock),
                    key=lambda block: block.start)
    return f"{blocks[0].start} {block_text(blocks[0], places)}" if blocks else None


def question_text(record_id: str, record: dict) -> str:
    text = record.get("question") or record.get("text") or record_id
    return f"{record_id}：{text}" + (f"；不答默认 {record['default']}" if record.get("default") else "")


def notice_lines(state: dict) -> list[str]:
    return [f"注意：{notice['user_line']}" for notice in state.get("notices") or []]


def day_ids(day) -> set[str]:
    """What a check ID names when it concerns this day: the date, its hard nodes or its blocks (see `checks.py`)."""
    return ({str(day.date)} | {anchor.id for anchor in day.anchors}
            | {block.id for plan in day.plans for track in plan.tracks for block in track.blocks})


def replan_lines(state: dict, day=None) -> list[str]:
    """A committed plan that fails what was recorded after it says so before anything else is read. A day view lists
    the conflicts that name its hard nodes or blocks and points at the trip's front screen for the rest."""
    if not state.get("needs_replan"):
        return []
    warnings = [warning for warning in state.get("warnings") or [] if isinstance(warning, dict)]
    lines = ["需重排：这一版计划和后来记下的情况冲突，先重排再照它走。"]
    if day is not None:
        ids = day_ids(day)
        here = [warning for warning in warnings if ids & set(str(warning.get("id", "")).split(":"))]
        if warnings and not here:
            lines.append(f"这一天没有冲突；冲突见 {at(state, 'README.md')}。")
        warnings = here
    lines += [f"- 冲突：{warning.get('text') or warning.get('id')}" for warning in warnings[:3]]
    if len(warnings) > 3:
        lines.append(f"- 另有 {len(warnings) - 3} 处，见 {at(state, 'README.md')}" if day is not None else f"- 另有 {len(warnings) - 3} 处")
    return lines


def memory_line(memory: dict | None) -> list[str]:
    if not memory:
        return []
    found = counts(memory)
    return [f"记忆：待确认 {found['pending']} 条｜已确认沿用 {found['confirmed']} 条｜这趟不用 {found['not_used']} 条"]


def repo_readme(states: list[dict], *, current: str | None, snapshot: dict, memory: dict | None = None) -> str:
    """The repository's front screen (at most 25 lines): the current trip first, then every trip."""
    lines = [generated_header(["tripwright.yaml", "profile/", "trips/"], head_of(snapshot)), "# 我的旅行"]
    chosen = next((state for state in states if state["trip"]["id"] == current), None)
    if chosen is None:
        lines += [f"现在：{current}，还没有提交的计划" if current else "现在：没有进行中的行程", base_line(snapshot, "UTC")]
    else:
        trip = Trip.model_validate(chosen["trip"])
        now = now_of(chosen)
        lines += notice_lines(chosen)
        replan = "｜需重排" if chosen.get("needs_replan") else ""
        lines.append(f"现在：{trip.title}｜{phase(trip, now.date())}{replan}｜当地时区 {trip.timezone}")
        lines.append(base_line(snapshot, trip.timezone))
        upcoming = upcoming_anchors(trip, now)
        lines.append("下一个硬节点：" + (hard_line(trip, upcoming[0][1], upcoming[0][2], with_day=True) if upcoming else "没有"))
        checks = due_checks(chosen["records"], now)
        if checks:
            first_id, first = checks[0]
            lines.append(f"到期待核：{len(checks)} 条，最早 {stamp_day_clock(first['due_at'], trip.timezone)}（{first_id}：{first['question']}）")
        else:
            lines.append("到期待核：没有")
        questions = open_records(chosen["records"], "question")
        lines.append(f"要你定：{len(questions)} 条（{question_text(*questions[0])}）" if questions else "要你定：没有")
        lines += [line + f"（{at(chosen, 'views/memory.md')}）" for line in memory_line(memory)]
        lines.append(f"agent 读法：先调 turn；没有工具就读 {at(chosen, f'views/packs/{now.date()}.md')}")
    lines += ["", "行程："]
    # Every trip's line reads only its plan's title, dates and timezone: other trips carry nothing else, and a plan
    # committed under an older model must not stop the current trip's front screen.
    for state in sorted(states, key=lambda state: str(state["trip"]["start"])):
        trip = state["trip"]
        lines.append(f"- {at(state, 'README.md')}：{trip['title']}｜{short_day(str(trip['start']))}—{short_day(str(trip['end']))}"
                     f"｜{phase(trip, now_of(state).date())}")
    return "\n".join(lines) + "\n"


def trip_readme(state: dict, *, memory: dict | None = None) -> str:
    """One trip's front screen (`trips/<trip>/README.md`, at most 40 lines). Paths in its file map are relative to the
    trip folder, where the file sits."""
    trip = Trip.model_validate(state["trip"])
    now = now_of(state)
    places = names(trip)
    lines = [generated_header([at(state, "trip.yaml"), at(state, "plan"), at(state, "ledger")], head_of(state["snapshot"])),
             f"# {trip.title}"]
    lines += notice_lines(state)
    lines += replan_lines(state)
    lines.append(f"阶段：{phase(trip, now.date())}｜{short_day(str(trip.start))}—{short_day(str(trip.end))}｜{trip.timezone}")
    lines.append("人：" + "、".join(person.id for person in trip.people))
    lines.append(base_line(state["snapshot"], trip.timezone))
    stays = [f"{short_day(str(day.date))} {places[day.base].name_local}" for day in trip.days if day.base]
    lines.append("住：" + ("；".join(stays) if stays else "没写"))
    upcoming = upcoming_anchors(trip, now, hours=48)
    lines.append("48 小时内硬节点：" + ("" if upcoming else "没有"))
    lines += [f"- {hard_line(trip, day, anchor, with_day=True)}" for _, day, anchor in upcoming[:4]]
    checks = due_checks(state["records"], now)
    lines.append("待核：" + ("" if checks else "没有"))
    lines += [f"- {stamp_day_clock(record['due_at'], trip.timezone)} {record['question']}（{record_id}）" for record_id, record in checks[:4]]
    questions = open_records(state["records"], "question")
    lines.append("要你定：" + ("" if questions else "没有"))
    lines += [f"- {question_text(record_id, record)}" for record_id, record in questions[:3]]
    lines += memory_line(memory)
    step = next_step(trip, now)
    lines.append(f"下一步：{step}" if step else "下一步：今天没有剩下的安排")
    lines += ["", "文件：", *[f"- {path}：{what}" for path, what in FILES]]
    return "\n".join(lines) + "\n"


def memory_view(memory: dict, *, state: dict) -> str:
    """`trips/<trip>/views/memory.md`: the long-term content this trip imported and its status here."""
    lines = [generated_header(["profile/", at(state, "ledger")], head_of(state["snapshot"])), "# 记忆（长期内容）",
             base_line(state["snapshot"], state["trip"]["timezone"])]
    return "\n".join(lines + memory_lines(memory)) + "\n"


def at(state: dict, path: str) -> str:
    """A repo-relative path inside the trip folder; the trip may sit at the repository root (`path` empty)."""
    return f"{state['path']}/{path}" if state.get("path") else path


def check_lines(records: dict, day: str, zone: str) -> list[str]:
    lines = []
    for record_id, record in open_records(records, "check"):
        scope = record.get("scope", {})
        if (scope.get("start") or day) <= day <= (scope.get("end") or day):
            lines.append(f"- {stamp_day_clock(record['due_at'], zone)} {record['question']} → {record['consequence']} → "
                         f"{record['alternative']}（{record_id}）")
    return lines


def day_lines(state: dict, day_id: str) -> list[str]:
    trip = Trip.model_validate(state["trip"])
    places = names(trip)
    day = next(day for day in trip.days if str(day.date) == day_id)
    lines = []
    if day.anchors:
        lines.append("硬节点")
        for anchor in sorted(day.anchors, key=lambda anchor: anchor.time):
            lines.append(f"- {hard_line(trip, day, anchor, with_day=False)}｜误了：{archived_text(anchor.consequence)}｜退路：{anchor.alternative}")
    used: list[str] = []
    for plan in day.plans:
        lines += ["", f"## {plan.name}"]
        for track in plan.tracks:
            lines.append("、".join(track.subject))
            for block in sorted(track.blocks, key=lambda block: block.start):
                span = f"{block.start}—{block.end}" if block.end else block.start
                lines.append(f"{span} {block_text(block, places)}")
                used += [place_id for place_id in (block.place, *block.options, block.origin, block.destination) if place_id]
    used += [anchor.place for anchor in day.anchors if anchor.place]
    if used:
        lines += ["", "地点"]
        for place_id in dict.fromkeys(used):
            place = places[place_id]
            lines += [place.name_local, f"{place.name} · Google 地图搜 {place.name_local} · [Google 地图]({google_search(place.name_local)})"]
    checks = check_lines(state["records"], day_id, trip.timezone)
    if checks:
        lines += ["", "待核", *checks]
    return lines


def day_view(state: dict, day_id: str) -> str:
    """`views/days/<date>.md`: one day for people to read."""
    trip = Trip.model_validate(state["trip"])
    day = next((day for day in trip.days if str(day.date) == day_id), None)
    if day is None:
        raise ValueError(f"The trip has no day {day_id}")
    lines = [generated_header([at(state, f"plan/days/{day_id}.yaml"), at(state, "plan/places.yaml"), at(state, "ledger")],
                              head_of(state["snapshot"])),
             f"# {short_day(day_id)} · {day.title}", base_line(state["snapshot"], trip.timezone)]
    lines += replan_lines(state, day)
    return "\n".join(lines + day_lines(state, day_id)) + "\n"


def today_view(state: dict) -> str:
    """`views/today.md`: the next step first, then hard nodes, then the plans. Outside the trip dates it shows the next
    day (or the last day once the trip has ended) and says so."""
    trip = Trip.model_validate(state["trip"])
    now = now_of(state)
    days = sorted(str(day.date) for day in trip.days)
    today = str(now.date())
    shown = today if today in days else next((day for day in days if day > today), days[-1] if days else None)
    lines = [generated_header([at(state, "plan/days/"), at(state, "plan/places.yaml"), at(state, "ledger")], head_of(state["snapshot"]))]
    lines += notice_lines(state)
    if shown is None:
        return "\n".join(lines + ["# 今天", "行程里还没有任何一天。"]) + "\n"
    day = next(day for day in trip.days if str(day.date) == shown)
    lines.append(f"# 今天 {short_day(today)}" + (f" · {day.title}" if shown == today else ""))
    lines.append(base_line(state["snapshot"], trip.timezone) + f"｜编于 {stamp_with_zone(state['snapshot']['taken_at'], trip.timezone)}")
    lines += replan_lines(state, day)
    if shown != today:
        lines.append(f"今天不在行程里；下面是 {short_day(shown)} · {day.title}。")
    else:
        step = next_step(trip, now)
        lines.append(f"下一步：{step}" if step else "下一步：今天没有剩下的安排")
    return "\n".join(lines + day_lines(state, shown)) + "\n"


def pack_view(pack: dict, *, state: dict, sources: dict | None = None) -> str:
    """`views/packs/<date>.md`: the day's pack for a host without tools (this day, everyone, conversation)."""
    rendered = render_pack(pack, sources=sources)["markdown"]
    header = generated_header([at(state, "ledger"), at(state, "plan"), f"约束包 {pack['pack_id']}"], head_of(pack["snapshot"]))
    return header + "\n" + rendered


def scope_words(scope: dict) -> str:
    parts = []
    if scope.get("start") or scope.get("end"):
        start, end = scope.get("start"), scope.get("end")
        parts.append(short_day(start) if start == end else f"{short_day(start) if start else ''}—{short_day(end) if end else ''}")
    if scope.get("time_from") or scope.get("time_to"):
        parts.append(f"{scope.get('time_from') or ''}—{scope.get('time_to') or ''}")
    if scope.get("place"):
        parts.append(scope["place"])
    if scope.get("channel"):
        parts.append(ZH["channel"].get(scope["channel"], scope["channel"]))
    return " ".join(parts) or "全程"


def summary(record: dict) -> str:
    if record["type"] == "decision":
        return f"{record['question']} → {record['choice']}"
    if record["type"] in ("check", "question"):
        return record.get("question") or record.get("text", "")
    return record.get("text") or record.get("quote", "")


def item_kind(record: dict) -> str:
    """The pack's section when A has classified the item, otherwise its ledger kind."""
    if record.get("section") in KIND:
        return KIND[record["section"]]
    return {"constraint": "约束", "preference": "偏好", "rule": "规矩", "goal": "目标"}.get(record.get("kind"), "条目")


def index_view(state: dict) -> str:
    """`views/index.md`: one line per record (ID, kind, one sentence, people, scope, status), without full text."""
    lines = [generated_header([at(state, "ledger")], head_of(state["snapshot"])), "# 条目索引",
             base_line(state["snapshot"], state["trip"]["timezone"])]
    records = state["records"]
    for kind in TYPE_ORDER:
        for record_id in sorted(record_id for record_id, record in records.items() if record["type"] == kind):
            record = records[record_id]
            label = item_kind(record) if kind == "item" else KIND[kind]
            lines.append(f"[{record_id}] {label}｜{summary(record)}｜{'、'.join(record.get('subject', [])) or '全员'}"
                         f"｜{scope_words(record.get('scope', {}))}｜{STATUS.get(record.get('status'), record.get('status') or '现行')}")
    return "\n".join(lines) + "\n"


def decisions_view(state: dict) -> str:
    """`views/decisions.md`: the user's and agent's decisions for people to read, with the plan fields each covers (locked only for a must)."""
    lines = [generated_header([at(state, "ledger/decisions")], head_of(state["snapshot"])), "# 决定",
             base_line(state["snapshot"], state["trip"]["timezone"])]
    events = state.get("events") or {}
    decisions = sorted((record_id, record) for record_id, record in state["records"].items() if record["type"] == "decision")
    if not decisions:
        lines.append("还没有决定。")
    for record_id, record in decisions:
        decided = {"user": "用户定", "agent": "agent 定"}.get(record["decided_by"], record["decided_by"])
        lines += ["", f"## {record['question']} → {record['choice']}",
                  f"[{record_id}]｜{decided}｜{'、'.join(record['subject'])}｜{scope_words(record.get('scope', {}))}"
                  f"｜{STATUS.get(record['status'], record['status'])}"]
        held = "锁定" if record.get("must", True) else f"{decided}（可调整）"  # only a must locks (D-53); no `must`: an older record
        for pointer, value in record.get("locked_paths", {}).items():
            lines.append(f"{held}：{pointer} = {value}")
        event = events.get(record.get("source_event"))
        if event:
            lines.append(f"原话：{record['source_event']}（{event['speaker']}，{stamp_day_clock(event['received_at'], state['trip']['timezone'])}）"
                         f"「{event['text']}」")
        elif record.get("source_event"):
            lines.append(f"原话：{record['source_event']}")
    return "\n".join(lines) + "\n"


def repo_files(states: list[dict], *, current: str | None, snapshot: dict, packs: dict | None = None,
               sources: dict | None = None, memory: dict | None = None) -> dict[str, str]:
    """Every file B generates in the user repository, by repo-relative path. `packs` maps trip IDs to `{date: pack}`
    (A compiles them: this day, everyone, conversation); `memory` maps trip IDs to that trip's memory data. Only the
    current trip's folder is generated: other trips come with their plan alone and appear in the repository README."""
    memory = memory or {}
    files = {"README.md": repo_readme(states, current=current, snapshot=snapshot, memory=memory.get(current))}
    for state in states:
        trip_id = state["trip"]["id"]
        if trip_id != current:
            continue
        files[at(state, "README.md")] = trip_readme(state, memory=memory.get(trip_id))
        files[at(state, "views/today.md")] = today_view(state)
        files[at(state, "views/index.md")] = index_view(state)
        files[at(state, "views/decisions.md")] = decisions_view(state)
        for day in state["trip"]["days"]:
            files[at(state, f"views/days/{day['date']}.md")] = day_view(state, day["date"])
        for day, pack in ((packs or {}).get(trip_id) or {}).items():
            files[at(state, f"views/packs/{day}.md")] = pack_view(pack, state=state, sources=sources)
        if trip_id in memory:
            files[at(state, "views/memory.md")] = memory_view(memory[trip_id], state=state)
    return files
