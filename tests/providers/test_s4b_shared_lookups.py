"""s4b: transit lookups shared across processes. On 11-03 02:1x four replay rounds ran in parallel, each with its own
local stdio server: the same searches went upstream four times, the day's 720 route searches ran out
(ROUTING_BUDGET_EXCEEDED) and the rest timed out behind each other. Answers now live in a shared folder for as long as
they are good, one process fetches a request while the others wait for its answer, requests from all processes are
paced and limited to a few at once, and a refused request is retried after a pause."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest

from tripwright.providers.transitous import TransitousProvider

from .fake_upstream import FakeUpstream

WHEN = datetime(2026, 10, 28, 9, tzinfo=ZoneInfo("Europe/Vienna"))
BATCH = [("47,13", "47.1,13.1"), ("47.2,13.2", "47.3,13.3"), ("47.4,13.4", "47.5,13.5")]
PLAN = {"itineraries": [{"startTime": "2026-10-28T08:00:00Z", "endTime": "2026-10-28T08:30:00Z", "duration": 1800,
                         "legs": [{"mode": "BUS", "displayName": "1", "from": {"name": "A", "track": "2"}, "to": {"name": "B"},
                                   "startTime": "2026-10-28T08:00:00Z", "endTime": "2026-10-28T08:30:00Z", "duration": 1800}]}]}

CHILD = """
import asyncio, json, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from tripwright.providers.transitous import TransitousProvider

async def main():
    when = datetime(2026, 10, 28, 9, tzinfo=ZoneInfo("Europe/Vienna"))
    provider = TransitousProvider(timezone_name="Europe/Vienna")
    out = []
    try:
        for origin, destination in json.loads(sys.argv[1]):
            result = await provider.plan(origin, destination, when, fresh=sys.argv[2] == "fresh")
            out.append(dict(lines=[leg.line for item in result.value for leg in item.legs], fetched=result.fetched_at.isoformat()))
    finally:
        await provider.aclose()
    print(json.dumps(out))

