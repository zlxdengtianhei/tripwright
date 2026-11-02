"""Counter-example B of judge round 4: nested tool inputs of the wrong shape are refused at the boundary with the field
path, over the service and over real stdio and Streamable HTTP clients; the legal shape still closes the check."""
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
from pydantic import ValidationError

from test_b3d_lock_aliases import trip
from test_b3d_mcp import environment
from tripwright.ledger import Changes, Item

BAD_IDS = [[], {"id": "P-x"}]


def pending_check(event_id: str) -> dict:
    return dict(question="Is the lake path open?", due_at="2030-06-10T15:00:00+02:00", responsible="host", subject=["p1"],
                source=dict(event=event_id), consequence="No lake walk", alternative="Take the bus")


def resolution(identifier) -> dict:
    return dict(id=identifier, status="unverified", result="Not found yet", evidence_ids=[])


@pytest.mark.parametrize("identifier", BAD_IDS, ids=["array", "object"])
def test_resolve_pending_id_of_the_wrong_type_is_refused_with_its_path(identifier):
    with pytest.raises(ValidationError) as refused:
        Changes.model_validate(dict(resolve_pending=[resolution(identifier)]))
    assert refused.value.errors()[0]["loc"] == ("resolve_pending", 0, "id") and "valid string" in str(refused.value)


def test_check_predicate_with_a_wrong_nested_type_is_refused_with_its_field(item):
    with pytest.raises(ValidationError, match="check.value must be a number, got list"):
        Item.model_validate(dict(item, check=dict(metric="walk_minutes", op="max", value=[60])))
    with pytest.raises(ValidationError, match="check.currency must be a string"):
        Item.model_validate(dict(item, check=dict(metric="budget", op="max", value=60, currency=["EUR"])))
    assert Item.model_validate(dict(item, check=dict(metric="walk_minutes", op="max", value=60))).check["value"] == 60


def test_a_string_id_closes_the_pending_check_and_commits(seeded):
    turn = seeded.turn("alice", "Check the lake path.", "add-check")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="add-check",
                           changes=dict(pending_checks=[pending_check(turn["receipt"]["event_id"])]))
    seeded.commit("alice", draft["draft_id"], "add-check")
    check_id = seeded.store.all("pending_checks", "alice")[0]["id"]
    turn = seeded.turn("alice", "Not found yet.", "close-check")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=2, idempotency_key="close-check",
                           changes=dict(resolve_pending=[resolution(check_id)]))
    seeded.commit("alice", draft["draft_id"], "close-check")
    assert seeded.store.get("pending_checks", "alice", check_id)["status"] == "unverified"


async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def probe(session) -> dict:
    results = {}
    _, _, turn = await call(session, "turn", dict(text="Create", request_id="seed"))
    _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed"))
    await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
    _, _, turn = await call(session, "turn", dict(text="Check the lake path.", request_id="add-check"))
    _, _, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="add-check",
                                                      changes=dict(pending_checks=[dict(pending_check(turn["receipt"]["event_id"]), subject=["*"])])))
    await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="add-check"))
    check_id = (await call(session, "state", dict(view="ledger")))[2]["pending_checks"][0]["id"]
    for label, identifier in zip(("array", "object"), BAD_IDS, strict=True):
        _, _, turn = await call(session, "turn", dict(text="Not found yet.", request_id=f"bad-{label}"))
        error, text, _ = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                                                             idempotency_key=f"bad-{label}", changes=dict(resolve_pending=[resolution(identifier)])))
        results[label] = (error, "resolve_pending" in text and "id" in text and "valid string" in text, text)
    _, _, turn = await call(session, "turn", dict(text="Not found yet.", request_id="good"))
    error, text, draft = await call(session, "propose", dict(ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                                                             idempotency_key="good", changes=dict(resolve_pending=[resolution(check_id)])))
    error_commit, text_commit, _ = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="good"))
    ledger = (await call(session, "state", dict(view="ledger")))[2]
    closed = [check["status"] for check in ledger["pending_checks"] if check["id"] == check_id]
    results["legal"] = (error or error_commit, closed == ["unverified"], text + text_commit)
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
    variables = dict(environment(), TRIPWRIGHT_TOKENS=json.dumps({"inputs-only-token": "alice"}))
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"), "serve", "--http", "--port", str(port)],
                               env=variables, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    address = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx2.AsyncClient() as client:
            for _ in range(200):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited: " + process.stderr.read())
                try:
                    if (await client.get(address)).status_code == 401:
                        break
                except httpx2.TransportError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("HTTP server did not start")
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer inputs-only-token"}) as client:
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
def test_resolve_pending_shapes_over_real_mcp_transports(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert set(results) == {"array", "object", "legal"}, results
    for label, (error, readable, text) in results.items():
        if label == "legal":
            assert not error and readable, (label, text)
        else:
            assert error and readable, (label, text)
