#!/usr/bin/env python3
"""Replay user messages against a trip repository through a fresh, isolated Claude Code session.

Each run clones the repository into its own workspace, sends the messages in order (one headless
turn each, resuming the same session), and keeps the raw stream, the reply text, tool calls and
timings. Only project settings are loaded, so the host sees what a cloud session sees from the repo.

`--now` replays at the moment the user asked (A7): the trip server reads it as TRIPWRIGHT_NOW, and a `date` command
on a PATH only this session sees prints the same clock, so the host's `date`, the server's `turn` and its lookups
agree. The clock starts at `--now` when the first message is sent and runs in real time from there. With several
moments (comma separated), message N starts at moment N, as the user sent them.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DATE_SHIM = """#!/bin/sh
# Pinned clock of a tripwright replay (host_replay.py --now): the real date, shown at the replayed moment.
now=$(( {moment} + $({date} +%s) - {since} ))
if {date} -r 0 >/dev/null 2>&1; then exec {date} -r "$now" "$@"; else exec {date} -d "@$now" "$@"; fi
"""


def pin_clock(out: Path, moment: str) -> dict:
    """Environment for a replay at `moment`: TRIPWRIGHT_NOW for the server, and a `date` shim first on PATH."""
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise SystemExit("--now needs an ISO time with offset, e.g. 2026-10-31T16:40:00+01:00")
    since = int(time.time())
    real = shutil.which("date") or "/bin/date"
    folder = out / "clock"
    folder.mkdir(exist_ok=True)
    shim = folder / "date"
    shim.write_text(DATE_SHIM.format(moment=int(parsed.timestamp()), since=since, date=real))
    shim.chmod(0o755)
    return {"TRIPWRIGHT_NOW": parsed.isoformat(), "TRIPWRIGHT_NOW_SET_AT": str(since),
            "PATH": f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"}


def hook_counts(log: Path) -> dict:
    """How often each of the trip repository's hooks ran during the replay and how often it blocked, from the one line
    per call they write (`<time> <event> block|pass|error <note>`). Project hooks run in an untrusted workspace too
    (measured on Claude Code 2.1.301: only permissions.allow is ignored there), so the replay does not touch trust."""
    counts: dict[str, dict] = {}
    for line in log.read_text(encoding="utf-8").splitlines() if log.is_file() else []:
        parts = line.split(" ", 3)
        if len(parts) >= 3:
            entry = counts.setdefault(parts[1], {"runs": 0, "blocked": 0, "errors": 0})
            entry["runs"] += 1
            entry["blocked"] += parts[2] == "block"
            entry["errors"] += parts[2] == "error"
    return counts


def run_turn(ws: Path, text: str, session: str | None, model: str, out: Path, n: int, extra: list[str], clock: dict | None = None):
    cmd = ["claude", "-p", text, "--setting-sources", "project", "--permission-mode", "bypassPermissions",
           "--output-format", "stream-json", "--verbose", "--model", model, *extra]
    if session:
        cmd += ["--resume", session]
    # The trip server needs about 20-30 s to start on this machine, against Claude Code's default 30 s connect
    # timeout; without more room the tools fail at random and the replay measures the race, not the answer.
    env = dict(os.environ, MCP_TIMEOUT=os.environ.get("MCP_TIMEOUT", "90000"), **(clock or {}))
    started = time.time()
    raw = out / f"turn{n}.stream.jsonl"
    # Each stream line is stamped with the seconds since the message was sent, so the reply records when each
    # text the user sees appeared (the first part must come before the checks, the second after them).
    arrived = []
    with raw.open("w") as sink:
        proc = subprocess.Popen(cmd, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        for line in proc.stdout:
            sink.write(line)
            arrived.append(round(time.time() - started, 1))
        stderr = proc.stderr.read()
        proc.wait()
    elapsed = time.time() - started
    lines = raw.read_text().splitlines()
    events = [(at, json.loads(line)) for at, line in zip(arrived, lines) if line.strip().startswith("{")]
    session_id, texts, tools, result = session, [], [], None
    for at, event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            session_id = event.get("session_id", session_id)
        if event.get("type") == "assistant":
            subagent = event.get("parent_tool_use_id")
            for block in event["message"].get("content", []):
                if block.get("type") == "text" and block["text"].strip() and not subagent:
                    texts.append((at, block["text"]))
                elif block.get("type") == "tool_use":
                    tools.append({"at": at, "name": block["name"], "input": block.get("input"), **({"subagent": subagent} if subagent else {})})
        if event.get("type") == "user":
            for block in event["message"].get("content", []) if isinstance(event["message"].get("content"), list) else []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    tools.append({"error_for": block.get("tool_use_id"), "content": str(block.get("content"))[:500]})
        if event.get("type") == "result":
            result = event
    summary = {"turn": n, "message": text, "session_id": session_id, "seconds": round(elapsed, 1),
               "text_at": [at for at, _ in texts],
               "exit": proc.returncode, "stderr_tail": stderr[-1500:], "tool_calls": tools,
               "result_subtype": result and result.get("subtype"), "cost_usd": result and result.get("total_cost_usd"),
               "num_turns": result and result.get("num_turns")}
    (out / f"turn{n}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    reply = "\n\n---\n\n".join(f"<!-- at {at} s -->\n{body}" for at, body in texts)
    (out / f"turn{n}.reply.md").write_text(f"<!-- {round(elapsed)} s; {len(tools)} tool events -->\n\n{reply}\n")
    return session_id, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repository to clone (path or URL)")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--messages", required=True, help="JSON file: list of message strings")
    parser.add_argument("--out", required=True, help="Run directory (outside any repository with agent instructions)")
    parser.add_argument("--model", default="opus")
    parser.add_argument("--extra", default="", help="Extra CLI arguments, space separated")
    parser.add_argument("--now", help="Replay at this moment (ISO with offset): the server and the host's date show it. "
                                      "Several moments, comma separated, pin each message to its own (the user's real times)")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    moments = [moment.strip() for moment in args.now.split(",")] if args.now else []
    clock = pin_clock(out, moments[0]) if moments else None
    ws, remote = out / "ws", out / "remote.git"
    # Pushes from the session land in a bare copy inside the run, never in the source repository.
    subprocess.run(["git", "clone", "-q", "--bare", args.repo, str(remote)], check=True)
    # Replay on a branch whose tip is exactly the ref: a detached HEAD makes the server fail to start, and a
    # branch tip ahead of the ref would be pulled in by the server's first sync.
    subprocess.run(["git", "branch", "-f", "replay", args.ref], cwd=remote, check=True)
    subprocess.run(["git", "clone", "-q", "--branch", "replay", str(remote), str(ws)], check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True).stdout.strip()
    messages = json.loads(Path(args.messages).read_text())
    session = None
    log = []
    for n, text in enumerate(messages, 1):
        if 1 < n <= len(moments):
            # The user asked the next message at a later real moment; one --now only runs the clock on from the first.
            clock = pin_clock(out, moments[n - 1])
        session, summary = run_turn(ws, text, session, args.model, out, n, args.extra.split(), clock)
        log.append(summary)
        print(json.dumps({k: summary[k] for k in ("turn", "seconds", "text_at", "exit", "result_subtype", "cost_usd")}, ensure_ascii=False), flush=True)
    commits = subprocess.run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=ws, capture_output=True, text=True).stdout
    hooks = hook_counts(ws / ".tripwright" / "hook.log")
    (out / "run.json").write_text(json.dumps({"repo": args.repo, "ref": args.ref, "base": base, "model": args.model,
                                               "extra": args.extra, "now": args.now, "turns": log, "commits": commits, "hooks": hooks},
                                              ensure_ascii=False, indent=1))
    # Mechanical checks on what the user saw (answer_checks.py writes answer_checks.json next to the turns).
    checks = subprocess.run([sys.executable, str(Path(__file__).with_name("answer_checks.py")), str(out)], capture_output=True, text=True)
    print(checks.stdout, end="", flush=True)


if __name__ == "__main__":
    sys.exit(main())
