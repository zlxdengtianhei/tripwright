"""Open-Meteo hourly forecasts with lead-time dependent expiry."""

from datetime import date as Date, timedelta

from .base import Place
from .common import HTTPProvider, budgeted, validate_place

ATTRIBUTION = "Weather data: Open-Meteo (https://open-meteo.com/), CC BY 4.0."


class OpenMeteoProvider(HTTPProvider):
    def __init__(self, base_url="https://api.open-meteo.com", **kwargs):
        kwargs.setdefault("daily_cap", 10000)
        kwargs.setdefault("min_interval", 1.0)
        super().__init__("open-meteo", base_url, attribution=ATTRIBUTION, **kwargs)

    @budgeted
    async def day(self, place: Place, date: str, *, fresh=False):
        validate_place(place)
        target = Date.fromisoformat(date)
        params = {"latitude": place.lat, "longitude": place.lon, "start_date": date, "end_date": date,
                  "timezone": "auto", "hourly": "precipitation_probability,temperature_2m,wind_speed_10m"}

        async def fetch():
            payload = await self.get_json("v1/forecast", params)
            hourly = payload["hourly"]
            times = hourly["time"]
            keys = ("precipitation_probability", "temperature_2m", "wind_speed_10m")
            if not times or any(len(hourly[key]) != len(times) for key in keys):
                raise ValueError("Incomplete hourly forecast")
            if any(not stamp.startswith(date + "T") for stamp in times):
                raise ValueError("Forecast covers a different date")
            return {"date": date, "latitude": place.lat, "longitude": place.lon,
                    "timezone": payload["timezone"], "hourly_units": payload["hourly_units"],
                    "hourly": [{"time": stamp, **{key: hourly[key][index] for key in keys}}
                               for index, stamp in enumerate(times)]}

        lead = (target - self.clock().date()).days
        ttl = timedelta(hours=3 if lead <= 1 else 6 if lead <= 2 else 12)
        return await self.cached("day", params, fetch, ttl=ttl, retention=timedelta(days=2), fresh=fresh)
