"""Offline checks for the bare chat host's run provenance and Markdown summary."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("chat_host", ROOT / "scripts/chat_host.py")
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


def result(directory: Path, **changes) -> None:
    metrics = dict(
        commit="abcdef1234567890abcdef1234567890abcdef12+dirty",
        reply_flow="two",
        model="group/glm",
        scenario="s2_on_the_road_zh",
        instructions="on",
        status="completed",
        messages=[dict(seconds_to_reply=1.25, tool_order=["turn", "query"], tool_errors=[],
                       first_part_before_query=True)],
    )
    metrics.update(changes)
    (directory / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")


def test_summary_includes_provenance_and_message_metrics(tmp_path, capsys):
    run = tmp_path / "current"
    run.mkdir()
    result(run)

    assert host.main(["--summary", str(run)]) == 0
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| current |"))
    assert "abcdef1+dirty" in row and "two" in row
    assert "1.25s" in row and "turn → query" in row
    assert "errors=0" in row and "first_part_before_query=True" in row


def test_decoy_tools_put_web_search_first_only_when_on():
    server = [{"type": "function", "function": {"name": "turn", "description": "", "parameters": {"type": "object"}}}]
    tools = host.decoy_tools(server, decoy=True)
    assert tools[0]["function"]["name"] == "web_search"
    assert tools[0]["function"]["description"] == "Search the web and return the top results."
    assert tools[0]["function"]["parameters"]["required"] == ["query"]
    assert [tool["function"]["name"] for tool in tools[1:]] == ["turn"]
    assert host.decoy_tools(server, decoy=False) == server


def test_message_metrics_counts_web_search_and_first_tool_is_not_turn():
    entry = dict(user="在路边", failed=None, reply="答", mid_texts=[], usage=[], seconds=4.0, rounds=[], tool_calls=[
        dict(round=1, name="web_search", arguments='{"query": "oslo"}', result_bytes=44, seconds=0.01, error=None,
             result="No relevant results were found for: oslo"),
        dict(round=2, name="web_search", arguments='{"query": "oslo cafe"}', result_bytes=50, seconds=0.01,
             error=None, result="No relevant results were found for: oslo cafe"),
        dict(round=3, name="turn", arguments="{}", result_bytes=2, seconds=0.01, error=None, result="{}"),
    ])

    metrics = host.message_metrics(entry)

    assert metrics["web_search_calls"] == 2
    assert metrics["tool_order"] == ["web_search", "web_search", "turn"]
    assert metrics["first_tool_is_turn"] is False
    assert metrics["tool_errors"] == []


def test_summary_marks_old_runs_as_unlabelled(tmp_path, capsys):
    run = tmp_path / "legacy"
    run.mkdir()
    result(run)
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    del metrics["commit"], metrics["reply_flow"]
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    assert host.main(["--summary", str(run)]) == 0
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| legacy |"))
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[1:4] == ["—", "—", "—"]
    assert "web_search=—" in row


def test_summary_shows_decoy_column_and_web_search_counts(tmp_path, capsys):
    run = tmp_path / "with_decoy"
    run.mkdir()
    result(run, decoy_web_search=True,
           messages=[dict(seconds_to_reply=2.5, tool_order=["web_search", "turn"], web_search_calls=1, tool_errors=[],
                          first_part_before_query=None)])

    assert host.main(["--summary", str(run)]) == 0
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| with_decoy |"))
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[3] == "True"
    assert "web_search=1" in row


def test_failed_run_has_provenance_without_calling_a_model(tmp_path):
    out = tmp_path / "failed"
    code = host.main([
        "--mcp-stdio", "unused", "--model", "unused", "--scenario", str(tmp_path / "missing.yaml"),
        "--instructions", "off", "--out", str(out),
    ])

    assert code == 2
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "failed"
    assert metrics["commit"] == host.source_commit(host.REPO)
    assert metrics["reply_flow"] == "two"


def test_source_commit_marks_dirty_worktree(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-q", "-m", "initial"], check=True)
    clean = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    assert host.source_commit(tmp_path) == clean

    tracked.write_text("dirty\n", encoding="utf-8")
    assert host.source_commit(tmp_path) == clean + "+dirty"
