import asyncio
import threading
from datetime import datetime, timedelta

import httpx
import pytest

from tripwright.checks import check_plan
from tripwright.providers.openmeteo import OpenMeteoProvider
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store


@pytest.fixture
def upstream_service(tmp_path, plan):
    state = dict(requests=[], mode="ok", delay=0, entered=threading.Event(), cancelled=threading.Event())

    async def upstream(request):
        state["requests"].append(request)
        state["entered"].set()
        if state["delay"]:
            try:
                await asyncio.sleep(state["delay"])
            except asyncio.CancelledError:
                state["cancelled"].set()
                raise
        if state["mode"] == "failure":
            return httpx.Response(503)
        if request.url.path.endswith("one-to-many"):
            return httpx.Response(200, json=[{"duration": 600} for _ in request.url.params["many"].split(",")])
        if request.url.path.endswith("forecast"):
            date = request.url.params["start_date"]
            return httpx.Response(200, json={"timezone": "Europe/Oslo", "hourly_units": {},
                "hourly": {"time": [date + "T11:00"], "precipitation_probability": [20], "temperature_2m": [14], "wind_speed_10m": [5]}})
        departure = datetime.fromisoformat(request.url.params["time"])
        if state["mode"] == "wrong-time":
            departure += timedelta(hours=1)
        arrival = departure + timedelta(minutes=30)
        if state["mode"] == "late-departure":
            departure += timedelta(minutes=5)
        return httpx.Response(200, json={"itineraries": [{"startTime": departure.isoformat(), "endTime": arrival.isoformat(),
            "legs": [{"mode": "RAIL", "displayName": "R1", "from": {"name": "Origin"}, "to": {"name": "Destination"},
                      "startTime": departure.isoformat(), "endTime": arrival.isoformat(), "cancelled": state["mode"] == "cancelled"}]}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    store = Store(tmp_path / "integration.sqlite3")
    router = QueryRouter(store, transit=TransitousProvider(client=client, min_interval=0),
                         weather=OpenMeteoProvider(client=client, min_interval=0))
    service = Tripwright(store, query_router=router)
    for place in plan["places"]:
        place["coords"] = [59.91, 10.75] if place["id"] != "oslo-cafe" else [59.92, 10.76]
    plan["days"][0]["plans"] = [{"name": "Recommended", "tracks": [{"subject": ["p1"], "blocks": [
        dict(id="origin", start="09:00", end="09:30", kind="flex", place="oslo-art"),
        dict(id="train", start="10:00", end="10:30", kind="travel", mode="rail", certainty="fixed", place="oslo-cafe", origin="oslo-art", line="R1"),
        dict(id="outdoor", start="11:00", end="12:00", kind="visit", place="oslo-park", transfer_minutes=10),
    ]}]}]
    plan["days"][0]["anchors"] = [dict(id="onward", time="15:00", place="oslo-art", destination="oslo-cafe", line="R1",
                                        subject=["p1"], text="Onward train", travel_minutes=30, buffer_minutes=15)]
    turn = service.turn("alice", "Create", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    yield service, state
    service.jobs.stop()
    asyncio.run(client.aclose())
    store.close()


def draft_current(service, key="check"):
    turn = service.turn("alice", "Check current", key)
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=service.state("alice")["plan"], idempotency_key=key)


def targeted_query(service, draft=None):
    target = dict(block_id="train", date="2030-06-10", fact="timetable")
    if draft:
        target["draft_id"] = draft["draft_id"]
    return asyncio.run(service.query("alice", "transit", {"target": target}))


def test_query_binds_transitous_fake_upstream_to_the_exact_plan_fact(upstream_service):
    service, state = upstream_service
    result = targeted_query(service)
    evidence = result["evidence"]
    assert evidence["facts"] == ["timetable"] and evidence["source"] == "transitous"
    assert evidence["fetched_at"] and evidence["valid_until"] and evidence["binding"]["block_id"] == "train"
    plan = service.state("alice")["plan"]
    block = plan["days"][0]["plans"][0]["tracks"][0]["blocks"][1]
    block["evidence_ids"] = [evidence["id"]]
    report = check_plan(service.store, "alice", plan)
    assert next(check for check in report["checks"] if check["id"].endswith(":train:timetable"))["status"] == "passed"
    block["start"] = "10:05"
    report = check_plan(service.store, "alice", plan)
    assert next(check for check in report["checks"] if check["id"].endswith(":train:timetable"))["status"] == "unverified"


def test_deep_job_requeries_transit_hard_node_and_weather_and_commit_reuses_evidence(upstream_service):
    service, state = upstream_service
    draft = draft_current(service)
    assert service.jobs.run_once("alice")
    result = service.result("alice", draft["job_id"])
    assert result["status"] == "completed", result
    report = result["result"]["value"]["checks"]
    for suffix in (":train:timetable", ":outdoor:weather", ":onward"):
        assert next(check for check in report["checks"] if check["id"].endswith(suffix))["status"] == "passed"
    assert next(check for check in report["checks"] if check["id"].endswith(":outdoor:opening_hours"))["status"] == "unverified"
    assert any(note.get("fact") == "opening_hours" and "No opening-hours provider" in note["reason"]
               for note in result["result"]["value"]["external_requery"])
    version = service.commit("alice", draft["draft_id"], "checked", accept_unverified=True)
    assert next(check for check in version["checks"]["checks"] if check["id"].endswith(":train:timetable"))["status"] == "passed"
    assert not version["checks"]["verified"]
    assert len(state["requests"]) == 3


def test_evidence_refresh_preserves_a_user_locked_block_choice(upstream_service):
    service, state = upstream_service
    block = service.state("alice")["plan"]["days"][0]["plans"][0]["tracks"][0]["blocks"][1]
    turn = service.turn("alice", "I choose this train.", "locked-train", nature="user_verbatim", native_ref="fixture:locked-train",
                        speaker="p1")
    decision = dict(question="Which train?", options=["R1", "Later"], choice="R1", decided_by="user", subject=["p1"],
                    source={"event": turn["receipt"]["event_id"], "basis": "user_explicit"}, locked_paths={"/blocks/train": block})
    locked = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                             changes={"decisions": [decision]}, idempotency_key="locked-train")
    service.commit("alice", locked["draft_id"], "locked-train", accept_unverified=True)
    draft = draft_current(service)
    service.jobs.run_once("alice")
    result = service.result("alice", draft["job_id"])
    assert result["status"] == "completed"
    assert not any(check["id"].startswith("locked:") for check in result["result"]["value"]["checks"]["conflicts"])
    service.commit("alice", draft["draft_id"], "refreshed-lock", accept_unverified=True)


@pytest.mark.parametrize("field,value", [("destination", "oslo-park"), ("line", "R2")])
def test_hard_node_route_cannot_be_silently_changed_after_provider_fields_are_added(upstream_service, field, value):
    service, state = upstream_service
    plan = service.state("alice")["plan"]
    plan["days"][0]["anchors"][0][field] = value
    turn = service.turn("alice", "Change route", "changed-route")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="changed-route")
    assert draft["status"] == "rejected"
    assert any(check["id"] == "preserved-anchor:onward" for check in draft["checks"]["conflicts"])


def test_synthetic_provider_cannot_verify_the_same_target(upstream_service):
    service, state = upstream_service
    service.query_router = QueryRouter(service.store, mode="synthetic")
    evidence = targeted_query(service)["evidence"]
    assert evidence["facts"] == [] and evidence["synthetic"]
    draft = draft_current(service)
    service.jobs.run_once("alice")
    report = service.result("alice", draft["job_id"])["result"]["value"]["checks"]
    assert next(check for check in report["checks"] if check["id"].endswith(":train:timetable"))["status"] == "unverified"
    assert state["requests"] == []


@pytest.mark.parametrize("mode", ["wrong-time", "late-departure", "cancelled", "failure"])
def test_nonmatching_or_failed_upstream_never_certifies_a_target(upstream_service, mode):
    service, state = upstream_service
    state["mode"] = mode
    result = targeted_query(service)
    assert result.get("evidence", {}).get("facts", []) == []
    if mode == "failure":
        assert result["status"] == "unverified" and result["reason"]


def test_target_cannot_launder_a_different_route_branch_date_or_owner(upstream_service):
    service, state = upstream_service
    target = dict(block_id="train", date="2030-06-10", fact="timetable")
    for arguments in ({"target": dict(target, place_id="oslo-park")}, {"target": target, "origin": "wrong"},
                      {"target": dict(target, date="2030-06-11")}):
        with pytest.raises(ValueError):
            asyncio.run(service.query("alice", "transit", arguments))
    draft = draft_current(service)
    with pytest.raises(ValueError, match="Unknown drafts"):
        asyncio.run(service.query("bob", "transit", {"target": dict(target, draft_id=draft["draft_id"])}))
    assert state["requests"] == []


def test_live_configuration_selects_real_adapters_without_network(service, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_PROVIDERS", "live")
    router = QueryRouter(service.store)
    assert isinstance(router.transit, TransitousProvider)
    assert isinstance(router.weather, OpenMeteoProvider)
    asyncio.run(router.aclose())


def day_request(key="day"):
    return dict(action="plan_day", idempotency_key=key, timeout=1, day={"day": "2030-06-10", "timezone": "Europe/Oslo",
        "start": {"id": "s", "at": "59.91,10.75", "time": "08:00"}, "end": {"id": "e", "at": "59.91,10.75", "by": "20:00"},
        "candidates": [{"id": "c", "at": "59.92,10.76", "priority": 2, "stay": 30}]})


def test_day_solver_runs_in_a_durable_job_and_deduplicates(upstream_service):
    service, state = upstream_service
    job = asyncio.run(service.query("alice", "transit", day_request()))
    assert job["status"] == "queued" and state["requests"] == []
    assert asyncio.run(service.query("alice", "transit", day_request()))["job_id"] == job["job_id"]
    assert service.jobs.run_once("alice")
    result = service.result("alice", job["job_id"])
    assert result["status"] == "completed", result
    assert result["result"]["value"]["plans"]
    assert service.state("alice")["number"] == 1


def test_running_day_job_can_be_cancelled_without_publishing(upstream_service):
    service, state = upstream_service
    state["delay"] = 10
    job = asyncio.run(service.query("alice", "transit", dict(day_request(), timeout=20)))
    service.jobs.start()
    assert state["entered"].wait(2)
    assert service.result("alice", job["job_id"], cancel=True)["status"] == "cancelled"
    assert state["cancelled"].wait(2)
    service.jobs.stop()
    assert service.store.all("inbox", "alice") == []
    assert service.state("alice")["number"] == 1


def test_day_job_timeout_returns_a_visible_reason(upstream_service):
    service, state = upstream_service
    state["delay"] = 10
    job = asyncio.run(service.query("alice", "transit", dict(day_request(), timeout=0.01)))
    service.jobs.run_once("alice")
    result = service.result("alice", job["job_id"])
    assert result["status"] in ("queued", "failed") and "SOLVER_TIMEOUT" in result["notice"]
    assert service.store.all("inbox", "alice") == []


def test_deep_network_does_not_hold_the_store_lock_and_stale_work_cannot_publish(upstream_service):
    service, state = upstream_service
    state["delay"] = 10
    draft = draft_current(service)
    service.jobs.start()
    assert state["entered"].wait(2)
    other = draft_current(service, "new-version")
    service.commit("alice", other["draft_id"], "new-version", accept_unverified=True)
    assert service.result("alice", draft["job_id"])["status"] == "stale"
    assert state["cancelled"].wait(2)
    service.jobs.stop()
    assert service.state("alice")["number"] == 2
    assert not any(message["job_id"] == draft["job_id"] for message in service.store.all("inbox", "alice"))


def test_deep_timeout_and_lease_renewal_are_visible_and_bounded(upstream_service):
    service, state = upstream_service
    state["delay"] = 0.05
    service.jobs.lease_seconds = 0.03
    draft = draft_current(service)
    service.jobs.run_once("alice")
    assert service.result("alice", draft["job_id"])["status"] == "completed"
    service.deep_timeout = 0.01
    second = draft_current(service, "deep-timeout")
    service.jobs.run_once("alice")
    result = service.result("alice", second["job_id"])
    assert result["status"] == "queued" and "Deep verification exceeded its time budget" in result["notice"]


def test_deep_requery_refreshes_cached_transit_and_cannot_keep_a_cancelled_train_passed(upstream_service):
    service, state = upstream_service
    evidence = targeted_query(service)["evidence"]
    assert evidence["facts"] == ["timetable"]
    plan = service.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][1]["evidence_ids"] = [evidence["id"]]
    turn = service.turn("alice", "Attach checked timetable", "attach")
    attached = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="attach")
    service.commit("alice", attached["draft_id"], "attach", accept_unverified=True)
    state["mode"] = "cancelled"
    draft = draft_current(service)
    service.jobs.run_once("alice")
    report = service.result("alice", draft["job_id"])["result"]["value"]["checks"]
    assert next(check for check in report["checks"] if check["id"].endswith(":train:timetable"))["status"] == "unverified"
    assert len(state["requests"]) == 4


