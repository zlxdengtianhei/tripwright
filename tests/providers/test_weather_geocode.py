import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tripwright.providers.base import Place, ProviderUnavailable
from tripwright.providers.nominatim import NominatimProvider
from tripwright.providers.openmeteo import OpenMeteoProvider

from .fake_upstream import FakeUpstream


@pytest.fixture(params=["weather", "geocode"])
def setup(request):
    fake = FakeUpstream()
    clock = [datetime(2026, 10, 28, tzinfo=timezone.utc)]
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    cls = OpenMeteoProvider if request.param == "weather" else NominatimProvider
    provider = cls(client=client, clock=lambda: clock[0], timeout=0.05)
    def query(**kwargs):
        return provider.day(Place("Town", 47, 13), "2026-10-31", **kwargs) if request.param == "weather" else provider.search("Town", **kwargs)
    yield provider, fake, clock, query
    asyncio.run(client.aclose())


def test_normal(setup):
    provider, fake, _, query = setup
    result = asyncio.run(query())
    assert result.source == provider.source and result.fetched_at.tzinfo and result.notes
    assert fake.requests[0].headers["user-agent"].startswith("tripwright")
    if provider.source == "open-meteo":
        assert result.value["hourly"][0]["precipitation_probability"] == 20
        assert result.value["hourly"][0]["temperature_2m"] == 12.5
        assert result.value["hourly"][0]["wind_speed_10m"] == 8
        assert result.valid_until - result.fetched_at == timedelta(hours=12)
    else:
        assert result.value == [Place("Testtown", 47, 13, "42")]


def test_timeout(setup):
    _, fake, _, query = setup
    fake.delay = 0.2
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_TIMEOUT"):
        asyncio.run(query())


def test_upstream_5xx(setup):
    _, fake, _, query = setup
    fake.mode = "503"
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_ERROR"):
        asyncio.run(query())


def test_offline_returns_old_cache(setup):
    provider, fake, clock, query = setup
    first = asyncio.run(query())
    clock[0] += timedelta(hours=14) if provider.source == "open-meteo" else timedelta(days=8)
    fake.mode = "offline"
    second = asyncio.run(query())
    assert second.stale and second.value == first.value
    assert second.fetched_at == first.fetched_at and second.valid_until < clock[0]


def test_cache_hit(setup):
    _, fake, _, query = setup
    asyncio.run(query())
    asyncio.run(query())
    assert len(fake.requests) == 1


def test_malformed_payload_is_unavailable(setup):
    _, fake, _, query = setup
    fake.mode = "garbage"
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_ERROR"):
        asyncio.run(query())


def test_cache_beyond_retention_fails(setup):
    _, fake, clock, query = setup
    asyncio.run(query())
    clock[0] += timedelta(days=31)
    fake.mode = "offline"
    with pytest.raises(ProviderUnavailable):
        asyncio.run(query())


def test_weather_today_has_three_hour_expiry():
    fake = FakeUpstream()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
            provider = OpenMeteoProvider(client=client, clock=lambda: datetime(2026, 10, 31, tzinfo=timezone.utc))
            result = await provider.day(Place("Town", 47, 13), "2026-10-31")
            assert result.valid_until - result.fetched_at == timedelta(hours=3)
    asyncio.run(run())


def test_nominatim_enforces_one_request_per_second():
    async def run():
        stamps = []
        async def handle(request):
            stamps.append(asyncio.get_running_loop().time())
            return httpx.Response(200, json=[])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = NominatimProvider(client=client, min_interval=0, concurrency=4)
            await asyncio.gather(provider.search("A"), provider.search("B"))
            assert stamps[1] - stamps[0] >= 1 and provider.gate.free == 1
    asyncio.run(run())
