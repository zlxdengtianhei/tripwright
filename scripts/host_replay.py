#!/usr/bin/env python3
"""Replay user messages against a trip repository through a fresh, isolated Claude Code session.

Each run clones the repository into its own workspace, sends the messages in order (one headless
turn each, resuming the same session), and keeps the raw stream, the reply text, tool calls and
timings. Only project settings are loaded, so the host sees what a cloud session sees from the repo.

`--now` replays at the moment the user asked (A7): the trip server reads it as TRIPWRIGHT_NOW, and a `date` command
on a PATH only this session sees prints the same clock, so the host's `date`, the server's `turn` and its lookups
agree. The clock starts at `--now` when the first message is sent and runs in real time from there. With several
moments (comma separated), message N starts at moment N, as the user sent them.

`--no-subagents` (s4b) replays the single-agent shape on Claude Code: the session is started without the Agent tool
(and its older name Task), so the host answers alone; everything else is the same.

`--host codex` (s4b) replays through `codex exec` instead, connected to the same local stdio server: the servers of the
workspace's `.mcp.json` are handed to Codex with `-c mcp_servers.<name>....` for this run only; `--ignore-user-config`
and `--ignore-rules` keep `~/.codex/config.toml` and the user's rules out (the login in CODEX_HOME is still used). Codex
0.162 writes a folder it runs in with a writable sandbox, or one named in a `projects."<ws>".trust_level` override, into
~/.codex/config.toml as trusted; so the run uses the read-only sandbox and no trust override (the workspace's AGENTS.md
is read, a project `.codex/config.toml` is not), and each turn records whether config.toml changed
(`codex_config_changed`). The trip server runs outside the sandbox, so its writes go through as in a Claude Code replay. With `--now`, Codex's
own environment context (today's real date and the machine's zone) is left out, so the model sees the pinned clock
only: the server's `now` and the `date` shim on PATH. Codex's JSONL events are kept as
`turn<N>.codex.jsonl` and rewritten as `turn<N>.stream.jsonl` in the shape Claude Code writes (assistant text and
tool_use blocks, user tool_result blocks; an MCP tool is named mcp__<server>__<tool>, a shell command is Bash), so
Each turn after the first resumes the same Codex session.

A first part that ends with turn's `first_part.later` (4a, D-88: a host without subagents, an app with only the MCP
connection, ends the reply there and gives the second part when the user says “继续”) is answered automatically: the
replay sends “继续” (“go on” after the English sentence) in the same session, on the clock that runs on from the
question's moment, and both replies are recorded as that question's turn: `turn<N>.stream.jsonl` holds the first reply,
a `{"type": "user", "replay": "continue"}` line, then the second; `text_at` and `seconds` count from the question;
`turn<N>.json` has `continued`. The second reply's own files are kept as `cont<N>.*`.

`--mcp-only` (s4b) replays an app that has only the MCP connection: the workspace keeps `.mcp.json` and the trip's data,
and loses the agent files (CLAUDE.md, AGENTS.md, .agents/, .codex/, .claude/skills and .claude/agents); the removal
is committed on the replay branch before the session starts, so the server sees a
clean copy. It checks that the MCP server alone carries a question through.
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


# turn's first_part.later (4a, service.LATER) and what the user answers to it
LATER = {"zh": "要核过的第二段，回我一句“继续”。", "en": "Reply “go on” for the checked second part."}
CONTINUE = {"zh": "继续", "en": "go on"}
AGENT_FILES = ("CLAUDE.md", "AGENTS.md", ".agents", ".codex", ".claude/skills", ".claude/agents")


def loose_end(text: str) -> str:
    # quote marks do not count: a host may copy “继续” as "继续" (S4e, dry_cc_ns_synth)
    return "".join(text.split()).translate(str.maketrans("", "", "“”\"「」『』'‘’")).rstrip("。.!！")


# the service's part headings (service.FIRST_TITLES, SECOND_TITLES): a reply with the first and without the second
# stopped after the first part (reply flow "two"), so the user's next word is “继续”
FIRST_TITLE = {"zh": "按存的计划先说", "en": "From the stored plan first"}
SECOND_TITLE = {"zh": "核过之后", "en": "After checking"}


def asks_to_continue(texts: list[str]) -> str | None:
    """The language to say “继续” in: the reply ends with the old `later` sentence, or holds only the first part."""
    lines = [line for line in (texts[-1] if texts else "").splitlines() if line.strip()]
    last = loose_end(lines[-1]) if lines else ""
    asked = next((language for language, sentence in LATER.items() if last.endswith(loose_end(sentence))), None)
    whole = "\n".join(texts)
    return asked or next((language for language, title in FIRST_TITLE.items()
                          if title in whole and SECOND_TITLE[language] not in whole), None)


def main_texts(stream: Path) -> list[str]:
    """The main agent's visible texts in a stream written in Claude Code's shape (either host)."""
    found = []
    for line in stream.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") == "assistant" and not event.get("parent_tool_use_id"):
            found += [block["text"] for block in event["message"].get("content", []) if block.get("type") == "text" and block["text"].strip()]
    return found


def merge_continued(out: Path, n: int, first: dict, second: dict, offset: float, word: str) -> dict:
    """One question's turn from its two replies: the first, then the reply to `word` sent `offset` seconds later."""
    name = f"turn{n}"
    with (out / f"{name}.stream.jsonl").open("a") as sink:
        sink.write(json.dumps({"type": "user", "replay": "continue", "at": offset,
                               "message": {"content": [{"type": "text", "text": word}]}}, ensure_ascii=False) + "\n")
        sink.write((out / f"cont{n}.stream.jsonl").read_text())
    shifted = [dict(call, at=round(offset + call["at"], 1)) if "at" in call else call for call in second["tool_calls"]]
    costs = [value for value in (first.get("cost_usd"), second.get("cost_usd")) if value is not None]
    merged = dict(first, session_id=second["session_id"], seconds=round(offset + second["seconds"], 1),
                  text_at=first["text_at"] + [round(offset + at, 1) for at in second["text_at"]],
                  exit=first["exit"] or second["exit"], stderr_tail=(first["stderr_tail"] + second["stderr_tail"])[-1500:],
                  tool_calls=first["tool_calls"] + shifted, result_subtype=second["result_subtype"],
                  cost_usd=sum(costs) if costs else None,
                  num_turns=(first.get("num_turns") or 0) + (second.get("num_turns") or 0) if first.get("num_turns") is not None else None,
                  continued={"message": word, "sent_at": offset, "seconds": second["seconds"], "text_at": second["text_at"]})
    if first.get("codex_config_changed") is not None:
        merged["codex_config_changed"] = first["codex_config_changed"] or second.get("codex_config_changed", False)
    (out / f"{name}.json").write_text(json.dumps(merged, ensure_ascii=False, indent=1))
    first_reply = (out / f"{name}.reply.md").read_text()
    (out / f"{name}.reply.md").write_text(first_reply + f"\n<!-- user: {word} (at {offset} s) -->\n\n" + (out / f"cont{n}.reply.md").read_text())
    return merged


