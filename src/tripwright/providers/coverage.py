"""Where and when Transitous's data can answer (A8, D-57, D-60), read from `transitous_coverage.json`.

An empty result outside the data's dates is "not in the data yet", never "no service"; a place in an area whose local
transport the data leaves out gets that said beside the results. Every region is read the same way: a stop belongs to
the dataset its Transitous ID starts with ("at-Railway-Current-Reference-Data-2026_…"), and the coverage file says,
for all of Transitous's feed lists, which datasets are skipped and, where a timetable-year rule is known, until when
they run. `refresh` regenerates that from the feed lists, so a new timetable year or a dataset taken back in changes
the data file, not the code. The rules and the outlines (`periods`, `areas`) are kept by hand; outlines are
approximate near their borders.

Run: `python -m tripwright.providers.coverage refresh <transitous checkout>/feeds [--commit SHA]` (prints what changed).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("transitous_coverage.json")
WORDS = {
    "zh": dict(not_yet="{day} 起换新时刻表，这段日期的新时刻表还没进来，现在查不到；不能说没车",
               too_old="{day} 以前的时刻表已不在数据里，查不到；不能说没车"),
    "en": dict(not_yet="New timetables start on {day} and are not in the data yet, so nothing can be found; do not say there is no service",
               too_old="Timetables before {day} are no longer in the data, so nothing can be found; do not say there is no service"),
}
STATIC = {"http", "ftp", "transitland-atlas", "mobility-database"}  # feed entries with timetables; `url` ones are mostly live feeds


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    return json.loads(Path(path or DATA).read_text(encoding="utf-8"))


def inside(lon: float, lat: float, polygon: list[list[float]]) -> bool:
    """Ray casting on [lon, lat] vertices."""
    found = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            found = not found
    return found


def file_of(stop_id: str, data: dict) -> str | None:
    """The feed list a Transitous stop ID comes from: its longest known prefix "<file>-" ("au-nsw" before "au")."""
    known = [stem for stem in data["files"] if stop_id.startswith(stem + "-")]
    return max(known, key=len) if known else None


def dataset_of(stop_id: str) -> str:
    return stop_id.split("_", 1)[0]


def located(end: dict) -> bool:
    return end.get("lat") is not None and end.get("lon") is not None


def files_for(ends: list[dict], stops: list[str], data: dict) -> set[str]:
    """The feed lists a lookup touches: those of its stop IDs, and for an end given as coordinates, the region whose
    outline holds it."""
    found = {stem for stop in [*(end.get("stop_id") for end in ends), *stops] if stop for stem in [file_of(stop, data)] if stem}
    for end in ends:
        if not end.get("stop_id") and located(end):
            found |= {area["file"] for area in data["areas"] if area.get("file") and not area.get("dataset")
                      and inside(float(end["lon"]), float(end["lat"]), area["polygon"])}
    return found


def assess(ends: list[dict], days: list[date], language: str = "zh", *, stops: list[str] = (), data: dict | None = None) -> list[dict]:
    """Coverage notes for a lookup between `ends` ({stop_id, lat, lon} as the upstream resolved them) on `days` (local
    dates searched), `stops` being the stop IDs its results passed: {kind: "not_yet" | "too_old" | "gap", id, text}.
    "not_yet" and "too_old": a day lies outside a touched region's timetable year, so an empty result says nothing about
    service. "gap": an end lies in an area whose local transport is in a skipped dataset."""
    data = data or load()
    said = WORDS.get(language, WORDS["en"])
    notes = []
    touched = files_for(ends, list(stops), data)
    for period in data["periods"]:
        valid = data["valid"].get(period["id"])
        if period["file"] not in touched or not valid:
            continue
        first, last = date.fromisoformat(valid["from"]), date.fromisoformat(valid["until"])
        if any(day > last for day in days):
            notes.append(dict(kind="not_yet", id=period["id"], until=last.isoformat(),
                              text=said["not_yet"].format(day=(last + timedelta(days=1)).isoformat())))
        elif any(day < first for day in days):
            notes.append(dict(kind="too_old", id=period["id"], since=first.isoformat(), text=said["too_old"].format(day=first.isoformat())))
    for area in data["areas"]:
        family = area.get("dataset")
        if not family or not any(key == family or re.fullmatch(re.escape(family) + r"-\d{4}", key) for key in data["skipped"]):
            continue
        if any(located(end) and inside(float(end["lon"]), float(end["lat"]), area["polygon"]) for end in ends):
            notes.append(dict(kind="gap", id=area["id"], area=area["name"].get(language, area["name"]["en"]),
                              text=area["text"].get(language, area["text"]["en"])))
    return notes


def second_saturday_of_december(year: int) -> date:
    first = date(year, 12, 1)
    return first + timedelta(days=(5 - first.weekday()) % 7 + 7)


def refresh(data: dict, feeds: dict[str, dict]) -> dict:
    """The data with its generated parts recomputed from Transitous's feed lists ({file stem: feeds/<stem>.json}):
    per feed list, how many timetable datasets, skipped ones and live feeds it has; every skipped dataset with the
    reason; and for each period rule, the years of its file's datasets that carry the rule's field and are not skipped,
    and the dates they run (a year Y runs from the day after the second Saturday of December Y-1 to that of Y)."""
    updated = json.loads(json.dumps(data))
    files, skipped = {}, {}
    for stem, listing in sorted(feeds.items()):
        sources = listing.get("sources", [])
        static = {source["name"] for source in sources if source.get("type") in STATIC}
        off = {source["name"]: source.get("skip-reason") or "" for source in sources if source.get("skip") and source.get("type") in STATIC}
        files[stem] = dict(timetables=len(static), skipped=len(off), live=len({source["name"] for source in sources if source.get("spec") in ("gtfs-rt", "siri")}))
        skipped.update({f"{stem}-{name}": reason for name, reason in off.items()})
    valid = {}
    for period in updated["periods"]:
        sources = feeds.get(period["file"], {}).get("sources", [])
        named = [source["name"] for source in sources if source.get(period["requires"]) and source.get("type") in STATIC and not source.get("skip")]
        years = sorted({int(found[1]) for name in named for found in [re.search(r"-(\d{4})$", name)] if found})
        if years:
            valid[period["id"]] = {"years": years, "from": (second_saturday_of_december(years[0] - 1) + timedelta(days=1)).isoformat(),
                                   "until": second_saturday_of_december(years[-1]).isoformat(),
                                   "datasets": sorted(f"{period['file']}-{name}" for name in set(named))}
    updated.update(files=files, skipped=dict(sorted(skipped.items())), valid=valid)
    return updated


def write(data: dict, path: Path) -> None:
    """One key per line at the top, each value on one line, so a refresh shows as a short diff."""
    lines = ["{"] + [f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}," for key, value in data.items()]
    lines[-1] = lines[-1].rstrip(",")
    path.write_text("\n".join(lines + ["}"]) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tripwright.providers.coverage", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("refresh", help="recompute the generated parts from Transitous's feed lists")
    command.add_argument("feeds", help="the feeds directory of a Transitous checkout (one JSON per region)")
    command.add_argument("--commit", help="the Transitous commit the feeds were read at")
    command.add_argument("--data", default=str(DATA), help="the coverage file to update")
    args = parser.parse_args(argv)
    path = Path(args.data)
    old = json.loads(path.read_text(encoding="utf-8"))
    listings = {file.stem: json.loads(file.read_text(encoding="utf-8")) for file in sorted(Path(args.feeds).glob("*.json"))}
    if not listings:
        parser.error(f"no feed lists in {args.feeds}")
    new = refresh(old, listings)
    new["read"] = date.today().isoformat()
    if args.commit:
        new["commit"] = args.commit
    write(new, path)
    load.cache_clear()
    print(f"{len(new['files'])} feed lists, {len(new['skipped'])} skipped datasets")
    print("\n".join(f"{key}: {value['from']}—{value['until']}" for key, value in new["valid"].items()))
    gone = sorted(set(old.get("skipped", {})) - set(new["skipped"]))
    added = sorted(set(new["skipped"]) - set(old.get("skipped", {})))
    print("no longer skipped: " + (", ".join(gone) or "none") + "; newly skipped: " + (", ".join(added) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
