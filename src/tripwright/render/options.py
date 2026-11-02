"""Options when the user's own preferences compete (design 9.5, CONTRACTS 3.4, red-team E5).

The agent does not rank two of the user's preferences. `build_options` turns a day's choice points into two or three
complete options: points that trade the same preferences are summed over the whole day, so three small trade-offs
(10 minutes for €4 each) show up as one 30-minute, €12 difference instead of three defaults below a threshold. A user
order between the preferences, when the user gave one, is applied as stated and adds no extra choice. Independent
trade-offs go into the same options, and a line says they can be chosen separately, so no side is decided for the user.
"""
from __future__ import annotations

import re

LETTERS = "ABC"
TRADE_WORDS = ("权衡掉", "牺牲", "traded off", "trade off", "trade-off", "sacrific")
RANKING_WORDS = ("你更在乎", "更看重", "对你更重要", "你更想要", "优先考虑你", "you care more", "more important to you",
                 "you value", "you prefer")
FACT_WORDS = ("雨", "雪", "风", "天气", "末班", "封", "关门", "停运", "晚点", "余量", "排队", "售罄", "天黑", "疼", "伤", "满",
              "rain", "snow", "wind", "weather", "last ", "closed", "closure", "delay", "margin", "queue", "sold out", "dark",
              "pain", "hurt", "injur", "full")
UNKNOWN_WORDS = ("没核", "待核", "not checked")
DIMENSIONS = ("walk_minutes", "euros", "transfers", "duration_minutes")
WORDS = {
    "zh": {"walk_minutes": ("少走 {n} 分钟", "多走 {n} 分钟", "步行"), "euros": ("每人少花 €{n}", "每人多花 €{n}", "花费"),
           "transfers": ("少换乘 {n} 次", "多换乘 {n} 次", "换乘"), "duration_minutes": ("少用 {n} 分钟", "多用 {n} 分钟", "用时"),
           "base": "比 {letter} ", "same": "和 {letter} 的{dims}相同", "unknown": "{dim}没核", "join": "，", "stop": "。",
           "list": "、", "separate": "{topics}可以分开选，比如{first}按 A、{second}按 B。", "point": "第 {n} 处",
           "applied": "按你定的次序（原话 {event}）：{how}。", "how_join": "；"},
    "en": {"walk_minutes": ("{n} minutes less walking", "{n} minutes more walking", "walking"),
           "euros": ("€{n} less each", "€{n} more each", "cost"), "transfers": ("{n} fewer changes", "{n} more changes", "changes"),
           "duration_minutes": ("{n} minutes faster", "{n} minutes slower", "travel time"),
           "base": "Compared with {letter}: ", "same": "same {dims} as {letter}", "unknown": "{dim} not checked", "join": ", ",
           "stop": ".", "list": ", ", "separate": "{topics} can be chosen separately, for example {first} as in A and {second} as in B.",
           "point": "point {n}", "applied": "As you ordered (message {event}): {how}.", "how_join": "; "},
}


def amount(dimension: str, value: float) -> str:
    return f"{abs(value):.2f}" if dimension == "euros" else f"{abs(round(value)):g}"


def axis_of(point: dict) -> tuple[str, ...]:
    return tuple(point["choices"])


def user_order(axis: tuple[str, ...], orders: list[dict]) -> dict | None:
    for order in orders:
        if order["status"] == "user" and set(axis) <= set(order["order"] or []):
            return order
    return None


def difference(points: list[dict], base: dict[tuple, str], other: dict[tuple, str], letter: str, language: str) -> str:
    w = WORDS[language]
    parts, same, unknown = [], [], []
    compared = [point for point in points if axis_of(point) in base]
    for dimension in DIMENSIONS:
        if not any(dimension in choice for point in compared for choice in point["choices"].values()):
            continue
        values = []
        for point in compared:
            first = point["choices"][base[axis_of(point)]].get(dimension)
            second = point["choices"][other[axis_of(point)]].get(dimension)
            values.append(None if first is None or second is None else second - first)
        if any(value is None for value in values):
            unknown.append(w["unknown"].format(dim=w[dimension][2]))
            continue
        total = sum(values)
        if abs(total) < 1e-9:
            same.append(w[dimension][2])
        else:
            parts.append(w[dimension][0 if total < 0 else 1].format(n=amount(dimension, total)))
    if not parts and not unknown:
        return w["same"].format(letter=letter, dims=w["list"].join(same) or w["walk_minutes"][2]) + w["stop"]
    return w["base"].format(letter=letter) + w["join"].join(parts + unknown) + w["stop"]


