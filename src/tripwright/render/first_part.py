"""The first part of an answer, drafted by the service from what it stores (A10, D-67).

a9b: the host dispatched its checks and sent only an English status line, so neither question had a first part. `turn`
now returns this draft as `first_part`; its `next` says to send it as it is (only the form of address and the language
may change) before any lookup. It is a guarantee that needs no host hook or skill: a host that reads neither still gets
the text in the tool result (harness neutral, R-62, R-69).

The draft uses stored data only: the clock of the read on the first line and the plan's next hard nodes. A stored
“末班、只剩、最晚” never enters it (D-64 two; the A10 replay quoted one, with “这轮查到更晚的 04:07，这条作废”): at most
one line says the plan's stored service claims are not checked and part 2 gives them (A11: that line names no “末班”
either). A question about the latest departure, the last service or making it in time opens with “几点出发要查，第二段给”
(A16: no “最晚” before a lookup) and lists the known hard nodes; the real latest comes from the lookup in the second part. A node's time that no source
checked (`checked` empty) says “（存档，没核）”, unless it is the plan's own target (A11: “07:05 证件柜台关” read as a
fact); a node done online says nobody has to be there (A11 C). At most 8 lines; the first part checks of check_answer
pass on it.

A12 (quality judge 6, A): that draft reads the plan, not the question. With the question's points (`turn(points=…)`, the
host's reading of it) the draft answers each point from what the trip stores (point_draft), says on line 2 where the
user is, and says “第二段给” for what is not stored; without them it is the plan's draft above.

A16 (quality judge 8): a go point's first line gives the plan's own way to its stored train when the plan has one from
where the user is (packs.planned_way: when to leave, from where, the way on the line below, archived); a host without
the tools gets this same draft from `tripwright first-part` (Tripwright.draft_first_part).
"""
from __future__ import annotations

import re
from datetime import date as Date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# A question whose answer is a latest time or a last service: the draft does not answer it from the plan.
LATEST = re.compile(r"最晚|末班|最后一班|几点(?:出发|走|出门|动身|去)|来得及|来不及|赶得上|赶不上|赶紧|还有(?:车|班)|"
                    r"\blast (?:train|bus|connection|one)\b|\blatest\b|\bin time\b|\bhurry\b", re.I)
LINES = 8
# S4a: the line that ends a reply with the first part in an app that cannot show text between tool calls (core step 4);
# turn returns it as first_part.later. S4a2: check_answer and the Stop hook take it as the first part's last line.
LATER = dict(zh="要核过的第二段，回我一句“继续”。", en="Reply “go on” for the checked second part.")
# S5b (Claude Code replays s5_ns_on, s5_mcponly): what turn says first (`do_first`, its first field), read at the moment
# the host acts: the first part as the reply's only message; after “继续”, nothing before the second part (the Stop hook
# opens its push after “继续” with DO_DUE too); without usable points, call turn again first.
DO_DUE = dict(zh="这一轮在第二段之前不写任何字：查、核、过 check_answer 之后只发第二段这一条，发完结束这一轮；不提检查和工具回执。",
              en="Write nothing before the second part in this reply: look up, check, pass check_answer, then send only the second "
                 "part as one message and end the reply; never mention the check or tool receipts.")
