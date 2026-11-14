"""Whether the user's quoted words give a definite new value for a protected field (2026-11-13, the user, D-19):

“假如用户已经说了，那肯定要认啊……1. 先去分析用户说的内容到底是不是一个确定的时间或车次。2. 如果是不确定的，那肯定不能定，
需要再去询问。3. 假如用户确实给出了明确的修改信息，那就必须认。不只是车次，其他类似的问题也都要这样处理。”

One check for every report (`changes.reports`), by the kind of value the field holds:
- time (HH:MM, or a field named time/start/end): a clock time in the words, “16:30”, “9 点”, “九点半”, “早上 9 点”,
  “9am”, “at 9”, “9 o'clock”;
- date (YYYY-MM-DD): “6 月 11 日”, “11 号”, “June 11”, “11th”, “明天”, “tomorrow” (from the day the message came);
- number (an integer field): the number in digits;
- code (a text value with a digit, such as a line or a train number “IC 23”): the code itself, spaces and case aside;
- place (a place ID field): the place's name, local name or ID.
Other fields (a booking's status, “我买了票”) are not read for a value, as before.

`definite`: the words name the new value. `approximate`: they name it with a word of doubt (“左右”, “大概”, “around”),
or within 30 minutes of a time said that way: per the user's point 2 the agent asks first, and the words count once
the user has answered (service: cited from the answer's turn). `missing`: the words name no value of that kind (“改坐早一点的
车”, “可以，就按这样存吧。”). `other`: the words name a different value. The service reads only the words; it cannot tell
whether an answer agrees with what the agent asked (deploy/README)."""
from __future__ import annotations

import re
from datetime import date, timedelta

