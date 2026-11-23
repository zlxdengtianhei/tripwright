"""Find what the user's repository already holds for one question (Q19, 2026-11-23: the user's Git repository is
the database; the core is finding the right entries in it).

`search` ranks every stored thing a later answer may need: the plan's places (their hours, prices and notes are the
guide), the plan's days (anchors, blocks, notes), bookings, the ledger (facts, items, decisions, open checks), the web
checks and earlier lookups the service recorded, and the user's own messages that no ledger record was taken from.
Each hit says where it comes from and when (the user's words and when said; the page and when read; the provider
and when fetched, and whether that has expired), so the answer can name its source.

Matching is lexical and has no model: words in Latin script (case and accents folded), numbers and clock times, and
character pairs of Chinese, Japanese or Korean text; a small table of travel words joins the user's language to the
local one (机场 = Flughafen = airport); a place of the plan named by any of its names brings all its names. A short
question (up to six informative words) keeps the entries that cover at least half of it, as the old all-words match
did for one or two words; a long one (a whole message) keeps the best entries by weight. Entries no longer in force
(superseded, withdrawn, expired) come after the ones in force and say so.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timedelta
from functools import cache
from importlib.resources import files
from zoneinfo import ZoneInfo

import yaml

SHORT = 6  # informative words up to which a question is short (coverage rule) rather than long (best weight)
COVERAGE = 0.5
LONG_SHARE = 0.2
CHECKED_BOOST = 1.3
KIND_CAP = 3  # a turn's recall: entries of one kind before the others get a place
TEXT_LIMIT = 240
QUOTE_LIMIT = 60  # a record's quote counts as words of the record only when short; a long one is the whole message
LATIN = re.compile(r"\d{1,2}:\d{2}|\d{1,2}-\d{1,2}|[a-z0-9]+")
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
LIVE = ("active", "open", "review", "pending", None)
LOOKUP_KINDS = ("transit", "latest_departure", "weather")
WORDS = {
    "zh": dict(said="{who}原话 {when}", page="{host} 页面，{when} 读", snippet="{host} 搜索摘要，{when} 读（不算核过）",
               lookup="{source}，{when} 查", expired="已过期，要重查", not_in_force="已不作数", plan="计划里存的",
               booking="预订里存的", checked="（{checked}）", user="你", someone="", unknown="时刻不明"),
    "en": dict(said="{who}words, {when}", page="{host}, read {when}", snippet="{host} search snippet, read {when} (not a check)",
               lookup="{source}, fetched {when}", expired="expired, look again", not_in_force="no longer in force",
               plan="stored in the plan", booking="stored in the bookings", checked=" ({checked})", user="your ", someone="",
               unknown="time unknown"),
}


@cache
def table() -> dict:
    return yaml.safe_load(files("tripwright").joinpath("recall.yaml").read_text(encoding="utf-8"))


def fold(text: str) -> str:
    """Lower case without accents (ö → o, ß → ss), the full-width forms made plain."""
    text = unicodedata.normalize("NFKC", str(text)).casefold().replace("ß", "ss")
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def tokens(text: str) -> set[str]:
    folded = fold(text)
    stop = set(table()["stop"])
    found = {word for word in LATIN.findall(folded) if word not in stop and (len(word) > 2 or word.isdigit() or ":" in word or "-" in word)}
    for run in CJK.findall(folded):
        pairs = {run[i:i + 2] for i in range(len(run) - 1)} if len(run) > 1 else {run}
        found |= {pair for pair in pairs if pair not in stop}
    return found


def groups_in(text: str) -> set[str]:
    """The travel-word groups (recall.yaml) a text names, as tokens `§<group>`."""
    folded = fold(text)
    words = set(LATIN.findall(folded))
    hits = set()
    for name, forms in table()["groups"].items():
        for form in forms:
            form = fold(form)
            if (CJK.search(form) and form in folded) or (not CJK.search(form) and (form in words or " " in form and form in folded)):
                hits.add(f"§{name}")
                break
    return hits


def without_group_words(text: str) -> str:
    """The text with the Chinese forms of the travel-word groups taken out: in a question they count once, as their
    group, not again as character pairs (车站 行李寄存 is two things, station and luggage, not five pairs); the words
    for today and tomorrow count as the day they name."""
    folded = fold(text)
    forms = sorted({fold(form) for forms in [*table()["groups"].values(), table()["today"]["zh"], table()["tomorrow"]["zh"]]
                    for form in forms if CJK.search(form)}, key=len, reverse=True)
    for form in forms:
        folded = folded.replace(form, " ")
    return folded


def place_names(place: dict) -> list[str]:
    names = [place.get("name"), (place.get("name_local") or "").split(",")[0], *(place.get("aliases") or [])]
    return [name for name in names if name and len(fold(name)) >= 2]


def places_in(text: str, plan: dict | None) -> set[str]:
    """The plan's places a text names (by any of its names or its ID), as tokens `§place:<id>`."""
    folded = fold(text)
    words = set(LATIN.findall(folded)) - set(table()["stop"]) - set(table()["generic_names"])
    hits = set()
    for place in (plan or {}).get("places", []):
        if place.get("id") and fold(place["id"]) in folded:
            hits.add(f"§place:{place['id']}")
            continue
        for name in place_names(place):
            name = fold(name)
            head = name.split("(")[0].strip()
            if head and len(head) >= 3 and head in folded:
                hits.add(f"§place:{place['id']}")
                break
            # a name said or transcribed loosely ("Block Factory" for Blockfabrik): a word of five letters or more
            # that begins one of the name's words
            if any(len(word) >= 5 and any(part.startswith(word) or word.startswith(part) and len(part) >= 5
                                          for part in LATIN.findall(head)) for word in words):
                hits.add(f"§place:{place['id']}")
                break
    return hits


