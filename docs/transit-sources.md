# Transit sources for a region Transitous lacks

[中文](transit-sources.zh-CN.md)

**Short version.** Tripwright gets its timetables from [Transitous](https://transitous.org/). Where Transitous has no data for a region, or has it without real time, you can run your own [MOTIS](https://github.com/motis-project/motis) server (the engine Transitous runs) next to Tripwright, feed it that region's GTFS timetable and, if there is one, its GTFS-RT real-time feed, and tell Tripwright where it is with one setting. Searches with both ends inside that region then go to your server; everything else still goes to Transitous. Nothing changes until you set it up.

When a plan reaches a region without a real-time source, the assistant tells you so once and asks whether you want one, with a link to this page. Say no and it is not asked again on that trip.

Tested on 2026-11-14 with MOTIS 2.11.4 and the Aachen (AVV) timetable, on Docker Desktop on a Mac, through both setups below. Steps marked **not tested** were not run.

## 1. Find a source

You need a **GTFS** file (a zip with `stops.txt`, `stop_times.txt` and so on) for the timetable, and optionally a **GTFS-RT** address for real time. MOTIS also reads SIRI Lite (`protocol: siri`), still marked work in progress by MOTIS (**not tested** here).

Where to look, in this order:

1. **Is it in Transitous already?** [transitous.org/sources](https://transitous.org/sources) lists every source by country, with the operators it covers and whether real time comes with it. On the [Transitous map](https://transitous.org/), a trip shown grey has no real-time data. If your region is there with real time, there is nothing to set up.
2. **[Mobility Database](https://mobilitydatabase.org)** (run by MobilityData): more than 6,000 GTFS, GTFS-RT and GBFS feeds in more than 99 countries. Browsing the site is free; its API needs a free account. Each feed record says whether the feed needs a key (`authentication_type`: 0 none, 1 in the URL, 2 in a header) and links its licence (`license_url`).
3. **[Transitland](https://www.transit.land/)** (run by Interline): GTFS and GTFS-RT; its feed list, [Transitland Atlas](https://github.com/transitland/transitland-atlas), is open on GitHub. Its API always needs a key.
4. **The country's national access point.** EU member states must each run one for transport data (Delegated Regulation (EU) 2017/1926); [NAPCORE lists them](https://napcore.eu/description-naps/national-access-point). Most are catalogues that link to the publishers, not download pages.
5. **The operator or the city's open-data portal.** A rule of thumb from Transitous: if Google Maps shows public transport in a region, a GTFS for it very likely exists.

Examples of what you find (read on 2026-11-14): Switzerland's [opentransportdata.swiss](https://opentransportdata.swiss/en/cookbook/timetable-cookbook/gtfs) has GTFS and GTFS-RT, the real time with a free key limited to 2 requests a minute; Germany's [gtfs.de](https://gtfs.de/en/feeds) has a free GTFS (CC BY 4.0, the next 30 days) and a beta GTFS-RT; England, Wales and Scotland's buses are on the Bus Open Data Service with a free key; Japan's are mostly behind the ODPT API with a free token. In mainland China we found no open GTFS: only map companies' APIs, which need a key and a verified identity and cannot be loaded into MOTIS (section 6).

Before you use a source, check four things:

| Check | Good sign | Stop sign |
|---|---|---|
| Format | GTFS zip; GTFS-RT `.pb` address | Only a PDF, a web page, or an app-only API (that is route 3 below) |
| Up to date | `feed_info.txt` or `calendar.txt` covers your travel dates | Dates end before your trip: MOTIS then finds nothing for those days |
| Licence | An open licence (CC BY, ODbL, a national open-data licence); note the attribution it asks for | No licence, or "no redistribution" terms that forbid your use |
| Key | None, or a free key you can request yourself | A contract or payment: only you can sign it |

The key and any contract belong to you. The assistant cannot apply, sign or pay for you.

## 2. Three ways to connect it

1. **Give it to Transitous** (best for everyone, slowest for you). Transitous lists its sources in the [public-transport/transitous](https://github.com/public-transport/transitous) repository, one JSON file per region (`feeds/<ISO code>.json`, for example `feeds/at.json`). Add an entry (`"type": "http"` with the GTFS address, or `"type": "url"` with `"spec": "gtfs-rt"` for real time) and open a pull request; its checks fetch and test the feed, and you can try one region first with `./src/fetch.py feeds/<code>.json` ([how](https://transitous.org/doc)). Once the maintainers accept it, every Tripwright gets it with no change to your deployment. Their documentation names no review time (**not tested**). Two limits: a source that needs a key can go in only with the key encrypted for the maintainers (`AGE-ENCRYPTED:`, [how](https://github.com/public-transport/transitous/blob/main/docs/docs/index.md#private-api-keys)), and Transitous then uses that key for everyone, so check that the key's terms allow it; and Transitous's own terms apply to everyone who uses it: open-source clients, an identifying User-Agent, no commercial use ([API terms](https://transitous.org/api)). Adding the feed to [Mobility Database](https://github.com/MobilityData/mobility-database-catalogs) as well (a form or a pull request, answered within a week, says its README) helps other projects find it.
2. **Run your own MOTIS** (the default here; sections 3–5). Works today, for your deployment only.
3. **The region's own API** (for example Wiener Linien or VAO in Austria, or a map company's API where there is no open GTFS). Their real-time interfaces are their own APIs, not a GTFS-RT feed MOTIS reads (as far as we found); each needs its own adapter in Tripwright, and some need a contract or a verified identity in your name. No such adapter exists yet. A key in your name whose terms allow only your own use stays on your own server.

## 3. Prepare the files

Make one folder for everything, `transit/`, beside the folder that holds your Tripwright data (for the compose setup: `../../transit` from `deploy/`). Put in it:

- the region's GTFS zip;
- an OpenStreetMap extract that covers the region, as `.osm.pbf` ([Geofabrik](https://download.geofabrik.de/) has countries and regions; MOTIS needs it for stop names and walking);
- `config.yml`: copy [deploy/transit/config.yml](../deploy/transit/config.yml) and change the two file names. Keep the dataset tag short, lower case, without `_`;
- `sources.yaml`: copy [deploy/transit/sources.yaml](../deploy/transit/sources.yaml) and fill it in (below).

The tested example (Aachen):

```bash
mkdir transit && cd transit
curl -L -o aachen.osm.pbf https://github.com/motis-project/test-data/raw/aachen/aachen.osm.pbf
curl -L -o avv.gtfs.zip https://opendata.avv.de/current_GTFS/AVV_GTFS_Masten_mit_SPNV.zip
```

with `osm: aachen.osm.pbf`, the dataset tag `avv` and `path: avv.gtfs.zip` in `config.yml`.

Then import once (5.5 seconds and 30 MB for Aachen in our run; for all of Germany, one published run reports a peak of about 7 GB of memory during import, [source](https://github.com/helpcode-ai/anythingmcp/blob/main/deploy/motis/Dockerfile); **not tested** here):

```bash
docker run --rm -v "$PWD":/data -w /data ghcr.io/motis-project/motis:2.11.4 /motis import
```

The MOTIS image runs as its own unprivileged user. On Docker Desktop that user could write into the folder; on a Linux server, if the import stops with "permission denied", add `--user "$(id -u):$(id -g)"` to this command and to the MOTIS container below (**not tested** on Linux).

**`sources.yaml`.** One entry per MOTIS server:

```yaml
sources:
  - id: avv
    name: {en: "Aachen AVV (own MOTIS)", zh: "亚琛 AVV（自托管 MOTIS）"}
    api: http://motis:8080/api
    datasets: [avv]
    bbox: [5.95, 50.65, 6.25, 50.85]
    match: [aachen, 亚琛]
    realtime: false
    attribution: "Timetable: Aachener Verkehrsverbund (AVV) open data; map data © OpenStreetMap contributors"
```

- `bbox` is west, south, east, north: the area where this source has **all** the local transport. A search goes to your server only when both ends are inside it. `python -m tripwright.providers.sources bbox avv.gtfs.zip` prints the box around every stop of a GTFS; narrow it when the feed reaches further (the AVV feed's trains reach Cologne, so its box is far larger than Aachen).
- `match` lists words of trip areas this source covers; planning uses them to know whether a trip's region has real time.
- `realtime: true` only after you added a working `rt:` feed in `config.yml` (**not tested** with a real feed).
- `attribution` is required: the data's licence usually asks for it, and answers show it.

## 4. Start it

### With `deploy/compose.yaml`

```bash
cd deploy
echo 'COMPOSE_FILE=compose.yaml:compose.transit.yaml' >> .env
docker compose run --rm --no-deps motis /motis import    # if you did not import in section 3
docker compose up -d
```

[deploy/compose.transit.yaml](../deploy/compose.transit.yaml) adds the `motis` service on the private network, mounts `../../transit` into Tripwright read-only and sets `TRIPWRIGHT_TRANSIT_SOURCES=/transit/sources.yaml`. To switch it off, remove the `COMPOSE_FILE` line and run `docker compose up -d --remove-orphans` (this also stops the `motis` container).

### With the `docker run` setup from SETUP.md

From the folder that holds `transit/`:

```bash
docker network create tripwright-transit
docker run -d --name tripwright-motis --network tripwright-transit --network-alias motis --restart unless-stopped \
  -v "$PWD/transit":/data -w /data ghcr.io/motis-project/motis:2.11.4 /motis server /data/data
docker rm -f tripwright
docker run -d --name tripwright --restart unless-stopped --network tripwright-transit -p 127.0.0.1:8000:8000 \
  -v tripwright-data:/data \
  -e TRIPWRIGHT_TRANSIT_SOURCES='[{id: avv, api: "http://motis:8080/api", bbox: [5.95, 50.65, 6.25, 50.85], match: [aachen], attribution: "AVV open data; © OpenStreetMap contributors"}]' \
  tripwright
```

Removing and recreating the `tripwright` container keeps your trips: they are in the `tripwright-data` volume. The setting holds the list itself here, so no file has to be mounted.

## 5. Check that it is connected

The container is `tripwright-app` in the compose setup and `tripwright` in the `docker run` setup; both already carry the setting.

```bash
docker exec tripwright python -m tripwright.providers.sources check avv "Aachen Bushof" "Aachen West" \
  --zone Europe/Berlin --when 2026-11-16T09:00
```

In our run it printed:

```
avv (http://motis:8080/api): Aachen, Bushof [avv_000000100101] -> Aachen West [avv_000000108670], 3 connections from 2026-11-16T09:00:00+01:00
  09:01 -> 09:12  bus 1  (timetable)
  09:07 -> 09:14  bus 73  (timetable)
  09:13 -> 09:20  bus 33  (timetable)
```

Then ask through Tripwright itself; the result's `evidence.source` is `motis:<id>` for your server and `transitous` otherwise:

```bash
docker exec -e HOME=/tmp tripwright tripwright query transit --from "Aachen Bushof" --to "Aachen West" \
  --when 2026-11-16T09:00:00+01:00 --timezone Europe/Berlin
```

If the check finds no connections: the timetable may not cover that date (MOTIS loads from today for `num_days`), or the names match no stop (try `--text` with `tripwright query transit --action stops`). If it cannot reach the server, `docker logs tripwright-motis` shows whether MOTIS started.

## 6. When there is no source

Then there is nothing to connect. If a printed timetable exists, you could type it into a GTFS yourself and load it as in section 3 (**not tested**). Tripwright keeps answering from Transitous where it has data, says when a result is timetable only, and ends the answer with the local app to check real-time delays, platforms and disruptions in. An empty result is "not found", never "no service".
