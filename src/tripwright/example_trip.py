"""The packaged example trip's own names, the one list the general word tables must stay clear of.

`recall.yaml` and the generated `place_aliases.yaml` carry general travel words and general place names, never a
place of the one example trip that ships with this repository. This module gathers those names from one place: the
trip's plan (`plan/places.yaml`), its areas (`trip.yaml`) and the destinations its own words and entries name besides
the plan's pins (listed below with where they were read). `scripts/build_place_aliases.py` drops them from the table
and the guard tests check both tables against this list, so the generator and the guards cannot disagree about what
"a place of the example trip" means.

It reads the repository checkout, so it is for the generator and the tests; the service does not use it at run time.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

from tripwright import recall

ROOT = Path(__file__).resolve().parents[2]
TRIP = ROOT / "examples/trip-2026-10-bud-wien/trips/2026-10-bud-wien"

# Destinations the example trip names in its own words and entries besides its plan's pins (plan/places.yaml):
#   Hallstatt / 哈尔施塔特, Sankt Wolfgang / 圣沃尔夫冈 — the 10-30 question which of the two to visit
#     (raw/2026-10-31/20261031T193703Z-me-4s7x05.md, quoted in profile/items/I-261031-*);
#   Bad Ischl / 巴德伊舍, Gosau / 戈绍 and 戈绍湖 — the 10-29 message about catching the last bus back from the lake
#     (raw/2026-10-31/20261031T193703Z-me-rhpzk9.md) and the 10-30 message about the luggage at the station
#     (...-ub04jp.md, ...-4msg8c.md), quoted in profile/items/I-261031-*;
#   Vienna / 维也纳, Budapest / 布达佩斯 — the trip's two cities, spelled in English and Chinese in its words.
DESTINATIONS = (
    "Hallstatt", "哈尔施塔特",
    "Sankt Wolfgang", "St. Wolfgang", "圣沃尔夫冈",
    "Bad Ischl", "巴德伊舍",
    "Gosau", "戈绍", "戈绍湖",
    "Vienna", "维也纳", "Budapest", "布达佩斯",
)


@cache
def reserved_names() -> frozenset[str]:
    """The example trip's places, areas, IDs and named destinations, folded: the names neither word table may hold."""
    meta = yaml.safe_load((TRIP / "trip.yaml").read_text(encoding="utf-8")) or {}
    places = yaml.safe_load((TRIP / "plan/places.yaml").read_text(encoding="utf-8")) or []
    names = {str(area) for area in meta.get("areas") or []}
    for place in places:
        head = str(place.get("name") or "").split("（")[0].strip()
        if head:
            names.add(head)
        local = str(place.get("name_local") or "").split(",")[0].strip()
        if local:
            names.add(local.split()[0])
        names.add(str(place.get("id") or ""))
    names.update(DESTINATIONS)
    return frozenset(folded for folded in (recall.fold(name) for name in names) if folded)
