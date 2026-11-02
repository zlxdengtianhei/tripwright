"""Answer data → the Markdown the host copies (CONTRACTS 3.4, design 9.2).

Pure. Three outlines: quick, first ("how to go now") and second ("checked plan"). Conflict notices come first, place
names stand alone on their own line, each transport leg is one line that names the app and the exact search, and every
unchecked fact is one line of what → consequence → what to do on site. The second reply's title follows from the verdict, the commit and the write state
(CONTRACTS terms), so the data never stores a separate "checked" flag.
"""
from __future__ import annotations

from tripwright.render.lines import leg_text, place_lines
from tripwright.render.options import TRADE_WORDS, option_lines, options_problems

WORDS = {
    "zh": {"first": "# 第一段 · 现在怎么走（{time}）", "now": "现在：", "latest": "最晚：", "unchecked": "没核：",
           "unchecked_second": "没核到：", "checked": "核过的安排", "unfinished": "安排（没核完）", "draft": "（草稿）",
           "second": "# 第二段 · {title}", "differences": "和第一段不同：", "hard_nodes": "硬节点",
           "schedule": "# 今天的安排（从 {time} 起）", "todo": "要你做的：", "conditions": "按你说的：", "stop": "。"},
    "en": {"first": "# First reply · How to go now ({time})", "now": "Now: ", "latest": "Latest: ", "unchecked": "Not checked: ",
           "unchecked_second": "Not checked: ", "checked": "Checked plan", "unfinished": "Plan (not fully checked)",
           "draft": " (draft)", "second": "# Second reply · {title}", "differences": "Changed since the first reply: ",
           "hard_nodes": "Hard nodes", "schedule": "# Today's plan (from {time})", "todo": "For you to do: ",
           "conditions": "Based on what you said: ", "stop": "."},
}


def render_answer(answer: dict, *, orders: list[dict] | None = None) -> str:
    """Render one answer. `orders` are the pack's preference orders; when they include two preferences without a user
    order, no line may say that one of them was traded off (design 9.5)."""
    problems = answer_problems(answer, orders)
    if problems:
        raise ValueError("; ".join(problems))
    w = WORDS["en" if answer["language"] == "en" else "zh"]
    skeleton = answer["skeleton"]
    if skeleton == "quick":
        blocks = quick(answer, w)
    elif skeleton == "first":
        blocks = first(answer, w)
    elif skeleton == "second":
        blocks = second(answer, w)
    else:
        raise ValueError(f"Unknown answer outline {skeleton!r}")
    notices = [notice["line"] for notice in answer["notices"]]
    blocks = [block for block in blocks if block]
    if notices:
        blocks = [notices + blocks[0], *blocks[1:]] if blocks else [notices]
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def answer_problems(answer: dict, orders: list[dict] | None = None) -> list[str]:
    problems = options_problems(answer["options"]) if answer.get("options") else []
    unordered = [order for order in orders or [] if order["status"] == "none"]
    if answer.get("options") or unordered:
        texts = [line for key in ("answer", "now", "differences", "todo", "conditions") for line in answer.get(key) or []]
        texts += [entry.get("text", "") for entry in answer.get("schedule") or []]
        if any(word in text.lower() for text in texts for word in TRADE_WORDS):
            problems.append("two of the user's preferences without a user order go into different options; "
                            "the answer must not say one was traded off")
    return problems


def labeled(label: str, items: list[str]) -> list[str]:
    if len(items) == 1:
        return [label + items[0]]
    return [label.rstrip("：: ")] + [f"- {item}" for item in items] if items else []


def unchecked(line: dict, label: str, stop: str) -> str:
    return f"{label}{line['what']} → {line['consequence']} → {line['onsite']}{stop}"


def places(answer: dict) -> list[list[str]]:
    return [place_lines(place, answer["language"]) for place in answer["places"]]


def legs(answer: dict) -> list[str]:
    return [leg_text(leg, answer["language"]) for leg in answer["legs"]]


def quick(answer: dict, w: dict) -> list[list[str]]:
    tail = []
    if answer["hard_node"]:
        tail.append(w["latest"] + answer["hard_node"]["line"])
    if answer["check"]:
        tail.append(unchecked(answer["check"], w["unchecked"], w["stop"]))
    return [list(answer["answer"]), *places(answer), legs(answer), tail]


def first(answer: dict, w: dict) -> list[list[str]]:
    head = [w["first"].format(time=answer["heading_time"])] + labeled(w["now"], answer["now"])
    head += [w["latest"] + line["line"] for line in answer["latest"]]
    options = answer["options"]
    if options:
        head += options["common"]
    tail = [answer["tool_hint"]["line"]] if answer["tool_hint"] else []
    tail += [unchecked(line, w["unchecked"], w["stop"]) for line in answer["checks"]]
    return [head, option_lines(options, answer["language"]) if options else [], *places(answer), legs(answer), tail]


def second_title(answer: dict, w: dict) -> str:
    checked = answer["result"]["verdict"] == "passed" and answer["commit"]["accepted"]
    draft = answer["write"] is None or answer["write"]["state"] != "effective"
    return w["second"].format(title=(w["checked"] if checked else w["unfinished"]) + (w["draft"] if draft else ""))


def second(answer: dict, w: dict) -> list[list[str]]:
    head = [second_title(answer, w)] + labeled(w["differences"], answer["differences"])
    hard = [w["hard_nodes"]] + [f"- {line['line']}" for line in answer["hard_nodes"]] if answer["hard_nodes"] else []
    schedule = [w["schedule"].format(time=answer["schedule_from"])]
    schedule += [f"{entry['time']} {leg_text(entry['leg'], answer['language']) if 'leg' in entry else entry['text']}"
                 for entry in answer["schedule"]]
    options = option_lines(answer["options"], answer["language"]) if answer["options"] else []
    tail = labeled(w["todo"], answer["todo"])
    tail += [unchecked(line, w["unchecked_second"], w["stop"]) for line in answer["unchecked"]]
    tail += labeled(w["conditions"], answer["conditions"])
    return [head, hard, schedule, options, tail]