def build_options(points: list[dict], orders: list[dict], *, names: dict[str, str], decide_before: str | None = None,
                  common: list[str] = (), language: str = "zh") -> dict:
    """Return `{options, applied, totals}`.

    Each point is `{"id", "where", "topic"?, "choices": {item_id: {"how", "walk_minutes"?, "euros"?, "transfers"?,
    "duration_minutes"?}}}`: one choice per preference it serves. `names` gives the short label of each preference
    ("省钱"). `options` is None when user orders settle every point; `applied` lists the points settled that way.
    """
    w = WORDS["en" if language == "en" else "zh"]
    language = "en" if language == "en" else "zh"
    axes: list[tuple[str, ...]] = []
    for point in points:
        if len(point["choices"]) < 2:
            raise ValueError(f"Point {point['id']} needs at least two choices")
        if axis_of(point) not in axes:
            axes.append(axis_of(point))
    settled: dict[tuple, str] = {}
    applied = []
    for axis in axes:
        order = user_order(axis, orders)
        if order:
            settled[axis] = next(item for item in order["order"] if item in axis)
            for point in points:
                if axis_of(point) == axis:
                    applied.append(dict(point=point["id"], item_id=settled[axis], source_event=order["source_event"]))
    open_axes = [axis for axis in axes if axis not in settled]
    applied_lines = []
    for event in dict.fromkeys(entry["source_event"] for entry in applied):
        hows = [point["where"] + point["choices"][settled[axis_of(point)]]["how"] for point in points
                if axis_of(point) in settled and user_order(axis_of(point), orders)["source_event"] == event]
        applied_lines.append(w["applied"].format(event=event, how=w["how_join"].join(hows)))
    if not open_axes:
        return {"options": None, "applied": applied, "applied_lines": applied_lines, "totals": {}}
    count = min(3, max(len(axis) for axis in open_axes))
    picks = [{axis: axis[min(index, len(axis) - 1)] for axis in open_axes} for index in range(count)]
    variants, totals = [], {}
    for index, pick in enumerate(picks):
        letter = LETTERS[index]
        served = list(dict.fromkeys(pick[axis] for axis in open_axes))
        hows = [point["where"] + point["choices"][pick[axis_of(point)]]["how"] for point in points if axis_of(point) in pick]
        variants.append(dict(label=f"{letter} {w['list'].join(names.get(item, item) for item in served)}",
                             diff=None if index == 0 else difference(points, picks[0], pick, "A", language),
                             how=w["how_join"].join(hows) + w["stop"], item_ids=served, plan=None))
        totals[letter] = {dimension: sum(point["choices"][pick[axis_of(point)]].get(dimension) or 0 for point in points
                                         if axis_of(point) in pick) for dimension in DIMENSIONS}
    lines = list(common) + applied_lines
    if len(open_axes) > 1:
        topics = []
        for number, axis in enumerate(open_axes, start=1):
            point = next(point for point in points if axis_of(point) == axis)
            topics.append(point.get("topic") or w["point"].format(n=number))
        lines.append(w["separate"].format(topics=w["list"].join(topics), first=topics[0], second=topics[1]))
    options = dict(common=lines, decide_before=decide_before, variants=variants, recommend=None, default=None)
    return {"options": options, "applied": applied, "applied_lines": applied_lines, "totals": totals}


def unauthorized_choices(points: list[dict], orders: list[dict], variants: list[dict]) -> dict:
    """Count, for any set of options, the points where the agent chose for the user (an unordered point with only one
    side offered) and the points where it offered a choice the user had already settled by an order."""
    offered = {item for variant in variants for item in variant["item_ids"]}
    unauthorized = extra = 0
    for point in points:
        axis = axis_of(point)
        sides = offered & set(axis)
        if user_order(axis, orders):
            extra += len(sides) > 1
        elif len(sides) < len(axis):
            unauthorized += 1
    return {"unauthorized": unauthorized, "extra_choices": extra, "variants": len(variants)}


def options_problems(options: dict) -> list[str]:
    """The rules of CONTRACTS 3.4 that a renderer can hold mechanically. Whether a reason is really a fact stays a
    judgement for replay and judges; this only refuses a reason that ranks the user's preferences or cites nothing."""
    problems = []
    variants = options["variants"]
    if variants and variants[0]["diff"]:
        problems.append("the first option is the baseline: its diff must be empty")
    for variant in variants[1:]:
        diff = variant["diff"] or ""
        if not diff:
            problems.append(f"option {variant['label']} must state its difference from {variants[0]['label'].split()[0]}")
        elif not re.search(r"\d", diff) and not any(word in diff for word in UNKNOWN_WORDS):
            problems.append(f"option {variant['label']} must give its difference in numbers or mark it not checked")
    letters = [variant["label"].split()[0] for variant in variants]
    if len(set(letters)) != len(letters):
        problems.append("option labels must start with distinct letters")
    for key in ("recommend", "default"):
        choice = options.get(key)
        if choice and choice["label"].split()[0] not in letters:
            problems.append(f"{key} names {choice['label']!r}, which is not an option")
    recommend = options.get("recommend")
    if recommend:
        reason = recommend["reason"].lower()
        if any(word in reason for word in RANKING_WORDS):
            problems.append("a recommendation cannot rest on which preference the user cares about more")
        elif not re.search(r"\d", reason) and not any(word in reason for word in FACT_WORDS):
            problems.append("a recommendation needs a factual reason (a time, distance, price, closure, weather or last service)")
    if options.get("default") and options.get("decide_before"):
        problems.append("a default is only for a choice that must be made now; with a decide-before point leave it to the user")
    texts = list(options["common"]) + [variant["how"] for variant in variants] + [variant["diff"] or "" for variant in variants]
    if any(word in text.lower() for text in texts for word in TRADE_WORDS):
        problems.append("options must not say a preference was traded off")
    return problems


def option_lines(options: dict, language: str) -> list[str]:
    en = language == "en"
    letters = [variant["label"].split()[0] for variant in options["variants"]]
    joined = (" or " if en else " 或 ").join(letters)
    if options["decide_before"]:
        lines = [f"{options['decide_before']}: {joined}" if en else f"{options['decide_before']} {joined}："]
    elif options.get("default"):
        lines = [f"Decide now: {joined}" if en else f"现在定 {joined}："]
    else:
        lines = [f"Choose {joined}" if en else f"选 {joined}："]
    for variant in options["variants"]:
        lines.append(variant["label"] + ((": " if en else "：") + variant["diff"] if variant["diff"] else ""))
        lines.append(variant["how"])
    for key, word in (("recommend", "Recommended" if en else "推荐"), ("default", "Default" if en else "默认")):
        if options.get(key):
            lines.append(f"{word} {options[key]['label']}{': ' if en else '：'}{options[key]['reason']}")
    return lines
