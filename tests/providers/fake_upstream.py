"""In-process fake upstream: never opens a socket."""

import asyncio

import httpx


class FakeUpstream:
    def __init__(self):
        self.mode = "ok"
        self.requests = []
        self.delay = 0
        self.plan_body = None
        self.walk_seconds = 1500

    async def handle(self, request):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("fake timeout", request=request)
        if self.mode in ("offline", "dns"):
            raise httpx.ConnectError("fake connection failure", request=request)
        if self.mode.isdigit():
            return httpx.Response(int(self.mode), text="fake error")
        if self.mode == "html":
            return httpx.Response(200, text="<html>")
        if self.mode == "garbage":
            return httpx.Response(200, json={"itineraries": [{"foo": 1}]})
        path = request.url.path
        if path.endswith("geocode"):
            body = [{"type": "STOP", "name": "Teststation", "id": "xx_test:1", "lat": 47.0, "lon": 13.0}]
        elif path.endswith("one-to-many"):
            body = [{"duration": self.walk_seconds} for _ in request.url.params["many"].split(",")]
        elif path.endswith("forecast"):
            day = request.url.params["start_date"]
            body = {"timezone": "Europe/Vienna", "hourly_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h", "precipitation_probability": "%"},
                    "hourly": {"time": [day + "T09:00", day + "T10:00"], "precipitation_probability": [20, 35],
                               "temperature_2m": [12.5, 13], "wind_speed_10m": [8, 10]}}
        elif path.endswith("search"):
            body = [{"display_name": "Testtown", "lat": "47.0", "lon": "13.0", "place_id": 42}]
        else:
            body = self.plan_body if self.plan_body is not None else {
                "itineraries": [{"startTime": "2026-10-28T08:00:00Z", "endTime": "2026-10-28T08:30:00Z", "duration": 1800,
                                 "legs": [{"mode": "BUS", "displayName": "1", "from": {"name": "A", "track": "2"},
                                           "to": {"name": "B"}, "startTime": "2026-10-28T08:00:00Z", "endTime": "2026-10-28T08:30:00Z", "duration": 1800}]}]}
        return httpx.Response(200, json=body)
