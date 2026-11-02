"""Cached Nominatim searches, serialized at no more than one request/second."""

from datetime import timedelta

from .base import Place
from .common import HTTPProvider, budgeted, validate_limit, validate_place

ATTRIBUTION = "Geocoding: Nominatim, © OpenStreetMap contributors (https://www.openstreetmap.org/copyright), ODbL."


class NominatimProvider(HTTPProvider):
    def __init__(self, base_url="https://nominatim.openstreetmap.org", **kwargs):
        kwargs["min_interval"] = max(1.0, kwargs.get("min_interval", 1.0))
        kwargs["concurrency"] = 1
        super().__init__("nominatim", base_url, attribution=ATTRIBUTION, **kwargs)

    @budgeted
    async def search(self, text: str, *, limit=5, fresh=False):
        validate_limit(limit)
        text = str(text).strip()
        if not text:
            raise ValueError("query must not be empty")
        params = {"q": text, "format": "jsonv2", "limit": limit}

        async def fetch():
            payload = await self.get_json("search", params)
            if not isinstance(payload, list):
                raise ValueError("Expected geocoding list")
            results = []
            for item in payload:
                place = Place(item["display_name"], float(item["lat"]), float(item["lon"]), str(item["place_id"]))
                validate_place(place)
                results.append(place)
            return results[:limit]

        return await self.cached("search", params, fetch, ttl=timedelta(days=7), retention=timedelta(days=30), fresh=fresh)
