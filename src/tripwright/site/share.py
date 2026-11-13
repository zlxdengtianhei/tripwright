"""The page friends read: the committed plan as a static site with the private parts rewritten (D-83).

A home page (intro, route, map, one row per day) and one page per day: key times, a figure, places to go with a map
link each, how to get around, then the details folded. The places to go carry numbered pins, the same on the figure
(`figure`); the look is a scheme (`theme`). Decisions, memory, messages, evidence, fares and bookings are never read
into it. Rewrites, from the trip's own records:
- every person in `profile/people.yaml` and the plan's `people` becomes a code (Traveler A / 旅伴 A), the user too;
- a place to stay loses its name, address and coordinates: "a hostel in 1100 Wien" / "1100 Wien 的青旅"; the way in
  and out of it is written as for any other place, but minutes of walking from the door and door wording are cut;
- a flight number becomes "flight" / "航班".
The pages are written to a new folder beside the output and scanned there (`scan_tree`). A name, a stay's name or a
flight number the scan still finds is swapped for its generic word and the page is kept; an email, a phone number,
an order or ticket number, a passport number or a word of the user's own list stops the build: the old output stays,
and the finding names its category and page, never the value. Only a clean folder replaces the output, in one rename.

English is the default language. Program words are bilingual; the user's own words (Chinese in the plan) are shown in
English from `english` (rewritten Chinese → English, supplied by the agent), else as they are, and listed in
`untranslated` so the agent can fill them in and build again.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote

from jinja2 import DictLoader, Environment, select_autoescape
from markupsafe import Markup, escape

from . import figure as fig, theme as themes
from .assets import SHARE_SCRIPT
from .privacy import POLICY_WORD, Policy, PrivacyError, STAY_WORDS, flight_numbers, phone_digits, phone_numbers, redact, scan_text, stay_text, swap_words
from .templates import SHARE_BASE, SHARE_DAY, SHARE_FIGURE, SHARE_INDEX, SHARE_LOG

MARKER = ".tripwright-share"
CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")
# kinds of place that are someone's bed: never named, never on a map
STAY_KINDS = {"住宿": ("酒店", "hotel"), "住处": ("酒店", "hotel"), "酒店": ("酒店", "hotel"), "lodging": ("酒店", "hotel"),
              "hotel": ("酒店", "hotel"), "accommodation": ("酒店", "hotel"), "stay": ("酒店", "hotel"), "青旅": ("青旅", "hostel"),
              "hostel": ("青旅", "hostel"), "公寓": ("公寓", "apartment"), "apartment": ("公寓", "apartment"), "airbnb": ("公寓", "apartment"),
              "民宿": ("民宿", "guesthouse"), "guesthouse": ("民宿", "guesthouse"), "pension": ("民宿", "guesthouse"),
              "home": ("住处", "home"), "家": ("住处", "home"), "residence": ("住处", "home"), "private": ("住处", "home")}
# names that are already codes or pronouns: shown as codes in lists, never searched for in text
PLACEHOLDER = re.compile(r"^(?:我|你|me|i|本人|自己|(?:同行人?|队友|旅伴|朋友|traveler|traveller|companion|mate|friend)\s*[A-Za-z0-9]{0,3})$", re.I)
GENERIC = {"hotel", "hostel", "apartment", "apartments", "apartmenthaus", "haus", "house", "inn", "guesthouse", "pension", "gasthof",
           "gasthaus", "residence", "suites", "rooms", "room", "city", "central", "centre", "center", "zentrum", "the", "and", "und",
           "boutique", "design", "budget", "garni", "home", "studio", "flat"}
KINDS = {"visit": ("去玩", "Visit"), "travel": ("路上", "Getting around"), "meal": ("吃饭", "Meal"), "flex": ("自由安排", "Free time"),
         "anchor": ("要准时", "Fixed time"), "reserve": ("留出时间", "Time set aside")}
WEEKDAYS = (("周一", "Mon"), ("周二", "Tue"), ("周三", "Wed"), ("周四", "Thu"), ("周五", "Fri"), ("周六", "Sat"), ("周日", "Sun"))
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ShareBlocked(PrivacyError):
    """The scan found private data; `findings` maps each category to the pages it was on (no values)."""

    def __init__(self, findings: dict[str, list[str]]):
        self.findings = findings
        parts = "; ".join(f"{category} ({', '.join(pages)})" for category, pages in findings.items())
        super().__init__(f"Share page blocked, nothing replaced: {parts}")


@dataclass(frozen=True)
class Text:
    zh: str
    en: str
    translated: bool = True


def is_stay(place: dict | None) -> bool:
    return bool(place) and str(place.get("kind") or "").strip().casefold() in STAY_KINDS


DISTRICT = re.compile(r"(?:district|bezirk|ward|kerület)\b|\b(?:\d{1,2}\.?\s*)?(?:kerület|bezirk)", re.I)


def stay_area(place: dict) -> str:
    """The district a stay is in: its `district`, else the postcode and town at the end of its address, else a part of
    the address that names a district ("District X", then the area it is in), else its area."""
    if place.get("district"):
        return str(place["district"])
    segments = str(place.get("name_local") or "").split(",")[1:]
    for segment in reversed(segments):
        if re.fullmatch(r"\d{4,5}\s+[^\d,]+", segment.strip()):
            return segment.strip()
    for segment in segments:
        if DISTRICT.search(segment):
            return f"{segment.strip()}, {place.get('area')}"
    return str(place.get("area") or "")


def article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _tokens(text: str) -> list[str]:
    return [token.strip("()（）[]·.;:") for token in re.split(r"[\s,，、/]+", text) if token.strip("()（）[]·.;:")]


@dataclass
class Rules:
    """What the trip's own records say is private, as two rewrite policies (Chinese and English words) and one scan."""
    zh: Policy
    en: Policy
    codes: dict[str, tuple[str, str]]

    @classmethod
    def of(cls, plan: dict, people: list[dict] = (), extra: Policy | None = None) -> "Rules":
        zh, en = Policy(flight="航班"), Policy(flight="flight")
        persons = {}
        for person in [*plan.get("people", []), *people]:
            persons.setdefault(person.get("id") or person.get("name"), person)
        codes = {}
        for index, (identifier, person) in enumerate(persons.items()):
            letter = chr(ord("A") + index) if index < 26 else str(index + 1)
            codes[identifier] = (f"旅伴 {letter}", f"Traveler {letter}")
            name = str(person.get("name") or "").strip()
            for word in dict.fromkeys(filter(None, (name, *map(str, person.get("aliases") or ())))):
                # a placeholder such as "队友 1" is replaced but not denied; a bare pronoun such as "我" is left alone
                placeholder = bool(PLACEHOLDER.match(word))
                if placeholder and not (re.search(r"[A-Za-z0-9]$", word) and len(word) > 2):
                    continue
                zh.add(word, codes[identifier][0], "person name", deny=not placeholder)
                en.add(word, codes[identifier][1], "person name", deny=not placeholder)
        places = plan.get("places", [])
        # a phone number on a place record that is not a stay is that business's published number
        for place in places:
            if not is_stay(place):
                for value in _strings(place):
                    for number in phone_numbers(value):
                        zh.public_phones.add(phone_digits(number))
                        en.public_phones.add(phone_digits(number))
        public = " ".join(str(place.get(key) or "") for place in places if not is_stay(place) for key in ("name", "name_local", "area"))
        public = (public + " " + " ".join(map(str, plan.get("areas", []))) + " " +
                  " ".join(str(day.get("area") or "") for day in plan.get("days", []))).casefold()
        for place in filter(is_stay, places):
            area = stay_area(place).casefold()
            shown = {area, *(part.strip() for part in area.split(","))}
            words = {str(place[key]) for key in ("name", "name_local") if place.get(key)}
            segments = [segment.strip() for segment in str(place.get("name_local") or "").split(",") if segment.strip()]
            # the name and the street; the district, town or country after them stay public
            words.update(segment for segment in segments[:2] if segment.casefold() not in shown and segment.casefold() not in public)
            for source in (segments[:2] if segments else []) + [str(place.get("name") or "")]:
                for token in _tokens(source):
                    folded = token.casefold()
                    if (len(token) >= 4 and not CJK.search(token) and folded not in GENERIC and folded not in area
                            and not re.fullmatch(r"[\d-]+", token) and folded not in public):
                        words.add(token)
            for value in place.get("coords") or []:
                if re.search(r"\.\d{4,}", str(value)):
                    words.add(str(value))
            for word in words:
                zh.add(word, "住处", "lodging")
                en.add(word, "our stay", "lodging")
        for booking in plan.get("bookings", []):
            texts = [str(booking.get(key) or "") for key in ("what", "line", "number", "flight")]
            if str(booking.get("kind") or "").casefold() in ("flight", "航班", "机票"):
                for text in texts:
                    for number in {*flight_numbers(text), *BOOKED_FLIGHT.findall(text)}:
                        zh.add(number, "航班", "flight number")
                        en.add(number, "flight", "flight number")
            for key, value in _private_fields(booking):
                zh.add(value, None, "order-like identifier")
                en.add(value, None, "order-like identifier")
        if extra is not None:
            for policy in (zh, en):
                policy.replacements.extend(extra.replacements)
                policy.forbidden.extend(extra.forbidden)
                policy.categories.update(extra.categories)
        return cls(zh=zh, en=en, codes=codes)


