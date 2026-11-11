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
    assert cells[1:3] == ["—", "—"]


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
