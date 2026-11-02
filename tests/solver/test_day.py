import asyncio
import copy
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest

from tripwright.providers.base import ProviderUnavailable
from tripwright.providers.transitous import TransitousProvider
from tripwright.solver import ServiceError, Solver, apply_overrides, prepare, solve_day


def day(count=2):
    return {"day": "2026-10-28", "timezone": "Europe/Vienna",
            "start": {"id": "s", "at": "47,13", "time": "08:00"},
            "end": {"id": "e", "at": "47,13", "by": "20:00"},
            "candidates": [{"id": f"c{index}", "at": f"47.{index},13.{index}", "priority": 2, "stay": 60} for index in range(count)]}


@pytest.fixture
def setup():
    state = {"hits": 0, "delay": 0, "error": False}
    async def handle(request):
        state["hits"] += 1
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        if state["error"]:
            return httpx.Response(503)
        if request.url.path.endswith("one-to-many"):
            return httpx.Response(200, json=[{"duration": 600} for _ in request.url.params["many"].split(",")])
        return httpx.Response(200, json=[])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = TransitousProvider(client=client, min_interval=0)
    yield provider, state
    asyncio.run(client.aclose())


def test_too_many_candidates_rejected_before_upstream(setup):
    provider, state = setup
    with pytest.raises(ServiceError, match="TOO_MANY_CANDIDATES"):
        asyncio.run(solve_day(day(6), provider=provider))
    assert state["hits"] == 0


def test_plan_day_works_with_fake_upstream(setup):
    provider, _ = setup
    result = asyncio.run(solve_day(day(), provider=provider))
    assert result["plans"] and result["nodes"] and result["evidence"]
    assert all(plan["reasons"] and "missing_data" in plan for plan in result["plans"])


def test_bad_day_inputs_rejected_before_upstream(setup):
    provider, state = setup
    patches = [{"candidates": [{"id": "a", "at": "47,13", "priority": 2, "stay": 30, "open": ["09:00"]}]},
               {"start": {"id": "s", "at": "47,13", "time": "xx"}}, {"day": "9999-12-31"},
               {"overrides": [{"from": "s", "to": "e", "deps": [[]]}]},
               {"overrides": [{"from": "nope", "to": "e", "minutes": 5}]}]
    for patch in patches:
        with pytest.raises(ServiceError, match="INVALID_INPUT"):
            asyncio.run(solve_day({**day(), **patch}, provider=provider))
    assert state["hits"] == 0


def test_priority_accepts_numeric_strings(setup):
    provider, _ = setup
    payload = day(1)
    payload["candidates"][0]["priority"] = "3"
    assert asyncio.run(solve_day(payload, provider=provider))["plans"]


def test_timeout_cancels_requests_instead_of_creating_hidden_job(setup):
    provider, state = setup
    state["delay"] = 0.2
    async def run():
        with pytest.raises(ServiceError, match="SOLVER_TIMEOUT"):
            await solve_day(day(), provider=provider, timeout=0.01)
        hits = state["hits"]
        await asyncio.sleep(0.05)
        assert state["hits"] == hits and provider.gate.free == 1
    asyncio.run(run())


def test_explicit_cancellation_stops_job(setup):
    provider, state = setup
    state["delay"] = 0.2
    async def run():
        task = asyncio.create_task(solve_day(day(), provider=provider))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.gate.free == 1
    asyncio.run(run())


def test_sibling_requests_stop_after_failure(setup):
    provider, state = setup
    state["error"] = True
    async def run():
        with pytest.raises(ProviderUnavailable):
            await solve_day(day(4), provider=provider)
        hits = state["hits"]
        await asyncio.sleep(0.01)
        assert state["hits"] == hits
    asyncio.run(run())


def test_override_from_end_rejected_before_upstream(setup):
    provider, state = setup
    payload = {**day(), "overrides": [{"from": "e", "to": "c0", "minutes": 5}]}
    with pytest.raises(ServiceError, match="INVALID_INPUT"):
        asyncio.run(solve_day(payload, provider=provider))
    assert state["hits"] == 0


def test_candidate_without_name_shows_its_id(setup):
    provider, _ = setup
    result = asyncio.run(solve_day(day(), provider=provider))
    assert all(isinstance(step["name"], str) for plan in result["plans"] for step in plan["timeline"])


