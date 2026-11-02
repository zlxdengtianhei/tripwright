import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from tripwright.providers.base import Place, ProviderUnavailable
from tripwright.providers.common import PriorityGate, gather_cancel, request_scope
from tripwright.providers.transitous import TransitousProvider, day_window

from .fake_upstream import FakeUpstream

WHEN = datetime(2026, 10, 28, 9, tzinfo=ZoneInfo("Europe/Vienna"))


@pytest.fixture
def setup():
    fake = FakeUpstream()
    clock = [datetime(2026, 10, 28, tzinfo=timezone.utc)]
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    provider = TransitousProvider(client=client, min_interval=0, clock=lambda: clock[0])
    yield provider, fake, clock
    asyncio.run(client.aclose())


def plan(provider, **kwargs):
    return provider.plan("47,13", "47.1,13.1", WHEN, **kwargs)


def test_normal_plan(setup):
    provider, fake, _ = setup
    result = asyncio.run(plan(provider))
    assert result.value[0].depart.hour == 9
    assert result.value[0].legs[0].line == "1"
    assert result.value[0].legs[0].platform == "2"
    assert result.source == "transitous" and result.notes
    assert result.fetched_at.tzinfo and result.valid_until > result.fetched_at
    assert "AIRPLANE" not in fake.requests[0].url.params["transitModes"]


def test_cache_hit_no_second_upstream_call(setup):
    provider, fake, _ = setup
    first = asyncio.run(plan(provider))
    count = len(fake.requests)
    second = asyncio.run(plan(provider))
    assert len(fake.requests) == count and second.fetched_at == first.fetched_at


def test_timezone_changes_local_times(setup):
    provider, _, _ = setup
    result = asyncio.run(plan(provider, timezone_name="Asia/Shanghai"))
    assert result.value[0].depart.hour == 16


@pytest.mark.parametrize("mode", ["offline", "dns"])
def test_offline_connections(setup, mode):
    provider, fake, _ = setup
    fake.mode = mode
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_UNREACHABLE"):
        asyncio.run(plan(provider))


def test_upstream_timeout(setup):
    provider, fake, _ = setup
    fake.mode = "timeout"
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_TIMEOUT"):
        asyncio.run(plan(provider))


@pytest.mark.parametrize("mode,code", [("503", "UPSTREAM_ERROR"), ("429", "UPSTREAM_BUSY"), ("400", "UPSTREAM_REJECTED"), ("html", "UPSTREAM_ERROR")])
def test_upstream_error_codes(setup, mode, code):
    provider, fake, _ = setup
    fake.mode = mode
    with pytest.raises(ProviderUnavailable, match=code):
        asyncio.run(plan(provider))


def test_cache_expired_refetches_when_upstream_up(setup):
    provider, fake, clock = setup
    asyncio.run(plan(provider))
    clock[0] += timedelta(hours=2)
    asyncio.run(plan(provider))
    assert len(fake.requests) == 2


def test_cache_expired_upstream_down_serves_stale_with_note(setup):
    provider, fake, clock = setup
    first = asyncio.run(plan(provider))
    clock[0] += timedelta(hours=2)
    fake.mode = "503"
    result = asyncio.run(plan(provider))
    assert result.stale and result.fetched_at == first.fetched_at and result.valid_until < clock[0]
    assert any("UPSTREAM_ERROR" in note for note in result.notes)


def test_cache_too_old_upstream_down_is_error(setup):
    provider, fake, clock = setup
    asyncio.run(plan(provider))
    clock[0] += timedelta(days=3)
    fake.mode = "offline"
    with pytest.raises(ProviderUnavailable):
        asyncio.run(plan(provider))


def test_bad_inputs(setup):
    provider, fake, _ = setup
    for origin, when in [("99,13", WHEN), ("47,13", "tomorrow"), ("47,13", datetime(2026, 10, 28))]:
        with pytest.raises(ValueError):
            asyncio.run(provider.plan(origin, "47,13", when))
    assert not fake.requests


def test_call_budget_exceeded(setup):
    provider, _, _ = setup
    provider.call_budget = 1
    with pytest.raises(ProviderUnavailable, match="CALL_BUDGET_EXCEEDED"):
        asyncio.run(provider.plan("Name A", "Name B", WHEN))


def test_client_hourly_budget(setup):
    provider, _, _ = setup
    provider.hourly_budget = 2
    with pytest.raises(ProviderUnavailable, match="RATE_LIMITED"):
        asyncio.run(provider.plan("Name A", "Name B", WHEN))


def test_daily_budget(setup):
    provider, _, _ = setup
    provider.daily_cap = 1
    asyncio.run(plan(provider))
    with pytest.raises(ProviderUnavailable, match="SERVICE_BUDGET_EXCEEDED"):
        asyncio.run(plan(provider, fresh=True))


def test_sibling_requests_stop_after_first_upstream_error(setup):
    provider, fake, _ = setup
    fake.mode = "503"
    async def run():
        with pytest.raises(ProviderUnavailable):
            await gather_cancel(*(plan(provider, fresh=True) for _ in range(6)))
        count = len(fake.requests)
        await asyncio.sleep(0.01)
        assert len(fake.requests) == count
    asyncio.run(run())


