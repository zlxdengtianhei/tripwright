"""The short list of recent versions every `turn` receipt carries, so the host agent can decide for itself which version
the user wants back (2026-11-28): the service does not read the user's words for an undo and does not go back by itself.
Pure functions over stored records; the service gathers the records (`Service._versions_brief`)."""
from __future__ import annotations

from tripwright.locks import plan_changes, plan_objects

SHOWN = 5  # the newest versions listed
SAID_LIMIT = 80  # characters of the user's words
CHANGE_LIMIT = 80  # characters of the one-sentence change
SEPARATOR = dict(zh="；", en="; ")
WORDS = dict(
    zh=dict(restored="撤回到第 {number} 版", edited="页面上直接改的", ledger="只记了要求，计划没改", nothing="计划没有变化",
            first="第一版", more="等 {n} 处", end="结束", things="{n} 处改动"),
    en=dict(restored="undo to version {number}", edited="edited directly on the page", ledger="only requirements recorded; the plan did not change",
            nothing="no change to the plan", first="first version", more="and {n} more", end="end", things="{n} changes"))


def cut(text: str, limit: int) -> str:
    """`text` on one line, at most `limit` characters."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _label(objects: dict, target: str) -> str:
    """What a block is called in a sentence: its own words, else its ID (agents name them by what they are: d1-dinner)."""
    block = (objects.get(target) or (None, {}))[1]
    return str(block.get("text") or target.split(":", 1)[-1])


def _length(start: str, end: str) -> int | None:
    """Minutes from `start` to `end` (HH:MM), None when either is not a clock time."""
    try:
        (a, b), (c, d) = start.split(":"), end.split(":")
        return (int(c) * 60 + int(d)) - (int(a) * 60 + int(b))
    except (ValueError, AttributeError):
        return None


def change_sentence(before: dict | None, after: dict | None, language: str) -> str:
    """One sentence on what `after` changes in `before`, from the plan comparison the change log uses (`plan_changes`):
    “晚饭 19:00→17:00” (also when the block moved as a whole); a block whose start and end moved by different amounts shows both (“晚饭 19:00-20:00→17:00-18:30”). A change that is
    not a time of a block is counted, not described."""
    words = WORDS["en" if language == "en" else "zh"]
    if not after:
        return words["nothing"]
    if before is None:
        return words["first"]
    lines = plan_changes(before, after)
    if not lines:
        return words["nothing"]
    seen = plan_objects(after)
    objects = seen | {key: value for key, value in plan_objects(before).items() if key not in seen}
    times: dict[str, dict] = {}
    rest = 0
    for line in lines:
        if line["target"].startswith("block:") and line["field"] in ("start", "end") and line["change"] == "changed":
            times.setdefault(line["target"], {})[line["field"]] = (line["old"], line["new"])
        else:
            rest += 1
    parts = []
    for target, moved in times.items():
        label = _label(objects, target)
        if len(moved) == 2 and _length(*moved["start"][0:1], *moved["end"][0:1]) == _length(moved["start"][1], moved["end"][1]):
            parts.append(f"{label} {moved['start'][0]}→{moved['start'][1]}")  # moved as a whole: the start says it
        elif len(moved) == 2:
            parts.append(f"{label} {moved['start'][0]}-{moved['end'][0]}→{moved['start'][1]}-{moved['end'][1]}")
        elif "start" in moved:
            parts.append(f"{label} {moved['start'][0]}→{moved['start'][1]}")
        else:
            parts.append(f"{label} {words['end']} {moved['end'][0]}→{moved['end'][1]}")
    if rest and not parts:
        return words["things"].format(n=rest)
    if rest:
        parts.append(words["more"].format(n=rest))
    return cut(SEPARATOR["en" if language == "en" else "zh"].join(parts), CHANGE_LIMIT)


def entry(version: dict, *, previous: dict | None, said: str | None, why: str | None, taken_back: int, local_at: str, language: str) -> dict:
    """One line of the list for `version`. `said`: the words of the message that made it (None for a version the service
    or the page made); `why`: the reason the proposal gave, which wins over the plan comparison."""
    words = WORDS["en" if language == "en" else "zh"]
    restored = version.get("restored_from")
    if restored is not None:
        spoken = words["restored"].format(number=restored)
    elif version.get("origin") == "external_edit":
        spoken = words["edited"]
    else:
        spoken = cut(said or "", SAID_LIMIT)
    if why and restored is None:
        change = cut(why, CHANGE_LIMIT)
    elif version.get("plan") and previous is not None and previous.get("plan") == version.get("plan"):
        change = words["ledger"]
    else:
        change = change_sentence((previous or {}).get("plan"), version.get("plan"), language)
    return dict(number=version["number"], at=local_at, said=spoken, change=change,
                **({"restored_from": restored} if restored is not None else {}),
                **({"taken_back": taken_back} if taken_back else {}))