def test_out_of_range_numbers_rejected(setup):
    provider, state = setup
    for patch in [{"start": {"id": "s", "at": "47,13", "time": "99:99"}},
                  {"overrides": [{"from": "s", "to": "c0", "minutes": -600}]}]:
        with pytest.raises(ServiceError, match="INVALID_INPUT"):
            asyncio.run(solve_day({**day(), **patch}, provider=provider))
    assert state["hits"] == 0


def test_prepare_does_not_mutate_inputs():
    payload = day()
    before = copy.deepcopy(payload)
    prepare(payload)
    assert payload == before


def test_hard_node_and_opening_window_are_enforced():
    payload = day(1)
    payload["candidates"][0].update(priority="must", open=["10:00", "11:00"])
    prepared = prepare(payload)
    matrix = {"s": {"c0": {"walk": 10}, "e": {"walk": 10}}, "c0": {"e": {"walk": 10}}}
    plans = Solver(prepared, matrix).solve()
    assert plans and all("c0" in plan[2] for plan in plans)
    assert plans[0][3][1]["start"] == 600


def test_missing_data_is_not_a_feasible_hard_node():
    payload = day(1)
    payload["candidates"][0]["priority"] = "must"
    solver = Solver(prepare(payload), {"s": {"e": {"walk": 1}}})
    assert solver.solve() == [] and solver.diagnose()


def test_feasible_indirect_return_is_not_pruned():
    payload = day(2)
    for candidate in payload["candidates"]:
        candidate["priority"] = "must"
    matrix = {"s": {"c0": {"walk": 5}}, "c0": {"c1": {"walk": 5}}, "c1": {"e": {"walk": 5}}}
    plans = Solver(prepare(payload), matrix).solve()
    assert plans and plans[0][2] == ["c0", "c1"]


def test_cpu_solver_is_cancellable_and_bounded():
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ServiceError, match="SOLVER_TIMEOUT"):
        Solver(prepare(day()), {}, cancelled=cancelled).solve()
    with pytest.raises(ServiceError, match="SOLVER_LIMIT"):
        Solver(prepare(day()), {}, node_limit=0).solve()


def test_overrides_copy_matrix_and_add_departures():
    prepared = prepare({**day(0), "overrides": [{"from": "s", "to": "e", "deps": [["09:00", "09:30", "Ferry"]]}]})
    matrix = {"s": {"e": None}}
    result = apply_overrides(prepared, matrix)
    assert matrix["s"]["e"] is None and result["s"]["e"]["deps"][0][2] == "Ferry"


def test_missing_timezone_rejected_before_upstream(setup):
    provider, state = setup
    payload = day()
    del payload["timezone"]
    with pytest.raises(ServiceError, match="INVALID_INPUT"):
        asyncio.run(solve_day(payload, provider=provider))
    assert state["hits"] == 0


def test_nonexistent_dst_time_is_rejected():
    payload = day(0)
    payload["day"] = "2026-03-29"
    payload["start"]["time"] = "02:30"
    with pytest.raises(ServiceError, match="does not exist"):
        prepare(payload)


def test_override_arrival_before_departure_is_rejected(setup):
    provider, state = setup
    payload = {**day(), "overrides": [{"from": "s", "to": "c0", "deps": [["10:00", "09:00"]]}]}
    with pytest.raises(ServiceError, match="arrival precedes"):
        asyncio.run(solve_day(payload, provider=provider))
    assert state["hits"] == 0


def test_fall_back_solver_measures_real_elapsed_minutes():
    payload = day(0)
    payload["day"] = "2026-10-25"
    payload["start"]["time"] = "01:00"
    payload["end"]["by"] = "03:00"
    solver = Solver(prepare(payload), {"s": {"e": {"walk": 150}}})
    assert solver.end_by - solver.t0 == 180
    assert solver.solve()


def test_before_constraint_and_candidate_reasons(setup):
    provider, _ = setup
    payload = day(2)
    payload["candidates"][0].update(priority="must", before="c1")
    payload["candidates"][1]["priority"] = "must"
    result = asyncio.run(solve_day(payload, provider=provider))
    assert result["plans"][0]["order"] == ["c0", "c1"]
    assert all(candidate["reason"] and "missing_data" in candidate for candidate in result["candidates"].values())