DATE = re.compile(r"(?:20\d\d-)?(\d{2})-(\d{2})(?!\d)")


def dates_in(text: str, at: datetime | None, zone: str | None) -> set[str]:
    """Days a text names, as tokens `§day:MM-DD`: written dates (2026-10-31, 10-31), and for a question also the
    words for today and tomorrow (今天、今晚、现在、明天、明早; today, tonight, now, tomorrow) read at `at`."""
    found = {f"§day:{month}-{day}" for month, day in DATE.findall(str(text))}
    if at is not None:
        here = at.astimezone(ZoneInfo(zone)) if zone else at
        folded = fold(text)
        words = set(LATIN.findall(folded))
        if any(word in folded for word in table()["today"]["zh"]) or words & set(table()["today"]["en"]):
            found.add(f"§day:{here:%m-%d}")
        if any(word in folded for word in table()["tomorrow"]["zh"]) or words & set(table()["tomorrow"]["en"]):
            found.add(f"§day:{here + timedelta(days=1):%m-%d}")
    return found


def short(text: str, terms: set[str] | None = None, limit: int = TEXT_LIMIT) -> str:
    """The entry's text in at most `limit` characters, around the first matched term when it is long."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    folded = fold(text)
    starts = [folded.find(term) for term in terms or () if not term.startswith("§") and folded.find(term) > 0]
    start = max(0, min(starts) - 60) if starts else 0
    return ("…" if start else "") + text[start:start + limit - 1] + "…"


def local(at: str | None, zone: str | None) -> str | None:
    if not at:
        return None
    try:
        moment = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return str(at)
    if moment.tzinfo is not None and zone:
        moment = moment.astimezone(ZoneInfo(zone))
    return moment.strftime("%m-%d %H:%M")


def said_time(store, owner: str, event_id: str | None) -> str | None:
    """When the user said it: the message's native time (after '@'), else when it was received."""
    if not event_id:
        return None
    try:
        event = store.get("events", owner, event_id)
    except Exception:
        return None
    ref = (event.get("receipt") or {}).get("native_ref") or ""
    return ref.partition("@")[2] or (event.get("receipt") or {}).get("received_at")


