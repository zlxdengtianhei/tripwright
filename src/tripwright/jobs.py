from __future__ import annotations

import threading
import time
import asyncio
import inspect
import logging
from concurrent.futures import TimeoutError as FutureTimeout
from collections.abc import Callable

from tripwright.repo.snapshot import changed as moved, freshness, take
from tripwright.storage import Store, encode, identifier, now

log = logging.getLogger("tripwright.jobs")
IDLE_SECONDS = 1.0

STALE = "已过期，需重算"
UNSETTLED = "有新原话还没归属：有账先记账，没账用 ack_events 声明"


def version_number(store: Store, owner: str) -> int:
    return max((version["number"] for version in store.all("versions", owner)), default=0)


def outdated(store: Store, owner: str, job: dict, current: dict | None = None) -> list[str]:
    """What changed since the job's snapshot (CONTRACTS 1.2): the content key, not the version number, because two
    writers can each produce "version 4". Jobs stored without a snapshot (solver jobs) compare the version number."""
    if job.get("snapshot"):
        return moved(job["snapshot"], current or take(store, owner))
    return ["plan"] if job["base_version"] != version_number(store, owner) else []


class Jobs:
    def __init__(self, store: Store, handlers: dict[str, Callable] | None = None, *, lease_seconds: float = 30):
        self.store = store
        self.handlers = handlers or {}
        self.lease_seconds = lease_seconds
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.loop = None
        self.loop_thread = None
        self.runtime_lock = threading.RLock()
        self.active = {}
        self.async_cleanup = None
        self.seen = None  # the store's change marker after the last background pass that found no live job

    def submit(self, coroutine):
        with self.runtime_lock:
            if self.loop is None:
                ready = threading.Event()

                def work():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self.loop = loop
                    ready.set()
                    try:
                        loop.run_forever()
                    finally:
                        loop.close()

                self.loop_thread = threading.Thread(target=work, name="tripwright-provider-loop", daemon=True)
                self.loop_thread.start()
                ready.wait()
            return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def cancel(self, owner: str, job_id: str) -> dict:
        with self.store.transaction():
            job = self.store.get("jobs", owner, job_id)
            if job["status"] in ("queued", "running"):
                job.update(status="cancelled", lease_token=None, lease_until=0, error="Cancelled by the caller")
                self.store.put("jobs", owner, job)
                with self.runtime_lock:
                    future = self.active.get((owner, job_id))
                    if future:
                        future.cancel()
        return self.result(owner, job_id)

    def enqueue(self, owner: str, kind: str, payload: dict, base_version: int, idempotency_key: str,
                *, max_attempts: int = 3, snapshot: dict | None = None) -> dict:
        with self.store.transaction():
            for job in self.store.all("jobs", owner):
                if job["idempotency_key"] == idempotency_key:
                    if (job["kind"], job["base_version"], encode(job["payload"])) != (kind, base_version, encode(payload)):
                        raise ValueError("Job idempotency key was reused with different input")
                    return job
            job = dict(id=identifier("J"), kind=kind, payload=payload, base_version=base_version,
                       idempotency_key=idempotency_key, status="queued", attempts=0, max_attempts=max_attempts,
                       lease_token=None, lease_until=0, next_attempt=0, created_at=now(), error=None, snapshot=snapshot, acked=[])
            self.store.put("jobs", owner, job, insert=True)
            return job

    def _stale(self, owner: str, job: dict):
        job.update(status="stale", lease_token=None, lease_until=0)
        self.store.put("jobs", owner, job)
        with self.runtime_lock:
            future = self.active.get((owner, job["id"]))
            if future:
                future.cancel()
        for message in self.store.all("inbox", owner):
            if message["job_id"] == job["id"]:
                message["state"] = "stale"
                self.store.put("inbox", owner, message)

    def invalidate(self, owner: str):
        with self.store.transaction():
            current = take(self.store, owner)
            for job in self.store.all("jobs", owner):
                if job["status"] != "stale" and outdated(self.store, owner, job, current):
                    self._stale(owner, job)

    def claim(self, owner: str, *, at: float | None = None) -> dict | None:
        at = time.time() if at is None else at
        with self.store.transaction():
            self.invalidate(owner)
            for job in self.store.all("jobs", owner):
                eligible = job["status"] == "queued" or (job["status"] == "running" and job["lease_until"] <= at)
                if not eligible or job["next_attempt"] > at:
                    continue
                if job["attempts"] >= job["max_attempts"]:
                    job.update(status="failed", error="Retry limit reached after an expired lease")
                    self.store.put("jobs", owner, job)
                    continue
                job.update(status="running", lease_token=identifier("lease"), lease_until=at + self.lease_seconds,
                           attempts=job["attempts"] + 1)
                self.store.put("jobs", owner, job)
                return job
        return None

    def renew(self, owner: str, job_id: str, lease_token: str, *, at: float | None = None) -> bool:
        at = time.time() if at is None else at
        with self.store.transaction():
            job = self.store.get("jobs", owner, job_id)
            if job["status"] != "running" or job["lease_token"] != lease_token or job["lease_until"] <= at:
                return False
            job["lease_until"] = at + self.lease_seconds
            self.store.put("jobs", owner, job)
            return True

    def finish(self, owner: str, job_id: str, lease_token: str, value: dict, *, at: float | None = None) -> bool:
        at = time.time() if at is None else at
        with self.store.transaction():
            job = self.store.get("jobs", owner, job_id)
            if outdated(self.store, owner, job):
                self._stale(owner, job)
                return False
            if job["status"] != "running" or job["lease_token"] != lease_token or job["lease_until"] <= at:
                return False
            message = dict(id=identifier("R"), job_id=job_id, base_version=job["base_version"],
                           state="generated", value=value, generated_at=now(), retrieved_at=None)
            self.store.put("inbox", owner, message, insert=True)
            job.update(status="completed", lease_token=None, lease_until=0, finished_at=now())
            self.store.put("jobs", owner, job)
            return True

    def publish(self, owner: str):
        with self.store.transaction():
            self.invalidate(owner)
            for message in self.store.all("inbox", owner):
                if message["state"] == "generated":
                    message["state"] = "available"
                    self.store.put("inbox", owner, message)

    def fail(self, owner: str, job_id: str, lease_token: str, error: str):
        with self.store.transaction():
            job = self.store.get("jobs", owner, job_id)
            if job["status"] != "running" or job["lease_token"] != lease_token:
                return
            if outdated(self.store, owner, job):
                self._stale(owner, job)
                return
            job.update(status="queued" if job["attempts"] < job["max_attempts"] else "failed", error=error,
                       lease_token=None, lease_until=0, next_attempt=time.time() + min(30, 2 ** job["attempts"]))
            self.store.put("jobs", owner, job)

    def run_once(self, owner: str | None = None) -> bool:
        for candidate in [owner] if owner is not None else self.store.owners("jobs"):
            self.publish(candidate)
            job = self.claim(candidate)
            if job is None:
                continue
            try:
                handler = self.handlers.get(job["kind"])
                if handler is None:
                    raise ValueError(f"No handler for job kind {job['kind']}")
                value = handler(candidate, job["payload"])
                if inspect.isawaitable(value):
                    future = self.submit(value)
                    with self.runtime_lock:
                        self.active[(candidate, job["id"])] = future
                    current_job = self.store.get("jobs", candidate, job["id"])
                    if current_job["status"] != "running" or current_job["lease_token"] != job["lease_token"]:
                        future.cancel()
                    while True:
                        try:
                            value = future.result(timeout=min(1, self.lease_seconds / 3))
                            break
                        except FutureTimeout:
                            if self.stopping.is_set() or not self.renew(candidate, job["id"], job["lease_token"]):
                                future.cancel()
                self.finish(candidate, job["id"], job["lease_token"], value)
                self.publish(candidate)
            except Exception as error:
                self.fail(candidate, job["id"], job["lease_token"], f"{type(error).__name__}: {error}")
            finally:
                with self.runtime_lock:
                    self.active.pop((candidate, job["id"]), None)
            return True
        return False

    def result(self, owner: str, job_id: str, *, ack_events: list[str] | None = None, body=None) -> dict:
        """CONTRACTS 1.4: every retrieval compares the job's snapshot with the current one, not only the stale mark.
        `ack_events` declares newer messages that carry no ledger change; the declaration is kept on the job."""
        with self.store.transaction():
            self.publish(owner)
            job = self.store.get("jobs", owner, job_id)
            current = take(self.store, owner)
            changed = outdated(self.store, owner, job, current)
            if changed or job["status"] == "stale":
                if job["status"] != "stale":
                    self._stale(owner, job)
                return dict(job_id=job_id, status="stale", changed=changed or ["plan"], unsettled_events=[], result=None, notice=STALE)
            if ack_events:
                self._ack(owner, job, current, ack_events)
            if job.get("snapshot"):
                _, _, open_events = freshness(self.store, owner, job["snapshot"], current, [ack["event"] for ack in job.get("acked", [])])
                if open_events:
                    return dict(job_id=job_id, status="unsettled", changed=[], unsettled_events=open_events, result=None, notice=UNSETTLED)
            messages = [message for message in self.store.all("inbox", owner) if message["job_id"] == job_id]
            if not messages:
                return dict(job_id=job_id, status=job["status"], changed=[], unsettled_events=[], result=None, notice=job["error"])
            message = messages[-1]
            message.update(state="retrieved", retrieved_at=message["retrieved_at"] or now())
            self.store.put("inbox", owner, message)
            return dict(job_id=job_id, status="completed", changed=[], unsettled_events=[],
                        result=dict(message, **(body(job, message) if body else {})),
                        notice="Retrieved does not mean the user has read it.")

    def _ack(self, owner: str, job: dict, current: dict, events: list[str]):
        received = [event["id"] for event in self.store.all("events", owner) if "received" in event]
        newer = set(received[job["snapshot"]["raw_watermark"]["count"]:current["raw_watermark"]["count"]]) if job.get("snapshot") else set()
        unknown = [event for event in events if event not in newer]
        if unknown:
            raise ValueError(f"ack_events must name messages received after the job's snapshot: {', '.join(unknown)}")
        acked = {ack["event"] for ack in job.get("acked", [])}
        job.setdefault("acked", []).extend(dict(event=event, at=now()) for event in events if event not in acked)
        self.store.put("jobs", owner, job)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()

        def work():
            reported = None
            while not self.stopping.is_set():
                try:
                    active = self.background_step()
                    reported = None
                except Exception as error:
                    active = False
                    if str(error) != reported:
                        log.error("job loop step failed: %s: %s", type(error).__name__, error)
                        reported = str(error)
                self.stopping.wait(0.05 if active else 0.25 if self.seen is None else IDLE_SECONDS)

        self.thread = threading.Thread(target=work, name="tripwright-jobs", daemon=True)
        self.thread.start()

    def background_step(self) -> bool:
        """run_once, skipped while nothing was written since a pass that left no queued or running job: publishing,
        invalidating and claiming depend only on the stored records then, so the idle loop decodes nothing."""
        if self.seen is not None and self.store.changes() == self.seen:
            return False
        marker = self.store.changes()
        active = self.run_once()
        live = active or any(self.store.where("jobs", owner, "status", ("queued", "running")) for owner in self.store.owners("jobs"))
        # A write during the pass (ours or a request's) cannot be told apart, so it reruns the pass next time.
        self.seen = marker if not live and self.store.changes() == marker else None
        return active

    def stop(self):
        self.stopping.set()
        with self.runtime_lock:
            for future in self.active.values():
                future.cancel()
        if self.thread:
            self.thread.join(timeout=5)
        with self.runtime_lock:
            if self.loop is None:
                return
            loop = self.loop

            async def cleanup():
                tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self.async_cleanup:
                    await self.async_cleanup()
                await loop.shutdown_asyncgens()

            try:
                asyncio.run_coroutine_threadsafe(cleanup(), loop).result(timeout=5)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                self.loop_thread.join(timeout=5)
                self.loop = None
                self.loop_thread = None
