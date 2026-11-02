"""E2 and promise 3 (CONTRACTS 1): what a message says counts in the same turn; every read is bound to one snapshot;
result and commit judge freshness by the content key, and newer unrecorded messages make a result unsettled."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from repo_fixtures import Writer, bare, new_repository, seeded
from tripwright.service import Tripwright
from tripwright.storage import Store

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "contracts/schema.json").read_text(encoding="utf-8"))


def errors(instance, definition: str) -> list[str]:
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"})
    return [f"{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in validator.iter_errors(instance)]


NO_LUGGAGE = dict(facts=[dict(key="luggage", value="none", text="p1 本人没有箱子", subject=["p1"], true_from="2030-06-10T09:00:00+02:00",
                              invalidated_by="user says otherwise", scope=dict(start="2030-06-10", end="2030-06-10"),
                              source=dict(event="this", basis="user_explicit"))])


@pytest.fixture
def writer(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo", remote=bare(tmp_path / "remote.git")))
    seeded(writer)
    yield writer
    writer.close()


def test_e2_a_fact_stated_with_an_urgent_question_is_in_that_turns_package(writer):
    turn = writer.turn("我本人没有箱子。现在怎么去画廊？", "urgent", nature="user_verbatim", native_ref="fixture:urgent", speaker="p1",
                       subject=["p1"], scope=dict(start="2030-06-10", end="2030-06-10"), scenario="quick", record=NO_LUGGAGE)
    [fact_id] = turn["recorded"]["changed"]
    assert fact_id in turn["pack"]["meta"]["required"]["facts"]["ids"]
    assert any(line.startswith("实况：") and f"[{fact_id}]" in line and "本人没有箱子" in line for line in turn["pack"]["markdown"].splitlines())
    assert turn["snapshot"]["ledger_revision"] == 1 and turn["snapshot"]["raw_watermark"]["last_event"] == turn["receipt"]["event_id"]
    assert turn["write"]["state"] == "received" and fact_id in writer.ops()[turn["write"]["op_id"]]["records"]
    assert errors(turn["snapshot"], "snapshot") == [] and errors(turn["write"], "write_ref") == []


def test_e2_a_turn_without_the_fact_does_not_have_it(writer):
    turn = writer.turn("现在怎么去画廊？", "urgent-only", subject=["p1"], scope=dict(start="2030-06-10", end="2030-06-10"), scenario="quick")
    assert turn["pack"]["meta"]["required"]["facts"]["ids"] == [] and "本人没有箱子" not in turn["pack"]["markdown"]


def deep_draft(writer, key="plan"):
    turn = writer.turn("Recheck the day.", key)
    plan = writer.service.state("local")["plan"]
    plan["days"][0]["title"] = "Rechecked"
    return writer.propose(turn, key, plan=plan)


def test_e2_a_fact_reported_during_a_deep_check_makes_its_result_stale(writer):
    draft = deep_draft(writer)
    writer.turn("我本人没有箱子。", "fact-during-check", nature="user_verbatim", native_ref="fixture:fact", record=NO_LUGGAGE)
    writer.service.jobs.run_once("local")
    result = writer.service.result("local", draft["job_id"])
    assert result == dict(job_id=draft["job_id"], status="stale", changed=["ledger"], unsettled_events=[], result=None, notice="已过期，需重算")
    assert errors(result, "result_response") == []
    with pytest.raises(ValueError, match="Ledger changed"):
        writer.commit(draft, "stale-plan", accept_unverified=True)


def test_a_newer_unrecorded_message_leaves_the_result_unsettled_until_acknowledged(writer):
    draft = deep_draft(writer)
    question = writer.turn("来得及吗？", "just-asking")
    writer.service.jobs.run_once("local")
    result = writer.service.result("local", draft["job_id"])
    assert result["status"] == "unsettled" and result["unsettled_events"] == [question["receipt"]["event_id"]] and result["result"] is None
    assert errors(result, "result_response") == []
    with pytest.raises(ValueError, match="Unsettled messages"):
        writer.commit(draft, "too-early", accept_unverified=True)
    with pytest.raises(ValueError, match="ack_events must name messages received after"):
        writer.service.result("local", draft["job_id"], ack_events=["E-300101-nosuch"])
    done = writer.service.result("local", draft["job_id"], ack_events=[question["receipt"]["event_id"]])
    assert done["status"] == "completed" and done["result"]["verdict"] in ("passed", "incomplete", "conflict")
    assert done["result"]["draft_id"] == draft["draft_id"] and errors(done, "result_response") == []
    assert writer.service.store.get("jobs", "local", draft["job_id"])["acked"][0]["event"] == question["receipt"]["event_id"]
    assert writer.commit(draft, "after-ack", accept_unverified=True)["accepted"]


def test_a_ledger_only_commit_is_not_held_back_by_unsettled_messages(writer):
    turn = writer.turn("Note this.", "ledger-only")
    draft = writer.propose(turn, "ledger-only", changes=dict(facts=[dict(NO_LUGGAGE["facts"][0], source=dict(event=turn["receipt"]["event_id"]))]))
    writer.turn("还有一句。", "unrelated")
    assert writer.commit(draft, "ledger-only")["accepted"]


def test_the_content_key_tells_two_version_fours_apart(writer, tmp_path):
    first = writer.service.versions.current("local")
    snapshot = writer.turn("x", "x")["snapshot"]
    assert snapshot["plan_version_id"] == first["id"] and snapshot["ledger_digest"].startswith("sha256:")
    other = Tripwright(Store(tmp_path / "other.sqlite3"))
    turn = other.turn("local", "x", "x")
    assert turn["snapshot"]["plan_version"] == 0 and turn["snapshot"]["repo"] is None, "single-database mode has no repository part"
    assert turn["write"]["state"] == "effective" and errors(turn["write"], "write_ref") == []
    other.jobs.stop()
    other.store.close()


def test_a_quick_question_answers_with_the_remote_unreachable_and_says_how_old_the_head_is(writer, tmp_path):
    head = writer.service.committer.status()["head"]
    (tmp_path / "remote.git").rename(tmp_path / "remote-away.git")
    assert writer.service.committer.sync()["fetched"] is False
    started = time.monotonic()
    turn = writer.turn("我在哪坐车？", "offline-quick", scenario="quick", record=NO_LUGGAGE)
    assert time.monotonic() - started < 2
    assert turn["sync"]["remote_reachable"] is False and turn["sync"]["head"] == head and turn["sync"]["head_age_seconds"] >= 0
    assert f"提交 {head}" in turn["pack"]["markdown"].splitlines()[1] and "未入仓 1 条" in turn["pack"]["markdown"].splitlines()[1]
    writer.service.committer.flush()
    state = writer.ops()[turn["write"]["op_id"]]["state"]
    assert state == "pending_sync" and writer.service.committer.status()["oldest_unsynced_at"] is not None
    (tmp_path / "remote-away.git").rename(tmp_path / "remote.git")
    writer.service.committer.sync()
    assert writer.ops()[turn["write"]["op_id"]]["state"] == "effective"