def mcp_only(ws: Path):
    """The workspace of an app with only the MCP connection: agent files and hooks removed, committed on the branch."""
    import shutil as files

    for relative in AGENT_FILES:
        target = ws / relative
        if target.is_dir():
            files.rmtree(target)
        elif target.exists():
            target.unlink()
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=replay", "-c", "user.email=replay@example.invalid", "commit", "-q", "-m",
                    "replay: only the MCP connection (agent files removed)"], cwd=ws, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:replay"], cwd=ws, check=True)


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


def run_turn(ws: Path, text: str, session: str | None, model: str, out: Path, n: int, extra: list[str], clock: dict | None = None,
             name: str | None = None):
    cmd = ["claude", "-p", text, "--setting-sources", "project", "--permission-mode", "bypassPermissions",
           "--output-format", "stream-json", "--verbose", "--model", model, *extra]
    if session:
        cmd += ["--resume", session]
    # The trip server needs about 20-30 s to start on this machine, against Claude Code's default 30 s connect
    # timeout; without more room the tools fail at random and the replay measures the race, not the answer.
    env = dict(os.environ, MCP_TIMEOUT=os.environ.get("MCP_TIMEOUT", "90000"), **(clock or {}))
    started = time.time()
    name = name or f"turn{n}"
    raw = out / f"{name}.stream.jsonl"
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
    (out / f"{name}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    reply = "\n\n---\n\n".join(f"<!-- at {at} s -->\n{body}" for at, body in texts)
    (out / f"{name}.reply.md").write_text(f"<!-- {round(elapsed)} s; {len(tools)} tool events -->\n\n{reply}\n")
    return session_id, summary


def codex_servers(ws: Path, clock: dict | None) -> list[str]:
    """The workspace's .mcp.json servers as `-c` overrides for this codex run: command, args, env and the workspace as
    their folder, the pinned clock in each server's env, room to start (about 20-30 s here), and tools approved for the
    run (Codex asks before a tool that writes and reaches outside, and an exec run cannot be asked)."""
    servers = json.loads((ws / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {}) if (ws / ".mcp.json").is_file() else {}
    pinned = {key: value for key, value in (clock or {}).items() if key.startswith("TRIPWRIGHT_")}
    overrides = []
    for name, server in servers.items():
        prefix = f"mcp_servers.{name}"
        if server.get("url"):  # a remote server (S4e, D-92): the address comes from the environment, so no key is in a file
            overrides += ["-c", f"{prefix}.url={json.dumps(os.path.expandvars(server['url']))}",
                          "-c", f"{prefix}.startup_timeout_sec=90", "-c", f"{prefix}.tool_timeout_sec=180",
                          "-c", f'{prefix}.default_tools_approval_mode="approve"']
            continue
        env = dict(server.get("env") or {}, **pinned)
        overrides += ["-c", f"{prefix}.command={json.dumps(server['command'])}",
                      "-c", f"{prefix}.args={json.dumps(server.get('args') or [])}",
                      "-c", f"{prefix}.cwd={json.dumps(str(ws))}",
                      "-c", f"{prefix}.env={{{', '.join(f'{key} = {json.dumps(value)}' for key, value in env.items())}}}",
                      "-c", f"{prefix}.startup_timeout_sec=90", "-c", f"{prefix}.tool_timeout_sec=180",
                      "-c", f'{prefix}.default_tools_approval_mode="approve"']
    return overrides


def codex_stream(events: list[tuple[float, dict]]) -> tuple[list[dict], str | None, list, list, dict | None]:
    """Codex exec events as Claude Code's stream lines: (lines, thread id, texts [(at, text)], tool calls, usage)."""
    lines, texts, tools, thread, usage = [], [], [], None, None
    for at, event in events:
        kind = event.get("type")
        if kind == "thread.started":
            thread = event.get("thread_id")
            lines.append({"type": "system", "subtype": "init", "session_id": thread, "host": "codex"})
        elif kind == "turn.completed":
            usage = event.get("usage")
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and str(item.get("text", "")).strip():
                texts.append((at, item["text"]))
                lines.append({"type": "assistant", "message": {"content": [{"type": "text", "text": item["text"]}]}})
            elif item.get("type") in ("mcp_tool_call", "command_execution"):
                mcp = item["type"] == "mcp_tool_call"
                name = f"mcp__{item.get('server')}__{item.get('tool')}" if mcp else "Bash"
                arguments = item.get("arguments") if mcp else {"command": item.get("command")}
                if mcp:
                    result = item.get("result") or {}
                    content = result.get("content") if isinstance(result.get("content"), list) else [
                        {"type": "text", "text": json.dumps(result.get("structured_content") or result, ensure_ascii=False)}]
                    failed = item.get("status") == "failed" or bool(item.get("error"))
                    if item.get("error"):
                        content = [{"type": "text", "text": str((item["error"] or {}).get("message", item["error"]))}]
                else:
                    content = [{"type": "text", "text": item.get("aggregated_output") or ""}]
                    failed = item.get("exit_code") not in (0, None)
                lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": item["id"], "name": name, "input": arguments}]}})
                lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": item["id"], "content": content, "is_error": failed}]}})
                tools.append({"at": at, "name": name, "input": arguments})
                if failed:
                    tools.append({"error_for": item["id"], "content": json.dumps(content, ensure_ascii=False)[:500]})
            elif item.get("type") not in (None, "reasoning"):
                tools.append({"at": at, "name": f"codex:{item['type']}"})
        elif kind in ("turn.failed", "error"):
            lines.append({"type": "result", "subtype": "error", "error": event.get("error") or event.get("message")})
    return lines, thread, texts, tools, usage


def run_codex_turn(ws: Path, text: str, session: str | None, model: str | None, out: Path, n: int, extra: list[str],
                   clock: dict | None = None, name: str | None = None):
    """One message through `codex exec` (resuming the session after the first), recorded as run_turn records it."""
    common = ["--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
              # read-only: with workspace-write, codex exec 0.162 writes the folder as trusted into ~/.codex/config.toml.
              # The trip server is not in the sandbox: its writes (turn, commit) go through as in a Claude Code replay.
              "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"',
              # The pinned clock (--now): Codex otherwise tells the model today's real date and the machine's zone.
              *(["-c", "include_environment_context=false", "-c", "features.current_time_reminder=false"] if clock else []),
              *codex_servers(ws, clock),
              *(["-m", model] if model else []), *extra]
    cmd = ["codex", "exec", "resume", *common, session, text] if session else ["codex", "exec", *common, text]
    env = dict(os.environ, **(clock or {}))
    config = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    before = config.read_bytes() if config.is_file() else None
    started = time.time()
    name = name or f"turn{n}"
    raw = out / f"{name}.codex.jsonl"
    events = []
    with raw.open("w") as sink:
        proc = subprocess.Popen(cmd, cwd=ws, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        for line in proc.stdout:
            sink.write(line)
            if line.strip().startswith("{"):
                events.append((round(time.time() - started, 1), json.loads(line)))
        stderr = proc.stderr.read()
        proc.wait()
    elapsed = time.time() - started
    # The run must leave the user's Codex configuration as it was; say so loudly when it did not.
    changed = (config.read_bytes() if config.is_file() else None) != before
    if changed:
        print(f"WARNING: codex changed {config} during turn {n}; compare it with your copy", file=sys.stderr, flush=True)
    lines, thread, texts, tools, usage = codex_stream(events)
    failed = any(line.get("type") == "result" for line in lines)
    lines.append({"type": "result", "subtype": "error" if failed or proc.returncode else "success", "usage": usage, "host": "codex"})
    (out / f"{name}.stream.jsonl").write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    summary = {"turn": n, "message": text, "session_id": thread or session, "seconds": round(elapsed, 1),
               "text_at": [at for at, _ in texts], "exit": proc.returncode, "stderr_tail": stderr[-1500:], "tool_calls": tools,
               "result_subtype": lines[-1]["subtype"], "cost_usd": None, "num_turns": None, "usage": usage, "host": "codex",
               "codex_config_changed": changed}
    (out / f"{name}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    reply = "\n\n---\n\n".join(f"<!-- at {at} s -->\n{body}" for at, body in texts)
    (out / f"{name}.reply.md").write_text(f"<!-- {round(elapsed)} s; {len(tools)} tool events -->\n\n{reply}\n")
    return thread or session, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repository to clone (path or URL)")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--messages", required=True, help="JSON file: list of message strings")
    parser.add_argument("--out", required=True, help="Run directory (outside any repository with agent instructions)")
    parser.add_argument("--host", choices=("claude", "codex"), default="claude",
                        help="Claude Code (claude -p) or Codex (codex exec), on the same local stdio server")
    parser.add_argument("--model", default=None, help="Model (Claude Code: default opus; Codex: its default unless given)")
    parser.add_argument("--no-subagents", action="store_true",
                        help="Claude Code without the Agent (Task) tool: the single-agent shape")
    parser.add_argument("--mcp-only", action="store_true",
                        help="Only the MCP connection: remove the workspace's agent files before the session")
    parser.add_argument("--extra", default="", help="Extra CLI arguments, space separated")
    parser.add_argument("--now", help="Replay at this moment (ISO with offset): the server and the host's date show it. "
                                      "Several moments, comma separated, pin each message to its own (the user's real times)")
    args = parser.parse_args()
    if args.host == "codex" and args.no_subagents:
        parser.error("--no-subagents is for Claude Code; a Codex run takes its agents from the workspace's .codex/")
    model = args.model or ("opus" if args.host == "claude" else None)
    extra = args.extra.split() + (["--disallowedTools", "Agent", "Task"] if args.no_subagents else [])
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
    if args.mcp_only:
        mcp_only(ws)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True).stdout.strip()
    messages = json.loads(Path(args.messages).read_text())
    session = None
    log = []
    for n, text in enumerate(messages, 1):
        if 1 < n <= len(moments):
            # The user asked the next message at a later real moment; one --now only runs the clock on from the first.
            clock = pin_clock(out, moments[n - 1])
        send = run_codex_turn if args.host == "codex" else run_turn
        asked = time.time()
        session, summary = send(ws, text, session, model, out, n, extra, clock)
        language = asks_to_continue(main_texts(out / f"turn{n}.stream.jsonl"))
        if language:
            # The reply ended with first_part.later: the user says “继续”, on the same clock, and both replies are this turn.
            offset = round(time.time() - asked, 1)
            session, second = send(ws, CONTINUE[language], session, model, out, n, extra, clock, name=f"cont{n}")
            summary = merge_continued(out, n, summary, second, offset, CONTINUE[language])
        log.append(summary)
        print(json.dumps({k: summary.get(k) for k in ("turn", "seconds", "text_at", "exit", "result_subtype", "cost_usd", "continued")
                          if k != "continued" or summary.get(k)}, ensure_ascii=False), flush=True)
    commits = subprocess.run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=ws, capture_output=True, text=True).stdout
    (out / "run.json").write_text(json.dumps({"repo": args.repo, "ref": args.ref, "base": base, "host": args.host, "model": model,
                                               "no_subagents": args.no_subagents, "mcp_only": args.mcp_only,
                                               "extra": " ".join(extra), "now": args.now,
                                               "turns": log, "commits": commits},
                                              ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
