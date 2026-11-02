"""Fake upstreams for A5, offline. A fictional MOTIS (Transitous) that answers like the real one in the shapes the
second quality judgment named: times in UTC ("Z"), each itinerary walking to the stop, riding, and walking on, so the
door-to-door start is not the train's departure; and an optional end of its data, so a search window can stop before
the answer's first choice. A fake web server for the quote check. Fictional places and lines only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from tripwright.providers.pages import PageReader
from tripwright.providers.transitous import TransitousProvider

ZONE = ZoneInfo("Europe/Oslo")
DAY = "2030-06-10"
ORIGIN = "60.1000,10.7000"  # coordinates: no stop lookup
DESTINATION = "60.2000,10.9000"
TRAINS = ("21:05", "21:35", "22:05", "22:35", "23:05", "23:35")  # every 30 minutes from Nordkai


def z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Timetable:
    """REX 7 from Nordkai to Fergeterminal: 5 minutes' walk to the platform, 20 minutes' ride, 3 minutes' walk on."""

    def __init__(self, trains=TRAINS, *, until: str | None = None, walk_before=5, ride=20, walk_after=3):
        self.trains = [datetime.fromisoformat(f"{DAY}T{clock}").replace(tzinfo=ZONE) for clock in trains]
        self.until = datetime.fromisoformat(f"{DAY}T{until}").replace(tzinfo=ZONE) if until else None
        self.walk_before, self.ride, self.walk_after = (timedelta(minutes=value) for value in (walk_before, ride, walk_after))
        self.requests: list[httpx.Request] = []

    def itinerary(self, train: datetime) -> dict:
        start, alight = train - self.walk_before, train + self.ride
        end = alight + self.walk_after
        return {"startTime": z(start), "endTime": z(end), "duration": int((end - start).total_seconds()), "legs": [
            {"mode": "WALK", "from": {"name": "START"}, "to": {"name": "Nordkai"}, "startTime": z(start), "endTime": z(train),
             "duration": int(self.walk_before.total_seconds())},
            {"mode": "REGIONAL_RAIL", "displayName": "REX 7", "headsign": "Fergeterminal", "agencyName": "Fjordbane (fictional)",
             "from": {"name": "Nordkai", "track": "2"}, "to": {"name": "Fergeterminal"}, "startTime": z(train), "endTime": z(alight)},
            {"mode": "WALK", "from": {"name": "Fergeterminal"}, "to": {"name": "END"}, "startTime": z(alight), "endTime": z(end),
             "duration": int(self.walk_after.total_seconds())}]}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("geocode"):
            return httpx.Response(200, json=[{"type": "STOP", "name": "Nordkai", "id": "fj_nordkai:1", "lat": 60.1, "lon": 10.7}])
        params = request.url.params
        moment = datetime.fromisoformat(params["time"])
        count = int(params["numItineraries"])
        trains = [train for train in self.trains if self.until is None or train <= self.until]
        if params["arriveBy"] == "true":
            chosen = [train for train in trains if train + self.ride + self.walk_after <= moment][-count:]
        else:
            chosen = [train for train in trains if train - self.walk_before >= moment][:count]
        return httpx.Response(200, json={"itineraries": [self.itinerary(train) for train in chosen], "direct": []})

    def provider(self, clock=None) -> TransitousProvider:
        """`clock` (returning an aware datetime) fixes the fetch time the evidence records."""
        return TransitousProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)), min_interval=0, clock=clock)


PAGES = {
    "https://ferry.example.org/rules": ("text/html; charset=utf-8",
                                        "<html><head><style>p{color:red}</style><script>var x='Counter closes';</script></head>"
                                        "<body><h1>Boarding</h1><p>The ferry counter closes <b>40&nbsp;minutes</b> before "
                                        "departure.</p></body></html>"),
    "https://ferry.example.org/moved": ("redirect", "https://ferry.example.org/rules"),
    "https://ferry.example.org/app": ("text/html", "<html><body><div id='root'></div><script>render()</script></body></html>"),
    "https://ferry.example.org/timetable.pdf": ("application/pdf", "%PDF-1.7"),
}


async def pages(request: httpx.Request) -> httpx.Response:
    kind, body = PAGES.get(str(request.url), ("missing", ""))
    if kind == "missing":
        return httpx.Response(404, text="not here")
    if kind == "redirect":
        return httpx.Response(302, headers={"location": body})
    return httpx.Response(200, headers={"content-type": kind}, text=body)


def page_reader() -> PageReader:
    """The real reader over the fake web; the public-address check needs DNS, so it is tested apart."""
    return PageReader(client=httpx.AsyncClient(transport=httpx.MockTransport(pages)), check_address=False)