NODES = 3  # hard nodes the draft names
# a stored claim as packs.archived_claims rewrote it inside a node's text: left out of the draft
ARCHIVED = re.compile(r"计划里写过“[^”]*”（存档[^）]*）。?|the plan stored \"[^\"]*\" \(archived[^)]*\)\.?")
WORDS = {
    "zh": dict(latest="按现在 {clock} 算：几点出发要查，第二段给。", plain="按现在 {clock} 算：先按存的计划说，要核的第二段给。",
               empty="按现在 {clock} 算：这趟还没有存的计划，第二段给。",
               board="计划里写过：{when}{time} 在 {stop} 坐 {line}{toward}（存档，没核）", toward=" 去 {place}", vehicle="车",
               external="{when}{time} {text}", unmarked="{when}{time} {text}",
               unchecked="（存档，没核）", online="（在手机上做，不用到场）", online_unchecked="（在手机上做，不用到场；存档，没核）",
               claims="计划里存的班次说法没核，第二段给。",
               check_latest="没核：今晚还有哪些车、几点必须出门{nodes}，第二段核完再发。", nodes="和要守的时间",
               check_plain="没核：上面的时刻是存的计划，第二段核完再发。", morning="明早 ", tomorrow="明天 "),
    "en": dict(latest="As of {clock}, when to leave needs checking; part 2 follows.",
               plain="As of {clock}, from the stored plan; what needs checking follows in part 2.",
               empty="As of {clock}, this trip has no stored plan yet; part 2 follows.",
               board="The plan stored: {when}{time} {line} from {stop}{toward} (archived, not checked)", toward=" to {place}",
               vehicle="service", external="{when}{time} {text}",
               unmarked="{when}{time} {text}", unchecked=" (archived, not checked)", online=" (done online, no need to be there)",
               online_unchecked=" (done online, no need to be there; archived, not checked)",
               claims="The plan's stored service claims are not checked; part 2 gives them.",
               check_latest="Not checked: tonight's services, when you must leave{nodes}; part 2 follows.", nodes=" and the times to keep",
               check_plain="Not checked: the times above are the stored plan's; part 2 follows.", morning="tomorrow morning ",
               tomorrow="tomorrow "),
}


# --- A12: the first part answers this question's points -------------------------------------------------------------------
# quality judge 6 (A): the draft above reads the plan, not the question; “饿了、想看万圣节、是不是要赶去机场” got the airport
# template. The host writes the question's points (D-08: reading it is the host's); the service writes one line per point
# from what it stores, the place the user is in (or the assumed one) on line 2, and “第二段给” for what it does not store.
POINTS = 7  # one `at` and up to six things asked: with the clock line and the last line, at most LINES


class Point(BaseModel):
    """One thing this question names, as the host read it."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["at", "go", "eat", "do", "ticket", "other"] = Field(description=(
        "at: where the user says they are now; go: getting to a place or whether to hurry there; eat: food; do: an "
        "activity or event; ticket: tickets or fares; other: anything else asked."))
    said: str = Field(min_length=1, max_length=60, description="The user's own words for this point, short (e.g. 有点饿想吃东西).")
    place: str | None = Field(default=None, description=(
        "A place of the trip (ID or stored name) or 'lat,lon': where the user is (at), where to (go), near where (eat) "
        "or which stored place (do)."))


def checked_points(points: list | None) -> list[dict] | None:
    """Turn's points as plain dicts. Long quotations are shortened, not rejected."""
    from pydantic import TypeAdapter, ValidationError

    if points is None:
        return None
    if isinstance(points, list):
        points = [dict(point, said=point["said"][:60]) if isinstance(point, dict) and isinstance(point.get("said"), str)
                  else point for point in points]
    try:
        found = [point.model_dump() for point in TypeAdapter(list[Point]).validate_python(points)]
    except ValidationError as error:
        raise ValueError("Invalid points: " + "; ".join(
            f"points{''.join(f'.{part}' for part in problem['loc'])}: {problem['msg']}" for problem in error.errors())) from None
    if len(found) > POINTS or sum(point["kind"] == "at" for point in found) > 1:
        raise ValueError(f"Invalid points: at most {POINTS} points with one 'at' (where the user is), got {len(found)} "
                         f"with {sum(point['kind'] == 'at' for point in found)}")
    return found


def spaced(*parts: str) -> str:
    """Words joined the way the answer writes them: a space only where a Latin letter or digit meets another word."""
    text = parts[0]
    for part in parts[1:]:
        joined = text and part and not text[-1].isspace() and not part[0].isspace()
        text += (" " if joined and (text[-1].isascii() and text[-1].isalnum() or part[0].isascii() and part[0].isalnum()) else "") + part
    return text