# in a flight booking's own words any designator counts, also one that looks like a train (EN 8811) or ICAO (RYR755)
BOOKED_FLIGHT = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{2,3}|[A-Z][0-9]|[0-9][A-Z])\s?\d{1,4}[A-Z]?(?![A-Za-z0-9])")
ORDER_KEYS = re.compile(r"order|reference|^ref$|confirmation|pnr|booking_?(?:no|number|code|id)|ticket_?(?:no|number)|locator|voucher", re.I)


def _strings(record):
    if isinstance(record, dict):
        for value in record.values():
            yield from _strings(value)
    elif isinstance(record, list):
        for value in record:
            yield from _strings(value)
    elif isinstance(record, str):
        yield record


def _private_fields(record, key: str = ""):
    """Values under order-like keys in a booking (and its fares), however deeply nested."""
    if isinstance(record, dict):
        for name, value in record.items():
            yield from _private_fields(value, name)
    elif isinstance(record, list):
        for value in record:
            yield from _private_fields(value, key)
    elif record not in (None, "") and ORDER_KEYS.search(key) and re.search(r"\d", str(record)) and len(str(record)) >= 5:
        yield key, str(record)


class Words:
    """Bilingual texts for the templates; remembers which Chinese texts had no English."""

    def __init__(self, rules: Rules, english: dict[str, str] | None):
        self.rules = rules
        self.english = {}
        for key, value in (english or {}).items():
            if value:
                # a key written with a name in it is found as the rewritten text the page shows
                self.english[redact(str(key).strip(), rules.zh)] = str(value)
        self.untranslated: dict[str, None] = {}

    def __call__(self, text, english: str | None = None) -> Text | None:
        if text is None or str(text).strip() == "":
            return None
        zh = redact(str(text).strip(), self.rules.zh)
        given = english if english is not None else self.english.get(zh)
        if given is None and CJK.search(zh):
            self.untranslated[zh] = None
            return Text(zh=zh, en=zh, translated=False)
        return Text(zh=zh, en=redact(given if given is not None else str(text).strip(), self.rules.en))

    def pair(self, zh: str, en: str) -> Text:
        return Text(zh=zh, en=en)


