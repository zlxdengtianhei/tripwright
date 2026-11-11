"""X1a: nested query arguments of the wrong shape are refused with the field and the expected type, over the service
and over real stdio and Streamable HTTP clients; S6a end to end over both transports."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx2
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

import tripwright
from test_b3d_lock_aliases import GROUP, LOCKED, find, trip

TARGET = "{block_id or anchor_id, date, fact, optional place_id and draft_id}"
BAD = [
    ("transit", {"target": []}, f"arguments.target must be an object {TARGET}, got array"),
    ("transit", {"target": "d2-fort"}, f"arguments.target must be an object {TARGET}, got string"),
    ("transit", {"target": {"block_id": 7, "date": "2031-05-01", "fact": "timetable"}}, "arguments.target.block_id must be a string, got integer"),
    ("transit", {"target": {"block_id": "x", "date": "2031-05-01", "fact": "price"}},
     "arguments.target.fact must be 'timetable', 'hard_node', 'weather' or 'opening_hours', got string"),
    ("transit", {"target": {"block_id": "x", "fact": "timetable"}}, "arguments.target.date is required"),
    ("transit", {"target": {"block_id": "x", "anchor_id": "y", "date": "2031-05-01", "fact": "timetable"}}, "exactly one of block_id or anchor_id"),
    ("transit", {"action": "stops", "text": ["Bahnhof"]}, "arguments.text must be a string, got array"),
    ("transit", {"action": "stops", "text": "Bahnhof", "limit": "many"}, "arguments.limit must be an integer, got string"),
    ("transit", {"origin": "A"}, "arguments.destination is required; arguments.when is required"),
    ("weather", {"place": "Lakeside", "date": "2031-05-01"}, "arguments.place must be an object {name, lat, lon}, got string"),
    ("weather", {"place": {"name": "Lakeside", "lat": "north", "lon": 13.0}, "date": "2031-05-01"}, "arguments.place.lat must be a number, got string"),
    ("nearby", {"lat": 47.8, "lon": 13.0, "km": [1]}, "arguments.km must be a number, got array"),
    ("transit", {"action": "plan_day", "idempotency_key": "k", "day": "today"}, "arguments.day must be an object {day, timezone, start, end, candidates}, got string"),
]


@pytest.mark.parametrize("kind,arguments,reason", BAD)
def test_x1a_service_refuses_nested_shapes_with_field_and_type(service, kind, arguments, reason):
    with pytest.raises(ValueError, match="Invalid query arguments") as refused:
        asyncio.run(service.query("alice", kind, arguments))
    assert reason in str(refused.value)


def test_x1a_legal_queries_still_run(service):
    turn = service.turn("alice", "Create", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    plain = asyncio.run(service.query("alice", "transit", {"origin": "A", "destination": "B", "when": "2031-05-01T08:00:00+02:00", "target": {}}))
    assert plain["status"] == "unverified" and plain["evidence"]["facts"] == []
    assert asyncio.run(service.query("alice", "transit", {"action": "stops", "text": "Bahnhof", "limit": 3}))["status"] == "unverified"
    weather = asyncio.run(service.query("alice", "weather", {"target": {"block_id": "d1-park", "date": "2031-05-01", "fact": "weather"}}))
    assert weather["evidence"]["binding"]["block_id"] == "d1-park" and weather["evidence"]["facts"] == []
    assert asyncio.run(service.query("alice", "saved", {"text": "Seepark"}))["evidence"]["value"]


async def text_of(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def probe(session) -> dict:
    results = {}
    for kind, arguments, reason in BAD[:3] + BAD[9:10]:
        error, text, _ = await text_of(session, "query", dict(kind=kind, arguments=arguments))
        results[json.dumps(arguments)] = (error, reason in text, text)
    error, text, _ = await text_of(session, "query", dict(kind="transit", arguments={"origin": "A", "destination": "B",
                                                                                      "when": "2031-05-01T08:00:00+02:00", "target": {}}))
    results["legal"] = (error, "unverified" in text, text[:120])
    _, _, turn = await text_of(session, "turn", dict(text="Create", request_id="seed"))
    _, _, draft = await text_of(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed"))
    _, _, committed = await text_of(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
    block = find(committed["plan"], LOCKED)[4]
    _, _, turn = await text_of(session, "turn", dict(text="第一天饭后去岩馆 A，定了", request_id="lock", nature="user_verbatim", native_ref="fixture:lock",
                                                     speaker="u1"))
    decision = dict(question="去哪", options=["岩馆 A"], choice="岩馆 A", subject=GROUP, decided_by="user", must=True,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"), locked_paths={f"/blocks/{LOCKED}": block})
    _, _, draft = await text_of(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(decisions=[decision]), idempotency_key="lock"))
    _, _, committed = await text_of(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="lock"))
    for name in ("Recommended", "Option B"):
        plan = deepcopy(committed["plan"])
        entry, rank, source, track, moved = find(plan, LOCKED)
        track["blocks"].remove(moved)
        entry["plans"][1].update(name=name)
        entry["plans"][1]["tracks"].append(dict(subject=list(GROUP), blocks=[moved]))
        _, _, turn = await text_of(session, "turn", dict(text="agent moves it", request_id="move-" + name, nature="agent_inference"))
        _, _, draft = await text_of(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="move-" + name))
        error, text, _ = await text_of(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="move-" + name, accept_unverified=True))
        results["move into " + name] = (draft["status"] == "rejected" and error, "(plan, rank changed)" in text, text)
    return results


def environment() -> dict:
    """The server subprocess imports the same tripwright as this test, so a mutated copy reaches the server too."""
    source = str(Path(tripwright.__file__).resolve().parents[1])
    variables = {key: value for key, value in os.environ.items() if not key.startswith("TRIPWRIGHT_")}
    variables["PYTHONPATH"] = os.pathsep.join([source, *filter(None, [os.environ.get("PYTHONPATH")])])
    return variables


async def over_stdio(directory: Path) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await probe(session)


async def over_http(directory: Path) -> dict:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    key = "x1a-only-" + "k" * 40
    variables = dict(environment(), TRIPWRIGHT_URL_KEY=key)
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"), "serve", "--http", "--port", str(port)],
                               env=variables, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}/mcp"
    address = base + "/" + key
    try:
        async with httpx2.AsyncClient() as client:
            for attempt in range(200):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited: " + process.stderr.read())
                try:
                    if (await client.get(base)).status_code == 401:
                        break
                except httpx2.TransportError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("HTTP server did not start")
        async with httpx2.AsyncClient() as client:
            async with streamable_http_client(address, http_client=client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    return await probe(session)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("transport", [over_stdio, over_http], ids=["stdio", "http"])
def test_x1a_and_s6a_over_real_mcp_transports(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == 7, results
    for label, (error, readable, text) in results.items():
        if label == "legal":
            assert not error and readable, (label, text)
        else:
            assert error and readable, (label, text)