POINT_WORDS = {
    "zh": dict(here="按现在 {clock} 算，你在{place}（你说的“{said}”）", here_said="按现在 {clock} 算，你在“{said}”",
               assumed="按现在 {clock} 算，假设你在{place}（计划里这时在这；不在的话说一声）",
               nowhere="按现在 {clock} 算，你没说在哪，先按计划说",
               go="去{place}：", night="今晚不用赶，夜里一直有车", train="；火车最后一班 {time} {line}，{stop} 开",
               gap="夜里有空档，{kind}最后一班 {time} {line}，{stop} 开{leave}；之后要等到 {after} 的 {after_line} 才再有车",
               gap_rail="火车", gap_last="空档前",
               last="今晚最后一班 {time} {line}，{stop} 开", last_rides="今晚最后一班 {time} 在 {stop} 上车：{lines}，{last_from} 开",
               rides_join=" 接 ", leave="，最晚 {leave} 出门", looked="（{fetched} 查过）",
               go_check="几点出发要查，第二段给。", go_planned="计划里是{when}{time} 在 {stop} 坐 {line}（存档，没核）；几点出门、怎么走，第二段核完给。",
               go_way="计划里是 {leave} 从 {origin} 出门{arrive}，坐{when}{time} {line}（存档，没核）；还有没有更晚的车，第二段核完给。",
               arrive="，{arrive} 到 {stop}", way="怎么走（存档，没核）：{how}", tonight="今晚 ",
               eat_open="吃：{places}（存的营业时间，最近的离你约 {km} 公里）", eat_place="{name}开到 {closes}", more="等 {count} 处",
               eat_closed="吃：附近存的吃饭地点现在都关着，第二段给。", eat_none="吃：附近没存吃饭地点，第二段给。",
               eat_nowhere="吃：不知道你在哪，第二段给。",
               do_place="{said}：{places}（存的）", do_open="{name}开到 {closes}", do_closed="{name}现在关着",
               do_unknown="{name}，营业时间没存", do_none="{said}：没存，第二段给。",
               ticket_none="{said}：没存票，第二段给。", other="{said}：第二段给。", plain="先按存的计划说，要核的第二段给。",
               tail="没核：上面存的时刻和说法，第二段核完再发。", join="、"),
    "en": dict(here="As of {clock}, you are at{place} (you said “{said}”)", here_said="As of {clock}, you are at “{said}”",
               assumed="As of {clock}, assuming you are at{place} (where the plan has you now; say if not)",
               nowhere="As of {clock}; you did not say where you are, so this follows the plan",
               go="To {place}: ", night="no need to hurry tonight, services run all night",
               gap="services stop for a while tonight: {kind} {time} {line} leaves {stop}{leave}; the next one is not until the "
                   "{after} {after_line}", gap_rail="the last train", gap_last="the last one before the gap",
               train="; the last train {time} {line} leaves {stop}", last="tonight's last one {time} {line} leaves {stop}",
               last_rides="tonight's last one boards at {stop} at {time}: {lines}, which leaves {last_from}", rides_join=" then ",
               leave=", leave by {leave}", looked=" (checked at {fetched})", go_check="when to leave needs checking; part 2 follows.",
               go_planned="the plan stores the {time} {line} from {stop}{when} (archived, not checked); when to leave and how follow in part 2.",
               go_way="the plan has you leave {origin} at {leave}{arrive} for the {time} {line}{when} (archived, not checked); "
                      "whether a later one runs follows in part 2.",
               arrive=", at {stop} by {arrive}", way="How (archived, not checked): {how}", tonight=" tonight",
               eat_open="Food: {places} (stored hours; the nearest about {km} km from you)", eat_place="{name}until {closes}",
               more="and {count} more", eat_closed="Food: the stored places nearby are closed now; part 2 follows.",
               eat_none="Food: no stored place to eat nearby; part 2 follows.", eat_nowhere="Food: where you are is not known; part 2 follows.",
               do_place="{said}: {places} (stored)", do_open="{name}until {closes}", do_closed="{name}closed now",
               do_unknown="{name}, hours not stored", do_none="{said}: nothing stored; part 2 follows.",
               ticket_none="{said}: no ticket stored; part 2 follows.", other="{said}: part 2 follows.",
               plain="From the stored plan; what needs checking follows in part 2.",
               tail="Not checked: the stored times and claims above; part 2 follows.", join=", "),
}