def _date_words(day: str, number: int) -> tuple[Text, Text, Text, str]:
    value = date.fromisoformat(day)
    weekday, month = WEEKDAYS[value.weekday()], MONTHS[value.month - 1]
    short = Text(zh=f"{value.month}月{value.day}日", en=f"{value.day} {month}")
    long = Text(zh=f"{value.month}月{value.day}日 {weekday[0]}", en=f"{weekday[1]} {value.day} {month}")
    label = Text(zh=f"第 {number} 天 · {weekday[0]}", en=f"Day {number} · {weekday[1]}")
    return short, long, label, f"{value.month}/{value.day}"


def _span(start: str, end: str) -> Text:
    first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    zh = f"{first.year} 年 {first.month} 月 {first.day} 日 — {last.month} 月 {last.day} 日"
    en = f"{first.day} {MONTHS[first.month - 1]} – {last.day} {MONTHS[last.month - 1]} {last.year}"
    return Text(zh=zh, en=en)


def _quiet(item: Text | None) -> Text | None:
    """A text of a segment that starts or ends at a stay: the text without what pins the stay to one point (walking
    minutes from the door, door wording), in both languages; None when nothing is left in one of them."""
    if item is None:
        return None
    zh, en = stay_text(item.zh), stay_text(item.en)
    return Text(zh=zh, en=en, translated=item.translated) if zh and en else None


