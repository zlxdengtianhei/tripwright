#!/usr/bin/env python3
"""Run release preflight checks against fresh, local, synthetic Tripwright servers.

Each check seeds demo-coords at 2030-06-10T20:30:00+02:00, starts an HTTP server on
127.0.0.1 with a random URL key, and stops it afterwards. Conformance invokes
``npx -y @modelcontextprotocol/conformance@0.1.16``; Codex checks invoke the
separately installed, already logged-in ``codex`` CLI (model gpt-6-luna by default).
Thus npx/network access and Codex login are prerequisites for those respective checks.

result.json separates passed, failed and not_verified checks and contains Codex
observations; each check's redacted raw output is in <check>.txt. Failures make the
exit code 1; unavailable or skipped checks alone do not. No global Codex config changes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx2
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chat_host import REPO, seed_database, source_commit  # noqa: E402

CONFORMANCE_VERSION = "0.1.16"
CONFORMANCE_PACKAGE = f"@modelcontextprotocol/conformance@{CONFORMANCE_VERSION}"
CONFORMANCE_SCENARIOS = ("server-initialize", "ping", "tools-list")
CODEX_SCENARIOS = ("s1_first_use_en", "s2_on_the_road_zh", "s3_change_and_remember_zh")
MOMENT = datetime.fromisoformat("2030-06-10T20:30:00+02:00")


def redact(text: str) -> str:
    return re.sub(r"/mcp/[^\s\"'<>]+", "/mcp/***", text)


def write_json(path: Path, data) -> None:
    path.write_text(redact(json.dumps(data, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")

def parse_events(text: str) -> dict:
    observed = dict(thread_id=None, tools=[], successful_tools=[], tool_sequence=[], first_tool_is_turn=False,
                    web_searches=0, replies=[], reply="", usage=None)
    for line in text.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("type") == "thread.started":
            observed["thread_id"] = data.get("thread_id")
        elif data.get("type") == "turn.completed":
            observed["usage"] = data.get("usage")
        item = data.get("item") or {}
        if data.get("type") != "item.completed":
            continue
        if item.get("type") == "agent_message":
            reply = item.get("text") or ""
            observed["replies"].append(reply)
            observed["reply"] = reply
        elif item.get("type") == "web_search":
            observed["web_searches"] += 1
            observed["tool_sequence"].append("web_search")
        elif item.get("type") == "mcp_tool_call" and item.get("server") == "tripwright":
            tool = item.get("tool")
            observed["tools"].append(tool)
            observed["tool_sequence"].append(tool)
            result = item.get("result") or {}
            failed = item.get("error") or result.get("isError") or result.get("is_error")
            if item.get("status") in ("completed", "success") and not failed:
                observed["successful_tools"].append(tool)
    observed["first_tool_is_turn"] = bool(observed["tools"]) and observed["tools"][0] == "turn"
    return observed


def s1_passed(observed: dict) -> bool:
    reply = observed.get("reply") or ""
    return bool(observed.get("successful_tools")) and ("fjord-friends" in reply or "Fictional fjord friends" in reply)

def observation_view(observed: dict) -> dict:
    view = dict(observed)
    view["replies"] = [reply[:300] for reply in observed["replies"]]
    view["reply"] = (observed.get("reply") or "")[:300]
    return view


@contextmanager
def service():
    with tempfile.TemporaryDirectory(prefix="release-check-") as scratch:
        directory = Path(scratch)
        database = seed_database(directory, "local", MOMENT, "demo-coords")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        key = secrets.token_urlsafe(32)
        environment = dict(os.environ, TRIPWRIGHT_URL_KEY=key, TRIPWRIGHT_PROVIDERS="synthetic",
                           TRIPWRIGHT_NOW=MOMENT.isoformat(), TRIPWRIGHT_NOW_SET_AT=str(time.time()))
        process = subprocess.Popen([sys.executable, "-m", "tripwright.cli", "--db", str(database), "serve", "--http",
                                    "--host", "127.0.0.1", "--port", str(port)], cwd=REPO, env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(200):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited: " + process.stderr.read())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("HTTP server did not start")
            yield f"http://127.0.0.1:{port}/mcp/{key}"
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def external(command: list[str], cwd: Path | None = None) -> tuple[subprocess.CompletedProcess, float]:
    started = time.monotonic()
    done = subprocess.run(command, cwd=cwd or REPO, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    return done, round(time.monotonic() - started, 2)


def conformance(scenario: str) -> tuple[dict, str]:
    with service() as url:
        command = ["npx", "-y", CONFORMANCE_PACKAGE, "server", "--url", url, "--scenario", scenario]
        done, seconds = external(command)
    raw = "$ " + " ".join(command) + "\n" + done.stdout + done.stderr
    status = "passed" if done.returncode == 0 else "failed"
    return dict(status=status, reason=f"exit code {done.returncode}", seconds=seconds), raw


async def state_number_async(url: str) -> int:
    async with httpx2.AsyncClient(timeout=60) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool("state", {})
                if result.is_error:
                    raise RuntimeError("state returned an error")
                return int(result.structured_content["number"])


def codex_message(url: str, model: str, cwd: Path, text: str, thread: str | None = None) -> tuple[dict, str]:
    config = f'mcp_servers.tripwright.url="{url}"'
    if thread:
        command = ["codex", "exec", "resume", thread, "-m", model, "--skip-git-repo-check", "--json", "-c", config, text]
    else:
        command = ["codex", "exec", "-m", model, "--skip-git-repo-check", "-C", str(cwd), "-s", "read-only",
                   "--json", "-c", config, text]
    done, seconds = external(command, cwd)
    observed = parse_events(done.stdout)
    observed.update(seconds=seconds, exit_code=done.returncode)
    return observed, "$ " + " ".join(command) + "\n" + done.stdout + done.stderr


def codex_scenario(stem: str, model: str) -> tuple[dict, str]:
    scenario = yaml.safe_load((REPO / "scripts/chat_scenarios" / f"{stem}.yaml").read_text(encoding="utf-8"))
    observations, raw_parts = [], []
    with service() as url, tempfile.TemporaryDirectory(prefix="codex-release-") as scratch:
        before = asyncio.run(state_number_async(url)) if stem.startswith("s3_") else None
        thread = None
        for text in scenario["messages"]:
            observed, raw = codex_message(url, model, Path(scratch), text, thread)
            observations.append(observed)
            raw_parts.append(raw)
            thread = thread or observed.get("thread_id")
            if observed["exit_code"] != 0:
                break
        successful = [tool for item in observations for tool in item["successful_tools"]]
        if stem.startswith("s1_"):
            passed = bool(observations) and observations[0]["exit_code"] == 0 and s1_passed(observations[0])
            reason = "requires exit 0, a successful Tripwright call and fjord-friends or its title in the reply"
        elif stem.startswith("s2_"):
            passed = len(observations) == 2 and all(item["exit_code"] == 0 for item in observations)
            reason = "requires both messages to finish with exit code 0; tool choice is observational"
        else:
            after = asyncio.run(state_number_async(url))
            passed = "commit" in successful and after is not None and before is not None and after > before
            reason = f"requires successful commit and state version increase (before={before}, after={after})"
        if any(item["exit_code"] != 0 for item in observations):
            reason += "; Codex exit codes: " + str([item["exit_code"] for item in observations])
    return dict(status="passed" if passed else "failed", reason=reason,
                messages=[observation_view(item) for item in observations]), "\n".join(raw_parts)


def unavailable(out: Path, name: str, reason: str) -> dict:
    (out / f"{name}.txt").write_text(reason + "\n", encoding="utf-8")
    return dict(status="not_verified", reason=reason)


def execute(out: Path, name: str, callback) -> dict:
    try:
        check, raw = callback()
    except Exception as error:
        check, raw = dict(status="failed", reason=f"{type(error).__name__}: {error}"), f"{type(error).__name__}: {error}\n"
    (out / f"{name}.txt").write_text(redact(raw), encoding="utf-8")
    return check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--skip", choices=("conformance", "codex"))
    arguments = parser.parse_args(argv)
    arguments.out.mkdir(parents=True, exist_ok=True)
    checks = {}
    for scenario in CONFORMANCE_SCENARIOS:
        name = f"conformance_{scenario}"
        if arguments.skip == "conformance":
            checks[name] = unavailable(arguments.out, name, "skipped by --skip conformance")
        elif not shutil.which("npx"):
            checks[name] = unavailable(arguments.out, name, "npx not found; conformance not verified")
        else:
            checks[name] = execute(arguments.out, name, lambda scenario=scenario: conformance(scenario))
    for stem in CODEX_SCENARIOS:
        name = f"codex_{stem}"
        if arguments.skip == "codex":
            checks[name] = unavailable(arguments.out, name, "skipped by --skip codex")
        elif not shutil.which("codex"):
            checks[name] = unavailable(arguments.out, name, "codex not found; Codex checks not verified")
        else:
            checks[name] = execute(arguments.out, name, lambda stem=stem: codex_scenario(stem, arguments.model))
    write_json(arguments.out / "result.json", dict(commit=source_commit(REPO), model=arguments.model,
               conformance_version=CONFORMANCE_VERSION, checks=checks))
    for status, label in (("passed", "通过的"), ("failed", "失败的"), ("not_verified", "没验的")):
        print(label + "：" + "、".join(name for name, check in checks.items() if check["status"] == status))
    return int(any(check["status"] == "failed" for check in checks.values()))

if __name__ == "__main__":
    sys.exit(main())