def go_line(point: dict, where: dict | None, said: dict) -> str:
    """A place to get to: an earlier lookup of tonight when one is stored (all night, the last train or tonight's last
    ride, with when it was looked up; the latest time to leave only when it was looked up from where the user is),
    else that the real latest needs checking. A place the trip does not store gets no name: never the user's words
    (a12b replay: “去最晚几点出发机场 怎么走：真正最晚要查”)."""
    head = said["go"].format(place=point["name"]) if point["name"] else ""
    frame, train, way = point["facts"].get("frame"), point["facts"].get("train"), point["facts"].get("way")
    if not frame:
        # A14 (quality judge 7): “真正最晚要查” alone did not answer; the plan's stored train to that place comes first. A16
        # (quality judge 8): with the plan's way there, when to leave and from where; never a “最晚” no lookup said
        if train and way:
            arrive = said["arrive"].format(arrive=way["arrive"], stop=train["stop"]) if way.get("arrive") else ""
            text = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])", "",
                          said["go_way"].format(**dict(train, leave=way["leave"], origin=way["origin"], arrive=arrive)))
        else:
            text = said["go_planned"].format(**train) if train else said["go_check"]
        text = re.sub(r"\s+(?=[ (（])|  +", " ", text)
        return head + (text if head else text[:1].upper() + text[1:])
    ride = frame["ride"]
    leave = said["leave"].format(leave=ride["leave"]) if where and frame["origin"] and frame["origin"] == where.get("place") and ride.get("leave") else ""
    gap = frame.get("gap")
    if gap:
        # 11-04 quality judge: “夜里一直有车” while nothing left for two hours after the last train
        before, after = gap["before"], gap["after"]
        here = said["leave"].format(leave=before["leave"]) if where and frame["origin"] and frame["origin"] == where.get("place") and before.get("leave") else ""
        text = said["gap"].format(kind=said["gap_rail" if before.get("rail") else "gap_last"], time=before["depart"], leave=here,
                                  line=before.get("line") or "", stop=before["from"], after=after["depart"], after_line=after.get("line") or "")
    elif frame["all_night"]:
        text = said["night"] + (said["train"].format(time=ride["time"], line=ride["line"] or "", stop=ride["stop"]) + leave if frame["train"] else "")
    elif len(ride.get("lines") or []) > 1:
        # fix2b: a last one with changes is not named by its first ride (“今晚最后一班 00:33 D”: the tram to the train)
        text = said["last_rides"].format(time=ride["time"], stop=ride["stop"], lines=said["rides_join"].join(ride["lines"]),
                                         last_from=ride["last_from"]) + leave
    else:
        text = said["last"].format(time=ride["time"], line=ride["line"] or "", stop=ride["stop"]) + leave
    return head + re.sub(r"  +", " ", text) + said["looked"].format(fetched=frame["fetched"])