def _json(data) -> str:
    """JSON for a script tag: no `<`, `>` or `&`, and the characters as they are (a scan reads them)."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _view(plan: dict, words: Words, extras: dict | None = None) -> dict:
    rules = words.rules
    extras = extras or {}
    places = {place["id"]: place for place in plan.get("places", [])}
    everyone = {person.get("id") or person.get("name") for person in plan.get("people", [])}
    zone = ZoneInfo(plan.get("timezone") or "UTC")
    marks = _changes(extras.get("changes"))

    def who(subject) -> Text:
        if everyone and set(subject or []) >= everyone:
            return words.pair("大家", "Everyone")
        codes = [rules.codes.get(identifier, ("旅伴", "a traveler")) for identifier in subject or []]
        return words.pair("、".join(code[0] for code in codes), ", ".join(code[1] for code in codes))

    def stay_phrase(place: dict) -> Text:
        kind, area = STAY_KINDS[str(place.get("kind")).strip().casefold()], stay_area(place)
        return words.pair(f"{area} 的{kind[0]}", f"{article(kind[1])} {kind[1]} in {area}")

    def place_view(identifier) -> dict | None:
        place = places.get(identifier)
        if place is None:
            return None
        if is_stay(place):
            return dict(id=identifier, stay=stay_phrase(place))
        local = redact(str(place.get("name_local") or place.get("name") or ""), rules.en)
        name = words(place.get("name"))
        if name is not None and name.zh == local:
            name = None
        return dict(id=identifier, stay=None, local=local, name=name, coords=list(place.get("coords") or []) or None,
                    map_url="https://www.google.com/maps/search/?api=1&query=" + quote(local))

    def block_view(block: dict, quiet: bool = False, arriving: bool = False) -> dict:
        place = place_view(block.get("place"))
        kind = KINDS.get(block.get("kind"), (str(block.get("kind") or ""), str(block.get("kind") or "")))
        scrub = _quiet if quiet else (lambda item: item)
        if place and place["stay"]:
            title = place["stay"]
        elif place:
            title = place["name"] or words(place["local"])
        else:
            # a segment that starts or ends at the stay is written as any other, without what pins the stay to a point
            title = scrub(words(block.get("text")))
            title = title or (words.pair("回住处", "Back to the lodging") if quiet and arriving else None) \
                or (words.pair("从住处出发", "From the lodging") if quiet else None) or words.pair(*kind)
        how = []
        if block.get("mode"):
            mode = scrub(words(block.get("mode")))
            if mode:
                how.append(mode)
        if block.get("line") and not any(str(block["line"]) in part for item in how for part in (item.zh, item.en)):
            line = scrub(words(block["line"]))
            if line:
                how.insert(0, line)
        if block.get("walk_minutes") and not block.get("mode") and not quiet:
            how.append(words.pair(f"步行 {block['walk_minutes']} 分钟", f"{block['walk_minutes']} min walk"))
        text = words(block.get("text")) if place else None
        return dict(key=str(block.get("kind") or ""), certainty=str(block.get("certainty") or ""),
                    start=block.get("start"), end=block.get("end"), kind=words.pair(*kind),
                    title=title, text=scrub(text), place=place,
                    how=words.pair(" · ".join(item.zh for item in how), " · ".join(item.en for item in how)) if how else None,
                    note=scrub(words(block.get("note"))),
                    options=[view for view in map(place_view, block.get("options") or []) if view and not view["stay"]])

    def quiet_flags(blocks: list, base_is_stay: bool) -> list[tuple[bool, bool]]:
        """Per timeline block: (it starts or ends at a stay, and the stay is the arriving end). A block touches the stay
        when its own place is one, its neighbour's is, its words name the lodging, or it is the day's first move out of
        the stay the day is based at."""
        flags = []
        for index, block in enumerate(blocks):
            here = places.get(block.get("place"))
            before = places.get(blocks[index - 1].get("place")) if index else None
            after = places.get(blocks[index + 1].get("place")) if index + 1 < len(blocks) else None
            arriving = is_stay(here) or any(
                re.search(r"回|back", f"{words(blocks[index].get(key)).zh} {words(blocks[index].get(key)).en}", re.I)
                for key in ("text", "note") if words(blocks[index].get(key)))
            # a block with its own public place (a visit, a meal) is not the way in or out of the stay however it
            # sits in the list, and its note may name the lodging as a landmark; a block without one (or a travel
            # block) is a movement whose ends are its neighbours and whose words may be the stay's own
            movement = block.get("place") is None or str(block.get("kind") or "").casefold() in ("travel", "路上")
            touching = is_stay(here) or (movement and (is_stay(before) or is_stay(after) or any(
                STAY_WORDS.search(f"{words(blocks[index].get(key)).zh} {words(blocks[index].get(key)).en}")
                for key in ("text", "mode", "note") if words(blocks[index].get(key))))) \
                or bool(base_is_stay and index == 0 and movement)
            flags.append((touching, arriving))
        return flags

    def glance(main: dict) -> tuple[list[dict], list[dict]]:
        """From the day's main plan: the places to go and the legs, in time order."""
        stops, legs, seen = [], [], set()
        many = len(main["tracks"]) > 1
        for track in main["tracks"]:
            label = track["who"] if many and not track["everyone"] else None
            for block in track["blocks"]:
                if block["key"] == "travel":
                    signature = (block["start"], block["end"], block["title"].en, block["how"] and block["how"].en)
                    if signature not in seen:
                        seen.add(signature)
                        legs.append(dict(block, who=label))
                    continue
                items = [item for item in ([block["place"]] if block["place"] else block["options"]) if item and not item["stay"]]
                signature = (block["start"], block["end"], tuple(item["id"] for item in items))
                if items and signature not in seen:
                    seen.add(signature)
                    stops.append(dict(start=block["start"], end=block["end"], kind=block["key"], certainty=block["certainty"],
                                      spots=items, who=label))
        return sorted(stops, key=lambda row: row["start"] or ""), sorted(legs, key=lambda row: row["start"] or "")

    def pinned(stops: list[dict], legs: list[dict]) -> dict | None:
        """Number the places to go (the same numbers on the map and in the lists), mark the two ends of each leg, and
        draw the day's figure."""
        if not stops:
            return None
        labels = fig.number_stops(stops)
        pins, path, branches, last = [], [], [], None
        for stop, numbers in zip(stops, labels):
            stop["spots"] = [dict(item, pin=dict(label=label, shape=form)) for item, (label, form) in zip(stop["spots"], numbers)]
            for item, (label, form) in zip(stop["spots"], numbers):
                coords = item["coords"] or [None, None]
                pins.append(fig.Pin(label=label, shape=form, lat=coords[0], lon=coords[1], href=item["map_url"],
                                    names=(item["name"].zh if item["name"] else item["local"], item["name"].en if item["name"] else item["local"])))
            if len(numbers) == 1:
                path.append(numbers[0][0])
                last = numbers[0][0]
            elif last:
                branches += [(last, label) for label, _ in numbers]
        for leg in legs:
            leg["ends"] = fig.leg_ends(leg, stops, labels, legs)
        return drawn(fig.Figure(pins=pins, path=path, branches=branches))

    def split_title(title: Text | None) -> tuple[Text | None, Text | None]:
        """"Place: what happens" into the place and the rest, when both languages have the colon."""
        if title is None:
            return None, None
        zh_head, zh_colon, zh_rest = title.zh.partition("：")
        en_head, en_colon, en_rest = title.en.partition(": ")
        if zh_colon and en_colon and zh_rest.strip() and en_rest.strip():
            return Text(zh=zh_head, en=en_head, translated=title.translated), Text(zh=zh_rest, en=en_rest, translated=title.translated)
        return None, None

    days = []
    raw_days = sorted(plan.get("days", []), key=lambda day: day["date"])
    for number, raw in enumerate(raw_days, 1):
        short, long, label, chip = _date_words(raw["date"], number)
        base = places.get(raw.get("base"))
        stay = None
        if base is not None:
            stay = stay_phrase(base) if is_stay(base) else words.pair(
                f"过夜：{redact(str(base.get('name') or base.get('name_local')), rules.zh)}",
                f"Overnight: {redact(str(base.get('name_local') or base.get('name')), rules.en)}")
        plans = [dict(name=words(item.get("name")), note=words(item.get("note")),
                      tracks=[dict(who=who(track.get("subject")), everyone=who(track.get("subject")).en == "Everyone",
                                   blocks=[block_view(block, quiet=quiet, arriving=arriving)
                                           for block, (quiet, arriving)
                                           in zip(track.get("blocks", []), quiet_flags(track.get("blocks", []), is_stay(base)))])
                              for track in item.get("tracks", [])])
                 for item in raw.get("plans", [])]
        stops, legs = glance(plans[0]) if plans else ([], [])
        figure = pinned(stops, legs)
        days.append(dict(
            date=raw["date"], short=short, long=long, label=label, chip=chip, title=words(raw.get("title")) or long,
            head=None, rest=None,
            area=words(raw.get("area")), sunset=raw.get("sunset"), stay=stay, notes=words(raw.get("notes")), rain=words(raw.get("rain_plan")),
            anchors=[dict(time=anchor.get("time"), text=words(anchor.get("text")),
                          at=datetime.fromisoformat(f"{raw['date']}T{anchor.get('time')}").replace(tzinfo=zone).isoformat())
                     for anchor in sorted(raw.get("anchors", []), key=lambda anchor: anchor.get("time") or "")],
            stops=stops, legs=legs, figure=figure, plans=plans, marks=marks.get(raw["date"], ()),
            candidates=[dict(name=words(slot), places=[view for view in map(place_view, identifiers) if view])
                        for slot, identifiers in (raw.get("candidates") or {}).items()]))
    codes = sorted(rules.codes[person.get("id") or person.get("name")] for person in plan.get("people", [])
                   if (person.get("id") or person.get("name")) in rules.codes)
    travelers = Text(zh="、".join(code[0] for code in codes), en=", ".join(code[1] for code in codes))
    for day in days:
        day["head"], day["rest"] = split_title(day["title"])
        day["changed"] = _marked(day)
    route = _route(days, extras, words, places, raw_days)
    for index, stop in enumerate(route, 1):
        stop["pin"] = str(index)
    route_figure = drawn(fig.Figure(pins=[fig.Pin(label=stop["pin"], shape="visit", lat=stop["lat"], lon=stop["lon"],
                                                  names=(stop["name"].zh, stop["name"].en), href=stop["href"], external=False)
                                          for stop in route], path=[stop["pin"] for stop in route], dashed=True, legend=False)) \
        if any(stop["lat"] is not None for stop in route) else None
    return dict(trip_title=words(plan.get("title")) or words.pair("行程", "Trip"), days=days, route=route,
                kicker=_span(plan.get("start"), plan.get("end")), intro=words(extras.get("intro")), zone=plan.get("timezone") or "UTC",
                figure=route_figure,
                travelers=travelers if len(everyone) > 1 and travelers.en else None)