APPROXIMATE_MINUTES = 30
VAGUE = re.compile(r"左右|大概|大约|约莫|约|前后|上下|差不多|多点|\baround\b|\babout\b|\bapprox(?:imately)?\b|\broughly\b|-?ish\b|\bor so\b",
                   re.IGNORECASE)
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_NUMBER = "[零〇一二两三四五六七八九十]{1,3}"
EN_NUMBERS = {word: number for number, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
LATE = ("下午", "晚上", "傍晚", "夜里", "夜间", "pm", "p.m.", "evening", "afternoon", "tonight")
EARLY = ("早上", "上午", "凌晨", "清早", "早晨", "am", "a.m.", "morning")
CN_TIME = re.compile(rf"(早上|上午|中午|下午|晚上|傍晚|凌晨|夜里|清早|早晨)?\s*(\d{{1,2}}|{CN_NUMBER})\s*(?:点|时)\s*"
                     rf"(半|一刻|三刻|(\d{{1,2}}|{CN_NUMBER})\s*分?)?")
CLOCK = re.compile(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)\s*(am|pm|a\.m\.|p\.m\.)?", re.IGNORECASE)
EN_TIME = re.compile(r"(?:\b(at|around|about)\s+)?\b(\d{1,2}|" + "|".join(EN_NUMBERS) + r")(?:\b|(?=[ap]\.?m\b))\s*(am|pm|a\.m\.|p\.m\.|o'?clock)?(\s*-?ish)?",
                     re.IGNORECASE)
# “早一点”, “晚一点”, “快一点”: “一点” is “a bit” there, not one o'clock
A_BIT = re.compile(r"[早晚快慢多少好大小些]$")
CN_DATE = re.compile(rf"(?:(\d{{1,2}}|{CN_NUMBER})\s*月\s*)?(\d{{1,2}}|{CN_NUMBER})\s*[日号]")
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
MONTHS = {name: number for number, name in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
EN_DATE = re.compile(r"\b(" + "|".join(MONTHS) + r"|" + "|".join(name[:3] for name in MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b"
                     r"|\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(MONTHS) + r"|" + "|".join(name[:3] for name in MONTHS) + r")\b"
                     r"|\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
RELATIVE = {"今天": 0, "今晚": 0, "明天": 1, "明早": 1, "明晚": 1, "后天": 2, "today": 0, "tonight": 0, "tomorrow": 1}
TIME_FIELDS = ("time", "start", "end")
PLACE_FIELDS = ("place", "destination", "origin")


def cn_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text.startswith("十"):
        text = "一" + text
    if "十" in text:
        tens, _, ones = text.partition("十")
        return CN_DIGITS.get(tens, 1) * 10 + (CN_DIGITS[ones] if ones else 0)
    return CN_DIGITS[text]


def vague_near(text: str, start: int, end: int) -> bool:
    return bool(VAGUE.search(text[max(0, start - 6):end + 6]))


def times(text: str) -> list[dict]:
    """Clock times in the words: {hour, minute, period (early|late|None), approximate}."""
    found, taken = [], []

    def add(hour, minute, period, start, end, approximate=False):
        if 0 <= hour <= 24 and 0 <= minute < 60:
            found.append(dict(hour=hour % 24, minute=minute, period=period, approximate=approximate or vague_near(text, start, end)))
            taken.append((start, end))

    for match in CLOCK.finditer(text):
        word = (match.group(3) or "").lower()
        add(int(match.group(1)), int(match.group(2)), "late" if word in LATE else "early" if word in EARLY else None, *match.span())
    for match in CN_TIME.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue
        if match.group(2) == "一" and not match.group(1) and not match.group(3) and A_BIT.search(text[:match.start()].rstrip()):
            continue
        minute = {"半": 30, "一刻": 15, "三刻": 45}.get(match.group(3) or "", None)
        if minute is None:
            minute = cn_number(match.group(4)) if match.group(4) else 0
        part = match.group(1) or ""
        add(cn_number(match.group(2)), minute, "late" if part in LATE else "early" if part in EARLY else None, *match.span())
    for match in EN_TIME.finditer(text):
        if any(start <= match.start() < end or start < match.end() <= end for start, end in taken):
            continue
        lead, number, word, ish = match.group(1), match.group(2), (match.group(3) or "").lower(), match.group(4)
        if not (lead or word or ish):
            continue  # a bare number is no time ("11 号", "2 people")
        hour = int(number) if number.isdigit() else EN_NUMBERS[number.lower()]
        add(hour, 0, "late" if word in LATE else "early" if word in EARLY else None, *match.span(),
            approximate=bool(ish) or (lead or "").lower() in ("around", "about"))
    return found


def same_time(said: dict, hour: int, minute: int) -> bool:
    if said["minute"] != minute:
        return False
    if said["period"] == "late" and said["hour"] < 12:
        return said["hour"] + 12 == hour
    if said["period"] == "early":
        return said["hour"] % 12 == hour % 12 and hour < 12
    return said["hour"] % 12 == hour % 12  # "9 点" is 09:00 or 21:00; the agent's plan says which


def judge_time(text: str, new: str) -> tuple[str, list[dict]]:
    hour, minute = (int(part) for part in str(new).split(":")[:2])
    said = times(text)
    if any(same_time(entry, hour, minute) and not entry["approximate"] for entry in said):
        return "definite", said
    target = hour * 60 + minute
    for entry in said:
        if not entry["approximate"]:
            continue
        hours = [entry["hour"]] if entry["period"] else [entry["hour"] % 12, entry["hour"] % 12 + 12]
        if entry["period"] == "late" and entry["hour"] < 12:
            hours = [entry["hour"] + 12]
        if any(abs(candidate * 60 + entry["minute"] - target) <= APPROXIMATE_MINUTES for candidate in hours):
            return "approximate", said
    return ("other" if said else "missing"), said


def dates(text: str, said_on: date | None) -> list[tuple[int | None, int, int | None]]:
    """(month or None, day, year or None) named in the words, relative days resolved from `said_on`."""
    found = [(int(match.group(2)), int(match.group(3)), int(match.group(1))) for match in ISO_DATE.finditer(text)]
    stripped = ISO_DATE.sub(" ", text)
    found += [(cn_number(match.group(1)) if match.group(1) else None, cn_number(match.group(2)), None) for match in CN_DATE.finditer(stripped)]
    for match in EN_DATE.finditer(stripped):
        if match.group(1):
            found.append((month_of(match.group(1)), int(match.group(2)), None))
        elif match.group(3):
            found.append((month_of(match.group(4)), int(match.group(3)), None))
        else:
            found.append((None, int(match.group(5)), None))
    if said_on is not None:
        for word, days in RELATIVE.items():
            if re.search(word if word.isascii() is False else rf"\b{word}\b", text, re.IGNORECASE):
                day = said_on + timedelta(days=days)
                found.append((day.month, day.day, day.year))
    return found


def month_of(name: str) -> int:
    name = name.lower().rstrip(".")
    return next(number for full, number in MONTHS.items() if full == name or full[:3] == name)


def judge_date(text: str, new: str, said_on: date | None) -> str:
    year, month, day = (int(part) for part in str(new).split("-"))
    said = dates(text, said_on)
    if any(d == day and m in (None, month) and y in (None, year) for m, d, y in said):
        return "definite"
    return "other" if said else "missing"


def squeezed(text: str) -> str:
    return re.sub(r"[\s\-_·.]", "", text).casefold()


def kind_of(field: str, new) -> str:
    if isinstance(new, bool):
        return "free"
    if field in TIME_FIELDS or isinstance(new, str) and re.fullmatch(r"\d{1,2}:\d{2}", new):
        return "time"
    if isinstance(new, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", new):
        return "date"
    if isinstance(new, int):
        return "number"
    if field in PLACE_FIELDS:
        return "place"
    if isinstance(new, str) and re.search(r"\d", new):
        return "code"
    return "free"


def judge(field: str, new, quote: str, *, said_on: date | None = None, place: dict | None = None) -> dict:
    """{kind, status, why}: status definite, approximate, missing, other, or free (a field whose value is not read)."""
    kind = kind_of(field, new)
    if kind == "time":
        status, _ = judge_time(quote, new)
    elif kind == "date":
        status = judge_date(quote, new, said_on)
    elif kind == "number":
        numbers = [int(number) for number in re.findall(r"(?<![\d:])\d+(?![\d:])", quote)]
        status = ("approximate" if VAGUE.search(quote) else "definite") if new in numbers else "other" if numbers else "missing"
    elif kind == "code":
        status = "definite" if squeezed(str(new)) in squeezed(quote) else "missing"
    elif kind == "place":
        names = [name for name in ((place or {}).get("name"), (place or {}).get("name_local"), str(new)) if name]
        status = "definite" if any(squeezed(name) in squeezed(quote) for name in names) else "missing"
    else:
        status = "free"
    return dict(kind=kind, status=status)


# An explicit no in the user's answer (the main session's recommended default, 2026-11-13, from the user's “来自用户的说明
# 肯定是最重要的”): the words just before it do not count when this answer declines them.
NEGATION = re.compile(r"^\s*(?:不|no|nope)\s*(?:[，,。.!！]|$)|不行|不要|不改|别改|别动|算了|还是原来|还是按原来|还是用原来|保持原样|不对|不是这样|不用了"
                      r"|\bdon'?t\b|\bdo not\b|\bkeep the old\b|\bkeep it as\b|\bnever ?mind\b|\bcancel\b|\bnot that\b",
                      re.IGNORECASE)


def declines(answer: str) -> bool:
    return bool(NEGATION.search(answer or ""))


def labels(value) -> set[str]:
    words = value if isinstance(value, list) else [value]
    return {token.casefold() for word in words if isinstance(word, str) for token in re.findall(r"[A-Za-z0-9]+", word)}


def judge_value(field: str, old, new, words: str, *, said_on: date | None = None, place: dict | None = None) -> dict:
    """`judge` for one changed value, given the old one too. Text and lists of text: a one-letter label or a number the
    new value adds (“B” in “B counter” against “A counter”, “12” in “Gate 12”) must stand in the words; words and mixed
    codes inside text are not read (a name may be said in another language). Lists of numbers (coordinates) are not read."""
    if new is None:
        return dict(kind="free", status="free")
    if isinstance(new, list):
        if not all(isinstance(entry, str) for entry in new):
            return dict(kind="free", status="free")
    elif not isinstance(new, str) or kind_of(field, new) != "free":
        return judge(field, new, words, said_on=said_on, place=place)
    added = [token for token in labels(new) - labels(old) if re.fullmatch(r"[a-z]|\d+", token)]
    if not added:
        return dict(kind="free", status="free")
    said = all(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", words.casefold()) for token in added)
    return dict(kind="label", status="definite" if said else "missing")


# Words that take a record back, without the ones that also say no (不要, 算了: “不要了” may drop the record or decline
# the question) or ask for a plan change (恢复, 改回). A user's record is withdrawn on them only together with a value
# of that record, or as the answer to the service's own question naming it (ledger._withdraw, D-21, 2026-11-14).
TAKE_BACK = re.compile(r"撤|取消|删|去掉|作废|\bremove\b|\bcancel\b|\bdrop\b|\bwithdraw\b|\bdelete\b|\btake (?:it|that|this) back\b",
                       re.IGNORECASE)
# A plain yes to a question (“好的”, “可以”, “ok”).
AFFIRM = re.compile(r"^\s*(?:好|可以|行|是|对|嗯|同意|没问题|yes|yep|yeah|ok|okay|sure)(?:的|啊|吧|呀)?(?:[\s，,。.!！~～]|$)", re.IGNORECASE)


def takes_back(words: str) -> bool:
    return bool(TAKE_BACK.search(words or ""))


def affirms(words: str) -> bool:
    return bool(AFFIRM.search(words or "")) and not declines(words)


# Words that ask for a change without saying to what (“门票换个窗口买吧”, “改坐早一点的车”, “another counter”).
VAGUE_CHANGE = re.compile(r"随便|无所谓|看着办|\bwhatever\b|\banything\b|换个|换一个|换一家|换别的|另一个|另外一个|别的|其他的|早一点|晚一点|早点|晚点|早些|晚些"
                          r"|\banother\b|\bdifferent\b|\bother one\b|\bearlier\b|\blater\b|\bsomewhere else\b", re.IGNORECASE)


def vague_change(words: str) -> bool:
    return bool(VAGUE_CHANGE.search(words or ""))
