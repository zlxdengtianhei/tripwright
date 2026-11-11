from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import quote, unquote


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ORDER = (
    re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.I),
    re.compile(r"\bHM[A-Z0-9]{8,}\b"),
    re.compile(r"(?<![\d.,])\d{9,}(?![\d.,])"),
    re.compile(r"(?:订单号|订单编号|预订号|确认号|确认码|预订码|订座号|取票号|取票码|票号|护照号|Buchungsnummer|Reservierungsnummer|Bestellnummer|"
               r"Booking\s*(?:number|no\.?|reference|ref\.?|code|id)|(?:confirmation|reservation|order|ticket)\s*(?:number|no\.?|code|id)|PNR)"
               r"\s*[:：#]?\s*[A-Za-z0-9][A-Za-z0-9-]{4,}", re.I),
)
# A flight number: a two-character airline designator, then 1–4 digits (ZQ731, U2 1234, W6 2345). Transit lines and
# trains must pass: REX 7, RJX 19959 and VAB 1 have three letters; U6, S7 and 18 路 have no number after the designator;
# N25 and S45 have one digit after a letter-digit designator; times, decimals and counted units are not numbers here.
# Two-letter train categories and common abbreviations are not airlines on a trip page.
FLIGHT = re.compile(r"(?<![A-Za-z0-9])(?P<carrier>[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])(?P<space>\s?)(?P<number>[0-9]{1,4})[A-Z]?"
                    r"(?![A-Za-z0-9]|[:.,][0-9]|\s?(?:站|路|号|个|個|次|分|楼|层|人|天|晚|km|min|m\b|h\b|′|'|″|stops?\b|Min))")
NOT_AIRLINES = frozenset("IC EC EN NJ RJ RE RB IR ER EX RS RX SB SE SN SV TL CJ WB EG OG UG DG AM PM KW CO NR XL XS".split())
PHONE = (
    re.compile(r"(?<![\w+])(?:\+|00)[1-9][0-9]{0,2}(?:[ ./-]?\(?[0-9]{1,4}\)?){2,6}(?![0-9])"),
    re.compile(r"(?<![\w.,:/-])0[0-9]{1,4}(?:[ /-][0-9]{2,}){1,4}(?![0-9:.,])"),
    re.compile(r"(?<![0-9])1[3-9][0-9](?:[ -]?[0-9]{4}){2}(?![0-9])"),
)
POLICY_WORD = "private policy word"

# A stay is shown as its district and type, never as a point: the way in and out of it is written as for any other
# place (line, stops, times), without what pins it to one corner of the district: minutes of walking from the door
# ("步行 4 分钟", "walk 5′", "a 4 minute walk") and door wording ("门口", "楼下", "at the door").
WALK_MINUTES = re.compile(r"(?:步行|走路|走)\s*[0-9０-９]+\s*(?:分钟|分|[′'])|(?:walk(?:ed|s|ing)?|foot)\b[^。\n;；]{0,12}?\b[0-9]+\s*"
                          r"(?:分钟|分\b|\bmin\b|\bminutes?|[′'])|[0-9０-９]+\s*[-–~]?\s*[0-9０-９]*\s*(?:分钟|分\b|\bmin\b|\bminutes?|[′'])\s*"
                          r"(?:步行|walks?|on foot)", re.I)
AT_THE_DOOR = re.compile(r"门口|门前|楼下|\bdoors?\b|\bdoorstep\b|\bdoor step\b", re.I)
STAY_WORDS = re.compile(r"住处|住宿|\bour stay\b|\bour lodging\b|\bthe lodging\b", re.I)
CLAUSE_END = re.compile(r"(?<=[，,;；。])")
CONNECTORS = " +,，;；:：→-–"


def stay_text(text: str) -> str | None:
    """A text of a segment that starts or ends at a stay, without what pins the stay to one point: walking minutes are
    cut out of their clause, a clause with door wording or minutes next to the lodging's own words is dropped. The line,
    the stops and the times stay. None when nothing is left."""
    kept = []
    for clause in CLAUSE_END.split(text or ""):
        if AT_THE_DOOR.search(clause) or (STAY_WORDS.search(clause) and re.search(r"[0-9０-９]+\s*(?:分钟|分\b|\bmin\b|\bminutes?\b)", clause, re.I)):
            continue
        body, tail = re.match(r"(.*?)([，,;；。]?\s*)$", clause, re.S).groups()
        cleaned = WALK_MINUTES.sub("", body)
        if cleaned != body:
            cleaned = re.sub(r"(?:\s*\+\s*){2,}", " + ", cleaned).strip(CONNECTORS)
            cleaned = re.sub(r"^(?:to|towards)\s+", "", cleaned, flags=re.I)
            if len(re.findall(r"[A-Za-z0-9\u3400-\u9fff]", cleaned)) < 3:
                continue
        kept.append(cleaned + tail)
    result = re.sub(r"\s{2,}", " ", "".join(kept)).strip(CONNECTORS)
    return result or None


