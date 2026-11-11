#!/usr/bin/env python3
"""A bare chat host: a model that sees only an MCP server, as a chat app does.

It connects to one MCP server (stdio command or Streamable HTTP address), turns `tools/list` into OpenAI function
definitions and runs a scenario against an OpenAI-compatible chat endpoint. No skill, no local file, no hook: the
system prompt is one neutral sentence, plus the server's `instructions` when `--instructions on`.

One user message yields one reply: the model may call tools for any number of rounds (at most --max-rounds) until it
answers without a tool call; that text is the reply the user sees. Text sent together with tool calls is kept apart
as "mid text". Each run writes a directory with `transcript.json` and `metrics.json`.

Scenario file (YAML):

    name: s1_first_use
    language: en                      # label only
    seed: demo-coords                 # optional, stdio only: the fictional fjord trip (`demo`: as shipped, places without
                                      # coordinates; `demo-coords`: the same places with invented coordinates)
    now: "2030-06-10T20:30:00+02:00"  # optional, stdio only: the server's pinned clock (TRIPWRIGHT_NOW)
    messages:
      - "first user message"
      - "second user message"

With --mcp-stdio the host starts the server itself and passes TRIPWRIGHT_DB (the seeded database), TRIPWRIGHT_PROVIDERS
(synthetic by default), TRIPWRIGHT_NOW and TRIPWRIGHT_NOW_SET_AT in its environment. With --mcp-url the server is
already running: the host seeds and pins nothing (the scenario's seed and now are ignored, noted in metrics).

Example (from the repository root):

    uv run python scripts/chat_host.py \\
      --mcp-stdio "uv run python -m tripwright.cli serve --stdio" \\
      --scenario scripts/chat_scenarios/s1_first_use_en.yaml --model group/glm --instructions on \\
      --out runs/example

`chat_host.py --reanalyze DIR...` recomputes the per-message figures of finished runs from their transcript.json.

Exit status: 0 when the run finished (a message that hit the round limit is recorded as failed in metrics, status
"completed_with_failures", exit 1); 2 when the run could not finish (API or server error, bad arguments): metrics.json then
says `"status": "failed"` with the error text, and no per-message success figures are written.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from datetime import datetime, timedelta
from pathlib import Path

import httpx2
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

SYSTEM_PROMPT = "You are a helpful assistant in a chat app. Answer the user in the language they write in."
INSTRUCTIONS_HEADER = "Instructions from the connected MCP server \"{name}\":\n\n"
REPO = Path(__file__).resolve().parents[1]

SEED_CODE = """
import sys
from pathlib import Path
import tripwright.demo as demo
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store

# The shipped demo places carry no coordinates and no stop IDs, so a chat model naming any of them is answered with
# "not a stored place" (service._unknown_places). `demo-coords` gives the same fictional places invented coordinates.
COORDS = {"oslo-art": (59.9139, 10.7522), "oslo-cafe": (59.9101, 10.7476), "oslo-park": (59.9220, 10.7390),
          "oslo-station": (59.9110, 10.7503), "bergen-view": (60.3989, 5.3210), "bergen-food": (60.3913, 5.3221)}
if sys.argv[3] == "demo-coords":
    original = demo.example

    def example():
        data = original()
        for place in data["trip"]["places"]:
            place["coords"] = list(COORDS[place["id"]])
        return data

    demo.example = example

store = Store(Path(sys.argv[1]))
try:
    demo.run_demo(Tripwright(store, query_router=QueryRouter(store, mode="synthetic")), owner=sys.argv[2], output=lambda *_: None)
finally:
    store.close()
"""


class HostError(Exception):
    """A failure that ends the run: the cause is printed, written to metrics.json and the exit status is 2."""


def leaves(error: BaseException) -> list[BaseException]:
    """The innermost exceptions of an anyio/ExceptionGroup nest (the MCP client wraps what a run raises)."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in leaves(inner)]
    return [error]


def now_s() -> float:
    return time.monotonic()


def norm(text: str) -> str:
    """Compare texts without white space, quote marks or markdown emphasis (a chat app renders those anyway)."""
    return re.sub(r"[\s\"'“”‘’「」『』*_`]+", "", text or "")


def tool_result_text(result) -> str:
    parts = [content.text for content in result.content if getattr(content, "text", None) is not None]
    if parts:
        return "\n".join(parts)
    if getattr(result, "structured_content", None) is not None:
        return json.dumps(result.structured_content, ensure_ascii=False)
    return ""


