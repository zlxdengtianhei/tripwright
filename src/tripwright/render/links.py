"""Map and timetable link formats.

Each builder owns one vendor's documented URL format and its encoding. None of these links has been opened on a
phone: whether a chat app hands them to the map app or to its in-app browser is not verified (未在手机上验证, design
9.3, W-13). Google and Apple have no departure-time parameter, so their links leave out the origin and start from the
phone's position at the current time; only the ÖBB Scotty query carries a date and a clock time.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote, quote_plus

MAX_LENGTH = 2048  # Google's documented URL limit; the shortest of the three formats.
PUBLIC = {"transit", "bus", "train", "tram", "metro", "subway", "ferry", "boat", "coach"}
GOOGLE_MODES = {"walk": "walking", "walking": "walking", "drive": "driving", "driving": "driving", "car": "driving",
                "taxi": "driving", "bike": "bicycling", "bicycle": "bicycling", "bicycling": "bicycling"}
APPLE_MODES = {"walking": "walking", "driving": "driving", "bicycling": "cycling", "transit": "transit"}
LABELS = {
    "google-maps": {"zh": "Google 地图", "en": "Google Maps"},
    "apple-maps": {"zh": "Apple 地图", "en": "Apple Maps"},
    "oebb-scotty": {"zh": "ÖBB / Scotty", "en": "ÖBB / Scotty"},
}


def label(tool: str, language: str = "zh") -> str:
    return LABELS[tool]["en" if language == "en" else "zh"]


def google_mode(mode: str) -> str:
    return "transit" if mode in PUBLIC else GOOGLE_MODES.get(mode, "transit")


def checked(url: str) -> str:
    if len(url) > MAX_LENGTH:
        raise ValueError(f"Link is {len(url)} characters; map links must stay within {MAX_LENGTH}")
    return url


def encoded(value: str) -> str:
    if not value.strip():
        raise ValueError("A link needs a non-empty place")
    return quote(value.strip(), safe="")


def google_directions(destination: str, *, origin: str | None = None, mode: str = "transit") -> str:
    """Google Maps directions (W1). Format verified against the documentation only; not opened on a phone."""
    query = "api=1" + (f"&origin={encoded(origin)}" if origin else "")
    return checked(f"https://www.google.com/maps/dir/?{query}&destination={encoded(destination)}&travelmode={google_mode(mode)}")


def google_search(text: str, *, place_id: str | None = None) -> str:
    """Google Maps place search (W1). Not opened on a phone."""
    extra = f"&query_place_id={encoded(place_id)}" if place_id else ""
    return checked(f"https://www.google.com/maps/search/?api=1&query={encoded(text)}{extra}")


def apple_directions(destination: str, *, origin: str | None = None, mode: str = "transit") -> str:
    """Apple Maps directions, iOS 18.4 format (W2). Not opened on a phone; older iOS versions are not checked."""
    source = f"source={encoded(origin)}&" if origin else ""
    apple = APPLE_MODES[google_mode(mode)]
    return checked(f"https://maps.apple.com/directions?{source}destination={encoded(destination)}&mode={apple}")


def apple_place(name: str, *, coordinate: tuple[float, float] | None = None) -> str:
    """Apple Maps place (W2). Not opened on a phone."""
    if coordinate:
        return checked(f"https://maps.apple.com/place?coordinate={coordinate[0]},{coordinate[1]}&name={encoded(name)}")
    return checked(f"https://maps.apple.com/place?address={encoded(name)}")


def scotty_query(origin: str, destination: str, day: str, clock: str, *, arrive: bool = False, english: bool = False) -> str:
    """ÖBB Scotty classic query (C1, C2). A curl request with real station names returned departures on 2026-10-31;
    the page calls itself the old version that is no longer maintained. Not opened on a phone."""
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("Scotty needs the time as HH:MM")
    when = date.fromisoformat(day).strftime("%d.%m.%Y")
    if not origin.strip() or not destination.strip():
        raise ValueError("A link needs a non-empty place")
    path = "en" if english else "dn"
    return checked(f"https://fahrplan.oebb.at/bin/query.exe/{path}?S={quote_plus(origin.strip())}&Z={quote_plus(destination.strip())}"
                   f"&date={when}&time={clock}&timesel={'arrive' if arrive else 'depart'}&start=1")