def drawn(figure: fig.Figure) -> dict:
    """A figure as the template takes it: the sketch, the data for the real map, and what the caption must say."""
    svg, square = fig.sketch(figure)
    return dict(svg=svg, square=square, missing=figure.missing(), placed=len(figure.placed()), legend=figure.legend,
                json=_json(fig.data(figure)), live=bool(figure.placed()))


def _changes(changes) -> dict[str, tuple[tuple[str, str], ...]]:
    """What really changed, per date: `changes` in the share extras, each {date, was, now}: the words of the plan that
    were replaced (`was`) and the words that replaced them (`now`). They only mark words the page already prints (struck
    through and inserted); words not on the page are not added."""
    marks: dict[str, list[tuple[str, str]]] = {}
    for change in changes or []:
        when, was, now = str(change.get("date") or ""), str(change.get("was") or "").strip(), str(change.get("now") or "").strip()
        if when and (was or now):
            marks.setdefault(when, []).append((was, now))
    return {when: tuple(pairs) for when, pairs in marks.items()}


def _marked(day: dict) -> bool:
    """Whether a mark of the day finds its words in a text the day page prints."""
    if not day.get("marks"):
        return False
    texts = [anchor["text"] for anchor in day["anchors"]]
    texts += [leg.get(key) for leg in day["legs"] for key in ("title", "how", "note")]
    texts += [block.get(key) for plan in day["plans"] for track in plan["tracks"] for block in track["blocks"]
              for key in ("title", "text", "how", "note")]
    words = [word for pair in day["marks"] for word in pair if word]
    return any(word in text.zh or word in text.en for text in texts if text for word in words)