def turn_info(text: str) -> tuple[str | None, str | None]:
    """(`first_part.text`, `do_first`) of a turn result's JSON text; None where the result has no such field."""
    try:
        data = json.loads(text)
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    part = data.get("first_part")
    first = part["text"] if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip() else None
    do_first = data.get("do_first") if isinstance(data.get("do_first"), str) else None
    return first, do_first


def openai_tools(listing) -> list[dict]:
    return [{"type": "function", "function": {"name": tool.name, "description": tool.description or "",
                                              "parameters": tool.input_schema}} for tool in listing.tools]


def patch_enum_types(node, counter: list) -> None:
    """Opt-in host-side patch (--patch-schema): a JSON Schema node with `enum` and no `type` (the `priority` of
    query's plan_day candidates mixes strings and integers) is refused by Kimi's strict validator. Give such a node
    `type: string` and keep only its string values. The server's own tools/list is not touched; the count goes to metrics."""
    if isinstance(node, dict):
        if "enum" in node and "type" not in node and not any(key in node for key in ("anyOf", "oneOf", "allOf", "$ref")):
            node["enum"] = [value for value in node["enum"] if isinstance(value, str)]
            node["type"] = "string"
            counter.append(1)
        for value in node.values():
            patch_enum_types(value, counter)
    elif isinstance(node, list):
        for value in node:
            patch_enum_types(value, counter)


def seed_database(directory: Path, owner: str, moment: datetime | None, kind: str) -> Path:
    """Run the fictional demo (demo.run_demo) into a new database on a clock one day before the scenario's `now`."""
    database = directory / "seed.sqlite3"
    environment = dict(os.environ, TRIPWRIGHT_PROVIDERS="synthetic")
    if moment is not None:
        environment.update(TRIPWRIGHT_NOW=(moment - timedelta(days=1)).isoformat(), TRIPWRIGHT_NOW_SET_AT=str(time.time()))
    done = subprocess.run([sys.executable, "-c", SEED_CODE, str(database), owner, kind], env=dict(environment, PYTHONPATH=str(REPO / "src")),
                          capture_output=True, text=True, cwd=str(directory))
    if done.returncode != 0:
        raise HostError("seeding the demo trip failed:\n" + done.stderr[-2000:])
    return database


class Api:
    def __init__(self, base: str, model: str, timeout: float, extra: dict, retries: int = 2):
        self.url = base.rstrip("/") + "/chat/completions"
        self.model, self.extra, self.retries = model, extra, retries
        self.client = httpx2.AsyncClient(timeout=timeout)
        self.errors: list[dict] = []  # every failed attempt, raw, for the run's record

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        body = dict(model=self.model, messages=messages, tools=tools, tool_choice="auto", **self.extra)
        last = ""
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.post(self.url, json=body)
            except httpx2.HTTPError as error:
                last = f"{type(error).__name__}: {error}"
                self.errors.append(dict(attempt=attempt, kind="transport", error=last))
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if response.status_code == 200:
                try:
                    data = response.json()
                    data["choices"][0]["message"]
                    return data
                except (ValueError, KeyError, IndexError):
                    last = "unreadable 200 body: " + response.text[:1500]
                    self.errors.append(dict(attempt=attempt, kind="bad_body", error=last))
                    continue
            last = f"HTTP {response.status_code}: {response.text[:1500]}"
            self.errors.append(dict(attempt=attempt, kind="http", status=response.status_code, error=last))
            if response.status_code < 500 and response.status_code != 429:
                break  # a request the gateway refuses (unknown model, bad body) is not retried
            await asyncio.sleep(3 * (attempt + 1))
        raise HostError(f"chat API call failed for model {self.model!r} at {self.url}: {last}")

    async def aclose(self):
        await self.client.aclose()


