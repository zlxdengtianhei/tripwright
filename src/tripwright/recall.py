"""Find what the user's repository already holds for one question (Q19, 2026-11-23: the user's Git repository is
the database; the core is finding the right entries in it).

`search` ranks every stored thing a later answer may need: the plan's places (their hours, prices and notes are the
guide), the plan's days (anchors, blocks, notes), bookings, the ledger (facts, items, decisions, open checks), the web
checks and earlier lookups the service recorded, and the user's own messages that no ledger record was taken from.
Each hit says where it comes from and when (the user's words and when said; the page and when read; the provider
and when fetched, and whether that has expired), so the answer can name its source.

Matching is lexical and has no model: words in Latin script (case and accents folded), numbers and clock times, and
the words Chinese, Japanese and Korean text cuts into (segments.py: a word table, not a model, not character pairs —
a long Chinese message used to make hundreds of pairs, most meaningless, so entries matched by accident); a table of
travel words joins the user's language to the local one (机场 = Flughafen = airport); a table of general place names
joins the two spellings of a far-away city or country (慕尼黑 = Munich = München, no name of any one trip); a place of
the plan named by any of its names brings all its names. A short question (up to six informative words) keeps the
entries that cover at least half of it, or that share a travel word or a general place name with it; a long one (a
whole message) keeps the best entries by weight, at least a fifth of the best score. Entries no longer in force
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

from tripwright import segments

SHORT = 6  # informative words up to which a question is short (coverage rule) rather than long (best weight)
COVERAGE = 0.5
LONG_SHARE = 0.2
CHECKED_BOOST = 1.3
PLACE_SCALE = 1.5  # naming a place of the plan counts more than a common word
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


def scale(term: str) -> float:
    """How much a matched term counts: a date alone never scores (it narrows, it does not rank); a place of the plan
    counts more than a common word the entry and the question happen to share."""
    if term.startswith("§day:"):
        return 0.0
    if term.startswith("§place:"):
        return PLACE_SCALE
    return 1.0


def fold(text: str) -> str:
    """Lower case without accents (ö → o, ß → ss), the full-width forms made plain."""
    text = unicodedata.normalize("NFKC", str(text)).casefold().replace("ß", "ss")
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def tokens(text: str) -> set[str]:
    """The words and numbers a text is searched by: Latin words over two letters (a place name or a topic word), and
    the words the CJK text cuts into (segments.py). A one-character CJK word is dropped like a two-letter Latin one:
    too common to tell entries apart. Character pairs are not used: a long Chinese message made hundreds of them and
    most meant nothing, so an entry could match by accident and a real word could not be seen."""
    folded = fold(text)
    stop = set(table()["stop"])
    found = {word for word in LATIN.findall(folded) if word not in stop and (len(word) > 2 or word.isdigit() or ":" in word or "-" in word)}
    for run in CJK.findall(folded):
        found |= {word for word in segments.words(run) if len(word) > 1 and word not in stop}
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


@cache
def aliases() -> dict:
    """The general place names the generator wrote from GeoNames (`place_aliases.yaml`): name → its spellings, the
    same shape as `names` in recall.yaml. A missing file is an empty table (an older package still runs)."""
    try:
        loaded = yaml.safe_load(files("tripwright").joinpath("place_aliases.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return loaded.get("places") or {}


@cache
def name_forms() -> dict:
    """Every general place name, ready to look up: Chinese forms by their folded text, Latin forms as whole words,
    multi-word Latin forms as strings. recall.yaml's hand-written `names` and the generated alias table are merged;
    the hand-written spelling wins when both hold the same form."""
    merged: dict[str, list[str]] = {}
    for source in (aliases(), table().get("names") or {}):
        for key, forms in source.items():
            merged.setdefault(key, [])
            merged[key] = list(forms or []) + merged[key]
    cjk, latin, spaced = {}, {}, []
    for key in sorted(merged):
        for form in merged[key]:
            folded = fold(form)
            if not folded:
                continue
            if CJK.search(folded):
                cjk.setdefault(folded, key)
            elif " " in folded:
                if not any(folded == other for other, _ in spaced):
                    spaced.append((folded, key))
            else:
                latin.setdefault(folded, key)
    return dict(cjk=cjk, latin=latin, spaced=spaced, longest=max((len(form) for form in cjk), default=0))


def names_in(text: str) -> set[str]:
    """The general place names (recall.yaml `names` and the generated alias table) a text names, in any of its
    languages, as tokens `§name:<key>`: 慕尼黑 and Munich are the same hit, so an entry that says one answers a
    question that says the other."""
    folded = fold(text)
    forms = name_forms()
    hits = set()
    for word in set(LATIN.findall(folded)):
        key = forms["latin"].get(word)
        if key:
            hits.add(f"§name:{key}")
    for form, key in forms["spaced"]:
        if form in folded:
            hits.add(f"§name:{key}")
    for run in CJK.findall(folded):
        for start in range(len(run)):
            for length in range(min(forms["longest"], len(run) - start), 1, -1):
                key = forms["cjk"].get(run[start:start + length])
                if key:
                    hits.add(f"§name:{key}")
                    break
    return hits


@cache
def cjk_table_forms() -> tuple[frozenset, int]:
    """The Chinese forms of the groups, the general place names and the words for today and tomorrow, longest first."""
    forms = {fold(form) for group in [*table()["groups"].values(), *((table().get("names") or {}).values()),
                                     *(aliases().values()), table()["today"]["zh"], table()["tomorrow"]["zh"]]
             for form in group if CJK.search(fold(form))}
    return frozenset(forms), max((len(form) for form in forms), default=0)


def without_table_words(text: str) -> str:
    """The text with the Chinese forms of the tables taken out: in a question they count once, as their group or name,
    not again as words (车站 行李寄存 is two things, station and luggage, not five); the words for today and tomorrow
    count as the day they name. Longest form first, so the longer name wins over a piece of it."""
    folded = fold(text)
    forms, longest = cjk_table_forms()
    out, position = [], 0
    while position < len(folded):
        taken = 0
        if CJK.match(folded[position]):
            for length in range(min(longest, len(folded) - position), 1, -1):
                if folded[position:position + length] in forms:
                    taken = length
                    break
        out.append(" " * taken if taken else folded[position])
        position += taken or 1
    return "".join(out)


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


def query_terms(text: str, plan: dict | None, *, at: datetime, zone: str | None) -> set[str]:
    """What a text is searched by: its words, travel-word groups, general place names, the plan's places and the days
    it names."""
    return (tokens(without_table_words(text)) | groups_in(text) | names_in(text) | places_in(text, plan)
            | dates_in(text, at, zone))


def entry_terms(entry: dict, plan: dict | None, *, zone: str | None = None) -> set[str]:
    """What one entry is searched by: the same words over its `haystack`, its days, and its own place."""
    terms = (tokens(entry["haystack"]) | groups_in(entry["haystack"]) | names_in(entry["haystack"])
             | places_in(entry["haystack"], plan) | dates_in(entry["haystack"], None, zone)
             | entry.get("days", set()))
    if entry["found"] == "place":
        terms.add(f"§place:{entry['id']}")
    return terms


def search(store, owner: str, plan: dict | None, text: str, *, at: datetime, zone: str | None = None, language: str = "zh",
           limit: int = 8, kinds: tuple[str, ...] | None = None, exclude_events: set[str] | None = None,
           kind_cap: int | None = None, repository=None) -> dict:
    """The entries that best answer `text`, best first: {found: [...], terms, rule}. Empty text: nothing (say what to
    look for). `exclude_events`: messages whose own records are left out (a turn's own message: what it recorded is
    not something the repository already held).

    A short question (up to SHORT informative terms) keeps entries covering half of it, or naming one of its travel
    words or general place names; a long one (a whole message) keeps the best by weight, at least LONG_SHARE of the
    best. A date in the question never scores by itself: it narrows, it does not rank.

    `repository`: the trip repository, when there is one. The words of every entry are then read from the rebuildable
    FTS5 index beside it (`recall_index.py`) instead of being cut again on every query, and each hit carries the file
    it is stored in, the line and the commit."""
    if repository is not None:
        from tripwright import recall_index

        return recall_index.search(repository, store, owner, plan, text, at=at, zone=zone, language=language,
                                   limit=limit, kinds=kinds, exclude_events=exclude_events, kind_cap=kind_cap)
    entries = [entry for entry in corpus(store, owner, plan, at=at, zone=zone, language=language)
               if (not kinds or entry["found"] in kinds) and not (exclude_events and entry.get("event") in exclude_events)]
    query = query_terms(text, plan, at=at, zone=zone)
    if not query:
        return dict(found=[], terms=[], rule="empty")
    for entry in entries:
        entry["terms"] = entry_terms(entry, plan, zone=zone)
    return rank(entries, query, plan=plan, at=at, zone=zone, language=language, limit=limit, kind_cap=kind_cap)


def rank(entries: list[dict], query: set[str], *, plan: dict | None, at: datetime, zone: str | None, language: str = "zh",
         limit: int = 8, kind_cap: int | None = None, total: int | None = None, frequency: dict[str, int] | None = None,
         widen: bool = False) -> dict:
    """The scoring part of `search`, over entries already chosen (all of them, or the index's candidates). `total` and
    `frequency` come from the whole corpus when the entries given are only the ones the index matched; `widen` also
    counts a query word the entry spells but did not cut into (the trigram fallback)."""
    if not query:
        return dict(found=[], terms=[], rule="empty")
    count = (total if total is not None else len(entries)) or 1
    if frequency is None:
        frequency = {term: sum(term in entry["terms"] for entry in entries) for term in query}
    weight = {term: math.log(1 + count / (1 + frequency[term])) * scale(term) for term in query}
    folded_haystacks = {entry["id"]: fold(entry["haystack"]) for entry in entries} if widen else {}
    informative = [term for term in query if not term.startswith("§day:")]
    rule = "short" if len(informative) <= SHORT else "long"
    scored = []
    for entry in entries:
        matched = query & entry["terms"]
        if widen and entry["id"] in folded_haystacks:
            matched |= {term for term in query - matched
                        if not term.startswith("§") and fold(term) in folded_haystacks[entry["id"]]}
        if not matched:
            continue
        score = sum(weight[term] for term in matched)
        # coverage counts words, not their weights: "half of what was asked" (a rare word must not outweigh the rest).
        # A travel-word topic the entry shares with the question is itself a match (民宿 车票: the place the question
        # names is spelled differently in the repository, or is not stored at all); a place name or a plan place alone
        # is not enough (the plan's day blocks carry the plan's place IDs).
        topics = [term for term in matched if term.startswith("§") and not term.startswith(("§day:", "§name:", "§place:"))]
        if rule == "short" and len([term for term in matched if not term.startswith("§day:")]) / max(1, len(informative)) < COVERAGE \
                and not topics:
            continue
        if rule == "long" and len(matched) < 2 and not topics:
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
    kept, seen = [], {}
    for row in scored:
        if seen.get(row[1]["found"], 0) < (kind_cap or limit):
            kept.append(row)
            seen[row[1]["found"]] = seen.get(row[1]["found"], 0) + 1
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
