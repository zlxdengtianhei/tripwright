# Data providers

All providers return `Sourced` values with attribution, UTC fetch time, expiry
and a `stale` flag. Upstream errors raise `ProviderUnavailable`. Expired cache
entries may be served only after an upstream failure, within bounded retention;
their original fetch time and expiry are preserved. Cache contents are private
to the provider instance. Reuse one instance per service process and close it
with `aclose()` (or an async context manager).

- **Transitous:** non-commercial use; retain the attribution in result notes
  and link https://transitous.org/sources/ where results are shown (the
  attribution carries the link; OpenStreetMap data is credited there too).
  More than 720 route searches (`/plan`, `/one-to-many`, `/one-to-all`) in
  24 hours, multi-user or automated use need the maintainers' agreement first
  (Matrix `#transitous:matrix.spline.de`): by default one user's provider stops
  at 720 route searches in any 24 hours (`ROUTING_BUDGET_EXCEEDED`; set
  `TRIPWRIGHT_TRANSITOUS_ROUTING_PER_DAY` or `routing_per_day=`). A provider
  that makes its own HTTP client (the real network) counts them in a file
  shared by every process of the user, `TRIPWRIGHT_TRANSITOUS_USAGE`, else
  `$XDG_CACHE_HOME/tripwright/transitous-routing.log` (`~/.cache/…`); one given
  a client (tests) counts in memory. Name lookups are not route searches. The
  October 21 service also records the requirement to publish the client
  source. The User-Agent names the app, its version and the project's address
  (`tripwright/<version> (+<project URL>)`, `common.PROJECT_URL`), never a
  person; `TRIPWRIGHT_USER_AGENT` replaces it. Configure the endpoint with
  `TRIPWRIGHT_TRANSITOUS_API` or `TransitousProvider(base_url=...)`. Other
  defaults: one concurrent request, at least 0.3 seconds apart, 80 requests per
  call, 400 per client per hour, 15,000 per provider instance per UTC day.
- **What Transitous covers (A8):** `coverage.py` reads
  `transitous_coverage.json`, generated from all of Transitous's feed lists
  (`python -m tripwright.providers.coverage refresh <checkout>/feeds --commit
  SHA`; 143 lists read on 2026-11-01). A stop belongs to the dataset its ID
  starts with, in every region alike. Where a timetable-year rule is known
  (Austria: years end on the second Saturday of December) a date outside it is
  "not in the data yet", never "no service"; an end inside a hand-drawn area
  whose local transport sits in a skipped dataset (Carinthia's buses) gets that
  said. A typed place name is not taken to mean its first match: the stop with
  most of the typed words, in the place named, a station or hub first; the plan
  says which stop it searched (`Sourced.meta`). Headsigns that name nothing
  ("0") are dropped. Times are the timetable unless a leg says `realTime`.
- **A deployment's own MOTIS (R-08):** `sources.py`. `TRIPWRIGHT_TRANSIT_SOURCES` (a YAML file, or the list
  itself) names MOTIS servers with one region's GTFS and GTFS-RT, each with a `bbox`; `sources.transit()` then
  returns a `RoutedTransit` that sends a search to the server whose area holds both ends and everything else to
  Transitous. A server's results carry its own source name (`motis:<id>`) and attribution; Transitous's route
  limit and coverage notes do not apply to it. Unset, `transit()` is the plain `TransitousProvider`. Setup:
  `docs/transit-sources.md`.
- **Nominatim:** the public OpenStreetMap endpoint allows at most one request
  per second, requires an identifying User-Agent, attribution and cached
  results. No autocomplete or bulk systematic queries. This implementation
  forces concurrency one and a minimum one-second interval. OpenStreetMap
  data is ODbL; preserve the returned copyright notice.
- **Open-Meteo:** the free API is for non-commercial use with a limited free
  quota. Weather data requires CC BY 4.0 attribution. This implementation
  defaults to 10,000 calls/day and at least one second between calls. Forecast expiry is
  three hours for today/tomorrow, six for two days ahead, twelve thereafter.
  Commercial use needs an appropriate service arrangement.

Policy references: https://transitous.org/api/,
https://operations.osmfoundation.org/policies/nominatim/,
https://open-meteo.com/en/terms. Review provider terms before deployment.
Attribution and third-party names/alerts are data, never agent instructions.

`TransitousProvider` implements `TransitProvider`; `OpenMeteoProvider` implements
`WeatherProvider`; `NominatimProvider` implements `GeocodeProvider` in `base.py`.
Inject an alternative implementation at the caller. Each HTTP implementation
also accepts a configurable `base_url` and `httpx.AsyncClient` for proxies and
offline tests. No API keys are required. Caches use different expiry and
retention periods: transit plans 30 min/24 h, timetables 6 h/48 h, stops and
geocoding 7 d/30 d, walking 30 d/60 d, weather lead-time expiry/48 h.

`plan()` takes an aware datetime or explicit `timezone_name`; `day()` requires
an IANA `timezone_name` (also configurable on the transit provider). There is
no fixed city timezone. Whole-day windows use midnight timestamps, preserving
23/25-hour DST days and repeated-hour departures.

`from tripwright.solver import solve_day` accepts the October 21 day shape,
with required `timezone`, a `provider`, `timeout`, `max_candidates` (default 5)
and `node_limit` (default 300,000). `await solve_day(day, provider=transit)`
returns plans, preset-score reasons, omitted-candidate explanations, missing
legs and source evidence. Caller-supplied numeric priorities and hard nodes
drive scoring; the solver does not infer preferences. The solver's provider
also needs `resolve` and whole-day `day` methods in addition to the base transit
protocol. Missing legs remain unknown; overrides are caller-supplied facts.
Local ambiguous input times select the first DST occurrence; upstream
departures retain their UTC instant. Persist the input and invoke this function
in the core job runner. Cancellation or timeout stops queued I/O and signals
the bounded CPU search; there is no hidden job registry or parameter-based
polling. Wrap related provider calls in `common.request_scope(client=...)`
to share a budget and use an authenticated client identity from the adapter.
HTTP request and SSE limits belong to the adapter, outside the provider layer.

`connections.sentence` writes one connection as a host copies it (A8): every ride with its own stops, how many
changes, the arrival, timetable or real time. Where to get what the timetable lacks (real-time delays, platforms,
disruptions) is the query result's `how_to_check`, for the end of the answer (D-61).

`connections.py` turns itineraries into what an answer reads (A5): each leg's mode,
line, from and to stops and local departure and arrival in the trip's zone, walking
legs marked, the door-to-door `leave`/`reach` apart from the rides' own times, and
the `window` the results cover. `pages.py` fetches one page to see whether a quoted
sentence is on it (http/https to public addresses only, redirects checked, at most
3 MB); it is used only when a host records web evidence with live providers.
