"""Client traces: tool-call orders recorded from real clients, replayed through the MCP server.

A trace (tests/client_traces/*.json) is what one real client sent for a few user messages: the client and the day it was
seen, the messages, and each tool call in order with its arguments, on the fictional demo trip only. A value the client
took from an earlier answer (ctx_id, draft_id, restore_version) is written {"$from": <step>, "path": "<dotted path>"}.
Each step may say what its answer must hold (`expect`), that the call is refused (`refused`, words of the error) or
that it could not be sent because an earlier call was refused (`not_sent`); `after` holds read-only calls that check
where the trip ended up. The orders come from live sessions with real hosts (ChatGPT, GLM, Claude, ...) where a host did
something no unit test had tried first; to add one, write the calls as the host made them, with fictional data, and the
outcome the service gives today. Every change to the ledger or the plan design must pass them all."""
from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tripwright.adapters.mcp import create_server
from tripwright.demo import example

TRACES = sorted((Path(__file__).resolve().parents[1] / "client_traces").glob("*.json"))
MISSING = object()


def walk(value, path: str):
    """A dotted path: a key, a list index, `key=value` (the first list element with that value) or `*` (every element)."""
    if not path:
        return value
    head, _, rest = path.partition(".")
    if head == "*":
        if not isinstance(value, list):
            return MISSING
        found = []
        for item in value:
            part = walk(item, rest)
            if part is not MISSING:
                found.extend(part if "*" in rest.split(".") else [part])
        return found
    if isinstance(value, list) and "=" in head:
        key, _, wanted = head.partition("=")
        return walk(next((item for item in value if isinstance(item, dict) and str(item.get(key)) == wanted), MISSING), rest)
    if isinstance(value, list) and head.isdigit():
        return walk(value[int(head)], rest) if int(head) < len(value) else MISSING
    if isinstance(value, dict) and head in value:
        return walk(value[head], rest)
    return MISSING


def resolve(value, answers: dict):
    if isinstance(value, dict) and set(value) == {"$from", "path"}:
        step = value["$from"]
        if step not in answers:
            raise LookupError(step)
        found = walk(answers[step], value["path"])
        assert found is not MISSING, f"step {step} gave no {value['path']}: {json.dumps(answers[step], ensure_ascii=False)[:1500]}"
        return found
    if isinstance(value, dict):
        return {key: resolve(item, answers) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, answers) for item in value]
    return value


def check(where: str, answer: dict, expectations: list[dict]):
    for expected in expectations:
        got = walk(answer, expected["path"])
        context = f"{where}: {expected['path']}"
        shown = json.dumps(answer, ensure_ascii=False)[:2000]
        if "equals" in expected:
            assert got == expected["equals"], f"{context} is {got!r}, expected {expected['equals']!r}; answer: {shown}"
        if "includes" in expected:
            assert isinstance(got, list) and expected["includes"] in got, \
                f"{context} is {got!r}, expected it to include {expected['includes']!r}; answer: {shown}"


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)


def seeded(service, monkeypatch, setup: dict):
    """The fictional demo trip as version 1, the clock at the trace's `now`."""
    assert setup["trip"] == "fjord_demo", "traces use the fictional demo trip only"
    monkeypatch.setenv("TRIPWRIGHT_NOW", setup["now"])
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "Create this fictional example trip.", "seed", scenario="status")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=deepcopy(example()["trip"]), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return create_server(service, local_user="alice", manage_jobs=False)


def test_there_is_a_trace():
    assert TRACES


@pytest.mark.parametrize("path", TRACES, ids=[path.stem for path in TRACES])
def test_a_recorded_client_order_replays_through_mcp(path, service, monkeypatch):
    trace = json.loads(path.read_text(encoding="utf-8"))
    server = seeded(service, monkeypatch, trace["setup"])
    answers = {}
    for number, step in enumerate(trace["steps"], start=1):
        where = f"{path.stem} step {number} ({step['tool']})"
        try:
            arguments = resolve(step["args"], answers)
        except LookupError as missing:
            assert "not_sent" in step, f"{where}: needs step {missing}, which was refused"
            continue
        assert "not_sent" not in step, f"{where}: the trace says it could not be sent, but every value it needs is there"
        try:
            answer = asyncio.run(server.call_tool(step["tool"], arguments)).structured_content
        except ToolError as error:
            assert "refused" in step and step["refused"] in str(error), f"{where} was refused: {error}"
            continue
        assert "refused" not in step, f"{where}: expected a refusal ({step['refused']!r}), got {json.dumps(answer, ensure_ascii=False)[:2000]}"
        answers[number] = answer
        check(where, answer, step.get("expect", []))
    for number, step in enumerate(trace.get("after", []), start=1):
        answer = asyncio.run(server.call_tool(step["tool"], step["args"])).structured_content
        check(f"{path.stem} after {number} ({step['tool']} {step['args']})", answer, step["expect"])
