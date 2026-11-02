"""When to look up a timetable (design 9.4; red-team 1.4 and E4).

By default no timetable is queried: each leg gets a link. One of five triggers makes a narrow lookup of the legs that
matter, and the answer then gives the target departure, one fallback and what to do if missed:

1. a hard node whose whole chain (every leg, transfers and buffer) leaves too little slack for the current plan;
   the slack is measured from the chain's latest departure, not from a fixed three-hour window before the node;
2. the user asks whether they can make it or how late they can leave;
3. a ticket question (validity, operator, upgrade): look up the ticket rule, not the timetable;
4. the user names a service or sends a screenshot: compare first, look up only on a mismatch;
5. frequency: known sparse (every 60 minutes or less often) → look up; known frequent → link only; unknown → look up
   once only when that leg decides the step in front of the user, otherwise link only, mark it not checked and do not
   promise that the whole day works.

Pure: `now` and all clock times are local "HH:MM" on the same day, passed in by the caller. Overnight chains are out
of scope for this version.
"""
from __future__ import annotations

import re

SPARSE_MINUTES = 60       # design 9.4: a gap of 60 minutes or more is sparse
HORIZON_MINUTES = 120     # the first reply covers the next 1—2 hours (design 9.2); 推荐默认, not measured
ASK_WORDS = ("能不能赶上", "赶得上", "赶不上", "来得及", "来不及", "最晚几点", "最晚什么时候", "卡末班", "末班",
             "make it", "in time", "latest", "last bus", "last train")
TICKET_WORDS = ("票能不能", "票能用", "能用吗", "补票", "哪家运营", "运营商", "车票", "ticket", "fare", "operator", "valid")
NAMED = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:的|那班|那趟)|\d{1,4}\s*路|\b(?:[A-Z]{1,3}\s?\d{2,5})\b|\bline \d+\b|\bbus \d+\b")
WORDS = {
    "zh": {"hard": "硬节点 {node} {time}：全链路 {travel} 分钟加缓冲 {buffer} 分钟，最晚 {latest} 出发，余量 {slack} 分钟",
           "late": "；按现在的安排 {start} 才出发，赶不上",
           "ask": "用户问能不能赶上、最晚几点", "ticket": "票的问题：查票的规则，不查班次",
           "named": "用户点名了车次或发了截图：先对照，对不上再查",
           "sparse": "班次间隔 {gap} 分钟，错过一班会打乱后面", "unknown_now": "班次频率没核，而这一段决定眼下这一步",
           "unknown_later": "班次没核", "frequent": "班次间隔 {gap} 分钟，给链接"},
    "en": {"hard": "hard node {node} {time}: whole chain {travel} minutes plus {buffer} minutes buffer, leave by {latest}, "
                   "{slack} minutes slack",
           "late": "; the current plan leaves at {start} and misses it",
           "ask": "the user asks whether they can make it or how late they can leave",
           "ticket": "ticket question: look up the ticket rule, not the timetable",
           "named": "the user named a service or sent a screenshot: compare first, look up only on a mismatch",
           "sparse": "a service every {gap} minutes; missing one disrupts the rest", "unknown_now":
           "frequency not checked, and this leg decides the next step", "unknown_later": "timetable not checked",
           "frequent": "a service every {gap} minutes; a link is enough"},
}


def minutes(clock: str) -> int:
    hour, minute = map(int, clock.split(":"))
    return hour * 60 + minute


