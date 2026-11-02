"""Bounded caches, request budgets and cancellable upstream scheduling."""

from __future__ import annotations

import asyncio
import contextvars
import copy
import heapq
import json
import os
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import httpx

from tripwright import __version__
from tripwright.storage import clock as storage_clock

from .base import Place, ProviderUnavailable, Sourced

try:
    import fcntl
except ImportError:  # not on Windows: a shared usage file is then not locked
    fcntl = None

# The contact a data provider sees in the User-Agent: the project's address, never a person's (A8; Transitous's terms).
PROJECT_URL = "https://github.com/zlxdengtianhei/tripwright"

client_id = contextvars.ContextVar("tripwright_provider_client", default="local")
request_priority = contextvars.ContextVar("tripwright_provider_priority", default=0)
_call_used = contextvars.ContextVar("tripwright_provider_call_used", default=None)


def user_agent() -> str:
    """Application name, version and contact; TRIPWRIGHT_USER_AGENT replaces the whole string."""
    return os.environ.get("TRIPWRIGHT_USER_AGENT") or f"tripwright/{__version__} (+{PROJECT_URL})"


class RollingBudget:
    """At most `limit` uses in any `window` (Transitous: 720 route searches in 24 hours). With `path`, the uses are
    kept in that file, one UTC time per line under a lock, so the command line's short-lived processes share them; without
    it they live in this object. Its clock is the wall clock, not the service's (a replay pins that one to the past)."""

    def __init__(self, limit: int, *, window=timedelta(hours=24), path: str | Path | None = None, clock=None,
                 source: str = "budget"):
        if limit < 1:
            raise ValueError("limit must be positive")
        self.limit, self.window, self.source = limit, window, source
        self.path = Path(path) if path else None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.uses: deque = deque()

    def take(self) -> bool:
        """Count one use if the window has room; False when it is full. A counter file that cannot be written (a
        read-only root file system, a full disk) raises ProviderError USAGE_FILE with the path and the fix: the search
        is not made, because uncounted searches could break the provider's terms."""
        try:
            return self._take()
        except OSError as error:
            raise ProviderError(self.source, "USAGE_FILE",
                                f"Cannot write the route-search counter {self.path} ({error.strerror or error}); the operator must "
                                "set TRIPWRIGHT_TRANSITOUS_USAGE to a writable file on the data volume", retryable=False) from error

    def _take(self) -> bool:
        stamp = self.clock()
        if self.path is None:
            while self.uses and stamp - self.uses[0] >= self.window:
                self.uses.popleft()
            if len(self.uses) >= self.limit:
                return False
            self.uses.append(stamp)
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+", encoding="utf-8") as handle:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            kept = []
            for line in handle.read().splitlines():
                try:
                    used = datetime.fromisoformat(line.strip())
                except ValueError:
                    continue
                if stamp - used < self.window:
                    kept.append(used)
            room = len(kept) < self.limit
            if room:
                kept.append(stamp)
            handle.seek(0)
            handle.truncate()
            handle.write("".join(used.isoformat() + "\n" for used in kept))
            return room

    def used(self) -> int:
        stamp = self.clock()
        if self.path is None:
            return sum(1 for used in self.uses if stamp - used < self.window)
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return 0
        return sum(1 for line in lines if line.strip() and stamp - datetime.fromisoformat(line.strip()) < self.window)


class ProviderError(ProviderUnavailable):
    def __init__(self, provider, code, reason, *, retryable=True, upstream=False):
        super().__init__(provider, f"[{code}] {reason}", retryable=retryable)
        self.code = code
        self.upstream = upstream


