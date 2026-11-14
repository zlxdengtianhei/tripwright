"""Transit sources a deployment adds beside Transitous (docs/transit-sources.md, R-07, R-08).

A source is a MOTIS server the deployment runs itself (the `motis` service of deploy/compose.transit.yaml) with one region's GTFS
and, where the region has one, its GTFS-RT feed. It speaks the same API as Transitous, so it is a `TransitousProvider`
with its own address, name and attribution. `TRIPWRIGHT_TRANSIT_SOURCES` names a YAML file listing the sources, or
holds the list itself (a value starting with "[" or "{", for a `docker run -e`); without it the service uses
Transitous alone, as before.

A search goes to a source when both its ends are inside that source's area: a place or coordinates inside its `bbox`,
a stop ID of one of its `datasets`, or a typed name whose best stop in that source carries every typed word. Anything
else, including a search that crosses the area's edge, goes to Transitous. `match` lists the words of trip areas the
source covers, so that planning knows whether a trip's region has real-time data (consider.asks).

Run: `python -m tripwright.providers.sources bbox <gtfs.zip>` prints the `bbox` of a GTFS's stops;
`python -m tripwright.providers.sources check [--file F] <id> <from> <to>` looks up one connection through a source.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .base import Place
from .common import ProviderUnavailable, budgeted, gather_cancel
from .transitous import COORD, TransitousProvider, ranked, words

ENV = "TRIPWRIGHT_TRANSIT_SOURCES"
DOC = {"en": "https://github.com/zlxdengtianhei/tripwright/blob/main/docs/transit-sources.md",
       "zh": "https://github.com/zlxdengtianhei/tripwright/blob/main/docs/transit-sources.zh-CN.md"}
KEYS = {"id", "name", "api", "bbox", "datasets", "match", "realtime", "attribution"}


def check_entry(entry: dict) -> dict:
    """One source from the file, with its defaults; a ValueError names what is wrong."""
    unknown = set(entry) - KEYS
    if unknown:
        raise ValueError(f"transit source {entry.get('id')!r}: unknown keys {sorted(unknown)}")
    source = str(entry.get("id") or "")
    if not re.fullmatch(r"[a-z0-9-]+", source):
        raise ValueError(f"transit source id {source!r}: lower-case letters, digits and '-' only")
    if not re.match(r"https?://", str(entry.get("api") or "")):
        raise ValueError(f"transit source {source}: api must be an http(s) address ending in /api")
    box = entry.get("bbox")
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(value, (int, float)) for value in box)
            and box[0] < box[2] and box[1] < box[3]):
        raise ValueError(f"transit source {source}: bbox is [west, south, east, north] in degrees")
    if not entry.get("attribution"):
        raise ValueError(f"transit source {source}: attribution is required (the data's licence asks for it)")
    name = entry.get("name") or source
    name = name if isinstance(name, dict) else {"en": name, "zh": name}
    return dict(id=source, name={"en": name.get("en") or name.get("zh"), "zh": name.get("zh") or name.get("en")},
                api=entry["api"].rstrip("/"), bbox=[float(value) for value in box],
                datasets=[str(tag) for tag in entry.get("datasets") or [source]],
                match=[str(word).casefold() for word in entry.get("match") or []],
                realtime=bool(entry.get("realtime")), attribution=str(entry["attribution"]))


@lru_cache(maxsize=4)
def read(setting: str) -> tuple[dict, ...]:
    """The sources a setting gives: a file's path, or the YAML (or JSON) itself; `{sources: [...]}` or the bare list."""
    inline = setting.lstrip().startswith(("[", "{"))
    data = yaml.safe_load(setting if inline else Path(setting).read_text(encoding="utf-8")) or {}
    entries = [check_entry(entry) for entry in (data if isinstance(data, list) else data.get("sources") or [])]
    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{ENV}: two transit sources share an id")
    return tuple(entries)


def configured() -> tuple[dict, ...]:
    """The deployment's sources (TRIPWRIGHT_TRANSIT_SOURCES), or none."""
    path = os.environ.get(ENV)
    return read(path) if path else ()