def point_line(point: dict, where: dict | None, said: dict) -> str:
    facts = point["facts"]
    if point["kind"] == "go":
        return go_line(point, where, said)
    if point["kind"] == "eat":
        opened = facts["open"]
        if opened:
            named = said["join"].join(spaced(row["name"], said["eat_place"].format(name="", closes=row["closes"])) for row in opened[:2])
            more = said["join"] + said["more"].format(count=len(opened)) if len(opened) > 2 else ""
            return said["eat_open"].format(places=named + more, km=f"{opened[0]['distance_km']:.1f}")
        return said["eat_closed" if facts["stored"] else "eat_none" if facts["near"] else "eat_nowhere"]
    if point["kind"] == "do":
        rows = facts["places"]
        if not rows:
            return said["do_none"].format(said=point["said"])
        return said["do_place"].format(said=point["said"], places=said["join"].join(
            spaced(row["name"], said["do_open" if row["open"] else "do_closed" if row["open"] is False else "do_unknown"].format(
                name="", closes=row.get("closes"))) for row in rows))
    if point["kind"] == "ticket":
        return facts["tickets"]["text"] if facts.get("tickets") else said["ticket_none"].format(said=point["said"])
    return said["other"].format(said=point["said"])


def place_head(where: dict | None, clock: str, said: dict) -> str:
    if where is None:
        return said["nowhere"].format(clock=clock)
    if where["assumed"]:
        head, tail = said["assumed"].split("{place}")
        return spaced(head.format(clock=clock), where["name"], tail)
    if not where.get("name"):
        return said["here_said"].format(clock=clock, said=where["said"])
    head, tail = said["here"].split("{place}")
    return spaced(head.format(clock=clock), where["name"], tail.format(said=where["said"]))


def point_draft(question: str, now: dict, anchors: list[dict], plan: dict | None, told: dict, language: str = "zh") -> dict:
    """The first part from this question's points (`told`: packs.point_facts): line 1 answers the first point (getting
    somewhere first: a time to keep comes before a meal), line 2 the clock and where the user is (said, or assumed from
    the plan), one line per other point (points of one kind to one place share one), then at most one hard node: the
    next time to be somewhere, at a place the question goes to when there is one (A14, quality judge 7: three lines of
    tomorrow's airport times crowded out what was asked; the plan's own train is in the go line), the last line what
    part 2 checks. At most LINES."""
    said, words = POINT_WORDS.get(language, POINT_WORDS["en"]), WORDS.get(language, WORDS["en"])
    where = told["at"]
    points = sorted(told["points"], key=lambda point: point["kind"] != "go")
    answers = [point_line(point, where, said) for point in points]
    head = place_head(where, now["time"], said)
    # the plan's way to its train goes under where the user is (A16): line 1 says when, this one how
    ways = [said["way"].format(how=point["facts"]["way"]["how"]) for point in points[:1] if point["kind"] == "go"
            and not point["facts"].get("frame") and point["facts"].get("train") and (point["facts"].get("way") or {}).get("how")]
    # A17 (self replay off_r1): “最晚几点出发机场” and “怎么走” were two go points to the airport, and the same line came
    # twice; one line per kind and place, and no line twice
    kept, seen = [], set()
    for point, answer in zip(points, answers):
        if point["place"] is None or (point["kind"], point["place"]) not in seen:
            kept.append(answer)
        seen.add((point["kind"], point["place"]))
    lines = list(dict.fromkeys([kept[0], head, *ways, *kept[1:]] if kept else [said["plain"], head]))
    from tripwright.query import anchor_meaning

    going = {point["place"] for point in points if point["kind"] == "go"}
    named_nodes = {anchor_id for point in points if point["kind"] == "go" for anchor_id in point["facts"].get("nodes", [])}
    be_there = sorted((anchor for anchor in anchors if not anchor.get("passed")
                       and ((anchor_meaning(anchor) == "arrival_deadline" and anchor.get("deadline") != "plan")
                            or anchor["id"] in named_nodes)),
                      key=lambda anchor: (not (anchor_meaning(anchor) == "arrival_deadline" and anchor.get("deadline") != "plan"),
                                          anchor.get("place") not in going, anchor["date"], anchor["time"]))
    nodes = [node_line(anchor, plan, now["date"], words) for anchor in be_there[:1]] if len(lines) < LINES - 1 else []
    lines = list(dict.fromkeys([*lines[:LINES - 1], *nodes, said["tail"]]))
    return dict(text="\n".join(lines), lines=lines, asks_latest=asks_latest(question) or any(point["kind"] == "go" for point in points),
                points=[dict(kind=point["kind"], said=point["said"], line=line) for point, line in zip(points, answers)],
                at={key: value for key, value in where.items() if key != "coords"} if where else None)


