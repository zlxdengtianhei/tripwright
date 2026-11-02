import pytest

from tripwright.jobs import Jobs
from tripwright.service import Tripwright
from tripwright.storage import Store


def test_restart_recovers_queued_job_and_idempotency_does_not_execute_twice(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    store = Store(path)
    jobs = Jobs(store)
    first = jobs.enqueue("alice", "work", {"answer": 42}, 0, "once")
    assert jobs.enqueue("alice", "work", {"answer": 42}, 0, "once")["id"] == first["id"]
    store.close()
    reopened = Store(path)
    executed = []
    restarted = Jobs(reopened, {"work": lambda owner, payload: executed.append(payload) or payload})
    assert restarted.run_once("alice")
    assert not restarted.run_once("alice")
    assert len(executed) == 1
    retrieved = restarted.result("alice", first["id"])
    assert retrieved["result"]["state"] == "retrieved"
    assert retrieved["result"]["value"] == {"answer": 42}
    assert restarted.result("alice", first["id"])["result"]["retrieved_at"] == retrieved["result"]["retrieved_at"]
    reopened.close()


def test_expired_lease_is_reclaimed_and_old_worker_is_fenced(service):
    jobs = Jobs(service.store, lease_seconds=10)
    job = jobs.enqueue("alice", "work", {}, 0, "lease")
    first = jobs.claim("alice", at=100)
    assert jobs.claim("alice", at=105) is None
    second = jobs.claim("alice", at=111)
    assert first["lease_token"] != second["lease_token"]
    assert not jobs.finish("alice", job["id"], first["lease_token"], {"old": True}, at=112)
    assert jobs.finish("alice", job["id"], second["lease_token"], {"new": True}, at=112)
    assert jobs.result("alice", job["id"])["result"]["value"] == {"new": True}


def test_old_running_job_result_cannot_overwrite_new_version(seeded):
    job = seeded.jobs.enqueue("alice", "work", {}, 1, "old")
    claimed = seeded.jobs.claim("alice")
    turn = seeded.turn("alice", "Keep current plan but create a new revision.", "new")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="new")
    new_version = seeded.commit("alice", draft["draft_id"], "new")
    assert not seeded.jobs.finish("alice", job["id"], claimed["lease_token"], {"plan": "stale"})
    assert seeded.result("alice", job["id"])["status"] == "stale"
    # commit now also returns its write state (CONTRACTS 2.2); the version itself is what state shows, read at `now` (A2)
    shown = seeded.state("alice")
    assert shown.pop("now")["timezone"] == new_version["plan"]["timezone"]
    assert shown == {key: value for key, value in new_version.items() if key not in ("accepted", "write")}
    assert all(message["job_id"] != job["id"] for message in seeded.store.all("inbox", "alice"))


def test_already_generated_result_becomes_stale_after_commit(seeded):
    job = seeded.jobs.enqueue("alice", "work", {}, 1, "old-completed")
    seeded.jobs.handlers["work"] = lambda owner, payload: {"checked": True}
    seeded.jobs.run_once("alice")
    assert seeded.store.all("inbox", "alice")[-1]["state"] == "available"
    turn = seeded.turn("alice", "New revision", "new")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="new")
    seeded.commit("alice", draft["draft_id"], "new")
    assert seeded.result("alice", job["id"])["status"] == "stale"


def test_failed_job_retries_and_stops_at_limit(service):
    executed = []

    def failure(owner, payload):
        executed.append(payload)
        raise RuntimeError("temporary source failure")

    jobs = Jobs(service.store, {"work": failure})
    job = jobs.enqueue("alice", "work", {}, 0, "retry", max_attempts=2)
    assert jobs.run_once("alice")
    pending = service.store.get("jobs", "alice", job["id"])
    assert pending["status"] == "queued" and pending["error"]
    assert not jobs.run_once("alice")
    pending["next_attempt"] = 0
    service.store.put("jobs", "alice", pending)
    assert jobs.run_once("alice")
    assert jobs.result("alice", job["id"])["status"] == "failed"
    assert len(executed) == 2
    with pytest.raises(ValueError, match="different"):
        jobs.enqueue("alice", "work", {"changed": True}, 0, "retry")


def test_generated_inbox_result_is_published_after_restart(service):
    job = service.jobs.enqueue("alice", "work", {}, 0, "generated")
    claimed = service.jobs.claim("alice")
    service.jobs.finish("alice", job["id"], claimed["lease_token"], {"done": True})
    assert service.store.all("inbox", "alice")[0]["state"] == "generated"
    restarted = Jobs(service.store)
    restarted.publish("alice")
    assert service.store.all("inbox", "alice")[0]["state"] == "available"