def corpus(store, owner: str, plan: dict | None, *, at: datetime, zone: str | None, language: str) -> list[dict]:
    """Every entry the search can return: {found, id, text, source, in_force, haystack, value?}."""
    w = WORDS["en" if language == "en" else "zh"]
    entries = []

    def add(found: str, record_id: str, text: str, source: str, haystack: str, *, in_force: bool = True, **extra):
        entries.append(dict(found=found, id=record_id, text=text, source=source, in_force=in_force,
                            haystack=" ".join([record_id, haystack]), **extra))

    for place in (plan or {}).get("places", []):
        detail = " ".join(str(place.get(key) or "") for key in ("kind", "area", "hours", "notes", "reason", "price"))
        add("place", place["id"], f"{place.get('name') or place.get('name_local')}：{detail}",
            w["plan"] + (w["checked"].format(checked=place["checked"]) if place.get("checked") else ""),
            " ".join(place_names(place)) + " " + detail, value=place)
    for day in (plan or {}).get("days", []):
        for anchor in day.get("anchors") or []:
            text = " ".join(str(anchor.get(key) or "") for key in ("time", "text", "alternative", "consequence", "app"))
            add("plan", f"{day['date']}/{anchor['id']}", f"{day['date']} {text}", w["plan"], f"{day['date']} {text} {anchor.get('place') or ''} {anchor.get('destination') or ''}")
        for option in day.get("plans") or []:
            for track in option.get("tracks") or []:
                for block in track.get("blocks") or []:
                    text = f"{day['date']} {block.get('start') or ''}–{block.get('end') or ''} {block.get('text') or ''} {block.get('note') or ''}"
                    add("plan", f"{day['date']}/{block['id']}", text, w["plan"], f"{text} {block.get('place') or ''} {block.get('kind') or ''}")
        if day.get("notes"):
            add("plan", f"{day['date']}/notes", f"{day['date']} {day['notes']}", w["plan"], f"{day['date']} {day['notes']}")
    for booking in (plan or {}).get("bookings", []):
        text = f"{booking.get('date') or ''} {booking.get('what') or ''} {' '.join(booking.get('where') or [])}"
        add("booking", booking["id"], f"{text}（{booking.get('status')}）", w["booking"], f"{text} {booking.get('kind') or ''} {booking.get('place') or ''}")

    taken = set()
    for table_name, found in (("facts", "fact"), ("items", "item"), ("decisions", "decision"), ("pending_checks", "check")):
        for record in store.all(table_name, owner):
            source = record.get("source") or {}
            taken.add(source.get("event"))
            when = local(said_time(store, owner, source.get("event")), zone) or w["unknown"]
            text = record.get("text") or record.get("question") or record.get("title") or record.get("what") or ""
            in_force = record.get("status") in LIVE
            add(found, record["id"], text, w["said"].format(who=w["user"] if source.get("basis") in ("user_explicit", None) else w["someone"], when=when)
                + ("" if in_force else f"；{w['not_in_force']}"),
                " ".join(str(record.get(key) or "") for key in ("key", "text", "question", "title", "what"))
                + (f" {record['quote']}" if record.get("quote") and len(record["quote"]) <= QUOTE_LIMIT else ""),
                in_force=in_force, status=record.get("status"), event=source.get("event"), **({"key": record["key"]} if record.get("key") else {}))
    for record in store.all("evidence", owner):
        expired = bool(record.get("stale")) or bool(record.get("valid_until")) and aware_utc(record["valid_until"]) <= at
        if record.get("kind") == "web":
            host = record.get("source") or ""
            source = (w["snippet"] if record.get("read_from") == "search_snippet" else w["page"]).format(host=host, when=local(record.get("fetched_at"), zone))
            add("web", record["id"], record.get("claim") or "", source + (f"；{w['expired']}" if expired else ""),
                " ".join(str(record.get(key) or "") for key in ("claim", "quote", "url", "type")), in_force=not expired,
                url=record.get("url"), quote=record.get("quote"), counts_as_checked=record.get("counts_as_checked", False),
                fetched_at=record.get("fetched_at"), valid_until=record.get("valid_until"), expired=expired)
        elif record.get("kind") in LOOKUP_KINDS:
            told = record.get("sentences") or []
            question = record.get("question") or {}
            text = "；".join(told[:3]) if told else str(question)
            add("lookup", record["id"], text, w["lookup"].format(source=record.get("source"), when=local(record.get("fetched_at"), zone))
                + (f"；{w['expired']}" if expired else ""), f"{record['kind']} {record.get('date') or ''} {question} {' '.join(told)}",
                in_force=not expired, kind=record["kind"], date=record.get("date"), fetched_at=record.get("fetched_at"),
                valid_until=record.get("valid_until"), expired=expired,
                synthetic=bool(record.get("synthetic")) or str(record.get("source", "")).startswith("demo:"))
    for event in store.all("events", owner):
        receipt, received = event.get("receipt") or {}, event.get("received") or {}
        if event["id"] in taken or receipt.get("nature") not in ("user_verbatim", "screenshot_extraction", "voice_transcript", "paste"):
            continue
        text = received.get("text") or receipt.get("received_text") or ""
        when = local(said_time(store, owner, event["id"]), zone) or w["unknown"]
        add("said", event["id"], text, w["said"].format(who=w["user"], when=when), text, event=event["id"])
    return entries


def aware_utc(value: str) -> datetime:
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo("UTC"))