def clock_of(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}" if value >= 0 else f"-{(-value) // 60:02d}:{(-value) % 60:02d}"


def markers_from_text(text: str) -> dict:
    """A starting point for message markers; the host may set them directly when it understands the message better."""
    lowered = text.lower()
    return {"asks_catch": any(word in lowered for word in ASK_WORDS),
            "ticket": any(word in lowered for word in TICKET_WORDS),
            "named_service": bool(NAMED.search(text)), "screenshot": False}


def chain_latest(node: dict, legs: dict[str, dict]) -> tuple[int, int]:
    """Latest departure (minutes after midnight) for the node's chain, and the chain's travel time with transfers."""
    travel = sum(legs[leg_id]["minutes"] + legs[leg_id].get("transfer_minutes", 0) for leg_id in node["chain"])
    return minutes(node["time"]) - node.get("buffer_minutes", 0) - travel, travel


def evaluate(now: str, legs: list[dict], hard_nodes: list[dict] = (), markers: dict | None = None, *,
             horizon_minutes: int = HORIZON_MINUTES, language: str = "zh") -> dict:
    """Decide which legs to look up.

    `legs` are today's remaining transport legs in order: `{id, minutes, depart?, transfer_minutes?, headway_minutes?}`,
    where `headway_minutes` is the gap between services from a provider (None when unknown). `hard_nodes` are
    `{id, time, buffer_minutes, chain: [leg ids]}`. `markers` are `{asks_catch, ticket, named_service, screenshot,
    leg?}`; `leg` names the leg a ticket or named-service marker is about. Each query lists its kinds: `timetable`,
    `ticket_rules` (look up the ticket rule) or `compare_first` (compare with the named service or screenshot first).
    """
    w = WORDS["en" if language == "en" else "zh"]
    markers = markers or {}
    by_id = {leg["id"]: leg for leg in legs}
    current = minutes(now)
    fired, latest = [], {}
    queries: dict[str, dict] = {}

    def query(leg_id: str, rule: int, kind: str = "timetable") -> None:
        entry = queries.setdefault(leg_id, {"leg": leg_id, "kinds": [], "rules": []})
        if rule not in entry["rules"]:
            entry["rules"].append(rule)
        if kind not in entry["kinds"]:
            entry["kinds"].append(kind)

    for node in hard_nodes:
        leave, travel = chain_latest(node, by_id)
        slack = leave - current
        latest[node["id"]] = {"latest_departure": clock_of(leave), "slack_minutes": slack, "travel_minutes": travel}
        first = by_id[node["chain"][0]] if node["chain"] else None
        planned = minutes(first["depart"]) if first and first.get("depart") else None
        misses = planned is not None and planned > leave
        if slack <= horizon_minutes or misses:
            reason = w["hard"].format(node=node["id"], time=node["time"], travel=travel, buffer=node.get("buffer_minutes", 0),
                                      latest=clock_of(leave), slack=slack)
            if misses:
                reason += w["late"].format(start=first["depart"])
            fired.append({"rule": 1, "name": "hard_node", "legs": list(node["chain"]), "reason": reason})
            for leg_id in node["chain"]:
                query(leg_id, 1)

    if markers.get("asks_catch"):
        nearest = min(hard_nodes, key=lambda node: minutes(node["time"]), default=None)
        target = list(nearest["chain"]) if nearest else ([markers["leg"]] if markers.get("leg") else [legs[0]["id"]] if legs else [])
        fired.append({"rule": 2, "name": "asks_catch", "legs": target, "reason": w["ask"]})
        for leg_id in target:
            query(leg_id, 2)
    if markers.get("ticket"):
        target = [markers["leg"]] if markers.get("leg") else []
        fired.append({"rule": 3, "name": "ticket", "legs": target, "reason": w["ticket"]})
        for leg_id in target:
            query(leg_id, 3, "ticket_rules")
    if markers.get("named_service") or markers.get("screenshot"):
        target = [markers["leg"]] if markers.get("leg") else []
        fired.append({"rule": 4, "name": "named_or_screenshot", "legs": target, "reason": w["named"]})
        for leg_id in target:
            query(leg_id, 4, "compare_first")

    link_only, whole_day = [], True
    for index, leg in enumerate(legs):
        gap = leg.get("headway_minutes")
        depart = minutes(leg["depart"]) if leg.get("depart") else None
        now_step = (depart is not None and 0 <= depart - current <= horizon_minutes) or (depart is None and index == 0)
        if gap is not None and gap >= SPARSE_MINUTES:
            fired.append({"rule": 5, "name": "sparse", "legs": [leg["id"]], "reason": w["sparse"].format(gap=gap)})
            query(leg["id"], 5)
        elif gap is None and now_step:
            fired.append({"rule": 5, "name": "unknown_frequency", "legs": [leg["id"]], "reason": w["unknown_now"]})
            query(leg["id"], 5)
        elif leg["id"] not in queries:
            note = w["unknown_later"] if gap is None else None
            whole_day = whole_day and gap is not None
            link_only.append({"leg": leg["id"], "note": note})
    return {"fired": fired, "queries": list(queries.values()), "link_only": link_only, "whole_day": whole_day, "latest": latest}
