"""B3f X1a: the solver day of query(transit, action=plan_day) and the entries of propose.usages are typed at the tool
boundary by one model each, which gives the MCP schema and the service's check. A wrong shape at any nested level is
refused with its path (list positions included) and the expected type; legal inputs still run. Over the service and
over real stdio and Streamable HTTP clients. Fictional places and people only."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from test_b3d_lock_aliases import D1, trip
from test_b3d_mcp import environment
from tripwright.query import SolverDay, arguments_schema
from tripwright.usage import Usage


def day() -> dict:
    return dict(day=D1, timezone="Europe/Vienna", start=dict(id="s", at="47.80,13.04", time="08:00"),
                end=dict(id="e", at="47.80,13.04", by="20:00"),
                candidates=[dict(id="c", at="47.81,13.05", priority=2, stay=60, open=["09:00", "18:00"], last_entry="17:00")],
                overrides=[{"from": "s", "to": "c", "minutes": 10, "deps": [["08:00", "08:10", "bus"]]}])


def changed(path: tuple, value) -> dict:
    result = day()
    parent = result
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return result


# (path inside day, wrong value, the refusal's words). Every nested layer: the day, start, end, a candidate and its
# fields, an override, its departures and one departure's times.
BAD_DAYS = [
    (("day",), [], "arguments.day.day must be a string, got array"),
    (("day",), "01/03/2031", "arguments.day.day: must be an ISO date"),
    (("timezone",), [], "arguments.day.timezone must be a string, got array"),
    (("timezone",), "Mars/Base", "arguments.day.timezone: Unknown IANA timezone"),
    (("start",), [], "arguments.day.start must be an object {id, at, time}, got array"),
    (("start", "id"), [], "arguments.day.start.id must be a string, got array"),
    (("start", "at"), [], "arguments.day.start.at must be a string, got array"),
    (("start", "time"), [], "arguments.day.start.time must be a string, got array"),
    (("start", "time"), "8am", "arguments.day.start.time: 时间要写成 HH:MM"),
    (("end",), "Hotel", "arguments.day.end must be an object {id, at, by}, got string"),
    (("end", "by"), ["20:00"], "arguments.day.end.by must be a string, got array"),
    (("end", "at"), {"lat": 47}, "arguments.day.end.at must be a string, got object"),
    (("candidates",), {}, "arguments.day.candidates must be an array, got object"),
    (("candidates", 0), [], "arguments.day.candidates.0 must be an object {id, at, priority, stay, optional open, last_entry, before, name}, got array"),
    (("candidates", 0, "id"), [], "arguments.day.candidates.0.id must be a string, got array"),
    (("candidates", 0, "at"), [], "arguments.day.candidates.0.at must be a string, got array"),
    (("candidates", 0, "priority"), [], "arguments.day.candidates.0.priority must be 'must'"),
    (("candidates", 0, "stay"), [], "arguments.day.candidates.0.stay must be an integer, got array"),
    (("candidates", 0, "stay"), 900, "arguments.day.candidates.0.stay must be less than or equal to 600"),
    (("candidates", 0, "open"), {}, "arguments.day.candidates.0.open must be an array, got object"),
    (("candidates", 0, "open", 0), [], "arguments.day.candidates.0.open.0 must be a string, got array"),
    (("candidates", 0, "open"), ["09:00"], "arguments.day.candidates.0.open: List should have at least 2 items"),
    (("candidates", 0, "last_entry"), 17, "arguments.day.candidates.0.last_entry must be a string, got integer"),
    (("overrides",), {}, "arguments.day.overrides must be an array, got object"),
    (("overrides", 0), [], "arguments.day.overrides.0 must be an object {from, to, optional minutes, deps}, got array"),
    (("overrides", 0, "from"), [], "arguments.day.overrides.0.from must be a string, got array"),
    (("overrides", 0, "minutes"), [], "arguments.day.overrides.0.minutes must be an integer, got array"),
    (("overrides", 0, "deps"), {}, "arguments.day.overrides.0.deps must be an array, got object"),
    (("overrides", 0, "deps", 0), {}, "arguments.day.overrides.0.deps.0 must be an array, got object"),
    (("overrides", 0, "deps", 0, 0), [], "arguments.day.overrides.0.deps.0.0 must be a string, got array"),
    (("overrides", 0, "deps", 0), ["08:00"], "arguments.day.overrides.0.deps.0: List should have at least 2 items"),
    (("overrides", 0, "deps", 0), ["8am", "08:10"], "arguments.day.overrides.0.deps.0: item 0: 时间要写成 HH:MM"),
    (("candiates",), [], "arguments.day.candiates is not a known field"),
]


def plan_day(value) -> dict:
    return dict(action="plan_day", idempotency_key="x1a-day", day=value)


@pytest.mark.parametrize("path,value,reason", BAD_DAYS, ids=[".".join(map(str, row[0])) + ":" + type(row[1]).__name__ for row in BAD_DAYS])
def test_x1a_each_layer_of_the_solver_day_is_typed(service, path, value, reason):
    with pytest.raises(ValueError, match="Invalid query arguments") as refused:
        asyncio.run(service.query("alice", "transit", plan_day(changed(path, value))))
    assert reason in str(refused.value), str(refused.value)
    assert service.store.all("jobs", "alice") == []


def test_x1a_a_legal_day_is_queued_unchanged(service):
    job = asyncio.run(service.query("alice", "transit", plan_day(day())))
    assert job["status"] == "queued"
    stored = service.store.get("jobs", "alice", job["job_id"])["payload"]["day"]
    assert stored["overrides"][0]["from"] == "s" and stored["candidates"][0]["stay"] == 60 and stored["start"]["at"] == "47.80,13.04"
    # A dump of the model without aliases (as a client built on it may send) is read the same way.
    dumped = SolverDay.model_validate(day()).model_dump(mode="json")
    assert "from_" in dumped["overrides"][0]
    again = asyncio.run(service.query("alice", "transit", dict(plan_day(dumped), idempotency_key="x1a-dumped")))
    assert service.store.get("jobs", "alice", again["job_id"])["payload"]["day"]["overrides"][0]["from"] == "s"


BAD_USAGES = [
    ({"item_id": "I-x", "status": "used", "reason": {"why": "because"}}, "usages.0.reason must be a string, got object"),
    ({"item_id": "I-x", "status": "traded_off", "reason": ["because"]}, "usages.0.reason must be a string, got array"),
    ({"item_id": [], "status": "used"}, "usages.0.item_id must be a string, got array"),
    ({"item_id": "I-x", "status": []}, "usages.0.status must be 'used', 'host_reported', 'traded_off' or 'not_applicable', got array"),
    ({"item_id": "I-x", "status": "done"}, "usages.0.status must be 'used', 'host_reported', 'traded_off' or 'not_applicable', got string"),
    ({"item_id": "I-x", "status": "used", "block_ids": {}}, "usages.0.block_ids must be an array, got object"),
    ({"item_id": "I-x", "status": "used", "block_ids": [{"id": "d1-after"}]}, "usages.0.block_ids.0 must be a string, got object"),
    ({"item_id": "I-x", "status": "used", "label": "x"}, "usages.0.label is not a known field"),
    (["I-x", "used"], "usages.0 must be an object, got array"),
]


def seeded_with_preference(service):
    turn = service.turn("alice", "Create", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    turn = service.turn("alice", "第一天轻松一点。", "pace", nature="user_verbatim", native_ref="fixture:pace", record=dict(items=[dict(
        kind="preference", key="pace", quote="轻松一点", text="第一天轻松一点", subject=["u1"], strength="prefer",
        scope=dict(start=D1, end=D1), source=dict(event="this"))]))
    return turn, service.store.all("items", "alice")[0]["id"]


@pytest.mark.parametrize("usage,reason", BAD_USAGES, ids=[f"usage-{index}" for index in range(len(BAD_USAGES))])
def test_x1a_each_field_of_a_usage_is_typed(service, usage, reason):
    turn, _ = seeded_with_preference(service)
    with pytest.raises(ValueError, match="Invalid usages") as refused:
        service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=service.state("alice")["plan"],
                        usages=[usage], idempotency_key="bad-usage")
    assert reason in str(refused.value), str(refused.value)


def test_x1a_legal_usages_and_a_versions_own_usages_are_accepted(service):
    turn, item_id = seeded_with_preference(service)
    usages = [dict(item_id=item_id, status="traded_off", reason="第一天要赶早班车")]
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=service.state("alice")["plan"],
                            usages=usages, idempotency_key="good-usage")
    assert draft["status"] == "proposed" and draft["usages"][0]["reason"] == "第一天要赶早班车"
    turn = service.turn("alice", "按轻松排", "used")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=service.state("alice")["plan"],
                            usages=[dict(item_id=item_id, status="used", block_ids=["d1-museum"])], idempotency_key="used-usage")
    version = service.commit("alice", draft["draft_id"], "used-usage", accept_unverified=True)
    echoed = version["usages"]
    assert {"verification", "reported_status"} <= set(echoed[0]), echoed
    turn = service.turn("alice", "再看一下", "again")
    again = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=service.state("alice")["plan"],
                            usages=echoed, idempotency_key="echo-usage")
    assert again["status"] == "proposed"


def test_x1a_one_model_gives_the_mcp_schema_and_the_check():
    schema = arguments_schema()
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    day_schema = next(branch for branch in schema["properties"]["day"]["anyOf"] if branch.get("type") == "object")
    assert day_schema == json.loads(json.dumps(day_schema)) and set(day_schema["required"]) == {"day", "timezone", "start", "end"}
    assert set(day_schema["properties"]["start"]["properties"]) == set(SolverDay.model_fields["start"].annotation.model_fields)
    assert "from" in day_schema["properties"]["overrides"]["items"]["properties"]
    assert Usage.model_json_schema()["properties"]["reason"]["anyOf"][0] == {"type": "string"}
    assert "verification" not in Usage.model_json_schema()["properties"]


# --- over real transports ---------------------------------------------------------------------------------------------

async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def probe(session) -> dict:
    results = {}
    listing = await session.list_tools()
    schemas = {tool.name: tool.input_schema for tool in listing.tools}
    query_day = next(branch for branch in schemas["query"]["properties"]["arguments"]["properties"]["day"]["anyOf"] if branch.get("type") == "object")
    results["schema"] = (False, "start" in query_day["properties"] and "deps" in json.dumps(query_day), "")
    for label, path, value, reason in [("start", ("start",), [], "arguments.day.start must be an object"),
                                       ("stay", ("candidates", 0, "stay"), [], "arguments.day.candidates.0.stay must be an integer, got array"),
                                       ("deps", ("overrides", 0, "deps", 0, 0), [], "arguments.day.overrides.0.deps.0.0 must be a string, got array"),
                                       ("at", ("candidates", 0, "at"), [], "arguments.day.candidates.0.at must be a string, got array")]:
        error, text, _ = await call(session, "query", dict(kind="transit", arguments=plan_day(changed(path, value))))
        results["day " + label] = (error, reason in text, text)
    error, text, data = await call(session, "query", dict(kind="transit", arguments=dict(plan_day(day()), idempotency_key="legal-day")))
    results["legal day"] = (error, bool(data and data.get("job_id")), text[:200])
    _, _, turn = await call(session, "turn", dict(text="Create", request_id="seed"))
    _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed"))
    _, _, committed = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
    _, _, turn = await call(session, "turn", dict(text="第一天轻松一点。", request_id="pace", nature="user_verbatim", native_ref="fixture:pace",
                                                  record=dict(items=[dict(kind="preference", key="pace", quote="轻松一点", text="第一天轻松一点",
                                                                          subject=["u1"], strength="prefer", scope=dict(start=D1, end=D1),
                                                                          source=dict(event="this"))])))
    item_id = (await call(session, "state", dict(view="ledger")))[2]["items"][0]["id"]
    for label, reason_value in [("reason object", {"why": "because"}), ("reason array", ["because"])]:
        error, text, _ = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key=label,
                                                             plan=committed["plan"], usages=[dict(item_id=item_id, status="traded_off", reason=reason_value)]))
        results[label] = (error, "usages.0.reason" in text and "valid string" in text, text)
    error, text, data = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="legal-usage",
                                                            plan=committed["plan"], usages=[dict(item_id=item_id, status="traded_off", reason="赶早班车")]))
    results["legal usage"] = (error, bool(data) and data["status"] == "proposed" and data["usages"][0]["reason"] == "赶早班车", text[:200])
    return results


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
    variables = dict(environment(), TRIPWRIGHT_TOKENS=json.dumps({"b3f-inputs-token": "alice"}))
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"), "serve", "--http", "--port", str(port)],
                               env=variables, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    address = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx2.AsyncClient() as client:
            for _ in range(1200):  # up to a minute on a loaded machine
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited: " + process.stderr.read())
                try:
                    if (await client.get(address)).status_code == 401:
                        break
                except httpx2.TransportError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("HTTP server did not start")
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer b3f-inputs-token"}) as client:
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
def test_x1a_day_and_usages_over_real_mcp_transports(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == 9, results
    for label, (error, readable, text) in results.items():
        if label in ("schema", "legal day", "legal usage"):
            assert not error and readable, (label, text)
        else:
            assert error and readable, (label, text)
