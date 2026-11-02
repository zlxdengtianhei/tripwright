"""Redo a plan change on a newer plan (D-75): when a plan commit is replayed on a head whose plan changed elsewhere, the
change this commit made against its own base is applied to the new plan, object by object, instead of refusing it.

The plan is compared as a tree of identified objects: days by date; people, places, bookings, fares, hard nodes, plans
and blocks by ID; a plan's tracks by their people. Other values (times, names, lists of IDs) are compared whole. A field
that only one side changed takes that side's value; a field both sides changed to different values is a conflict that
names the object and both values, for the user to choose. Order inside a list follows the side that changed it. There
is no text merge: the result is a plan that the commit then checks again in full on the new head (hard constraints,
must locks, the user's decisions), like any other.
"""
from __future__ import annotations

import json
from copy import deepcopy

ABSENT = object()
KEYED = {"people": "id", "places": "id", "bookings": "id", "fares": "id", "days": "date", "anchors": "id", "plans": "id",
         "tracks": "subject", "blocks": "id"}
FIELDS = {"start": "开始时间", "end": "结束时间", "place": "地点", "kind": "类型", "title": "标题", "note": "备注", "notes": "备注",
          "name": "名字", "status": "状态", "time": "时刻", "text": "内容", "area": "地区", "pace": "节奏", "base": "住处",
          "options": "备选地点", "subject": "人", "what": "内容", "date": "日期", "order": "顺序", "rain_plan": "雨天安排"}
REDONE = "在别处的改动之上重做了这次改动"


def identity(name: str, element) -> str | None:
    if not isinstance(element, dict):
        return None
    value = element.get(KEYED[name])
    if name == "tracks" and isinstance(value, list):
        return ",".join(sorted(map(str, value)))
    return value if isinstance(value, str) else None


def three_way(base: dict, ours: dict, theirs: dict) -> tuple[dict, list[dict]]:
    """`ours` changed `base`; `theirs` is the newer plan. Returns the newer plan with our changes on it, and the fields
    both changed differently as [{path, base, ours, theirs}] (then the plan returned is not to be used)."""
    conflicts: list[dict] = []
    merged = _merge(base, ours, theirs, [], conflicts)
    return merged, conflicts


def _merge(base, ours, theirs, path: list, conflicts: list):
    if _same(ours, base):
        return _copy(theirs)
    if _same(theirs, base) or _same(ours, theirs):
        return _copy(ours)
    if all(isinstance(value, dict) for value in (base, ours, theirs)):
        merged = {}
        for key in list(theirs) + [key for key in ours if key not in theirs]:
            value = _merge(base.get(key, ABSENT), ours.get(key, ABSENT), theirs.get(key, ABSENT), path + [key], conflicts)
            if value is not ABSENT:
                merged[key] = value
        return merged
    name = next((part for part in reversed(path) if isinstance(part, str)), None)
    if name in KEYED and all(isinstance(value, list) for value in (base, ours, theirs)):
        merged = _keyed(name, base, ours, theirs, path, conflicts)
        if merged is not None:
            return merged
    conflicts.append(dict(path=path, base=_plain(base), ours=_plain(ours), theirs=_plain(theirs)))
    return _copy(theirs)


def _keyed(name: str, base: list, ours: list, theirs: list, path: list, conflicts: list) -> list | None:
    """Merge a list of identified objects element by element; None when its elements cannot be told apart."""
    sides = []
    for side in (base, ours, theirs):
        keys = [identity(name, element) for element in side]
        if None in keys or len(set(keys)) != len(keys):
            return None
        sides.append(dict(zip(keys, side)))
    before, mine, newer = sides
    merged = {}
    for key in list(newer) + [key for key in mine if key not in newer] + [key for key in before if key not in newer and key not in mine]:
        value = _merge(before.get(key, ABSENT), mine.get(key, ABSENT), newer.get(key, ABSENT), path + [(name, key)], conflicts)
        if value is not ABSENT:
            merged[key] = value
    return [merged[key] for key in _order(name, list(before), list(mine), list(newer), merged, path, conflicts)]


def _order(name, before, mine, newer, merged, path, conflicts) -> list:
    """The newer order, unless only our side reordered the shared elements; our new elements go after their neighbour."""
    shared = [key for key in before if key in mine and key in newer]
    ours = [key for key in mine if key in shared]
    theirs = [key for key in newer if key in shared]
    if ours != shared and theirs != shared and ours != theirs:
        conflicts.append(dict(path=path + ["order"], base=shared, ours=ours, theirs=theirs))
    leading = mine if (ours != shared and theirs == shared) else newer
    result = [key for key in leading if key in merged]
    for position, key in enumerate(mine):
        if key in merged and key not in result:
            after = next((mine[index] for index in range(position - 1, -1, -1) if mine[index] in result), None)
            result.insert(result.index(after) + 1 if after is not None else 0, key)
    return result + [key for key in merged if key not in result]


def _same(left, right) -> bool:
    return left is right or (left is not ABSENT and right is not ABSENT and left == right)


def _copy(value):
    return value if value is ABSENT else deepcopy(value)  # the marker for a missing value must stay the same object


def _plain(value):
    return None if value is ABSENT else deepcopy(value)


def user_line(conflicts: list[dict], plan: dict) -> str:
    """One line for the user naming each clashing item and both values, ending with the question."""
    shown = [f"{label(conflict['path'], plan)}：别处改成了{value(conflict['theirs'], plan)}，这次改成了{value(conflict['ours'], plan)}"
             for conflict in conflicts[:2]]
    more = f"（另有 {len(conflicts) - 2} 处）" if len(conflicts) > 2 else ""
    return "两处改了同一项、改得不一样：" + "；".join(shown) + more + "。要用哪个？"


def label(path: list, plan: dict) -> str:
    names = {place["id"]: place.get("name") or place["id"] for place in plan.get("places", [])}
    keyed = [part for part in path if isinstance(part, tuple)]
    field = next((part for part in reversed(path) if isinstance(part, str) and part not in KEYED), None)
    date = next((key for name, key in keyed if name == "days"), None)
    where = "行程"
    if keyed:
        name, key = keyed[-1]
        element = _find(plan, keyed)
        if name == "blocks":
            title = names.get((element or {}).get("place")) or (element or {}).get("kind") or key
            where = f"{date} 的「{title}」（{key}）"
        elif name == "days":
            where = f"{date} 那天"
        elif name == "plans":
            where = f"{date} 的方案「{(element or {}).get('name') or key}」"
        elif name == "anchors":
            where = f"{date} 的硬节点「{(element or {}).get('text') or key}」"
        elif name == "places":
            where = f"地点「{names.get(key, key)}」"
        elif name == "bookings":
            where = f"预订「{(element or {}).get('what') or key}」"
        elif name == "tracks":
            where = f"{date} {key} 的安排"
        else:
            where = f"{date + ' 的' if date else ''}{key}"
    if field is None or (keyed and field == keyed[-1][0]):
        return where
    return f"{where}的{FIELDS.get(field, field)}"


def _find(plan: dict, keyed: list):
    node = plan
    for name, key in keyed:
        items = node.get(name) if isinstance(node, dict) else None
        node = next((item for item in items or [] if identity(name, item) == key), None)
        if node is None:
            return None
    return node


def value(raw, plan: dict) -> str:
    if raw is None:
        return "（删掉）"
    names = {place["id"]: place.get("name") or place["id"] for place in plan.get("places", [])}
    if isinstance(raw, str):
        return f"「{names.get(raw, raw)}」"
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("title") or raw.get("text") or raw.get("id") or raw
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    return f"「{text[:60]}」"