def for_areas(text: str) -> list[dict]:
    """The deployment's sources whose `match` words occur in a trip's areas (`text`, any case)."""
    said = text.casefold()
    return [entry for entry in configured() if any(word in said for word in entry["match"])]


class Local:
    def __init__(self, entry: dict, provider: TransitousProvider):
        self.entry = entry
        self.provider = provider

    def holds(self, lat, lon) -> bool:
        west, south, east, north = self.entry["bbox"]
        return lat is not None and lon is not None and west <= float(lon) <= east and south <= float(lat) <= north

    def owns(self, stop_id: str | None) -> bool:
        return bool(stop_id) and any(str(stop_id).startswith(tag + "_") for tag in self.entry["datasets"])


class RoutedTransit:
    """Transitous plus the deployment's own sources: each search goes to the one whose area holds both ends."""

    def __init__(self, default: TransitousProvider, sources: list[Local]):
        self.default = default
        self.sources = sources

    @property
    def timezone_name(self):
        return self.default.timezone_name

    async def _home(self, location, fresh=False) -> Local | None:
        if isinstance(location, Place):
            return next((local for local in self.sources if local.holds(location.lat, location.lon)), None)
        text = str(location).strip()
        found = COORD.fullmatch(text)
        if found:
            return next((local for local in self.sources if local.holds(float(found[1]), float(found[2]))), None)
        if re.search(r"[:_]", text) and " " not in text:
            return next((local for local in self.sources if local.owns(text)), None)
        said = words(text)
        for local in self.sources:
            try:
                hits = (await local.provider.stops(text, limit=5, fresh=fresh)).value
            except (ProviderUnavailable, ValueError):
                continue  # the source is down or knows no such stop: Transitous answers
            if hits:
                best = ranked(text, hits)[0][0]
                named = words(best.name).union(*(words(area) for area in best.areas))
                if local.holds(best.lat, best.lon) and said <= named:
                    return local
        return None

    async def _pick(self, origin, destination, fresh=False):
        first, second = await gather_cancel(self._home(origin, fresh), self._home(destination, fresh))
        return first.provider if first is not None and first is second else self.default

    def _owner(self, meta: dict) -> Local | None:
        stops = [*(meta.get(end, {}).get("stop_id") for end in ("origin", "destination")), *meta.get("stops", [])]
        return next((local for local in self.sources for stop in stops if local.owns(stop)), None)

    @budgeted
    async def stops(self, text: str, *, limit=8, fresh=False):
        home = await self._home(text, fresh)
        return await (home.provider if home else self.default).stops(text, limit=limit, fresh=fresh)

    @budgeted
    async def resolve(self, location, *, fresh=False):
        home = await self._home(location, fresh)
        return await (home.provider if home else self.default).resolve(location, fresh=fresh)

    @budgeted
    async def walk_minutes(self, origin: Place, destinations: list[Place], *, fresh=False, max_min=60):
        home = next((local for local in self.sources if all(local.holds(place.lat, place.lon) for place in [origin, *destinations])), None)
        return await (home.provider if home else self.default).walk_minutes(origin, destinations, fresh=fresh, max_min=max_min)

    @budgeted
    async def plan(self, origin, destination, when: datetime, **options):
        provider = await self._pick(origin, destination, options.get("fresh", False))
        return await provider.plan(origin, destination, when, **options)

    @budgeted
    async def day(self, origin, destination, day: str, **options):
        provider = await self._pick(origin, destination, options.get("fresh", False))
        return await provider.day(origin, destination, day, **options)

    def coverage(self, meta: dict, language: str = "zh") -> list[dict]:
        """Transitous's coverage notes (coverage.py) for its own searches; a source's search has none of them."""
        return [] if self._owner(meta) else self.default.coverage(meta, language)

    def feed_lists(self, meta: dict) -> set[str]:
        return set() if self._owner(meta) else self.default.feed_lists(meta)

    async def aclose(self):
        for provider in [self.default, *(local.provider for local in self.sources)]:
            await provider.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exception):
        await self.aclose()


LIVE = (TransitousProvider, RoutedTransit)