def asks_latest(text: str) -> bool:
    return bool(LATEST.search(text or ""))


def day_word(day: str, time: str, today: str, said: dict) -> str:
    if day == today:
        return ""
    if Date.fromisoformat(day) == Date.fromisoformat(today) + timedelta(days=1):
        return said["morning"] if time < "12:00" else said["tomorrow"]
    return day[5:] + " "


def node_line(anchor: dict, plan: dict | None, today: str, said: dict) -> str:
    """One line for a hard node in the answer's words (never “外部截止”, “计划目标”): a boarding node as stored and
    unchecked; a node to be at as the plan's own time, the operator's time or an unmarked stored time, its text without
    any stored last/only/late claim; an operator's or unmarked time no source checked marked archive (A11)."""
    from tripwright.query import anchor_meaning, place_label

    when = day_word(anchor["date"], anchor["time"], today, said)
    if anchor_meaning(anchor) == "boarding_time":
        toward = said["toward"].format(place=place_label(plan, anchor["destination"])) if anchor.get("destination") else ""
        return said["board"].format(when=when, time=anchor["time"], stop=place_label(plan, anchor["place"]),
                                    line=anchor.get("line") or said["vehicle"], toward=toward)
    from tripwright.query import margin_text

    kind = {"plan": "plan", "external": "external"}.get(anchor.get("deadline"), "unmarked")
    text = re.sub(r"（\s*）|\(\s*\)", "", ARCHIVED.sub("", anchor["text"])).strip()
    if kind == "plan":  # the plan's margin, said once (A16)
        return margin_text(text, when, anchor["time"], "zh" if said is WORDS["zh"] else "en")
    mark = ("online" if anchor_meaning(anchor) == "online_deadline" else "") + \
        ("_unchecked" if not anchor.get("checked") else "")
    return bracketed(said[kind].format(when=when, time=anchor["time"], text=text), said[mark.strip("_")] if mark else "")


def bracketed(text: str, mark: str) -> str:
    """A mark in brackets after text that ends in its own bracket, as one bracket (A16: “（起飞前 40 分钟；存档，没核）”)."""
    for opening, closing, joint in (("（", "）", "；"), (" (", ")", "; ")):
        if mark.startswith(opening) and text.endswith(closing):
            return text[:-1] + joint + mark[len(opening):]
    return text + mark


def draft(question: str, now: dict, anchors: list[dict], plan: dict | None, language: str = "zh", told: dict | None = None) -> dict:
    """The first part as {text, lines, asks_latest}. `now` is turn's clock ({date, time, …}); `anchors` are turn's hard
    nodes (marked `passed`, with archived claims already rewritten); `plan` names the places. With this question's points
    (`told`, A12) the draft answers them (point_draft); without, it is the stored plan's (A10)."""
    if told is not None and plan:
        return point_draft(question, now, anchors, plan, told, language)
    said = WORDS.get(language, WORDS["en"])
    latest = asks_latest(question)
    upcoming = sorted((anchor for anchor in anchors if not anchor.get("passed")), key=lambda anchor: (anchor["date"], anchor["time"]))
    head = said["empty" if not plan else "latest" if latest else "plain"].format(clock=now["time"])
    body = [node_line(anchor, plan, now["date"], said) for anchor in upcoming[:NODES]]
    if any(anchor.get("archived_claims") for anchor in upcoming[:NODES]):
        body.append(said["claims"])
    tail = said["check_latest"].format(nodes=said["nodes"] if upcoming else "") if latest else said["check_plain"] if upcoming else None
    lines = [head, *body[:LINES - 1 - bool(tail)], *([tail] if tail else [])]
    return dict(text="\n".join(lines), lines=lines, asks_latest=latest)