asyncio.run(main())
"""


class Upstream:
    """A local HTTP stand-in for Transitous: counts each request (path and parameters), answers slowly enough that
    processes overlap, and can refuse the first requests with 429."""

    def __init__(self, *, delay: float = 0.4, refuse: int = 0):
        self.requests: list[tuple[str, tuple]] = []
        self.delay, self.refuse = delay, refuse
        self.lock = threading.Lock()
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urlsplit(self.path)
                with upstream.lock:
                    upstream.requests.append((parts.path, tuple(sorted(parse_qsl(parts.query)))))
                    refused = upstream.refuse > 0
                    upstream.refuse -= refused
                time.sleep(upstream.delay)
                if refused:
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    return
                body = json.dumps(PLAN).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *arguments):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.close()


def processes(upstream: Upstream, folder, count: int, *, separate: bool = False, fresh: bool = False) -> list[list[dict]]:
    """`count` processes started together, each sending the whole batch; all count route searches in one file.
    `separate`: each process has its own shared folder (nothing is shared, as before s4b)."""
    environment = {key: value for key, value in os.environ.items() if not key.upper().endswith("_PROXY")}
    environment.update(TRIPWRIGHT_TRANSITOUS_API=upstream.url, TRIPWRIGHT_TRANSITOUS_USAGE=str(folder / "usage.log"),
                       NO_PROXY="127.0.0.1", XDG_CACHE_HOME=str(folder / "cache"))
    environment.pop("TRIPWRIGHT_TRANSITOUS_SHARED", None)
    started = []
    for number in range(count):
        own = dict(environment, TRIPWRIGHT_TRANSITOUS_SHARED=str(folder / f"shared-{number}")) if separate else environment
        started.append(subprocess.Popen([sys.executable, "-c", CHILD, json.dumps(BATCH), "fresh" if fresh else "plain"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=own))
    answers = []
    for process in started:
        out, err = process.communicate(timeout=120)
        assert process.returncode == 0, err[-2000:]
        answers.append(json.loads(out))
    return answers


def searches(upstream: Upstream) -> list[tuple]:
    return [request for request in upstream.requests if request[0].endswith("v6/plan")]


def counted(folder) -> int:
    return len((folder / "usage.log").read_text().splitlines())


def test_four_processes_sending_the_same_searches_reach_the_upstream_once(upstream, tmp_path):
    answers = processes(upstream, tmp_path, 4)
    assert all(len(answer) == len(BATCH) and all(item["lines"] == ["1"] for item in answer) for answer in answers)
    sent = searches(upstream)
    assert len(sent) == len(BATCH) and len(set(sent)) == len(BATCH), sent
    assert counted(tmp_path) == len(BATCH)  # the 720-a-day counter counts what went upstream, once


def test_by_default_the_folder_is_beside_the_counter_and_separate_folders_share_nothing(upstream, tmp_path):
    """The control: the shared folder is what keeps the upstream at one copy. It is on by default, beside the
    route-search counter; four processes with four folders search four times, as before."""
    from tripwright.providers import transitous

    assert transitous.shared_folder(tmp_path / "usage.log") == tmp_path / "transitous-shared"
    processes(upstream, tmp_path, 4, separate=True)
    assert len(searches(upstream)) == 4 * len(BATCH) and counted(tmp_path) == 4 * len(BATCH)


def test_a_fresh_lookup_is_not_answered_from_the_shared_folder(upstream, tmp_path):
    """D-57: a stored fact is checked again before it is used; that lookup goes upstream even when an answer is good."""
    processes(upstream, tmp_path, 1)
    upstream.requests.clear()
    processes(upstream, tmp_path, 1, fresh=True)
    assert len(searches(upstream)) == len(BATCH)


def test_a_refused_search_is_retried_after_a_pause_and_answered(tmp_path):
    upstream = Upstream(delay=0.05, refuse=2)
    try:
        answers = processes(upstream, tmp_path, 1)
    finally:
        upstream.close()
    assert all(item["lines"] == ["1"] for item in answers[0])
    assert len(searches(upstream)) == len(BATCH) + 2 and counted(tmp_path) == len(BATCH) + 2  # every try is counted


@pytest.fixture
def two_servers(tmp_path):
    """Two providers sharing one folder, as two processes would, on a pinned service clock and wall clock."""
    from tripwright.providers.common import SharedLookups

    fake = FakeUpstream()
    wall = [1_000_000.0]
    clock = [datetime(2026, 10, 28, 8, tzinfo=timezone.utc)]
    clients, providers = [], []
    for _ in range(2):
        client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
        shared = SharedLookups(tmp_path / "shared", min_interval=0, wall=lambda: wall[0])
        clients.append(client)
        providers.append(TransitousProvider(client=client, min_interval=0, clock=lambda: clock[0], shared=shared))
    yield providers, fake, wall, clock
    for client in clients:
        asyncio.run(client.aclose())


def test_a_shared_answer_keeps_its_age_and_expires_after_its_time(two_servers):
    (first, second), fake, wall, clock = two_servers
    asked = asyncio.run(first.plan("47,13", "47.1,13.1", WHEN))
    sent = len(fake.requests)
    wall[0] += 600
    clock[0] += timedelta(minutes=10)
    reused = asyncio.run(second.plan("47,13", "47.1,13.1", WHEN))
    assert len(fake.requests) == sent  # answered from the shared folder
    assert reused.fetched_at == asked.fetched_at and reused.valid_until == asked.valid_until  # ten minutes old, not new
    wall[0] += 25 * 60  # 35 minutes after the fetch: a route search is good for 30
    clock[0] += timedelta(minutes=25)
    again = asyncio.run(second.plan("47,13", "47.1,13.1", WHEN))
    assert len(fake.requests) > sent and again.fetched_at == clock[0]


def test_a_folder_that_cannot_be_written_turns_the_sharing_off_not_the_lookups(tmp_path):
    from tripwright.providers.common import SharedLookups

    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    fake = FakeUpstream()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    provider = TransitousProvider(client=client, min_interval=0, shared=SharedLookups(blocker / "shared", min_interval=0))
    try:
        result = asyncio.run(provider.plan("47,13", "47.1,13.1", WHEN))
    finally:
        asyncio.run(client.aclose())
    assert result.value and provider.shared.broken
