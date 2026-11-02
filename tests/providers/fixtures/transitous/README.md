# Transitous fixtures (A8)

Real answers of the public Transitous API (MOTIS), sent on 2026-11-01 at 20:31 CET by the transit-sources research
(`adhoc_jobs/req_method_trip_system_20261031-1426/agents/research_transit_sources_20261101-2025/`). Public stops and
public places only. Plans are slimmed: route geometry, steps, intermediate stops and debug output removed; each keeps
the request in `_probe`. `feeds_at.json` and `feeds_eu.json` are Transitous's feed lists (`feeds/at.json`,
`feeds/eu.json`) read the same day.

- `geocode_*.json`: `v1/geocode?type=STOP` for Bad Ischl (first match a bus stop 4 km from the station), Hallstatt,
  Wien Hbf, Graz Hbf.
- `plan_wienhbf_badischl_1104.json`: RJX/IC + Postbus with changes; Postbus 546 sends headsign "0".
- `plan_badischl_stwolfgang_1104.json`: Postbus 546, headsign "0".
- `plan_wienhbf_badischl_1220.json`: a date after the 12-13 timetable change: no itineraries.
- `plan_klagenfurt_1104.json`: Klagenfurt, whose local buses are skipped: S4 out and back instead of a bus.
- `plan_flughafen_wienhbf_1104_2330.json`: VAB 1, REX and RJ after 23:30.