def search(store, owner: str, plan: dict | None, text: str, *, at: datetime, zone: str | None = None, language: str = "zh",
           limit: int = 8, kinds: tuple[str, ...] | None = None, exclude_events: set[str] | None = None,
           kind_cap: int | None = None) -> dict:
    """The entries that best answer `text`, best first: {found: [...], terms, rule}. Empty text: nothing (say what to
    look for). `exclude_events`: messages whose own records are left out (a turn's own message: what it recorded is
    not something the repository already held)."""
    entries = [entry for entry in corpus(store, owner, plan, at=at, zone=zone, language=language)
               if (not kinds or entry["found"] in kinds) and not (exclude_events and entry.get("event") in exclude_events)]
    query = tokens(without_group_words(text)) | groups_in(text) | places_in(text, plan) | dates_in(text, at, zone)
    if not query:
        return dict(found=[], terms=[], rule="empty")
    for entry in entries:
        entry["terms"] = (tokens(entry["haystack"]) | groups_in(entry["haystack"]) | places_in(entry["haystack"], plan)
                          | dates_in(entry["haystack"], None, zone) | entry.get("days", set()))
        if entry["found"] == "place":
            entry["terms"].add(f"§place:{entry['id']}")
    count = len(entries) or 1
    frequency = {term: sum(term in entry["terms"] for entry in entries) for term in query}
    weight = {term: math.log(1 + count / (1 + frequency[term])) * (1.5 if term.startswith("§place:") else 1.0) for term in query}
    total = sum(weight.values()) or 1.0
    informative = [term for term in query if not term.startswith("§day:")]
    rule = "short" if len(informative) <= SHORT else "long"
    scored = []
    for entry in entries:
        matched = query & entry["terms"]
        if not matched:
            continue
        score = sum(weight[term] for term in matched)
        # coverage counts words, not their weights: "half of what was asked" (a rare word must not outweigh the rest)
        if rule == "short" and len([term for term in matched if not term.startswith("§day:")]) / max(1, len(informative)) < COVERAGE:
            continue
        if rule == "long" and len(matched) < 2 and not any(term.startswith("§") and not term.startswith("§day:") for term in matched):
            continue
        # what was checked (a web page, a lookup) and is still valid answers before what the plan restates
        boost = CHECKED_BOOST if entry["found"] in ("web", "lookup") and entry["in_force"] else 1.0
        scored.append((score * boost * (1.0 if entry["in_force"] else 0.5), entry, matched))
    scored.sort(key=lambda row: (-row[0], row[1]["found"], row[1]["id"]))
    if rule == "long" and scored:
        best = scored[0][0]
        scored = [row for row in scored if row[0] >= LONG_SHARE * best]
    # a date alone never makes a hit: an entry must share something else with the question
    scored = [row for row in scored if any(not term.startswith("§day:") for term in row[2])]
    # `kind_cap`: no more than that many entries of one kind while others wait (a turn's short list: six places
    # must not hide the one web check); a search the host asked for keeps the plain ranking
    kept, count = [], {}
    for row in scored:
        if count.get(row[1]["found"], 0) < (kind_cap or limit):
            kept.append(row)
            count[row[1]["found"]] = count.get(row[1]["found"], 0) + 1
    kept += [row for row in scored if row not in kept]
    found = []
    for score, entry, matched in kept[:limit]:
        shown = {key: value for key, value in entry.items() if key not in ("haystack", "terms")}
        shown["text"] = short(entry["text"], matched)
        shown["why"] = sorted(term.removeprefix("§") for term in matched)[:6]
        shown["score"] = round(score, 2)
        found.append(shown)
    return dict(found=found, terms=sorted(term.removeprefix("§") for term in query)[:24], rule=rule)


def lines(result: dict, language: str = "zh", limit: int = 3, kinds: tuple[str, ...] | None = None) -> list[dict]:
    """The best hits in force as opening-material lines, each with its source; only `kinds` when given."""
    out = []
    for entry in result.get("found", []):
        if kinds and entry["found"] not in kinds or not entry.get("in_force", True):
            continue
        # like every opening line, a stored entry is archived and not checked now (whatever check it once had)
        lead = "仓里存的" if language != "en" else "In the repository"
        out.append(dict(kind="recall", found=entry["found"], id=entry["id"], text=f"{lead}：{entry['text']}（{entry['source']}；存档，没核）"
                        if language != "en" else f"{lead}: {entry['text']} ({entry['source']}; archived, not checked)"))
        if len(out) >= limit:
            break
    return out
