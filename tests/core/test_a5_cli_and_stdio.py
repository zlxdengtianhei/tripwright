"""A5: the same lookups from the command line, for a host whose MCP connection is down. `tripwright query` prints the
MCP query tool's result as JSON on stdout and its one-line summary on stderr; a refusal reads the same. And one run over
real stdio: the new kinds are in the tool schema, they answer, and turn names the path of a wrong record. Offline:
fictional MOTIS and web (in process) or the demo providers (subprocesses). Fictional people, places and lines only."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

import tripwright.query as query_module
from a5_upstream import DESTINATION, ORIGIN, Timetable, page_reader
from test_a3_anchor_deadline import anchored_plan
from test_b3d_mcp import environment
from tripwright.adapters.mcp import create_server
from tripwright.cli import main
from tripwright.providers.transitous import TransitousProvider
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

PLAN = anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"})
RULE = dict(url="https://ferry.example.org/rules", quote="The ferry counter closes 40 minutes before departure.",
            claim="The counter closes 40 minutes before the ferry leaves", type="rule")
CASES = {
    "transit": (dict(origin=ORIGIN, destination=DESTINATION, when="2030-06-10T19:00:00Z", limit=3),
                ["--from", ORIGIN, "--to", DESTINATION, "--when", "2030-06-10T19:00:00Z", "--limit", "3"]),
    "latest_departure": (dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive"),
                         ["--from", ORIGIN, "--to", DESTINATION, "--anchor", "hn-arrive"]),
    "web_evidence": (RULE, ["--arguments", json.dumps(dict(url=RULE["url"], claim=RULE["claim"])), "--quote", RULE["quote"], "--type", "rule"]),
}
VOLATILE = ("id", "fetched_at", "valid_until")


def steady(result: dict) -> dict:
    """The result without what differs between two calls: the evidence ID and the service's clock."""
    result = json.loads(json.dumps(result))
    for key in VOLATILE:
        result.get("evidence", {}).pop(key, None)
    for key in ("summary", "reason"):
        if key in result and "EV-" in result[key]:
            result[key] = result[key].split("：", 1)[-1]
    return result


@pytest.fixture
def offline_live(monkeypatch):
    """Live providers whose network is the fictional MOTIS and web."""
    timetable = Timetable()

    class Transitous(TransitousProvider):
        def __init__(self, *arguments, **options):
            super().__init__(client=httpx.AsyncClient(transport=httpx.MockTransport(timetable.handle)), min_interval=0)

    monkeypatch.setattr(query_module, "TransitousProvider", Transitous)
    monkeypatch.setattr(query_module, "PageReader", page_reader)
    monkeypatch.delenv("TRIPWRIGHT_PROVIDERS", raising=False)
    return timetable