def test_only_the_departure_time_differs_and_the_target_stays_unverified(upstream_service):
    service, state = upstream_service
    state["mode"] = "late-departure"
    result = targeted_query(service)
    legs = result["evidence"]["value"][0]["legs"]
    # stored in the trip's zone since A5 (the same instants as 08:05 and 08:30 UTC)
    assert legs[0]["depart"].endswith("10:05:00+02:00") and legs[-1]["arrive"].endswith("10:30:00+02:00")
    assert result["connections"][0]["boards"]["time"] == "10:05" and result["window"]["by"] == "departure"
    assert result["status"] == "unverified" and result["evidence"]["facts"] == []


def test_evidence_stops_passing_once_its_validity_ends(upstream_service):
    service, state = upstream_service
    evidence = targeted_query(service)["evidence"]
    plan = service.state("alice")["plan"]
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][1]["evidence_ids"] = [evidence["id"]]
    expiry = datetime.fromisoformat(evidence["valid_until"])

    def timetable(at):
        report = check_plan(service.store, "alice", plan, at=at.isoformat())
        return next(check for check in report["checks"] if check["id"].endswith(":train:timetable"))["status"]

    assert timetable(expiry - timedelta(seconds=60)) == "passed"
    assert timetable(expiry + timedelta(seconds=1)) == "unverified"