async def run_message(api: Api, session: ClientSession, tools: list[dict], messages: list[dict], user_text: str,
                      max_rounds: int, transcript: list[dict]) -> dict:
    messages.append({"role": "user", "content": user_text})
    started = now_s()
    entry = dict(user=user_text, rounds=[], reply=None, mid_texts=[], tool_calls=[], usage=[], failed=None)
    transcript.append(entry)
    for round_number in range(1, max_rounds + 2):
        if round_number > max_rounds:
            entry["failed"] = f"tool_rounds_exceeded: more than {max_rounds} rounds of tool calls without a text reply"
            break
        api_started = now_s()
        data = await api.complete(messages, tools)
        message = data["choices"][0]["message"]
        usage = data.get("usage") or {}
        entry["usage"].append(dict(round=round_number, seconds=round(now_s() - api_started, 2), **{key: usage.get(key) for key in (
            "prompt_tokens", "completion_tokens", "total_tokens")}))
        content = message.get("content") or ""
        calls = message.get("tool_calls") or []
        entry["rounds"].append(dict(round=round_number, finish_reason=data["choices"][0].get("finish_reason"), text=content,
                                    reasoning=message.get("reasoning_content") or message.get("reasoning"),
                                    tool_calls=[call["function"]["name"] for call in calls]))
        kept = {key: value for key, value in message.items() if value is not None and key in (
            "role", "content", "tool_calls", "reasoning_content")}
        kept.setdefault("role", "assistant")
        if calls:
            kept["content"] = content or None
        messages.append(kept)
        if not calls:
            entry["reply"] = content
            break
        if content.strip():
            entry["mid_texts"].append(content)
        for call in calls:
            name = call["function"]["name"]
            raw_arguments = call["function"].get("arguments") or "{}"
            record = dict(round=round_number, name=name, arguments=raw_arguments, result_bytes=0, seconds=0.0, error=None, result=None)
            entry["tool_calls"].append(record)
            began = now_s()
            try:
                arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments are not a JSON object")
            except ValueError as error:
                text = f"Error: the tool arguments are not valid JSON ({error})"
                record["error"] = text
            else:
                try:
                    result = await session.call_tool(name, arguments)
                except Exception as error:  # protocol-level failure (unknown tool, schema rejection, transport)
                    text = f"Error calling tool {name}: {type(error).__name__}: {error}"
                    record["error"] = text
                else:
                    text = tool_result_text(result)
                    if result.is_error:
                        record["error"] = text
            record.update(seconds=round(now_s() - began, 2), result_bytes=len(text.encode()), result=text)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or f"call_{len(entry['tool_calls'])}", "content": text})
    entry["seconds"] = round(now_s() - started, 2)
    return entry


def message_metrics(entry: dict) -> dict:
    calls = entry["tool_calls"]
    reply = entry["reply"]
    usage = entry["usage"]
    turns = [turn_info(call["result"] or "") for call in calls if call["name"] == "turn" and not call["error"]]
    last_part, last_do_first = turns[-1] if turns else (None, None)
    # New servers identify the first part directly; old baseline servers used do_first.
    wants_first_part = bool(last_part) and (last_do_first is None or "first_part" in last_do_first)
    shown = "\n".join([*entry["mid_texts"], reply or ""])
    verbatim = norm(last_part) in norm(shown) if wants_first_part and last_part and shown else None
    before_query = None
    if wants_first_part and last_part:
        text_round = next((round_["round"] for round_ in entry["rounds"] if norm(last_part) in norm(round_["text"])), None)
        query_round = next((round_["round"] for round_ in entry["rounds"] if "query" in round_["tool_calls"]), None)
        before_query = bool(text_round is not None and query_round is not None and text_round <= query_round)
    return dict(
        user=entry["user"], failed=entry["failed"], reply_chars=len(reply) if reply is not None else None,
        tool_call_count=len(calls), tool_order=[call["name"] for call in calls], tool_rounds=len(entry["rounds"]),
        seconds_to_reply=entry["seconds"],
        tokens=dict(prompt=sum(item.get("prompt_tokens") or 0 for item in usage), completion=sum(item.get("completion_tokens") or 0 for item in usage),
                    total=sum(item.get("total_tokens") or 0 for item in usage), api_calls=len(usage),
                    first_call_prompt=usage[0].get("prompt_tokens") if usage else None),
        first_tool_is_turn=bool(calls) and calls[0]["name"] == "turn",
        turn_wants_first_part=wants_first_part, first_part_verbatim_in_reply=verbatim,
        first_part_before_query=before_query,
        mid_text_segments=len(entry["mid_texts"]),
        tool_errors=[dict(name=call["name"], round=call["round"], error=call["error"]) for call in calls if call["error"]],
    )