def seeded(path) -> None:
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, mode="synthetic"))
    try:
        turn = service.turn("local", "Create the trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=0, plan=PLAN, idempotency_key="seed")
        service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
    finally:
        service.jobs.stop()
        store.close()


def over_mcp(path, kind, arguments):
    store = Store(path)
    service = Tripwright(store, query_router=QueryRouter(store, mode="live"))
    try:
        answer = asyncio.run(create_server(service, manage_jobs=False).call_tool("query", dict(kind=kind, arguments=arguments)))
        return answer.is_error, " ".join(getattr(part, "text", "") for part in answer.content), answer.structured_content
    except ToolError as error:  # in process the refusal is raised; a client receives it as an error result
        return True, str(error).removeprefix("Error executing tool query: "), None
    finally:
        service.jobs.stop()
        store.close()


@pytest.mark.parametrize("kind", list(CASES))
def test_the_command_line_prints_what_the_mcp_tool_returns(tmp_path, offline_live, capsys, kind):
    database = tmp_path / "a5.sqlite3"
    seeded(database)
    arguments, flags = CASES[kind]
    error, _, over_tool = over_mcp(database, kind, arguments)
    assert not error and over_tool["status"] in ("sourced", "recorded")
    assert main(["--db", str(database), "query", kind, *flags]) == 0
    printed = capsys.readouterr()
    on_line = json.loads(printed.out)
    assert steady(on_line) == steady(over_tool)
    assert printed.err.strip() == on_line["summary"]
    assert on_line["evidence"]["id"] != over_tool["evidence"]["id"]  # each call records its own evidence


def test_a_refusal_on_the_command_line_reads_as_over_mcp(tmp_path, offline_live, capsys):
    database = tmp_path / "a5.sqlite3"
    seeded(database)
    error, text, _ = over_mcp(database, "latest_departure", dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-ghost"))
    assert error and "arguments.anchor_id: no hard node 'hn-ghost'" in text
    assert main(["--db", str(database), "query", "latest_departure", "--from", ORIGIN, "--to", DESTINATION, "--anchor", "hn-ghost"]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err.strip() == f"tripwright: {text}"
    assert main(["--db", str(database), "query", "transit", "--arguments", "[1]"]) == 1
    assert "--arguments must be a JSON object" in capsys.readouterr().err


def test_the_command_line_as_a_host_runs_it(tmp_path):
    """The installed entry point in a subprocess, with the demo providers (no network): JSON on stdout, summary on stderr."""
    run = subprocess.run([sys.executable, "-m", "tripwright.cli", "--db", str(tmp_path / "cli.sqlite3"), "query", "web_evidence",
                          "--url", RULE["url"], "--quote", RULE["quote"], "--claim", RULE["claim"], "--type", "rule",
                          "--read-from", "search_snippet", "--providers", "synthetic"],
                         env=environment(), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    printed = json.loads(run.stdout)
    assert printed["counts_as_checked"] is False and printed["evidence"]["read_from"] == "search_snippet"
    assert run.stderr.strip() == printed["summary"] and "不算核过" in run.stderr


# --- real stdio ----------------------------------------------------------------------------------------------------------

async def call(session, tool, arguments):
    answer = await session.call_tool(tool, arguments)
    return answer.is_error, " ".join(getattr(part, "text", "") for part in answer.content), answer.structured_content


async def probe(directory) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            found = {}
            schema = {tool.name: tool.input_schema for tool in (await session.list_tools()).tools}["query"]
            found["schema"] = (schema["properties"]["kind"]["enum"], sorted(schema["properties"]["arguments"]["properties"]))
            _, _, turn = await call(session, "turn", dict(text="Create the trip.", request_id="seed"))
            _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=PLAN, idempotency_key="seed"))
            await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
            found["latest"] = await call(session, "query", dict(kind="latest_departure", arguments=dict(
                origin=ORIGIN, destination=DESTINATION, anchor_id="hn-arrive")))
            found["web"] = await call(session, "query", dict(kind="web_evidence", arguments=RULE))
            found["web time"] = await call(session, "query", dict(kind="web_evidence", arguments=dict(RULE, fetched_at="2030-06-10T09:00:00+02:00")))
            found["record"] = await call(session, "turn", dict(text="Today I am alone.", request_id="alone", record=dict(facts=[dict(
                key="companions", value=[], text="Today p1 is alone", subject=["p1"], true_from="this", invalidated_by="Day ends",
                source=dict(event="this", basis="user_explicit"))])))
            return found


def test_the_new_kinds_and_the_record_path_over_real_stdio(tmp_path):
    found = asyncio.run(probe(tmp_path))
    kinds, properties = found["schema"]
    assert {"latest_departure", "web_evidence"} <= set(kinds)
    assert {"anchor_id", "by", "deadline", "external_deadline", "after", "url", "quote", "claim", "type", "read_from"} <= set(properties)
    error, text, latest = found["latest"]
    assert not error, text
    assert latest["status"] == "unverified" and latest["evidence"]["synthetic"] is True  # demo data: never checked
    assert latest["target"]["deadline"] == "plan" and latest["target"]["limit_from"] == "hn-counter" and "window" in latest
    error, text, web = found["web"]
    assert not error and web["quote_found"] == "unchecked" and web["evidence"]["fetched_at"]
    error, text, _ = found["web time"]
    assert error and "arguments.fetched_at: the service records the time of a web check itself" in text
    error, text, _ = found["record"]  # S5: the message is received; the record is left out with its field path
    assert not error and ("Invalid record: record.facts.0.true_from: must be an ISO time with a timezone offset "
                          "(e.g. 2030-06-10T09:00:00+02:00), got 'this'") in text, text