def _route(days: list[dict], extras: dict, words: Words, places: dict, raw_days: list[dict]) -> list[dict]:
    """Where the trip goes: the stops given with the trip (`route` in the share extras: name, note, leg to the next stop,
    the city's lat/lon, the day to open), else runs of days in one area with the nights at a stay between them."""
    dates = {day["date"] for day in days}
    given = extras.get("route") or []
    if given:
        return [dict(name=words(stop.get("name")), note=words(stop.get("note")), leg=words(stop.get("leg")),
                     lat=stop.get("lat"), lon=stop.get("lon"),
                     href=f"{stop['day']}.html" if stop.get("day") in dates else None)
                for stop in given if stop.get("name")]
    runs: list[list[int]] = []
    for index, raw in enumerate(raw_days):
        if runs and raw_days[runs[-1][-1]].get("area") == raw.get("area"):
            runs[-1].append(index)
        else:
            runs.append([index])
    route = []
    for run in runs:
        first, last = date.fromisoformat(raw_days[run[0]]["date"]), date.fromisoformat(raw_days[run[-1]]["date"])
        nights = sum(1 for index in run if is_stay(places.get(raw_days[index].get("base"))))
        points = [place["coords"] for index in run for block in _blocks(raw_days[index]) if (place := places.get(block.get("place")))
                  and not is_stay(place) and place.get("coords")]
        span = (f"{first.day}–{last.day} {MONTHS[last.month - 1]}" if first.month == last.month else
                f"{first.day} {MONTHS[first.month - 1]} – {last.day} {MONTHS[last.month - 1]}") if first != last else \
            f"{first.day} {MONTHS[first.month - 1]}"
        zh_span = f"{first.month} 月 {first.day}—{last.day} 日" if first != last and first.month == last.month else \
            f"{first.month} 月 {first.day} 日—{last.month} 月 {last.day} 日" if first != last else f"{first.month} 月 {first.day} 日"
        note = words.pair(zh_span + (f" · 住 {nights} 晚" if nights else ""), span + (f" · {nights} night{'s' * (nights != 1)}" if nights else ""))
        route.append(dict(name=words(raw_days[run[0]].get("area")), note=note, leg=None,
                          lat=sum(point[0] for point in points) / len(points) if points else None,
                          lon=sum(point[1] for point in points) / len(points) if points else None,
                          href=f"{raw_days[run[0]]['date']}.html"))
    return route


def _blocks(raw_day: dict):
    for item in (raw_day.get("plans") or [])[:1]:
        for track in item.get("tracks", []):
            yield from track.get("blocks", [])