@pytest.mark.parametrize("day,hours", [(date(2026, 10, 25), 25), (date(2026, 3, 29), 23)])
def test_dst_day_window(day, hours):
    _, extra = day_window(day, ZoneInfo("Europe/Vienna"))
    assert extra["searchWindow"] == hours * 3600


def test_day_request_uses_dst_window(setup):
    provider, fake, _ = setup
    asyncio.run(provider.day("47,13", "47.1,13.1", "2026-10-25", timezone_name="Europe/Vienna"))
    assert fake.requests[0].url.params["searchWindow"] == "90000"


def test_light_query_not_stuck_behind_queued_job_requests():
    async def run():
        gate = PriorityGate()
        await gate.acquire()
        order = []
        async def enter(name, priority):
            async with gate(priority):
                order.append(name)
        background = asyncio.create_task(enter("background", 1))
        await asyncio.sleep(0)
        light = asyncio.create_task(enter("light", 0))
        await asyncio.sleep(0)
        gate.release()
        await asyncio.gather(background, light)
        assert order == ["light", "background"] and gate.free == 1
    asyncio.run(run())


def test_cancelled_gate_waiter_returns_slot():
    async def run():
        gate = PriorityGate()
        await gate.acquire()
        task = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        gate.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert gate.free == 1
    asyncio.run(run())


def test_garbage_upstream_data_is_provider_error(setup):
    provider, fake, _ = setup
    fake.mode = "garbage"
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_ERROR"):
        asyncio.run(plan(provider))
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_ERROR"):
        asyncio.run(provider.stops("Foo"))


def test_stops_resolve_and_walk(setup):
    provider, _, _ = setup
    assert asyncio.run(provider.stops("Foo")).value[0].id == "xx_test:1"
    assert asyncio.run(provider.resolve("Foo")).value["label"] == "Teststation"
    assert asyncio.run(provider.walk_minutes(Place("A", 47, 13), [Place("B", 47.1, 13.1)])).value == [25]


def test_user_agent_from_environment(monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_USER_AGENT", "tripwright-test (+https://example.org/contact)")
    fake = FakeUpstream()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
            provider = TransitousProvider(client=client, min_interval=0)
            await plan(provider)
            assert fake.requests[0].headers["user-agent"].startswith("tripwright-test")
    asyncio.run(run())


def test_fresh_4xx_does_not_serve_cache(setup):
    provider, fake, _ = setup
    asyncio.run(plan(provider))
    fake.mode = "400"
    with pytest.raises(ProviderUnavailable, match="UPSTREAM_REJECTED"):
        asyncio.run(plan(provider, fresh=True))


def test_budget_scope_separates_clients(setup):
    provider, _, _ = setup
    provider.hourly_budget = 1
    async def run():
        for identity in ("one", "two"):
            with request_scope(client=identity):
                await plan(provider, fresh=True)
    asyncio.run(run())


def test_direct_walk_normalization(setup):
    provider, fake, _ = setup
    asyncio.run(plan(provider))
    fake.plan_body = {"itineraries": [], "direct": [{"duration": 1200, "legs": [{"mode": "WALK"}]}]}
    result = asyncio.run(plan(provider, fresh=True))
    assert result.value == [] and any("Direct walking: 20 minutes" == note for note in result.notes)


def test_invalid_walking_limit_does_not_query_stops(setup):
    provider, fake, _ = setup
    with pytest.raises(ValueError):
        asyncio.run(provider.plan("Name A", "Name B", WHEN, walk_min=0))
    assert not fake.requests


def test_repeated_hour_departures_are_not_deduplicated(setup):
    provider, fake, _ = setup
    itineraries = []
    for hour in (0, 1):
        start, end = f"2026-10-25T0{hour}:10:00Z", f"2026-10-25T0{hour}:30:00Z"
        itineraries.append({"startTime": start, "endTime": end,
                            "legs": [{"mode": "BUS", "displayName": "1", "from": {"name": "A"}, "to": {"name": "B"},
                                      "startTime": start, "endTime": end}]})
    fake.plan_body = {"itineraries": itineraries}
    result = asyncio.run(provider.day("47,13", "47.1,13.1", "2026-10-25", timezone_name="Europe/Vienna"))
    assert len(result.value) == 2
    assert [item.depart.hour for item in result.value] == [2, 2]
    assert result.value[0].depart.utcoffset() != result.value[1].depart.utcoffset()


def test_cache_values_cannot_be_mutated_by_callers(setup):
    provider, _, _ = setup
    first = asyncio.run(plan(provider))
    first.value.clear()
    assert asyncio.run(plan(provider)).value


def test_empty_transit_results_widen_walking(setup):
    provider, fake, _ = setup
    fake.plan_body = {"itineraries": []}
    result = asyncio.run(plan(provider))
    assert len(fake.requests) == 2 and not result.value
    assert fake.requests[1].url.params["maxPreTransitTime"] == "3600"
    assert any("widened" in note for note in result.notes)
