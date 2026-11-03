"""The page friends read: the committed plan as a static site with the private parts rewritten (D-83).

Only a home page and one page per day: when, where, what, how to get there, a map link per place. Decisions, memory,
messages, evidence, fares and bookings are never read into it. Rewrites, from the trip's own records:
- every person in `profile/people.yaml` and the plan's `people` becomes a code (Traveler A / 旅伴 A), the user too;
- a place to stay loses its name, address and coordinates: "Staying in <area> (hotel)";
- a flight number becomes "flight" / "航班".
The pages are written to a new folder beside the output and scanned there (`scan_tree`); only a clean folder replaces
the output, in one rename. A finding names its category and page, never the value.

English is the default language. Program words are bilingual; the user's own words (Chinese in the plan) are shown in
English from `english` (rewritten Chinese → English, supplied by the agent), else as they are, and listed in
`untranslated` so the agent can fill them in and build again.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from jinja2 import DictLoader, Environment, select_autoescape
from markupsafe import Markup, escape

from .assets import SCRIPT, STYLE
from .privacy import POLICY_WORD, Policy, PrivacyError, flight_numbers, phone_digits, phone_numbers, redact, scan_text
from .templates import SHARE_BASE, SHARE_DAY, SHARE_INDEX

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


def stay_area(place: dict) -> str:
    """The district a stay is in: its `district`, else the postcode and town at the end of its address, else its area."""
    if place.get("district"):
        return str(place["district"])
    for segment in reversed(str(place.get("name_local") or "").split(",")[1:]):
        if re.fullmatch(r"\d{4,5}\s+[^\d,]+", segment.strip()):
            return segment.strip()
    return str(place.get("area") or "")


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
            words = {str(place[key]) for key in ("name", "name_local") if place.get(key)}
            segments = [segment.strip() for segment in str(place.get("name_local") or "").split(",") if segment.strip()]
            # the name and the street; the district, town or country after them stay public
            words.update(segment for segment in segments[:2] if segment.casefold() != area and segment.casefold() not in public)
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
        self.english = {str(key).strip(): str(value) for key, value in (english or {}).items() if value}
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


def _date_words(day: str) -> tuple[Text, Text]:
    value = date.fromisoformat(day)
    weekday = WEEKDAYS[value.weekday()]
    short = Text(zh=f"{value.month}月{value.day}日", en=f"{MONTHS[value.month - 1]} {value.day}")
    long = Text(zh=f"{value.month}月{value.day}日 {weekday[0]}", en=f"{weekday[1]} {value.day} {MONTHS[value.month - 1]}")
    return short, long


def _view(plan: dict, words: Words) -> dict:
    rules = words.rules
    places = {place["id"]: place for place in plan.get("places", [])}

    def who(subject) -> Text:
        codes = [rules.codes.get(identifier, ("旅伴", "a traveler")) for identifier in subject or []]
        return words.pair("、".join(code[0] for code in codes), ", ".join(code[1] for code in codes))

    def place_view(identifier) -> dict | None:
        place = places.get(identifier)
        if place is None:
            return None
        if is_stay(place):
            kind = STAY_KINDS[str(place.get("kind")).strip().casefold()]
            area = stay_area(place)
            return dict(stay=words.pair(f"住在 {area}（{kind[0]}）", f"Staying in {area} ({kind[1]})"))
        local = redact(str(place.get("name_local") or place.get("name") or ""), rules.en)
        name = words(place.get("name"))
        if name is not None and name.zh == local:
            name = None
        return dict(stay=None, local=local, name=name,
                    map_url="https://www.google.com/maps/search/?api=1&query=" + quote(local))

    def block_view(block: dict) -> dict:
        place = place_view(block.get("place"))
        kind = KINDS.get(block.get("kind"), (str(block.get("kind") or ""), str(block.get("kind") or "")))
        if place and place["stay"]:
            title = place["stay"]
        elif place:
            title = place["name"] or words(place["local"])
        else:
            title = words(block.get("text")) or words.pair(*kind)
        how = [words(block.get("mode"))] if block.get("mode") else []
        if block.get("line") and not (block.get("mode") and str(block["line"]) in str(block["mode"])):
            how.insert(0, words(block["line"]))
        if block.get("walk_minutes") and not block.get("mode"):
            how.append(words.pair(f"步行 {block['walk_minutes']} 分钟", f"{block['walk_minutes']} min walk"))
        how = [item for item in how if item]
        return dict(start=block.get("start"), end=block.get("end"), kind=words.pair(*kind), title=title,
                    text=words(block.get("text")) if place else None,
                    how=words.pair(" · ".join(item.zh for item in how), " · ".join(item.en for item in how)) if how else None,
                    note=words(block.get("note")), place=place,
                    options=[view for view in map(place_view, block.get("options") or []) if view])

    days = []
    for raw in sorted(plan.get("days", []), key=lambda day: day["date"]):
        short, long = _date_words(raw["date"])
        base = places.get(raw.get("base"))
        stay = None
        if base is not None:
            stay = place_view(base["id"])["stay"] if is_stay(base) else words.pair(
                f"过夜：{redact(str(base.get('name') or base.get('name_local')), rules.zh)}",
                f"Overnight: {redact(str(base.get('name_local') or base.get('name')), rules.en)}")
        days.append(dict(
            date=raw["date"], short=short, long=long, title=words(raw.get("title")) or long, area=words(raw.get("area")),
            sunset=raw.get("sunset"), stay=stay, notes=words(raw.get("notes")), rain=words(raw.get("rain_plan")),
            anchors=[dict(time=anchor.get("time"), text=words(anchor.get("text"))) for anchor in raw.get("anchors", [])],
            plans=[dict(name=words(item.get("name")), tracks=[dict(who=who(track.get("subject")),
                                                                    blocks=[block_view(block) for block in track.get("blocks", [])])
                                                               for track in item.get("tracks", [])])
                   for item in raw.get("plans", [])],
            candidates=[dict(name=words(slot), places=[view for view in map(place_view, identifiers) if view])
                        for slot, identifiers in (raw.get("candidates") or {}).items()]))
    people = sorted(rules.codes.items(), key=lambda pair: pair[1])
    trip_people = [person.get("id") or person.get("name") for person in plan.get("people", [])]
    travelers = who([identifier for identifier, _ in people if identifier in trip_people])
    return dict(trip_title=words(plan.get("title")) or words.pair("行程", "Trip"), days=days,
                span=words.pair(f"{plan.get('start')} → {plan.get('end')}", f"{plan.get('start')} → {plan.get('end')}"),
                areas=[words(area) for area in plan.get("areas", []) if area],
                travelers=travelers if travelers.en else None)


def _environment() -> Environment:
    return Environment(loader=DictLoader(dict(base=SHARE_BASE, index=SHARE_INDEX, day=SHARE_DAY)),
                       autoescape=select_autoescape(default=True))


def _t(text: Text | None) -> Markup:
    if text is None:
        return Markup("")
    english = (Markup('<span class="en">{}</span>').format(text.en) if text.translated else
               Markup('<span class="en" lang="zh-CN">{} <span class="orig">(中文)</span></span>').format(text.en))
    return Markup('<span class="zh" lang="zh-CN">{}</span>').format(text.zh) + english


def _label(chinese: str, english: str) -> Markup:
    return Markup('<span class="zh" lang="zh-CN">{}</span><span class="en">{}</span>').format(escape(chinese), escape(english))


def render_pages(plan: dict, rules: Rules, *, english: dict[str, str] | None = None, now: datetime | None = None) -> tuple[dict[str, str], list[str]]:
    """Page name → HTML, and the rewritten Chinese texts that had no English."""
    words = Words(rules, english)
    view = _view(plan, words)
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    environment = _environment()
    common = dict(view, t=_t, label=_label, style=STYLE, script=SCRIPT, updated=clock.strftime("%Y-%m-%d %H:%M UTC"))
    pages = {"index.html": environment.get_template("index").render(**dict(common, title=view["trip_title"]))}
    days = view["days"]
    for index, day in enumerate(days):
        pages[f"{day['date']}.html"] = environment.get_template("day").render(
            **dict(common, title=Text(zh=day["long"].zh, en=f"{day['long'].en} · {view['trip_title'].en}"), day=day,
                   previous=days[index - 1] if index else None, following=days[index + 1] if index + 1 < len(days) else None))
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


def share_site(plan: dict, out_dir, *, people: list[dict] = (), english: dict[str, str] | None = None,
               privacy_policy: Policy | None = None, now: datetime | None = None) -> dict:
    """Build the share pages into `out_dir`: written and scanned in a new folder beside it, swapped in only when clean.

    Raises ShareBlocked (categories and pages, no values) and leaves `out_dir` as it was. Returns
    `{out, pages, untranslated, rules}`."""
    if not plan or not plan.get("days"):
        raise ValueError("No committed plan with days to share")
    rules = Rules.of(plan, list(people), privacy_policy)
    pages, untranslated = render_pages(plan, rules, english=english, now=now)
    output = Path(out_dir).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.new-", dir=output.parent))
    stage.chmod(0o755)  # mkdtemp's 0700 would make the published folder private to this user
    try:
        for name, text in pages.items():
            (stage / name).write_text(text, encoding="utf-8")
        findings = scan_tree(stage, rules.zh)
        if findings:
            raise ShareBlocked(findings)
        _replace(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return dict(out=output, pages=sorted(name for name in pages if name.endswith(".html")), untranslated=untranslated, rules=rules)


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
