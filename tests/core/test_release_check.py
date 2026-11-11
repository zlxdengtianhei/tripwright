"""Offline tests for release-check event parsing, verdicts and safe result files."""
from __future__ import annotations

import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/release_check.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def event(kind, **fields):
    return json.dumps({"type": kind, **fields})


def test_codex_events_describe_activity_before_first_reply():
    lines = "\n".join([
        event("thread.started", thread_id="thread-1"),
        event("item.completed", item={"type": "mcp_tool_call", "server": "tripwright", "tool": "turn", "status": "completed"}),
        event("item.completed", item={"type": "web_search", "query": "gallery"}),
        event("item.completed", item={"type": "mcp_tool_call", "server": "tripwright", "tool": "state", "status": "failed"}),
        event("item.completed", item={"type": "agent_message", "text": "Progress, not the final reply"}),
        event("item.completed", item={"type": "mcp_tool_call", "server": "tripwright", "tool": "query", "status": "completed"}),
        event("item.completed", item={"type": "agent_message", "text": "Last reply"}),
        event("turn.completed", usage={"total_tokens": 42}),
    ])

    observed = release.parse_events(lines)

    assert observed["tools"] == ["turn", "state", "query"]
    assert observed["tool_sequence"] == ["turn", "web_search", "state", "query"]
    assert observed["first_tool_is_turn"] is True
    assert observed["web_searches"] == 1
    assert observed["reply"] == "Last reply"
    assert observed["replies"] == ["Progress, not the final reply", "Last reply"]
    assert observed["usage"] == {"total_tokens": 42}
    assert observed["thread_id"] == "thread-1"


def test_s1_needs_both_a_successful_call_and_the_seeded_trip():
    assert release.s1_passed({"successful_tools": ["turn"], "reply": "Fictional fjord friends — Oslo and Bergen"})
    assert release.s1_passed({"successful_tools": ["state"], "reply": "The id is fjord-friends."})
    assert not release.s1_passed({"successful_tools": [], "reply": "fjord-friends"})
    assert not release.s1_passed({"successful_tools": ["turn"], "reply": "No trip named"})


def test_missing_codex_is_not_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(release.shutil, "which", lambda command: None if command == "codex" else "/usr/bin/npx")

    assert release.main(["--out", str(tmp_path), "--skip", "conformance"]) == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert all(item["status"] == "not_verified" for name, item in result["checks"].items() if name.startswith("codex_"))
    assert all("codex" in item["reason"] for name, item in result["checks"].items() if name.startswith("codex_"))


def test_s3_reads_state_afterwards_even_without_a_commit(monkeypatch):
    state_reads = []

    async def fake_state(url):
        state_reads.append(url)
        return 3

    def fake_message(url, model, cwd, text, thread=None):
        observed = release.parse_events(event("item.completed", item={"type": "agent_message", "text": "not changed"}))
        observed.update(seconds=0.1, exit_code=0)
        return observed, ""

    monkeypatch.setattr(release, "service", lambda: nullcontext("http://127.0.0.1/mcp/test"))
    monkeypatch.setattr(release, "state_number_async", fake_state)
    monkeypatch.setattr(release, "codex_message", fake_message)
    check, _ = release.codex_scenario("s3_change_and_remember_zh", "test-model")
    assert check["status"] == "failed"
    assert len(state_reads) == 2


def test_saved_json_redacts_url_key(tmp_path):
    path = tmp_path / "result.json"
    release.write_json(path, {"url": "http://127.0.0.1:8000/mcp/private-key", "output": "GET /mcp/private-key HTTP/1.1"})
    saved = path.read_text(encoding="utf-8")
    assert "private-key" not in saved
    assert saved.count("/mcp/***") == 2