def test_synthetic_itinerary_matching_the_block_exactly_still_cannot_pass(upstream_service):
    from tripwright.evidence import snapshot, supports
    from tripwright.query import DemoTransit

    service, state = upstream_service
    plan = service.state("alice")["plan"]
    train = plan["days"][0]["plans"][0]["tracks"][0]["blocks"][1]
    train["line"] = None
    turn = service.turn("alice", "No line", "no-line")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="no-line")
    target = dict(block_id="train", date="2030-06-10", fact="timetable")
    synthetic = asyncio.run(DemoTransit().plan("a", "b", datetime.fromisoformat("2030-06-10T10:00:00+02:00")))
    assert supports(plan, target, "transit", synthetic.value)
    service.query_router = QueryRouter(service.store, mode="synthetic")
    evidence = asyncio.run(service.query("alice", "transit", {"target": dict(target, draft_id=draft["draft_id"])}))["evidence"]
    assert evidence["synthetic"] and evidence["facts"] == []
    forged = dict(evidence, id="EV-forged-synthetic", facts=["timetable"], binding=snapshot(plan, target))
    service.store.put("evidence", "alice", forged, insert=True)
    train["evidence_ids"] = [evidence["id"], forged["id"]]
    report = check_plan(service.store, "alice", plan)
    assert next(check for check in report["checks"] if check["id"].endswith(":train:timetable"))["status"] == "unverified"
