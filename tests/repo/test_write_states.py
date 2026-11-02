"""Promise 2 (CONTRACTS 2): writes go through the outbox in the receipt's transaction and move received →
pending_sync → effective (received → effective with a local canonical branch); operation IDs, trailers, batching,
idempotency, waiting for effective and honest unsynced counts."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from repo_fixtures import Writer, bare, git, new_repository, plan, seeded

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "contracts/schema.json").read_text(encoding="utf-8"))


def errors(instance, definition: str) -> list[str]:
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"})
    return [error.message for error in validator.iter_errors(instance)]


def contract_op(op: dict) -> dict:
    keys = ("op_id", "kind", "request", "event", "draft_id", "version_id", "records", "transitions")
    shaped = dict(op, op_id=op["id"])
    return {key: shaped[key] for key in keys if key in shaped}


@pytest.fixture
def remote_writer(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo", remote=bare(tmp_path / "remote.git")))
    yield writer
    writer.close()


def test_received_then_pending_sync_then_effective_with_the_commit_named(remote_writer, monkeypatch):
    writer = remote_writer
    turn = writer.turn("Create the trip.", "first")
    assert turn["write"]["state"] == "received" and turn["write"]["unsynced"] == 1
    assert re.fullmatch(r"OP-\d{6}-[0-9a-z]{6}", turn["write"]["op_id"])
    monkeypatch.setattr(writer.repository.git, "push", lambda remote, branch: "unreachable")
    writer.service.committer.flush()
    op = writer.ops()[turn["write"]["op_id"]]
    assert op["state"] == "pending_sync" and op["transitions"][-1]["commit"] == git(writer.root, "rev-parse", "HEAD").strip()
    monkeypatch.undo()
    writer.service.committer.sync()
    op = writer.ops()[turn["write"]["op_id"]]
    assert [step["state"] for step in op["transitions"]] == ["received", "pending_sync", "effective"]
    assert errors(contract_op(op), "write_op") == []
    message = git(writer.root, "log", "-1", "--format=%B")
    assert f"Tripwright-Op: {op['id']}" in message and "Tripwright-Request: turn:first" in message


def test_a_local_canonical_branch_makes_a_commit_effective_at_once(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo"))
    try:
        turn = writer.turn("Create the trip.", "first")
        writer.service.committer.flush()
        op = writer.ops()[turn["write"]["op_id"]]
        assert [step["state"] for step in op["transitions"]] == ["received", "effective"] and op["transitions"][-1]["commit"]
        assert writer.service.committer.status()["canonical"] == "main"
    finally:
        writer.close()


def test_operations_within_the_batch_window_become_one_commit_and_each_version_its_own(remote_writer):
    writer = remote_writer
    turn = writer.turn("Create the trip.", "a")
    writer.turn("Second message.", "b")
    draft = writer.propose(turn, "seed", plan=plan())
    with pytest.raises(ValueError, match="Unsettled"):
        writer.commit(draft, "seed", accept_unverified=True)
    turn = writer.turn("Create it now.", "c")
    writer.commit(writer.propose(turn, "seed-2", plan=plan()), "seed-2", accept_unverified=True)
    before = int(git(writer.root, "rev-list", "--count", "HEAD"))
    writer.service.committer.flush()
    assert int(git(writer.root, "rev-list", "--count", "HEAD")) == before + 1
    assert git(writer.root, "log", "-1", "--format=%B").count("Tripwright-Op:") == 4


def test_commit_waits_for_effective_when_the_remote_answers(remote_writer):
    writer = remote_writer
    turn = writer.turn("Create the trip.", "first")
    draft = writer.propose(turn, "seed", plan=plan())
    version = writer.service.commit("local", draft["draft_id"], "seed", accept_unverified=True, wait=5)
    assert version["accepted"] and version["write"]["state"] == "effective" and version["write"]["unsynced"] == 0
    assert errors(version["write"], "write_ref") == []


def test_the_same_request_returns_the_same_operation_and_a_changed_one_is_refused(remote_writer):
    writer = remote_writer
    seeded(writer)
    record = dict(facts=[dict(key="luggage", value="none", text="no luggage", subject=["p1"], true_from="2030-06-10T09:00:00+02:00",
                              invalidated_by="user says otherwise", source=dict(event="this", basis="user_explicit"))])
    first = writer.turn("No luggage.", "same", record=record)
    again = writer.turn("No luggage.", "same", record=record)
    assert again["write"]["op_id"] == first["write"]["op_id"] and len(writer.store.all("facts", "local")) == 1
    with pytest.raises(ValueError, match="different content"):
        writer.turn("No luggage.", "same", record=dict(facts=[dict(record["facts"][0], text="other")]))
    turn = writer.turn("Rename.", "rename")
    draft = writer.propose(turn, "rename", plan=dict(writer.service.state("local")["plan"], title="Renamed"))
    committed = writer.commit(draft, "rename", accept_unverified=True)
    assert writer.commit(draft, "rename", accept_unverified=True)["write"]["op_id"] == committed["write"]["op_id"]
    with pytest.raises(ValueError, match="reused with different input"):
        writer.commit(draft, "rename", accept_unverified=False)


def test_failures_stay_in_the_outbox_with_backoff_and_the_count_is_honest(remote_writer, monkeypatch, tmp_path):
    writer = remote_writer
    monkeypatch.setattr(writer.repository.git, "push", lambda remote, branch: "unreachable")
    for index in range(3):
        writer.turn(f"message {index}", f"m{index}")
    writer.service.committer.flush()
    status = writer.service.committer.status()
    assert status["unsynced"] == 3 and status["remote_reachable"] is False and status["oldest_unsynced_at"]
    assert writer.service.committer.retry_at > 0 and writer.service.committer.failures == 1
    writer.service.committer.flush()
    assert writer.service.committer.failures == 2, "each failed push backs off further"
    assert writer.turn("and now?", "m3")["write"]["unsynced"] == 4


def test_the_background_committer_commits_after_the_batch_window_and_pushes(remote_writer):
    import time

    committer = remote_writer.service.committer
    committer.batch_seconds = 0.2
    turn = remote_writer.turn("Create the trip.", "background")
    committer.start()
    try:
        deadline = time.monotonic() + 10
        while remote_writer.ops()[turn["write"]["op_id"]]["state"] != "effective" and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        committer.stop()
    assert remote_writer.ops()[turn["write"]["op_id"]]["state"] == "effective"


def test_front_screens_count_this_commits_writes_as_in_the_repository_and_name_the_previous_commit(remote_writer):
    from repo_fixtures import TRIP
    from tripwright.repo import layout
    from tripwright.repo.files import check

    writer = remote_writer
    seeded(writer)
    parent = git(writer.root, "rev-parse", "--short=7", "HEAD").strip()
    writer.turn("No luggage.", "fact-a")
    writer.turn("Knee hurts.", "fact-b")
    writer.service.committer.flush()
    assert git(writer.root, "rev-parse", "--short=7", "HEAD~1").strip() == parent
    expected = f"提交 {parent} 的下一个，本次带入 2 条写入"
    screens = [writer.root / "README.md", writer.root / layout.trip_folder(TRIP) / "README.md"]
    lines = [next(line for line in path.read_text().splitlines() if line.startswith("基于：")) for path in screens]
    assert lines[0] == lines[1], "the repository and trip front screens say the same"
    assert expected in lines[0] and "未入仓 0 条" in lines[0] and "未入仓 2 条" not in lines[0]
    assert f"基于提交 {parent} 的下一个，本次带入 2 条写入；" in screens[0].read_text().splitlines()[0]
    day_pack = next((writer.root / layout.trip_folder(TRIP) / "views/packs").glob("*.md")).read_text()
    assert expected in day_pack and "未入仓 0 条" in day_pack
    assert check(writer.repository)["problems"] == []
    # The tools still report the commit ID and the writes not yet in the canonical branch.
    turn = writer.turn("Later.", "later")
    assert turn["snapshot"]["repo"]["head"] == git(writer.root, "rev-parse", "--short=7", "HEAD").strip()
    assert turn["snapshot"]["repo"]["unsynced_ops"] == [turn["write"]["op_id"]]