@contextmanager
def request_scope(*, client=None, priority=None):
    tokens = [(_call_used, _call_used.set([0]))]
    if client is not None:
        tokens.append((client_id, client_id.set(client)))
    if priority is not None:
        tokens.append((request_priority, request_priority.set(priority)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def budgeted(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        if _call_used.get() is not None:
            return await function(*args, **kwargs)
        with request_scope():
            return await function(*args, **kwargs)
    return wrapped


class PriorityGate:
    """FIFO within each priority; cancelled waiters never consume a slot."""

    def __init__(self, concurrency=1):
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self.free = concurrency
        self.queue = []
        self.sequence = 0

    async def acquire(self, priority=0):
        if self.free and not self.queue:
            self.free -= 1
            return
        future = asyncio.get_running_loop().create_future()
        self.sequence += 1
        heapq.heappush(self.queue, (priority, self.sequence, future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release()
            raise

    def release(self):
        while self.queue:
            _, _, future = heapq.heappop(self.queue)
            if not future.done():
                future.set_result(None)
                return
        self.free += 1

    def __call__(self, priority=0):
        gate = self

        class Lease:
            async def __aenter__(self):
                await gate.acquire(priority)

            async def __aexit__(self, *exception):
                gate.release()
        return Lease()


def validate_place(place: Place):
    if not (-90 <= place.lat <= 90 and -180 <= place.lon <= 180):
        raise ValueError("coordinates out of range")


def validate_limit(limit, maximum=20):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")


class HTTPProvider:
    def __init__(self, source, base_url, *, attribution, client=None, timeout=25,
                 min_interval=0.3, concurrency=1, call_budget=80,
                 hourly_budget=400, daily_cap=15000, cache_max=3000, clock=None):
        if timeout <= 0 or min_interval < 0 or cache_max < 1:
            raise ValueError("timeout/cache_max must be positive; interval cannot be negative")
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.attribution = attribution
        self.user_agent = user_agent()
        self.budgets: dict[str, tuple[RollingBudget, str]] = {}  # request path -> (budget, refusal reason)
        self.timeout = timeout
        self.min_interval = min_interval
        self.call_budget = call_budget
        self.hourly_budget = hourly_budget
        self.daily_cap = daily_cap
        self.cache_max = cache_max
        self.clock = clock or storage_clock  # the service's clock: pinned in replays (TRIPWRIGHT_NOW)
        self.cache = {}
        self.usage = {}
        self.daily = (None, 0)
        self.gate = PriorityGate(concurrency)
        self.rate_lock = asyncio.Lock()
        self.last_request = float("-inf")
        self.client = client or httpx.AsyncClient()
        self.owns_client = client is None

    async def aclose(self):
        if self.owns_client:
            await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exception):
        await self.aclose()

    def _charge(self, path=""):
        used = _call_used.get()
        stamp = self.clock()
        identity = client_id.get()
        history = self.usage.get(identity, deque())
        while history and (stamp - history[0]).total_seconds() >= 3600:
            history.popleft()
        today, count = self.daily
        if today != stamp.date():
            today, count = stamp.date(), 0
        if used is not None and used[0] >= self.call_budget:
            raise ProviderError(self.source, "CALL_BUDGET_EXCEEDED", "Upstream request budget exhausted")
        if len(history) >= self.hourly_budget:
            raise ProviderError(self.source, "RATE_LIMITED", "Hourly upstream budget exhausted")
        if count >= self.daily_cap:
            raise ProviderError(self.source, "SERVICE_BUDGET_EXCEEDED", "Daily upstream budget exhausted")
        if identity not in self.usage and len(self.usage) >= 5000:
            self.usage = {key: values for key, values in self.usage.items()
                          if values and (stamp - values[-1]).total_seconds() < 3600}
            if len(self.usage) >= 5000:
                raise ProviderError(self.source, "RATE_LIMITED", "Client budget table is full")
        limited = self.budgets.get(path.lstrip("/"))
        if limited and not limited[0].take():
            raise ProviderError(self.source, "ROUTING_BUDGET_EXCEEDED", limited[1])
        if used is not None:
            used[0] += 1
        history.append(stamp)
        self.usage[identity] = history
        self.daily = (today, count + 1)

    async def get_json(self, path, params):
        async with self.gate(request_priority.get()):
            async with self.rate_lock:
                delay = self.min_interval - (time.monotonic() - self.last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
                self._charge(path)
                self.last_request = time.monotonic()
            try:
                async with asyncio.timeout(self.timeout):
                    response = await self.client.get(
                        f"{self.base_url}/{path.lstrip('/')}", params=params,
                        headers={"User-Agent": self.user_agent}, timeout=self.timeout)
            except (TimeoutError, httpx.TimeoutException) as error:
                raise ProviderError(self.source, "UPSTREAM_TIMEOUT", "Upstream timed out; retry later", upstream=True) from error
            except (httpx.TransportError, OSError) as error:
                raise ProviderError(self.source, "UPSTREAM_UNREACHABLE", "Cannot reach upstream; retry later", upstream=True) from error
        if response.status_code == 429:
            raise ProviderError(self.source, "UPSTREAM_BUSY", "Upstream rate limited the request", upstream=True)
        if response.status_code >= 500:
            raise ProviderError(self.source, "UPSTREAM_ERROR", f"HTTP {response.status_code}", upstream=True)
        if response.status_code >= 400:
            raise ProviderError(self.source, "UPSTREAM_REJECTED", f"HTTP {response.status_code}", retryable=False)
        try:
            return response.json()
        except ValueError as error:
            raise ProviderError(self.source, "UPSTREAM_ERROR", "Upstream returned non-JSON", upstream=True) from error

    async def cached(self, kind, params, fetch, *, ttl, retention, fresh=False):
        key = json.dumps([self.base_url, kind, params], sort_keys=True, default=str)
        stamp = self.clock()
        old = self.cache.get(key)
        if old and stamp - old.fetched_at >= retention:
            del self.cache[key]
            old = None
        if old and not fresh and stamp < old.valid_until:
            return copy.deepcopy(old)
        try:
            try:
                value = await fetch()
            except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError) as error:
                raise ProviderError(self.source, "UPSTREAM_ERROR", "Unexpected upstream data format", upstream=True) from error
        except ProviderError as error:
            if old and error.upstream:
                return copy.deepcopy(replace(old, stale=True, notes=[*old.notes, error.reason]))
            raise
        fetched = self.clock()
        result = Sourced(value, self.source, fetched, fetched + ttl, notes=[self.attribution])
        self.cache[key] = copy.deepcopy(result)
        while len(self.cache) > self.cache_max:
            oldest = min(self.cache, key=lambda cache_key: self.cache[cache_key].fetched_at)
            del self.cache[oldest]
        return result


async def gather_cancel(*coroutines):
    tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