def transit(**options) -> TransitousProvider | RoutedTransit:
    """The live transit provider: Transitous, routed together with the deployment's sources when there are any.
    `options` go to the Transitous provider (usage_file, client, ...)."""
    default = TransitousProvider(**options)
    entries = configured()
    if not entries:
        return default
    return RoutedTransit(default, [Local(entry, TransitousProvider(entry["api"], source=f"motis:{entry['id']}",
                                                                   attribution=entry["attribution"],
                                                                   timezone_name=options.get("timezone_name")))
                                   for entry in entries])


# --- commands -------------------------------------------------------------------------------------------------------

def bbox(gtfs: Path, margin: float = 0.01) -> list[float]:
    """[west, south, east, north] around every stop in a GTFS zip, widened by `margin` degrees."""
    with zipfile.ZipFile(gtfs) as archive, archive.open("stops.txt") as raw:
        rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        points = [(float(row["stop_lat"]), float(row["stop_lon"])) for row in rows if row.get("stop_lat") and row.get("stop_lon")]
    points = [(lat, lon) for lat, lon in points if (lat, lon) != (0.0, 0.0)]
    if not points:
        raise ValueError(f"{gtfs}: stops.txt has no coordinates")
    lats, lons = [lat for lat, _ in points], [lon for _, lon in points]
    return [round(min(lons) - margin, 3), round(min(lats) - margin, 3), round(max(lons) + margin, 3), round(max(lats) + margin, 3)]


async def check(entry: dict, origin: str, destination: str, zone: str, when: datetime | None) -> int:
    provider = TransitousProvider(entry["api"], source=f"motis:{entry['id']}", attribution=entry["attribution"])
    try:
        when = when or datetime.now(ZoneInfo(zone)).replace(second=0, microsecond=0) + timedelta(minutes=5)
        result = await provider.plan(origin, destination, when.astimezone(ZoneInfo(zone)), limit=3)
    except ProviderUnavailable as error:
        print(f"{entry['id']}: no answer from {entry['api']}: {error.reason}")
        return 1
    finally:
        await provider.aclose()
    ends = [result.meta.get(end, {}) for end in ("origin", "destination")]
    print(f"{entry['id']} ({entry['api']}): {ends[0].get('name')} [{ends[0].get('stop_id')}] -> "
          f"{ends[1].get('name')} [{ends[1].get('stop_id')}], {len(result.value)} connections from {when.isoformat()}")
    for itinerary in result.value:
        rides = [leg for leg in itinerary.legs if leg.mode != "walk"]
        live = "real time" if rides and all(leg.extra.get("realTime") for leg in rides) else "timetable"
        said = " / ".join(" ".join(part for part in (leg.mode, leg.line) if part) for leg in rides) or "walk"
        print(f"  {itinerary.depart:%H:%M} -> {itinerary.arrive:%H:%M}  {said}  ({live})")
    if not all(Local(entry, provider).holds(end.get("lat"), end.get("lon")) for end in ends):
        print("  warning: an end lies outside this source's bbox, so the service would send this search to Transitous")
    return 0 if result.value else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tripwright.providers.sources", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    box = commands.add_parser("bbox", help="print the bbox of a GTFS zip's stops, for the sources file")
    box.add_argument("gtfs")
    probe = commands.add_parser("check", help="look up one connection through a source of the sources file")
    probe.add_argument("source")
    probe.add_argument("origin")
    probe.add_argument("destination")
    probe.add_argument("--file", default=os.environ.get(ENV), help=f"the sources file (default: ${ENV})")
    probe.add_argument("--zone", default="UTC", help="IANA time zone of the region, e.g. Europe/Berlin")
    probe.add_argument("--when", help="local departure, e.g. 2026-11-16T09:00 (default: in five minutes)")
    args = parser.parse_args(argv)
    if args.command == "bbox":
        print(bbox(Path(args.gtfs)))
        return 0
    if not args.file:
        parser.error(f"name the sources file with --file or ${ENV}")
    entry = next((entry for entry in read(args.file) if entry["id"] == args.source), None)
    if entry is None:
        parser.error(f"{args.file} has no source {args.source!r}")
    when = datetime.fromisoformat(args.when).replace(tzinfo=ZoneInfo(args.zone)) if args.when else None
    return asyncio.run(check(entry, args.origin, args.destination, args.zone, when))


if __name__ == "__main__":
    sys.exit(main())