def _log_view(data, words):
    def text(value, field, where="log"):
        if not isinstance(value, dict) or not all(str(value.get(key) or "").strip() for key in ("zh", "en")):
            raise ValueError(f"{where}: {field} requires non-empty zh and en")
        return words(value["zh"], value["en"])

    def field(record, name, where, path=None):
        return text(record.get(name) if isinstance(record, dict) else None, path or name, where)

    if not isinstance(data, dict):
        raise ValueError("log: expected a mapping")
    raw_pieces = data.get("pieces")
    if not isinstance(raw_pieces, list) or not raw_pieces:
        raise ValueError("log: pieces must contain at least one piece")
    pieces, identifiers = [], set()
    for number, item in enumerate(raw_pieces, 1):
        if not isinstance(item, dict):
            raise ValueError(f"log piece #{number}: piece must be a mapping")
        identifier = item.get("id")
        where = f"log piece {identifier or f'#{number}'}"
        if not isinstance(identifier, str) or re.fullmatch(r"[a-z0-9-]+", identifier) is None:
            raise ValueError(f"{where}: id must use only lowercase letters, digits and hyphens")
        if identifier in identifiers:
            raise ValueError(f"{where}: id must be unique")
        identifiers.add(identifier)
        raw_said = item.get("said")
        if not isinstance(raw_said, list) or not raw_said or not all(isinstance(entry, dict) for entry in raw_said):
            raise ValueError(f"{where}: said must contain at least one bilingual entry")
        said = [dict(time=entry.get("time"), retold=bool(entry.get("retold")), text=text(entry, f"said[{n}]", where))
                for n, entry in enumerate(raw_said, 1)]
        replay = item.get("replay")
        if replay is not None:
            if not isinstance(replay, dict):
                raise ValueError(f"{where}: replay must be a mapping")
            raw_parts = replay.get("parts")
            if not isinstance(raw_parts, list) or not raw_parts or not all(isinstance(part, dict) for part in raw_parts):
                raise ValueError(f"{where}: replay.parts must contain at least one part")
            replay = dict(setup=field(replay, "setup", where, "replay.setup"), asked=field(replay, "asked", where, "replay.asked"),
                          parts=[dict(label=field(part, "label", where, f"replay.parts[{n}].label"),
                                      text=field(part, "text", where, f"replay.parts[{n}].text"))
                                 for n, part in enumerate(raw_parts, 1)],
                          note=field(replay, "note", where, "replay.note") if replay.get("note") is not None else None)
        pieces.append(dict(id=identifier, title=field(item, "title", where), when=field(item, "when", where), said=said,
                           then=field(item, "then", where), became=field(item, "became", where),
                           match=field(item, "match", where) if item.get("match") is not None else None, replay=replay))
    return dict(intro=field(data, "intro", "log"), origin=field(data, "origin", "log") if data.get("origin") is not None else None,
                pieces=pieces)


def _environment() -> Environment:
    return Environment(loader=DictLoader(dict(base=SHARE_BASE, figure=SHARE_FIGURE, index=SHARE_INDEX, day=SHARE_DAY, log=SHARE_LOG)),
                       autoescape=select_autoescape(default=True))


REVISION = {"del": "tw-rev-old", "ins": "tw-rev-new"}  # class names of the web_ui_spec kit (sheet.css)


def _mark(text: str, marks) -> Markup:
    """The text, escaped, with the replaced words struck through and the new words marked as inserted. The words are
    found in the raw text and each piece is escaped on its own, so a word never matches inside an entity (2026-11-13,
    fix3-judge: "39" struck through the apostrophe's &#39;)."""
    kinds = {was: "del" for was, _ in marks if was} | {now: "ins" for _, now in marks if now}
    if not kinds:
        return Markup(escape(text))
    pattern = re.compile("|".join(re.escape(word) for word in sorted(kinds, key=len, reverse=True)))
    parts, last = [], 0
    for found in pattern.finditer(text):
        tag = kinds[found.group(0)]
        parts.append(str(escape(text[last:found.start()])))
        parts.append(f'<{tag} class="{REVISION[tag]}">{escape(found.group(0))}</{tag}>')
        last = found.end()
    parts.append(str(escape(text[last:])))
    return Markup("".join(parts))


def _hit(marks, *texts) -> bool:
    """Whether a mark finds its words in one of these texts (the row then carries `tw-revised`)."""
    words = [word for pair in marks or () for word in pair if word]
    return any(word in text.zh or word in text.en for text in texts if text for word in words)


def _t(text: Text | None, marks=()) -> Markup:
    if text is None:
        return Markup("")
    english = (Markup('<span class="en">{}</span>').format(_mark(text.en, marks)) if text.translated else
               Markup('<span class="en" lang="zh-CN">{}</span>').format(_mark(text.en, marks)))
    return Markup('<span class="zh" lang="zh-CN">{}</span>').format(_mark(text.zh, marks)) + english


def _label(chinese: str, english: str) -> Markup:
    return Markup('<span class="zh" lang="zh-CN">{}</span><span class="en">{}</span>').format(escape(chinese), escape(english))


def render_pages(plan: dict, rules: Rules, *, english: dict[str, str] | None = None, now: datetime | None = None,
                 extras: dict | None = None, theme: str | None = None) -> tuple[dict[str, str], list[str]]:
    """Page name → HTML, and the rewritten Chinese texts that had no English. `extras`: `intro` and `route` of the home
    page (texts as in the plan: Chinese, with their English in `english`) and `changes` (what really changed, see
    `_changes`). `theme`: the look (`tripwright.site.theme`), "atlas" when not given."""
    words = Words(rules, english)
    view = _view(plan, words, extras)
    log = _log_view(extras["log"], words) if extras and "log" in extras else None
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    environment = _environment()
    untranslated = list(words.untranslated)
    scheme = theme or themes.DEFAULT
    common = dict(view, t=_t, label=_label, revised=_hit, style=themes.style(scheme), theme=scheme, script=SHARE_SCRIPT, untranslated=untranslated,
                  updated=clock.strftime("%Y-%m-%d %H:%M UTC"), log=log)
    pages = {"index.html": environment.get_template("index").render(**dict(common, title=view["trip_title"], current="index"))}
    days = view["days"]
    for index, day in enumerate(days):
        pages[f"{day['date']}.html"] = environment.get_template("day").render(
            **dict(common, title=Text(zh=day["long"].zh, en=f"{day['long'].en} · {view['trip_title'].en}"), day=day, current=day["date"],
                   previous=days[index - 1] if index else None, following=days[index + 1] if index + 1 < len(days) else None))
    if log:
        pages["log.html"] = environment.get_template("log").render(
            **dict(common, title=Text(zh="旅行实录", en=f"Trip log · {view['trip_title'].en}"), current="log"))
    pages["robots.txt"] = "User-agent: *\nDisallow: /\n"
    pages[".nojekyll"] = ""
    pages[MARKER] = "Generated by tripwright share; the whole folder is replaced on each build.\n"
    return pages, list(words.untranslated)


