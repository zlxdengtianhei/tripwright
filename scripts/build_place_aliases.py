"""Write `src/tripwright/place_aliases.yaml` from a GeoNames dump (Q19, 2026-11-23).

A question says 哈尔施塔特 and the repository says Hallstatt; a question says 圣沃尔夫冈 and the repository says
Sankt Wolfgang im Salzkammergut. `recall.names_in` joins the two when both spellings are in a table. The big cities
are written by hand in recall.yaml; this script adds the rest from GeoNames, whose alternate names are the same join
for every other place.

Which places, which spellings:

- everywhere: a place of 100,000 people or more that has a Chinese name among its alternate names;
- the regions given by `--regions` (default `AT`, the country of the packaged example trip): every place of 500
  people or more that has a Chinese name — small towns are exactly the ones a traveller asks about and the
  hand-written table does not hold. Pass other country codes for another trip's region;
- a Chinese name with a region in it ("萨尔茨卡默古特地区圣沃尔夫冈") also gives its tail ("圣沃尔夫冈"); a German name
  with a qualifier ("St. Wolfgang im Salzkammergut") also gives its head ("St. Wolfgang");
- no name of the packaged example trip: the script drops every key and spelling that `example_trip.reserved_names()`
  names — the plan's places and areas, and the destinations the trip's own words and entries name (Hallstatt, Sankt
  Wolfgang, Bad Ischl, Gosau, the two cities). The guard tests on both word tables check that same list, so the
  generator and the guards cannot disagree about what a place of that trip is.

GeoNames is CC BY 4.0. Usage: download `cities500.zip` from https://download.geonames.org/export/dump/ , unzip, then

    uv run python scripts/build_place_aliases.py --cities /path/to/cities500.txt --regions AT

The output is checked in: the service builds no table at run time and needs no network.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path

from tripwright.example_trip import reserved_names

CHINESE = re.compile(r"^[\u4e00-\u9fff]{2,24}$")
LATIN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ .'’()-]{1,39}$")
REGION_TAIL = ("地区", "州", "省")
SUFFIX = ("市", "县", "区", "镇", "村")
QUALIFIER = re.compile(r"\s+(?:im|am|an der|ob der|bei|in der)\s+.*$", re.IGNORECASE)
GENERIC = {"hauptbahnhof", "bahnhof", "station", "flughafen", "airport", "gasse", "strasse", "platz", "kirche", "hbf"}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).casefold().replace("ß", "ss")
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", fold(text)) or "place"


def chinese_forms(form: str) -> list[str]:
    out = [form]
    for split in REGION_TAIL:
        if split in form and len(form.split(split)[-1]) >= 4:
            out.append(form.split(split)[-1])
    for suffix in SUFFIX:
        if form.endswith(suffix) and len(form) - 1 >= 2:
            out.append(form[:-1])
    return out


def latin_forms(form: str, names: set[str]) -> list[str]:
    out = []
    head = QUALIFIER.sub("", form).strip()
    for candidate in (head, form):
        if candidate and LATIN.match(candidate):
            out.append(candidate)
    if form.casefold() in names:
        out.append(form)
    return out


def same(form: str, trip_names: set[str]) -> bool:
    """The whole place is one of the example trip's own: a city of it under any language it is written in."""
    return fold(form) in trip_names


def clashes(form: str, trip_names: set[str]) -> bool:
    """A spelling the guard test would read as a place, area or operator of the example trip."""
    folded = fold(form)
    for name in trip_names:
        if not name or name in GENERIC or len(name) < 3:
            continue
        if folded == name or name in folded:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cities", required=True, type=Path, help="cities500.txt (or cities15000.txt) from GeoNames")
    parser.add_argument("--regions", default="AT", help="country codes whose small places are all kept (comma separated)")
    parser.add_argument("--world-population", default=100_000, type=int)
    parser.add_argument("--region-population", default=500, type=int)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "src/tripwright/place_aliases.yaml")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when the file would change")
    options = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    regions = {code.strip().upper() for code in options.regions.split(",") if code.strip()}
    trip_names = set(reserved_names())
    hand = __import__("yaml").safe_load((root / "src/tripwright/recall.yaml").read_text(encoding="utf-8")) or {}
    hand_names = {fold(form) for forms in (hand.get("names") or {}).values() for form in forms}

    places: dict[str, list[str]] = {}
    with options.cities.open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) < 15:
                continue
            geonameid, name, ascii_name, alternates, country, population = row[0], row[1], row[2], row[3], row[8], row[14]
            try:
                people = int(population or 0)
            except ValueError:
                continue
            wanted = country in regions and people >= options.region_population or people >= options.world_population
            if not wanted:
                continue
            forms = [form.strip() for form in alternates.split(",") if form.strip()]
            chinese = [form for form in forms if CHINESE.match(form)]
            if not chinese:
                continue
            names = {fold(name), fold(ascii_name)}
            kept: list[str] = []
            for form in chinese:
                kept += chinese_forms(form)
            for form in [name, ascii_name, *forms]:
                kept += latin_forms(form, names)
            if any(same(form, trip_names) for form in [name, ascii_name, *forms]):
                continue  # a place of the example trip, under any of the languages it is written in: out
            kept = [form for form in dict.fromkeys(normal for normal in (form.strip() for form in kept) if normal)
                    if not clashes(form, trip_names)]
            key = slug(QUALIFIER.sub("", ascii_name or name).strip())
            if not kept:
                continue
            if key in places:
                places[key] = list(dict.fromkeys(places[key] + kept))[:8]
                continue
            # a spelling the hand-written table already carries is not repeated; the hand-written entry stays
            places[key] = [form for form in kept if fold(form) not in hand_names][:8] or kept[:8]

    lines = ["# General place names and their other spellings, generated from GeoNames cities500",
             "# (https://www.geonames.org/, data https://download.geonames.org/export/dump/cities500.zip, CC BY 4.0",
             "# https://creativecommons.org/licenses/by/4.0/) by scripts/build_place_aliases.py.",
             "# Shape and use: recall.names_in. Changes: only places of 100,000+ people worldwide and 500+ in the",
             "# region(s) the script was run with are kept, each name cut to at most 8 spellings, region prefixes and",
             "# administrative suffixes and German qualifiers removed. No place, area or operator of the example trip",
             "# is here (example_trip.reserved_names() names them, the generator drops them, and the guard tests on",
             "# both word tables check this file too).",
             "places:"]
    for key in sorted(places):
        forms = ", ".join(__import__("json").dumps(form, ensure_ascii=False) for form in places[key])
        lines.append(f"  {key}: [{forms}]")
    text = "\n".join(lines) + "\n"
    if options.check:
        current = options.output.read_text(encoding="utf-8") if options.output.exists() else ""
        if current != text:
            print("place_aliases.yaml would change; run the script without --check", file=sys.stderr)
            return 1
        print(f"place_aliases.yaml is current ({len(places)} names)")
        return 0
    options.output.write_text(text, encoding="utf-8")
    print(f"wrote {options.output} with {len(places)} names, {len(text) / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
