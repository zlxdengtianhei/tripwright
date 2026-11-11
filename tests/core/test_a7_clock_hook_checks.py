"""A7 (D-44): a pinned clock for replays. Offline: the fictional MOTIS and web of A5, fixed moments.
Fictional people and places."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tripwright.storage as storage
from a5_upstream import DESTINATION, ORIGIN, Timetable, page_reader
from test_a3_anchor_deadline import anchored_plan
from test_a6_check_answer import RULE
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

ROOT = Path(__file__).resolve().parents[2]


def test_the_clock_pins_to_tripwright_now_and_runs_on_from_there(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_NOW", raising=False)
    assert abs(datetime.fromisoformat(storage.now()) - datetime.now(timezone.utc)) < timedelta(seconds=5)
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T16:40:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time() - 90))
    shown = datetime.fromisoformat(storage.now())
    assert timedelta(seconds=89) <= shown - datetime(2030, 6, 10, 14, 40, tzinfo=timezone.utc) <= timedelta(seconds=95)
    assert storage.identifier("EV").startswith("EV-300610-")
    monkeypatch.setenv("TRIPWRIGHT_NOW", "16:40")
    with pytest.raises(ValueError, match="TRIPWRIGHT_NOW must be an ISO time with offset"):
        storage.now()


def test_evidence_reads_the_pinned_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T20:45:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    store = Store(tmp_path / "pinned.sqlite3")
    service = Tripwright(store, query_router=QueryRouter(store, transit=Timetable().provider(), pages=page_reader()))
    try:
        turn = service.turn("alice", "Create the trip.", "seed")
        draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=anchored_plan(), idempotency_key="seed")
        service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
        asked = service.turn("alice", "How late can I leave?", "q1", scenario="quick")
        assert asked["now"]["date"] == "2030-06-10" and asked["now"]["time"] == "20:45"
        assert asked["pack"]["markdown"].startswith("# 现在 20:45（Europe/Oslo，06-10 周一）")
        latest = asyncio.run(service.query("alice", "latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-counter")))
        web = asyncio.run(service.query("alice", "web_evidence", RULE))
        for evidence in (latest["evidence"], web["evidence"]):
            assert evidence["fetched_at"].startswith("2030-06-10T18:4"), evidence["fetched_at"]
        assert web["evidence"]["id"].startswith("EV-300610-")
    finally:
        service.jobs.stop()
        store.close()


def test_a_replay_pins_the_server_and_the_hosts_date(tmp_path):
    spec = importlib.util.spec_from_file_location("host_replay", ROOT / "scripts/host_replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    variables = replay.pin_clock(tmp_path, "2030-06-10T16:40:00+02:00")
    assert variables["TRIPWRIGHT_NOW"] == "2030-06-10T16:40:00+02:00" and variables["PATH"].startswith(str(tmp_path / "clock"))
    shown = subprocess.run(["sh", "-c", "date -u +%Y-%m-%dT%H:%M; TZ=Europe/Oslo date +%H:%M"], env=dict(os.environ, **variables),
                           capture_output=True, text=True, check=True).stdout.split()
    assert shown == ["2030-06-10T14:40", "16:40"] or shown == ["2030-06-10T14:41", "16:41"]
    with pytest.raises(SystemExit, match="--now needs an ISO time with offset"):
        replay.pin_clock(tmp_path / "other", "16:40")