def scan_tree(folder: Path, policy: Policy) -> dict[str, list[str]]:
    """Category → files (relative paths) with private findings, for every file under `folder` except `.git/`."""
    findings: dict[str, list[str]] = {}
    for path in sorted(Path(folder).rglob("*")):
        relative = path.relative_to(folder).as_posix()
        if relative == ".git" or relative.startswith(".git/") or not path.is_file():
            continue
        text = relative + "\n" + path.read_bytes().decode("utf-8", errors="replace")
        for _, category in scan_text(text, policy):
            pages = findings.setdefault(category if category != POLICY_WORD else "private word", [])
            if relative not in pages:
                pages.append(relative)
    return findings


def _replace(stage: Path, output: Path):
    if output.exists():
        if not output.is_dir() or (any(output.iterdir()) and not (output / MARKER).is_file()):
            raise ValueError(f"{output} exists and is not a share page folder; refusing to replace it")
        old = Path(tempfile.mkdtemp(prefix=f".{output.name}.old-", dir=output.parent))
        old.rmdir()
        output.rename(old)
        stage.rename(output)
        shutil.rmtree(old, ignore_errors=True)
    else:
        stage.rename(output)


SWAPPED = ("person name", "lodging", "flight number")


def share_site(plan: dict, out_dir, *, people: list[dict] = (), english: dict[str, str] | None = None,
               privacy_policy: Policy | None = None, now: datetime | None = None, extras: dict | None = None,
               theme: str | None = None) -> dict:
    """Build the share pages into `out_dir`: written and scanned in a new folder beside it, swapped in only when clean.

    A person's name, a stay's name or a flight number that the scan finds anyway is swapped for its generic word in the
    page it was found on, and the build goes on; anything else it finds raises ShareBlocked (categories and pages, no
    values) and leaves `out_dir` as it was. Returns `{out, pages, untranslated, swapped, rules}`; `swapped` maps each
    swapped category to its pages."""
    if not plan or not plan.get("days"):
        raise ValueError("No committed plan with days to share")
    rules = Rules.of(plan, list(people), privacy_policy)
    pages, untranslated = render_pages(plan, rules, english=english, now=now, extras=extras, theme=theme)
    output = Path(out_dir).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.new-", dir=output.parent))
    stage.chmod(0o755)  # mkdtemp's 0700 would make the published folder private to this user
    swapped: dict[str, list[str]] = {}
    try:
        for name, text in pages.items():
            (stage / name).write_text(text, encoding="utf-8")
        findings = scan_tree(stage, rules.zh)
        if any(category in SWAPPED for category in findings):
            for category in SWAPPED:
                for name in findings.get(category, []):
                    (stage / name).write_text(swap_words((stage / name).read_text(encoding="utf-8"), rules.en), encoding="utf-8")
                    swapped.setdefault(category, []).append(name)
            findings = scan_tree(stage, rules.zh)
        if findings:
            raise ShareBlocked(findings)
        _replace(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return dict(out=output, pages=sorted(name for name in pages if name.endswith(".html")), untranslated=untranslated,
                swapped=swapped, rules=rules)


def load_trip(root, *, trip: str | None = None, rev: str | None = None) -> tuple[dict, list[dict]]:
    """The committed plan and the people records of a trip repository, read from Git without writing anything.

    `rev` defaults to the canonical branch (the remote branch when there is one): what counts as committed."""
    from tripwright.repo import index, layout
    from tripwright.repo.workspace import Repository

    repository = Repository(root)
    files = repository.committed(rev or repository.canonical())
    config = index.config_of(files)
    trip = trip or config.get("current_trip")
    if not trip:
        raise ValueError("The repository has no current trip")
    plan = layout.plan_from(index.trip_part(files, trip))
    if plan is None:
        raise ValueError(f"Trip {trip} has no committed plan yet")
    roster = f"{layout.PROFILE}/people.yaml"
    people = layout.parse(roster, files[roster]) if roster in files else []
    return plan, list(people or [])
