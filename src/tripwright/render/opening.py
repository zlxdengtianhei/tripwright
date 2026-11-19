"""Stored material the service hands the host for the short first message on the road (round 4 review 4).

A road question with no `first_part` left the host with nothing to say before its lookups; the third replay measured the
first message at 431 s (68 s before), and three runs sent none at all (baseline 2026-11-19, `AFTER3.md`). The host now
gets `turn.opening_material`: the nearest stored facts, each marked archived and unchecked, so one or two lines to act
on need no lookup and no model call.

Data only: the plan's hard nodes, its bought or booked tickets, the next planned place with its stored address, and one
stored last-service or latest sentence. The service writes no sentence for the answer here.
"""
from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from tripwright.render.labels import split_claims
from tripwright.trip import aware, local_instant

LIMIT = 6  # items in one receipt
NODES = 2  # hard nodes: today's and tomorrow's nearest
TICKETS = 2  # bought or booked tickets, nearest first
PLACES = 1  # the next planned place
CLAIMS = 1  # one stored last-service or latest sentence
BOUGHT = ("bought", "booked")
# the words the plan uses for a last, only or latest time (labels.ARCHIVE_WORDS leaves “最晚/最迟” out)
CLAIM = re.compile(r"末班|最后一班|最后一趟|最晚|最迟|\blast (?:train|bus|service|connection)\b|\blatest\b", re.I)
ADDRESS = re.compile(r"地址\s*([^；;（(。\n]+)")

WORDS = {
    "zh": dict(archive="（存档，没核）", tomorrow="明天 ", today="今天", bought="已买", booked="已订",
               place="下一个地点：{name}", place_address="下一个地点：{name}，{address}",
               ticket="{status}：{what}（{when}，存档，没核）"),
    "en": dict(archive=" (archived, not checked)", tomorrow="tomorrow ", today="today", bought="bought", booked="booked",
               place="Next place: {name}", place_address="Next place: {name}, {address}",
               ticket="{status}: {what} ({when}; archived, not checked)"),
}


def material(plan: dict | None, at: str, language: str = "zh") -> list[dict]:
    """The nearest stored facts for the short first message: at most LIMIT items, each with `kind` and `text`, and an
    empty list when the plan stores none of them. `at` is the turn's clock; the plan's timezone gives today."""
    plan = plan or {}
    words = WORDS.get(language, WORDS["en"])
    zone = plan.get("timezone") or "UTC"
    moment = aware(at)
    today = moment.astimezone(ZoneInfo(zone)).date()
    tomorrow = today + timedelta(days=1)
    places = {place["id"]: place for place in plan.get("places", [])}
    planned = [day for day in plan.get("days", []) if day["date"] in (today.isoformat(), tomorrow.isoformat())]
    items = [*_nodes(planned, today, moment, zone, words), *_tickets(plan, today, words),
             *_place(planned, places, today, moment, zone, words)]
    taken = [_bare(item["text"]) for item in items]
    items += _claims(planned, today, words, taken)
    return items[:LIMIT]


def _bare(text: str) -> str:
    """What a stored item says without its archive mark, to keep the same stored sentence from being listed twice."""
    return re.sub(r"[（(](?:存档|archived)[^）)]*[）)]\s*\.?", "", text).strip()


def _nodes(planned: list[dict], today, moment, zone: str, words: dict) -> list[dict]:
    found = []
    for day in planned:
        for anchor in day.get("anchors", []):
            instant = local_instant(day["date"], anchor["time"], zone)
            if instant is None or instant <= moment:
                continue
            found.append((instant, day["date"], anchor))
    found.sort(key=lambda node: node[0])
    items = []
    for _, date, anchor in found[:NODES]:
        when = words["tomorrow"] if date != today.isoformat() else ""
        text = f"{when}{anchor['time']} {anchor['text']}".strip() + words["archive"]
        items.append(dict(kind="hard_node", date=date, time=anchor["time"], text=text))
    return items


def _tickets(plan: dict, today, words: dict) -> list[dict]:
    found = []
    for order, booking in enumerate(plan.get("bookings", [])):
        if booking.get("status") not in BOUGHT or str(booking.get("date")) < today.isoformat():
            continue
        found.append((booking["date"], order, booking))
    found.sort(key=lambda ticket: (ticket[0], ticket[1]))
    items = []
    for date, _, booking in found[:TICKETS]:
        when = words["today"] if date == today.isoformat() else date
        status = words["bought"] if booking["status"] == "bought" else words["booked"]
        text = words["ticket"].format(status=status, what=booking["what"], when=when)
        items.append(dict(kind="ticket", date=date, status=booking["status"], text=text))
    return items


def _place(planned: list[dict], places: dict, today, moment, zone: str, words: dict) -> list[dict]:
    """The next block that names a stored place, today first, then tomorrow, with the address the plan stores for it."""
    now_clock = moment.astimezone(ZoneInfo(zone)).strftime("%H:%M")
    for day in planned:
        for option in day.get("plans", [])[:1]:
            blocks = sorted((block for track in option.get("tracks", []) for block in track.get("blocks", [])
                             if block.get("place")), key=lambda block: block["start"])
            for block in blocks:
                if day["date"] == today.isoformat() and block["start"] < now_clock:
                    continue
                place = places.get(block["place"])
                if place is None:
                    continue
                address = _address(place)
                text = (words["place_address"] if address else words["place"]).format(name=place["name"], address=address)
                if day["date"] != today.isoformat():
                    text = words["tomorrow"] + text
                return [dict(kind="place", place=place["id"], address=address or None, text=text + words["archive"])]
    return []


def _address(place: dict) -> str:
    """The address the plan stores: the “地址 …” part of the notes, else the local name when it carries a street
    number (the measured trips store “Som Kitchen, Am Hauptbahnhof 1, 1100 Wien” there)."""
    match = ADDRESS.search(place.get("notes") or "")
    if match:
        return match.group(1).strip()
    local = place.get("name_local") or ""
    return local if local and re.search(r"\d", local) and local.casefold() != (place.get("name") or "").casefold() else ""


def _claims(planned: list[dict], today, words: dict, taken: list[str]) -> list[dict]:
    """One stored last-service or latest sentence from today's or tomorrow's plan text, marked as archive."""
    for day in planned:
        texts = []
        for anchor in day.get("anchors", []):
            texts += [anchor.get("text"), anchor.get("consequence"), anchor.get("alternative")]
        for option in day.get("plans", [])[:1]:
            for track in option.get("tracks", []):
                for block in track.get("blocks", []):
                    texts += [block.get("text"), block.get("note")]
        for text in texts:
            for part in split_claims(text or ""):
                claim = part.strip("。； ")
                if not CLAIM.search(claim) or any(claim in seen for seen in taken):
                    continue
                when = words["tomorrow"] if day["date"] != today.isoformat() else ""
                return [dict(kind="stored_time", date=day["date"], text=f"{when}{claim}" + words["archive"])]
    return []