async def run(arguments, scratch: Path, transcript: list[dict]) -> tuple[dict, dict]:
    scenario = yaml.safe_load(Path(arguments.scenario).read_text(encoding="utf-8"))
    if not isinstance(scenario, dict) or not scenario.get("messages"):
        raise HostError(f"scenario {arguments.scenario} needs a `messages` list")
    moment = datetime.fromisoformat(str(scenario["now"])) if scenario.get("now") else None
    if moment is not None and moment.tzinfo is None:
        raise HostError("scenario `now` needs a UTC offset, e.g. 2030-06-10T20:30:00+02:00")
    setup: dict = dict(seeded=False, clock=None, providers=None)
    async with AsyncExitStack() as stack:
        if arguments.mcp_stdio:
            environment = dict(os.environ, TRIPWRIGHT_PROVIDERS=arguments.providers, TRIPWRIGHT_REPLY_FLOW=arguments.reply_flow)
            setup["providers"] = arguments.providers
            setup["reply_flow"] = arguments.reply_flow
            if scenario.get("seed") in ("demo", "demo-coords"):
                database = seed_database(scratch, arguments.local_user, moment, scenario["seed"])
                environment["TRIPWRIGHT_DB"] = str(database)
                setup["seeded"] = True
            elif scenario.get("seed"):
                raise HostError(f"unknown seed {scenario['seed']!r}; only `demo` and `demo-coords` exist")
            else:
                environment["TRIPWRIGHT_DB"] = str(scratch / "empty.sqlite3")
            if moment is not None:
                environment.update(TRIPWRIGHT_NOW=moment.isoformat(), TRIPWRIGHT_NOW_SET_AT=str(time.time()))
                setup["clock"] = moment.isoformat()
            words = shlex.split(arguments.mcp_stdio)
            parameters = StdioServerParameters(command=words[0], args=words[1:], env=environment, cwd=str(REPO))
            try:
                streams = await stack.enter_async_context(stdio_client(parameters))
            except Exception as error:
                raise HostError(f"cannot start the MCP server {arguments.mcp_stdio!r}: {error}") from error
        else:
            headers = {"Authorization": f"Bearer {arguments.token}"} if arguments.token else {}
            client = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, timeout=60))
            try:
                streams = await stack.enter_async_context(streamable_http_client(arguments.mcp_url, http_client=client))
            except Exception as error:
                raise HostError(f"cannot connect to {arguments.mcp_url}: {error}") from error
            setup["note"] = "mcp-url: the host seeded nothing and pinned no clock; the scenario's seed and now were ignored"
        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        try:
            init = await asyncio.wait_for(session.initialize(), 60)
        except Exception as error:
            raise HostError(f"MCP initialize failed: {type(error).__name__}: {error}") from error
        listing = await session.list_tools()
        tools = openai_tools(listing)
        patched: list = []
        if arguments.patch_schema:
            patch_enum_types(tools, patched)
        setup["schema_patched_nodes"] = len(patched)
        listing_bytes = len(json.dumps([tool.model_dump(by_alias=True, exclude_none=True, mode="json") for tool in listing.tools],
                                       ensure_ascii=False).encode())
        instructions = init.instructions or ""
        server_name = init.server_info.name if init.server_info else "server"
        system = SYSTEM_PROMPT
        if arguments.instructions == "on" and instructions:
            system += "\n\n" + INSTRUCTIONS_HEADER.format(name=server_name) + instructions
        messages = [{"role": "system", "content": system}]
        api = Api(arguments.api_base, arguments.model, arguments.timeout, extra=json.loads(arguments.extra_body or "{}"))
        try:
            for user_text in scenario["messages"]:
                await run_message(api, session, tools, messages, str(user_text), arguments.max_rounds, transcript)
        finally:
            await api.aclose()
            setup["api_errors"] = api.errors
    metrics = dict(
        scenario=scenario.get("name") or Path(arguments.scenario).stem, language=scenario.get("language"), model=arguments.model,
        instructions=arguments.instructions, tools_list_bytes=listing_bytes, openai_tools_bytes=len(json.dumps(tools, ensure_ascii=False).encode()),
        tool_count=len(tools), server_instructions_bytes=len(instructions.encode()),
        instructions_in_system_prompt=arguments.instructions == "on" and bool(instructions), setup=setup,
        messages=[message_metrics(entry) for entry in transcript],
    )
    failures = [item for item in metrics["messages"] if item["failed"]]
    metrics["status"] = "completed_with_failures" if failures else "completed"
    transcript_doc = dict(system_prompt=system, scenario=scenario, tool_names=[tool.name for tool in listing.tools], messages=transcript)
    return metrics, transcript_doc


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--mcp-stdio", help="command that starts the MCP server on stdio (run in the repository root)")
    where.add_argument("--mcp-url", help="Streamable HTTP address of a running MCP server")
    parser.add_argument("--token", default=os.environ.get("CHAT_HOST_MCP_TOKEN"), help="bearer token for --mcp-url (or CHAT_HOST_MCP_TOKEN)")
    parser.add_argument("--api-base", default="http://127.0.0.1:3425/v1", help="OpenAI-compatible base address (default: local gateway, no key)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--scenario", required=True, help="scenario YAML file")
    parser.add_argument("--instructions", choices=["on", "off"], required=True, help="put the server's instructions into the system prompt or not")
    parser.add_argument("--out", required=True, help="run directory (created; must not hold a finished run)")
    parser.add_argument("--max-rounds", type=int, default=20, help="tool-call rounds allowed per user message (default 20)")
    parser.add_argument("--timeout", type=float, default=300, help="seconds per chat API call (default 300)")
    parser.add_argument("--providers", choices=["synthetic", "live"], default="synthetic", help="stdio only: TRIPWRIGHT_PROVIDERS (default synthetic)")
    parser.add_argument("--reply-flow", choices=["one", "two"], default="one", help="stdio only: one reply with both parts, or end after the first part")
    parser.add_argument("--local-user", default="local", help="stdio only: the server's local user, owner of the seeded trip")
    parser.add_argument("--patch-schema", action="store_true",
                        help="host-side patch for strict validators (Kimi): enum nodes without `type` get type string; the count is in metrics")
    parser.add_argument("--extra-body", help="JSON object merged into every chat request body (e.g. reasoning settings)")
    return parser.parse_args(argv)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def redacted_argv(argv: list[str]) -> list[str]:
    """Keep the reproducible command without persisting the private URL key."""
    return [argument.split("/mcp/", 1)[0] + "/mcp/***" if "/mcp/" in argument else argument for argument in argv]


