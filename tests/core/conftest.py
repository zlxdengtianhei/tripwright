from copy import deepcopy

import pytest

from tripwright.demo import example
from tripwright.service import Tripwright
from tripwright.storage import Store, now


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "trip.sqlite3")
    service = Tripwright(store)
    yield service
    service.jobs.stop()
    store.close()


@pytest.fixture
def plan():
    result = deepcopy(example()["trip"])
    result["days"] = [result["days"][0]]
    result["bookings"] = []
    return result


@pytest.fixture
def seeded(service, plan):
    for day in plan["days"]:
        for candidate in day["plans"]:
            for track in candidate["tracks"]:
                for block in track["blocks"]:
                    if block["kind"] not in ("visit", "meal"):
                        continue
                    evidence_id = f"EV-fixture-{block['id']}"
                    service.store.put("evidence", "alice", dict(id=evidence_id, source="fixture:first-hand", fetched_at=now(),
                        valid_until="2100-01-01T00:00:00+00:00", stale=False, date=day["date"], place_id=block["place"], facts=["opening_hours"]), insert=True)
                    block["evidence_ids"] = [evidence_id]
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed")
    return service


@pytest.fixture
def source(service):
    receipt = service.events.receive("alice", "Prefer a relaxed pace. Maximum 60 minutes walking. Maximum 2 visits. Prefer art. Prefer parks.",
                                     "source", nature="user_verbatim", native_ref="fixture:source")
    return {"event": receipt["event_id"], "basis": "user_explicit"}


@pytest.fixture
def item(source):
    return dict(kind="preference", key="pace", quote="Prefer a relaxed pace.", text="Prefer a relaxed pace.",
                subject=["p1"], strength="prefer", source=source)
