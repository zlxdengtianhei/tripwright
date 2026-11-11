"""B3g over real stdio and Streamable HTTP clients (D-49). Counterexample A: a new user decision from a screenshot
extraction, an outside speaker or an earlier user message is refused naming the admission check, and the lock keeps its
value; the user's own decision in this turn replaces the lock, and propose and commit say so in lock_changes
(counterexample B). Counterexample C: each of the ten wrong turn.record shapes is refused with its field path and the
type it needs; the same part sent well-formed is recorded."""
from __future__ import annotations

import asyncio
import json
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

from test_b3d_mcp import environment
from test_b3e_reference_graph import GROUP, LOCKED, node
from test_b3f_three_paths import ENTRY, fixture
from test_b3g_admission import SAID, SHAPES, bad_record, carried, valid_record
from tripwright.storage import Store

LOCK = f"/blocks/{LOCKED}"


async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    return response.is_error, " ".join(getattr(content, "text", "") for content in response.content), response.structured_content


async def turn(session, key, text, **arguments):
    error, text, data = await call(session, "turn", dict(text=text, request_id=key, **arguments))
    assert not error, text
    return data


def decision(context, block, cited=None):
    return dict(question="门票怎么买", options=["已经买好"], choice="已经买好", decided_by="user", must=True, subject=GROUP,
                source=dict(event=(cited or context)["receipt"]["event_id"], basis="user_explicit"), locked_paths={LOCK: block})


async def scenario(session) -> dict:
    context = await turn(session, "seed", "Create")
    _, _, draft = await call(session, "propose", dict(ctx_id=context["ctx_id"], base_version=0, plan=fixture(), idempotency_key="seed"))
    await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
    plan = (await call(session, "state", {}))[2]["plan"]
    context = await turn(session, "lock", "整个活动和门票都定了：现场买。", nature="user_verbatim", native_ref="fixture:lock", speaker="u1")
    _, _, draft = await call(session, "propose", dict(ctx_id=context["ctx_id"], base_version=context["base_version"], idempotency_key="lock",
                                                      changes=dict(decisions=[decision(context, node(plan, f"block:{LOCKED}"))])))
    await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key="lock"))
    results = {}

    async def decide(label, text, *, nature="user_verbatim", speaker="u1", earlier=False):
        cited = await turn(session, label, text, nature=nature, native_ref=f"fixture:{label}" if nature == "user_verbatim" else None, speaker=speaker)
        context = await turn(session, f"{label}-agent", "agent records it", nature="agent_inference") if earlier else cited
        candidate = deepcopy((await call(session, "state", {}))[2]["plan"])
        node(candidate, ENTRY)["status"] = "bought"
        error, text, draft = await call(session, "propose", dict(
            ctx_id=context["ctx_id"], base_version=context["base_version"], idempotency_key=label, plan=candidate,
            changes=dict(decisions=[decision(context, node(candidate, f"block:{LOCKED}"), cited)])))
        version = None
        if not error:
            error, text, version = await call(session, "commit", dict(draft_id=draft["draft_id"], idempotency_key=label, accept_unverified=True))
        status = node((await call(session, "state", {}))[2]["plan"], ENTRY)["status"]
        return cited, error, text, draft, version, status

    for label, arguments, check in (("screenshot", dict(nature="screenshot_extraction"), "nature"),
                                    ("outsider", dict(speaker="outsider"), "speaker"),
                                    ("earlier", dict(earlier=True), "this turn")):
        _, error, text, _, _, status = await decide(label, "门票改成已经买好，定了。", **arguments)
        results[f"A-{label}"] = (error and f"not admitted ({check})" in text and status == "on_site", text[:300])
    cited, error, text, draft, version, status = await decide("legal", "门票我们改成已经买好，定了。")
    event = cited["receipt"]["event_id"]
    proposed, committed = (draft or {}).get("lock_changes", {}), (version or {}).get("lock_changes", {})
    results["A-legal"] = (not error and status == "bought", text[:300])
    results["B-legal"] = (proposed.get("decisions", [{}])[0].get("event") == event and proposed["decisions"][0]["takes_effect"] == "on commit"
                          and committed.get("decisions", [{}])[0].get("event") == event and committed["decisions"][0]["locks"] == [LOCK]
                          and len(committed["decisions"][0]["replaces"]) == 1, json.dumps([proposed, committed], ensure_ascii=False)[:600])

    for part, shape in SHAPES:
        record, refused = bad_record(part, shape)
        error, text, data = await call(session, "turn", dict(text=SAID, request_id=f"bad-{part}-{shape}", record=record))
        # S5: received, the record left out whole with the same words
        reason = (((data or {}).get("recorded") or {}).get("not_recorded") or [{}])[0].get("reason", "")
        results[f"C-{part}-{shape}"] = (not error and reason.startswith(f"Invalid record: {refused}") and "got array" in reason, text[:300])
    for part in dict.fromkeys(part for part, _ in SHAPES):
        error, text, data = await call(session, "turn", dict(text=SAID, request_id=f"good-{part}", nature="user_verbatim",
                                                              native_ref=f"fixture:good-{part}", speaker="u1", record=valid_record(part)))
        results[f"C-{part}-valid"] = (not error and bool(data["recorded"]["changed"]) and not data["recorded"]["not_recorded"], text[:300])
    return results


def seeded(path: Path, owner: str):
    """The server's database with a long-term entry already imported, so a well-formed carry record has one to answer."""
    store = Store(path)
    try:
        carried(store, owner)
    finally:
        store.close()


async def over_stdio(directory: Path) -> dict:
    seeded(directory / "stdio.sqlite3", "local")
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "stdio.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            return await scenario(session)


async def over_http(directory: Path) -> dict:
    seeded(directory / "http.sqlite3", "local")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    key = "b3g-admission-" + "k" * 40
    variables = dict(environment(), TRIPWRIGHT_URL_KEY=key)
    process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(directory / "http.sqlite3"), "serve", "--http", "--port", str(port)],
                               env=variables, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}/mcp"
    address = base + "/" + key
    try:
        async with httpx2.AsyncClient() as client:
            for _ in range(1200):  # up to a minute on a loaded machine
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
                    return await scenario(session)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()



@pytest.mark.parametrize("transport", [over_stdio, over_http], ids=["stdio", "http"])
def test_admission_receipts_and_record_shapes_over_real_mcp_transports(tmp_path, transport):
    results = asyncio.run(transport(tmp_path))
    assert len(results) == 4 + 1 + 10 + 5, sorted(results)
    failed = {label: text for label, (passed, text) in results.items() if not passed}
    assert not failed, failed