def reanalyze(directories: list[str]) -> int:
    """`chat_host.py --reanalyze DIR...`: recompute the per-message figures of finished runs from their transcript.json
    (after message_metrics changed); everything else in metrics.json stays."""
    for name in directories:
        directory = Path(name)
        metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        if not str(metrics.get("status", "")).startswith("completed"):
            continue
        transcript = json.loads((directory / "transcript.json").read_text(encoding="utf-8"))
        metrics["messages"] = [message_metrics(entry) for entry in transcript["messages"]]
        metrics["status"] = "completed_with_failures" if any(item["failed"] for item in metrics["messages"]) else "completed"
        write_json(directory / "metrics.json", metrics)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--reanalyze":
        return reanalyze(argv[1:])
    arguments = parse(argv)
    out = Path(arguments.out)
    if (out / "metrics.json").exists() and json.loads((out / "metrics.json").read_text()).get("status", "").startswith("completed"):
        print(f"chat_host: {out} already holds a finished run; use a new --out", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("metrics.json", "transcript.json", "partial_transcript.json"):
        (out / stale).unlink(missing_ok=True)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    run_info = dict(started=started, argv=redacted_argv(sys.argv[1:]))
    partial: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="chat-host-") as scratch:
        try:
            metrics, transcript_doc = asyncio.run(run(arguments, Path(scratch), partial))
        except BaseException as caught:
            host = [leaf for leaf in leaves(caught) if isinstance(leaf, HostError)]
            if host:
                message = str(host[0])
                print(f"chat_host: {message}", file=sys.stderr)
            else:  # anything else is a failure too: never leave a half run that looks finished
                message = "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves(caught))
                import traceback
                traceback.print_exception(caught)
            write_json(out / "metrics.json", dict(status="failed", error=message, model=arguments.model, **run_info))
            if partial:
                write_json(out / "partial_transcript.json", partial)
            return 2
    metrics.update(run_info)
    write_json(out / "transcript.json", transcript_doc)
    write_json(out / "metrics.json", metrics)
    for item in metrics["messages"]:
        print(f"[{item['seconds_to_reply']:>6}s] tools={item['tool_order']} first_turn={item['first_tool_is_turn']} "
              f"verbatim={item['first_part_verbatim_in_reply']} errors={len(item['tool_errors'])} failed={item['failed']}")
    print(f"{metrics['status']}: {out}")
    return 0 if metrics["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
