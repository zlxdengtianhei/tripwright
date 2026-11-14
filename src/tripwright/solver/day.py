"""Deterministic day ordering; callers own durable jobs and interpretation."""
from __future__ import annotations

import asyncio
import copy
import json
import re
import threading
import time
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

from tripwright.providers.base import Place
from tripwright.providers.common import gather_cancel, request_scope
from tripwright.providers.sources import transit as live_transit

MAX_CANDIDATES = 5
SOLVER_NODE_LIMIT = 300000


class ServiceError(ValueError):
    def __init__(self, code, message):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def elapsed_minute(day, value):
    if isinstance(value, (int, float)):
        return int(value)
    wall = mins(value)
    target_date = Date.fromisoformat(day["day"]) + timedelta(days=wall // 1440)
    tz = ZoneInfo(day["timezone"])
    start = datetime.combine(Date.fromisoformat(day["day"]), datetime.min.time(), tzinfo=tz)
    target = datetime(target_date.year, target_date.month, target_date.day, wall % 1440 // 60, wall % 60, tzinfo=tz)
    if datetime.fromtimestamp(target.timestamp(), tz).replace(tzinfo=None) != target.replace(tzinfo=None):
        raise ServiceError("INVALID_INPUT", "Local time does not exist on this DST transition day")
    return round((target.timestamp() - start.timestamp()) / 60)


def format_minute(day, value):
    tz = ZoneInfo(day["timezone"])
    start = datetime.combine(Date.fromisoformat(day["day"]), datetime.min.time(), tzinfo=tz)
    local = datetime.fromtimestamp(start.timestamp() + value * 60, tz)
    offset = (local.date() - start.date()).days
    return local.strftime("%H:%M") + (f"(+{offset})" if offset else "")


PACES = {
    "紧": {"weights": {3: 300, 2: 150, 1: 80}, "buffer_min": 3, "travel_cost_per_min": 0.5, "stop_cost": 0},
    "稍紧": {"weights": {3: 300, 2: 130, 1: 60}, "buffer_min": 5, "travel_cost_per_min": 0.75, "stop_cost": 10},
    "中": {"weights": {3: 300, 2: 120, 1: 40}, "buffer_min": 5, "travel_cost_per_min": 1.0, "stop_cost": 20},
    "松": {"weights": {3: 300, 2: 100, 1: 20}, "buffer_min": 10, "travel_cost_per_min": 1.5, "stop_cost": 40},
}
PACE_ALIASES = {"tight": "紧", "fairly_tight": "稍紧", "medium": "中", "relaxed": "松"}
PRIORITY = {"must": "must", "必去": "must", 3: 3, "3": 3, "最建议": 3, 2: 2, "2": 2, "建议": 2, 1: 1, "1": 1, "可选": 1}


def mins(value) -> int:
    """'HH:MM'、'HH:MM(+1)' → 当天 0 点起的分钟。"""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?:\(\+(\d)\))?\s*", str(value))
    if not match:
        raise ServiceError("INVALID_INPUT", f"时间要写成 HH:MM，收到 {value!r}。 / time must be HH:MM")
    if int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ServiceError("INVALID_INPUT", f"时间 {value!r} 超出范围（00:00—23:59，跨天写 HH:MM(+1)）。 / time out of range")
    return int(match.group(1)) * 60 + int(match.group(2)) + 1440 * int(match.group(3) or 0)


def hm(match: int) -> str:
    return f"{match // 60 % 24:02d}:{match % 60:02d}" + (f"(+{match // 1440})" if match >= 1440 else "")


def _prepare(day: dict, *, max_candidates: int = MAX_CANDIDATES) -> dict:
    """Validate a day before making any upstream requests."""
    day = copy.deepcopy(day)
    try:
        if not 2000 <= Date.fromisoformat(str(day["day"])).year <= 2100:
            raise ValueError
    except (KeyError, ValueError):
        raise ServiceError("INVALID_INPUT", "day 要写成 YYYY-MM-DD（2000—2100 年）。 / day must be YYYY-MM-DD")
    day["day"] = str(day["day"])
    pace = PACE_ALIASES.get(day.get("pace", "中"), day.get("pace", "中"))
    if pace not in PACES:
        raise ServiceError("INVALID_INPUT", f"pace 只能是 紧|稍紧|中|松（或 tight|fairly_tight|medium|relaxed），收到 {pace!r}。")
    day["pace"] = pace
    cands = day.get("candidates") or []
    if len(cands) > max_candidates:
        raise ServiceError("TOO_MANY_CANDIDATES", f"候选地点最多 {max_candidates} 个，收到 {len(cands)} 个。请先筛选，或拆成上午、下午两次。"
                                                  f" / at most {max_candidates} candidates (travel legs grow quadratically)")
    ids = [day["start"]["id"]] + [candidate["id"] for candidate in cands] + [day["end"]["id"]]
    if len(set(ids[1:-1])) != len(ids) - 2 or set(ids[1:-1]) & {ids[0], ids[-1]}:
        raise ServiceError("INVALID_INPUT", "候选 id 有重复，或与起点、终点同名。 / duplicate ids")
    for candidate in cands:
        if candidate.get("priority") not in PRIORITY:
            raise ServiceError("INVALID_INPUT", f"{candidate['id']} 的 priority 只能是 must|3|2|1，收到 {candidate.get('priority')!r}。")
        candidate["priority"] = PRIORITY[candidate["priority"]]
        if candidate.get("stay") is None:
            raise ServiceError("INVALID_INPUT", f"{candidate['id']} 缺 stay（停留分钟）。 / stay (minutes) required")
        if not candidate.get("name"):
            candidate["name"] = candidate["id"]
    for node in (day["start"], day["end"]):
        if not node.get("name"):
            node["name"] = node["id"]
    day["_cfg"] = dict(PACES[pace])

    mins(day["start"]["time"])
    mins(day["end"]["by"])
    for candidate in cands:
        if not (1 <= int(candidate["stay"]) <= 600):
            raise ServiceError("INVALID_INPUT", f"{candidate['id']} 的 stay 要在 1—600 分钟。 / stay out of range")
        if candidate.get("open") is not None:
            if len(candidate["open"]) != 2:
                raise ServiceError("INVALID_INPUT", f"{candidate['id']} 的 open 要写两项 ['开','关']，如 ['09:00','17:00']。 / open needs two items")
            mins(candidate["open"][0]), mins(candidate["open"][1])
        if candidate.get("last_entry"):
            mins(candidate["last_entry"])
    known = {item["id"] for item in [day["start"]] + cands + [day["end"]]}
    for override in day.get("overrides") or []:
        if override["from"] not in known - {day["end"]["id"]} or override["to"] not in known:
            raise ServiceError("INVALID_INPUT", f"override 的 from/to 要是起点、候选或终点的 id：{override['from']!r} → {override['to']!r}。")
        if override.get("minutes") is not None and not 0 <= int(override["minutes"]) <= 1440:
            raise ServiceError("INVALID_INPUT", "override 的 minutes 要在 0—1440。 / minutes out of range")
        for item in override.get("deps") or []:
            if len(item) < 2:
                raise ServiceError("INVALID_INPUT", "override 的 deps 每项至少写 [出发, 到达]，如 ['08:35','08:42','渡轮']。 / deps item needs [dep, arr]")
            mins(item[0]), mins(item[1])
    day["candidates"] = cands
    ZoneInfo(day["timezone"])
    if mins(day["end"]["by"]) < mins(day["start"]["time"]):
        raise ServiceError("INVALID_INPUT", "End precedes start; use HH:MM(+1) for the next day")
    for candidate in cands:
        candidate["stay"] = int(candidate["stay"])
        if candidate.get("before") and candidate["before"] not in known:
            raise ServiceError("INVALID_INPUT", "Unknown before target")
        if candidate.get("before") in (candidate["id"], day["start"]["id"]):
            raise ServiceError("INVALID_INPUT", "Candidate cannot precede itself or the start")
    return day


def apply_overrides(day: dict, matrix: dict) -> dict:
    matrix = json.loads(json.dumps(matrix))
    for override in day.get("overrides") or []:
        origin_id, target = override["from"], override["to"]
        if origin_id not in matrix:
            raise ServiceError("INVALID_INPUT", f"override 的 from {origin_id!r} 不是起点或候选。")
        cur = matrix[origin_id].get(target) or {"walk": None, "deps": []}
        if override.get("minutes") is not None:
            cur["walk"] = int(override["minutes"])
        if override.get("deps"):
            cur["deps"] = cur.get("deps", []) + [[str(item[0]), str(item[1]), str(item[2]) if len(item) > 2 else override.get("label", "手填")] for item in override["deps"]]
        matrix[origin_id][target] = cur
    return matrix


class Solver:
    def __init__(self, day, matrix, *, deadline=None, cancelled=None, node_limit=SOLVER_NODE_LIMIT):
        self.deadline = deadline
        self.cancelled = cancelled or threading.Event()
        self.node_limit = node_limit
        self.total_nodes = 0
        self.day, self.matrix, self.cfg = day, matrix, day["_cfg"]
        self.candidates_by_id = {candidate["id"]: candidate for candidate in day["candidates"]}
        self.t0, self.end_id, self.end_by = self.minute(day["start"]["time"]), day["end"]["id"], self.minute(day["end"]["by"])
        self.nodes = 0

    def minute(self, value):
        return elapsed_minute(self.day, value)

    def leg(self, origin_id, target, at_minute):
        if origin_id == target:
            return at_minute, 0, "原地"
        item = self.matrix.get(origin_id, {}).get(target)
        if not item:
            return None
        buf, best = self.cfg["buffer_min"], None
        if item.get("walk") is not None:
            best = (at_minute + buf + item["walk"], f"步行 {item['walk']}′")
        for dep, arr, label in item.get("deps") or []:
            dm, am = self.minute(dep), self.minute(arr)
            if dm >= at_minute + buf and am >= dm and (best is None or am < best[0]):
                best = (am, f"{self.format_minute(dm)}→{self.format_minute(am)} {label}")
        return None if best is None else (best[0], best[0] - at_minute, best[1])

    def window(self, candidate):
        op, cl = (self.minute(candidate["open"][0]), self.minute(candidate["open"][1])) if candidate.get("open") else (0, 2880)
        last = self.minute(candidate["last_entry"]) if candidate.get("last_entry") else cl - candidate["stay"]
        return op, min(last, cl - candidate["stay"])

    def solve(self, force=(), drop=(), topk=3):
        weights, lam, sc = self.cfg["weights"], self.cfg["travel_cost_per_min"], self.cfg["stop_cost"]
        self.nodes = 0
        must = {candidate_id for candidate_id, candidate in self.candidates_by_id.items() if candidate["priority"] == "must" and candidate_id not in drop} | set(force)
        best, labels = [], {}

        def dominated(key, at_minute, travel):
            state_labels = labels.setdefault(key, [])
            if any(t2 <= at_minute and tr2 <= travel for t2, tr2 in state_labels):
                return True
            state_labels[:] = [(t2, tr2) for t2, tr2 in state_labels if not (at_minute <= t2 and travel <= tr2)] + [(at_minute, travel)]
            return False

        def visit(cur, at_minute, seq, travel, value, tl):
            self.nodes += 1
            self.total_nodes += 1
            if self.cancelled.is_set() or (self.deadline is not None and time.monotonic() >= self.deadline):
                raise ServiceError("SOLVER_TIMEOUT", "Solver cancelled or deadline exceeded")
            if self.total_nodes > self.node_limit:
                raise ServiceError("SOLVER_LIMIT", "候选组合太多，求解超出上限。减少候选或收紧时间窗。 / search space too large")
            if dominated((frozenset(seq), cur), at_minute, travel):
                return
            if must <= set(seq):
                connection = self.leg(cur, self.end_id, at_minute)
                if connection and connection[0] <= self.end_by:
                    tr = travel + connection[1]
                    best.append((value - sc * len(seq) - lam * tr, tr, list(seq),
                                 tl + [{"id": self.end_id, "arrive": connection[0], "via": connection[2]}]))
            for cid, candidate in self.candidates_by_id.items():
                if cid in seq or cid in drop or (candidate.get("before") and candidate["before"] in seq):
                    continue
                connection = self.leg(cur, cid, at_minute)
                if not connection:
                    continue
                op, last = self.window(candidate)
                st = max(connection[0], op)
                if st > last:
                    continue
                lv = st + candidate["stay"]
                if lv > self.end_by:
                    continue
                seq.append(cid)
                weight = weights[3] if candidate["priority"] == "must" else weights[candidate["priority"]]
                visit(cid, lv, seq, travel + connection[1], value + weight,
                      tl + [{"id": cid, "arrive": connection[0], "start": st, "leave": lv, "via": connection[2]}])
                seq.pop()

        visit(self.day["start"]["id"], self.t0, [], 0, 0, [{"id": self.day["start"]["id"], "leave": self.t0}])
        best.sort(key=lambda item: (-item[0], item[1]))
        out, seen = [], set()
        for target in best:
            if frozenset(target[2]) not in seen:
                seen.add(frozenset(target[2]))
                out.append(target)
            if len(out) >= topk:
                break
        return out

    def format_minute(self, value):
        return format_minute(self.day, value)

    def why_alone_fails(self, cid):
        candidate, start = self.candidates_by_id[cid], self.day["start"]["id"]
        connection = self.leg(start, cid, self.t0)
        if not connection:
            return "从起点到这里没有交通数据（矩阵为 null，按官方时刻表补 overrides）"
        op, last = self.window(candidate)
        st = max(connection[0], op)
        if st > last:
            return f"最早 {self.format_minute(connection[0])} 到，晚于最晚开始 {self.format_minute(last)}"
        g2 = self.leg(cid, self.end_id, st + candidate["stay"])
        if not g2:
            return "从这里回终点没有交通数据（补 overrides）"
        if g2[0] > self.end_by:
            return f"停 {candidate['stay']}′ 后最早 {self.format_minute(g2[0])} 回到终点，晚于 {self.format_minute(self.end_by)}"
        return "单独去可行，但与必去项或先后要求冲突"

    def explain(self, plans):
        chosen, reasons = set(plans[0][2]), {}
        for cid in self.candidates_by_id:
            if cid in chosen:
                continue
            alt = self.solve(force=[cid], topk=1)
            if not alt:
                reasons[cid] = "放不进：" + self.why_alone_fails(cid)
                continue
            lost = sorted(chosen - set(alt[0][2]))
            reasons[cid] = (f"能去，但最好的含它方案得分 {alt[0][0]:.0f}，比推荐低 {plans[0][0] - alt[0][0]:.0f}"
                            + (f"，要舍掉 {'、'.join(self.candidates_by_id[candidate_id]['name'] for candidate_id in lost)}" if lost else "，多花通勤")
                            + f"（顺序：{' → '.join(self.candidates_by_id[candidate_id]['name'] for candidate_id in alt[0][2])}）")
        return reasons

    def diagnose(self):
        musts = [candidate_id for candidate_id, candidate in self.candidates_by_id.items() if candidate["priority"] == "must"]
        if not self.leg(self.day["start"]["id"], self.end_id, self.t0) and not musts:
            return ["起点到终点没有交通数据，也没有能去的候选"]
        out = [f"{self.candidates_by_id[candidate_id]['name']}：{self.why_alone_fails(candidate_id)}" for candidate_id in musts if not self.solve(force=[candidate_id], drop=set(musts) - {candidate_id}, topk=1)]
        if not out and musts:
            out.append("必去项单独都放得进，但放在一起超出时限；考虑缩短停留或改天")
        return out or ["时限内放不下任何方案"]


def render(day, plans, reasons, dt, missing, notes) -> dict:
    names = {**{candidate["id"]: candidate["name"] for candidate in day["candidates"]}, day["start"]["id"]: day["start"]["name"], day["end"]["id"]: day["end"]["name"]}
    res = {"day": day["day"], "timezone": day["timezone"], "pace": day["pace"], "settings": day["_cfg"], "elapsed_ms": round(dt * 1000, 1),
           "missing_legs": missing, "plans": [], "dropped": reasons, "sources": sorted(set(notes))[:8]}
    res["candidates"] = {candidate["id"]: {
        "included": candidate["id"] in plans[0][2],
        "reason": reasons.get(candidate["id"], f"Fits hard constraints; caller priority={candidate['priority']}, stay={candidate['stay']} min"),
        "missing_data": [pair for pair in missing if candidate["id"] in pair]}
        for candidate in day["candidates"]}
    for score, travel, seq, tl in plans:
        steps = []
        for item in tl:
            steps.append({"id": item["id"], "name": names[item["id"]], **{field_name: format_minute(day, item[field_name]) for field_name in ("arrive", "start", "leave") if field_name in item},
                          **({"wait_min": item["start"] - item["arrive"]} if "start" in item else {}), "via": item.get("via")})
            midnight = datetime.combine(Date.fromisoformat(day["day"]), datetime.min.time(), tzinfo=ZoneInfo(day["timezone"]))
            steps[-1].update({f"{key}_at": datetime.fromtimestamp(midnight.timestamp() + item[key] * 60, midnight.tzinfo).isoformat()
                              for key in ("arrive", "start", "leave") if key in item})
        res["plans"].append({"score": round(score, 1), "travel_min": travel, "order": seq, "timeline": steps,
                             "reasons": [f"Hard nodes, opening windows and end deadline satisfied; pace={day['pace']}",
                                         f"Preset score={score:.1f}; travel and waiting={travel} min"],
                             "missing_data": missing})
    return res



def prepare(day, *, max_candidates=MAX_CANDIDATES):
    try:
        result = _prepare(day, max_candidates=max_candidates)
        for value in [result["start"]["time"], result["end"]["by"]]:
            elapsed_minute(result, value)
        for candidate in result["candidates"]:
            for value in candidate.get("open") or []:
                elapsed_minute(result, value)
            if candidate.get("last_entry"):
                elapsed_minute(result, candidate["last_entry"])
        for override in result.get("overrides") or []:
            for departure in override.get("deps") or []:
                if elapsed_minute(result, departure[1]) < elapsed_minute(result, departure[0]):
                    raise ServiceError("INVALID_INPUT", "Override arrival precedes departure")
        return result
    except ServiceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        raise ServiceError("INVALID_INPUT", f"Malformed day input: {error}") from error


async def build_legs(day, tz, fresh=False, *, provider):
    nodes = [day["start"], *day["candidates"], day["end"]]
    resolved = await gather_cancel(*(provider.resolve(node["at"], fresh=fresh) for node in nodes))
    locations = {node["id"]: value.value for node, value in zip(nodes, resolved)}
    evidence = list(resolved)
    matrix = {}
    midnight = datetime.combine(Date.fromisoformat(day["day"]), datetime.min.time(), tzinfo=tz)

    def place(node):
        location = locations[node["id"]]
        return Place(location["label"], location["lat"], location["lon"], location["place"])

    async def one_origin(origin):
        destinations = [node for node in [*day["candidates"], day["end"]] if node["id"] != origin["id"]]
        walking = [None] * len(destinations)
        indices = [index for index, node in enumerate(destinations) if locations[node["id"]]["lat"] is not None]
        if locations[origin["id"]]["lat"] is not None and indices:
            result = await provider.walk_minutes(place(origin), [place(destinations[index]) for index in indices], fresh=fresh)
            evidence.append(result)
            for index, minutes in zip(indices, result.value):
                walking[index] = minutes

        async def pair(destination, walk):
            departures = []
            if walk is None or walk > 20:
                result = await provider.day(locations[origin["id"]]["place"], locations[destination["id"]]["place"],
                                            day["day"], timezone_name=str(tz), fresh=fresh)
                evidence.append(result)
                departures = [[round((itinerary.depart.timestamp() - midnight.timestamp()) / 60),
                               round((itinerary.arrive.timestamp() - midnight.timestamp()) / 60),
                               "+".join(leg.line or leg.mode for leg in itinerary.legs)] for itinerary in result.value]
            return destination["id"], ({"walk": walk, "deps": departures} if walk is not None or departures else None)

        matrix[origin["id"]] = dict(await gather_cancel(*(pair(node, walk) for node, walk in zip(destinations, walking))))

    await gather_cancel(*(one_origin(node) for node in [day["start"], *day["candidates"]]))
    return {"matrix": matrix, "nodes": {key: {"label": value["label"], "how": value["how"]} for key, value in locations.items()},
            "evidence": [{"source": item.source, "fetched_at": item.fetched_at.isoformat(),
                          "valid_until": item.valid_until.isoformat() if item.valid_until else None,
                          "stale": item.stale, "notes": item.notes} for item in evidence]}, [note for item in evidence for note in item.notes]


async def solve_day(day: dict, alternatives=2, fresh=False, *, provider=None, timeout=300,
                    max_candidates=MAX_CANDIDATES, node_limit=SOLVER_NODE_LIMIT):
    if timeout <= 0 or not isinstance(alternatives, int) or not 0 <= alternatives <= 9:
        raise ServiceError("INVALID_INPUT", "timeout must be positive and alternatives between 0 and 9")
    prepared = prepare(day, max_candidates=max_candidates)
    cancelled = threading.Event()
    deadline = time.monotonic() + timeout
    owned = provider is None
    provider = provider or live_transit()
    try:
        with request_scope(priority=1):
            async with asyncio.timeout(timeout):
                legs, notes = await build_legs(prepared, ZoneInfo(prepared["timezone"]), fresh, provider=provider)
                matrix = apply_overrides(prepared, legs["matrix"])
                if prepared.get("overrides"):
                    notes.append("Travel overrides supplied by caller; not independently verified")
                need = {(origin["id"], destination["id"]) for origin in [prepared["start"], *prepared["candidates"]]
                        for destination in [*prepared["candidates"], prepared["end"]] if origin["id"] != destination["id"]}
                missing = [[origin, destination] for origin, destination in sorted(need) if not matrix[origin].get(destination)]
                solver = Solver(prepared, matrix, deadline=deadline, cancelled=cancelled, node_limit=node_limit)

                def run():
                    started = time.monotonic()
                    plans = solver.solve(topk=1 + alternatives)
                    if not plans:
                        return {"day": prepared["day"], "error": "No feasible plan", "why": solver.diagnose(), "missing_legs": missing, "plans": [],
                                "sources": sorted(set(notes)), "candidates": {
                                    candidate["id"]: {"included": False, "reason": solver.why_alone_fails(candidate["id"]),
                                                       "missing_data": [pair for pair in missing if candidate["id"] in pair]}
                                    for candidate in prepared["candidates"]}}
                    return render(prepared, plans, solver.explain(plans), time.monotonic() - started, missing, notes)

                result = await asyncio.to_thread(run)
                result["nodes"] = legs["nodes"]
                result["evidence"] = legs["evidence"]
                return result
    except TimeoutError as error:
        raise ServiceError("SOLVER_TIMEOUT", "Day solve exceeded its deadline") from error
    finally:
        cancelled.set()
        if owned:
            await provider.aclose()