class PrivacyError(ValueError):
    pass


def flight_numbers(text: str) -> list[str]:
    """Flight numbers in a text, as written."""
    found = []
    for match in FLIGHT.finditer(text):
        carrier, number = match.group("carrier"), match.group("number")
        if carrier in NOT_AIRLINES:
            continue
        if not carrier.isalpha() and len(number) < 2 and not match.group("space"):
            continue  # N25, S45: a night bus or a train line, not a flight
        found.append(match.group(0))
    return found


def phone_numbers(text: str) -> list[str]:
    found = []
    for pattern in PHONE:
        for match in pattern.finditer(text):
            digits = sum(character.isdigit() for character in match.group(0))
            if digits >= (8 if match.group(0).startswith(("+", "00")) else 7):
                found.append(match.group(0))
    return found


def phone_digits(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return digits[2:] if number.lstrip().startswith("00") else digits


def word_pattern(word: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.I)


class Policy:
    """Private words: each line is `word` (denied) or `word => replacement` (replaced, then denied if still there).

    `add` takes the same with a category, which a scan reports instead of the word. `flight` replaces flight numbers.
    `public_phones` (digits) are a business's published numbers, which a scan lets through."""

    def __init__(self, lines: Iterable[str] = (), *, flight: str | None = None):
        self.replacements = []
        self.forbidden = []
        self.categories: dict[re.Pattern, str] = {}
        self.swaps: list[tuple[str, str]] = []
        self.flight = flight
        self.public_phones: set[str] = set()
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            word, separator, replacement = (part.strip() for part in line.partition("=>"))
            self.add(word, replacement if separator and replacement else None)

    def add(self, word: str, replacement: str | None = None, category: str = POLICY_WORD, *, deny: bool = True):
        pattern = word_pattern(word)
        if deny:
            self.forbidden.append(pattern)
            self.categories[pattern] = category
        if replacement:
            self.swaps.append((word, replacement))
            self.replacements.append((pattern, replacement))
            # longer words first, so a full name or address is replaced before a part of it
            self.replacements.sort(key=lambda pair: -len(pair[0].pattern))
        return pattern


def redact(text: str, policy: Policy) -> str:
    for pattern, replacement in policy.replacements:
        text = pattern.sub(lambda match: replacement, text)
    if policy.flight:
        for number in sorted(set(flight_numbers(text)), key=len, reverse=True):
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(number)}(?![A-Za-z0-9])", policy.flight, text)
    return text


def swap_words(text: str, policy: Policy) -> str:
    """Replace the policy's words wherever they are in a finished page, also as written in HTML or in a URL, and flight
    numbers as `policy.flight`. The words with a replacement only; a page is still scanned afterwards."""
    for word, replacement in sorted(policy.swaps, key=lambda pair: -len(pair[0])):
        for form in dict.fromkeys((word, html.escape(word), html.escape(word, quote=False), quote(word), quote(word, safe=""))):
            text = word_pattern(form).sub(lambda match: replacement, text)
    if policy.flight:
        for number in sorted(set(flight_numbers(text)), key=len, reverse=True):
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(number)}(?![A-Za-z0-9])", policy.flight, text)
    return text


def redact_values(value, policy: Policy):
    if type(value) is str:
        return redact(value, policy)
    if isinstance(value, dict):
        return {key: redact_values(item, policy) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_values(item, policy) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_values(item, policy) for item in value)
    return value


def scan_text(text: str, policy: Policy | None = None) -> list[tuple[int, str]]:
    """(line number, category) for each private finding; the found words themselves are never returned."""
    policy = policy or Policy()
    findings = []
    for number, line in enumerate(unquote(html.unescape(text)).splitlines(), 1):
        if EMAIL.search(line):
            findings.append((number, "email"))
        if any(pattern.search(line) for pattern in ORDER):
            findings.append((number, "order-like identifier"))
        if flight_numbers(line):
            findings.append((number, "flight number"))
        if any(phone_digits(found) not in policy.public_phones for found in phone_numbers(line)):
            findings.append((number, "phone number"))
        for category in dict.fromkeys(policy.categories.get(pattern, POLICY_WORD) for pattern in policy.forbidden if pattern.search(line)):
            findings.append((number, category))
    return findings


def publication_text(text: str, policy: Policy) -> str:
    findings = scan_text(text, policy)
    if findings:
        raise PrivacyError(f"Publication blocked: {len(findings)} privacy finding(s); sensitive values omitted")
    return text
