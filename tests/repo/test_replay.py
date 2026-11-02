"""E1 (filesystem design 5.6, packet 8): the outbox, replay and the point where a write becomes effective, with two
working copies and a local bare repository as the remote."""
from __future__ import annotations

import pytest

from repo_fixtures import Writer, bare, by_id, clone, git, new_repository, seeded
from tripwright.repo import index
from tripwright.storage import now


def fact(text: str, key: str) -> dict:
    return dict(facts=[dict(key=key, value=True, text=text, subject=["p1"], true_from=now(), invalidated_by="user says otherwise",
                            source=dict(event="this", basis="user_explicit"))])


@pytest.fixture
def pair(tmp_path):
    remote = bare(tmp_path / "remote.git")
    first = Writer(new_repository(tmp_path / "laptop", remote=remote))
    seeded(first)
    second = Writer(clone(remote, tmp_path / "phone"))
    yield first, second, remote
    first.close()
    second.close()


def remote_log(remote) -> str:
    return git(remote, "log", "--format=%B", "main")


def test_both_copies_start_from_the_same_index(pair):
    first, second, _ = pair
    assert by_id(first.tables()) == by_id(second.tables())
    assert second.service.state("local")["number"] == 1


def test_a_rejected_push_replays_by_operation_and_a_stale_plan_becomes_a_conflict_with_a_notice(pair):
    # A13: this is the case that stays a conflict: both sides changed the same field of the same day differently.
    first, second, remote = pair
    # The phone changes the plan and pushes first.
    turn = second.turn("Rename day one.", "phone-plan")
    changed = second.service.state("local")["plan"]
    changed["days"][0]["title"] = "Phone title"
    second.commit(second.propose(turn, "phone-plan", plan=changed), "phone-plan", accept_unverified=True)
    second.service.committer.flush()
    phone_version = second.service.state("local")["id"]
    # The laptop, still on V1, records a fact and commits its own plan change.
    noted = first.turn("I have no luggage.", "laptop-fact", record=fact("p1 has no luggage", "luggage"))
    turn = first.turn("Rename day one differently.", "laptop-plan")
    mine = first.service.state("local")["plan"]
    mine["days"][0]["title"] = "Laptop title"
    draft = first.propose(turn, "laptop-plan", plan=mine)
    committed = first.commit(draft, "laptop-plan", accept_unverified=True)
    assert committed["write"]["state"] == "received"
    outcome = first.service.committer.flush()
    assert outcome["pushed"] == "ok"
    ops = first.ops()
    plan_op = ops[committed["write"]["op_id"]]
    assert plan_op["state"] == "conflict" and plan_op["transitions"][-1]["reason"] == "plan_base_changed"
    assert [step["state"] for step in plan_op["transitions"]] == ["received", "pending_sync", "conflict"]
    assert ops[noted["write"]["op_id"]]["state"] == "effective", "a message and its fact never conflict"
    # The index now follows the phone's plan and keeps the laptop's fact; nothing is merged as text.
    assert first.service.state("local")["id"] == phone_version
    assert first.service.state("local")["plan"]["days"][0]["title"] == "Phone title"
    assert any(record["text"] == "p1 has no luggage" for record in first.store.all("facts", "local"))
    log = remote_log(remote)
    assert noted["write"]["op_id"] in log and committed["write"]["op_id"] not in log
    # Whoever saw the laptop's draft is told first, until a plan based on the new head is effective.
    later = first.turn("What now?", "after")
    [notice] = later["notices"]
    assert notice["kind"] == "write_conflict" and notice["reason"] == "plan_base_changed" and notice["op_id"] == plan_op["id"]
    # Both renamed the same day differently (D-75): the one question names the item and both values.
    assert notice["user_line"] == ("两处改了同一项、改得不一样：2030-06-10 那天的标题：别处改成了「Phone title」，"
                                   "这次改成了「Laptop title」。要用哪个？") and notice["replan"]["dates"] == ["2030-06-10"]
    redo = first.service.state("local")["plan"]
    redo["days"][0]["title"] = "Laptop title, on the new head"
    first.commit(first.propose(later, "redo", plan=redo), "redo", accept_unverified=True)
    first.service.committer.flush()
    assert first.turn("And now?", "after-redo")["notices"] == []


def test_a_push_whose_reply_is_lost_is_recognised_by_its_trailer_and_not_committed_twice(pair, monkeypatch):
    first, _, remote = pair
    push = first.repository.git.push

    def lost_reply(remote_name, branch):
        push(remote_name, branch)
        return "unreachable"

    monkeypatch.setattr(first.repository.git, "push", lost_reply)
    noted = first.turn("I have no luggage.", "lost-reply", record=fact("p1 has no luggage", "luggage"))
    first.service.committer.flush()
    assert first.ops()[noted["write"]["op_id"]]["state"] == "pending_sync"
    assert first.turn("Still there?", "while-pending")["write"]["unsynced"] == 2
    monkeypatch.setattr(first.repository.git, "push", push)
    first.service.committer.sync()
    assert first.ops()[noted["write"]["op_id"]]["state"] == "effective"
    assert remote_log(remote).count(f"Tripwright-Op: {noted['write']['op_id']}") == 1


def test_a_lost_database_is_rebuilt_from_the_repository_and_operations_recovered_from_trailers(pair, tmp_path):
    first, _, remote = pair
    first.turn("I have no luggage.", "before-loss", record=fact("p1 has no luggage", "luggage"))
    first.service.committer.flush()
    # The remote goes away: the next write is committed locally, pending_sync.
    (tmp_path / "remote.git").rename(tmp_path / "remote-away.git")
    offline = first.turn("My knee hurts.", "offline", record=fact("p1 knee hurts", "knee"))
    first.service.committer.flush()
    assert first.ops()[offline["write"]["op_id"]]["state"] == "pending_sync"
    received = first.turn("One more thing.", "only-received")
    before = first.tables()
    root = first.root
    first.close()
    (root / ".tripwright" / "state.sqlite").unlink()
    rebuilt = Writer(root)
    try:
        after = rebuilt.tables()
        # Everything committed comes back value for value; the write still only received was lost, and only it.
        lost = received["receipt"]["event_id"]
        expected = {table: {key: value for key, value in records.items() if key != lost and value.get("source_event") != lost}
                    for table, records in by_id(before).items()}
        expected["events"] = {key: value for key, value in expected["events"].items() if value.get("source_event") != lost}
        assert by_id(after) == expected
        ops = rebuilt.ops()
        assert ops[offline["write"]["op_id"]]["state"] == "pending_sync" and ops[offline["write"]["op_id"]]["replay"]["recovered"]
        (tmp_path / "remote-away.git").rename(tmp_path / "remote.git")
        rebuilt.service.committer.sync()
        assert rebuilt.ops()[offline["write"]["op_id"]]["state"] == "effective"
        assert remote_log(remote).count(offline["write"]["op_id"]) == 1
    finally:
        rebuilt.close()
        first.store = rebuilt.store


def test_two_writers_interleaving_messages_lose_nothing_and_end_with_equal_views(pair):
    first, second, remote = pair
    for round_ in range(4):
        for writer, name in ((first, "laptop"), (second, "phone")):
            writer.turn(f"{name} message {round_}", f"{name}-{round_}", record=fact(f"{name} fact {round_}", f"{name}-{round_}"))
        for writer in (second, first):
            writer.service.committer.flush()
    for writer in (first, second):
        writer.service.committer.sync()
    texts = lambda writer: sorted(event["received"]["text"] for event in writer.store.all("events", "local") if "received" in event)
    assert texts(first) == texts(second) and len(texts(first)) == 1 + 8
    assert sorted(record["text"] for record in first.store.all("facts", "local")) == sorted(record["text"] for record in second.store.all("facts", "local"))
    assert all(op["state"] == "effective" for writer in (first, second) for op in writer.ops().values())
    git(first.root, "fetch", "-q")
    assert git(first.root, "rev-parse", "HEAD") == git(first.root, "rev-parse", "origin/main")
    git(second.root, "fetch", "-q")
    assert git(second.root, "rev-parse", "HEAD") == git(first.root, "rev-parse", "HEAD")
    assert (first.root / "README.md").read_text() == (second.root / "README.md").read_text()


def test_the_index_swap_is_one_generation(pair, monkeypatch):
    """A reader during a swap sees the old generation or the new one: the replay happens beside the live index."""
    first, second, _ = pair
    second.turn("Phone note.", "phone-note", record=fact("phone note", "note"))
    second.service.committer.flush()
    first.turn("Laptop note.", "laptop-note", record=fact("laptop note", "note-2"))
    seen = []
    original = index.scratch

    def watching(tables, owner, runtime=None):
        seen.append(sorted(record["text"] for record in first.store.all("facts", "local")))
        return original(tables, owner, runtime)

    monkeypatch.setattr(index, "scratch", watching)
    first.service.committer.flush()
    assert seen and all(texts == ["laptop note"] for texts in seen[:1])
    assert sorted(record["text"] for record in first.store.all("facts", "local")) == ["laptop note", "phone note"]


def test_a_head_moved_outside_the_tool_keeps_the_writes_still_in_the_outbox(pair):
    first, second, _ = pair
    second.turn("Phone note.", "phone", record=fact("phone note", "note"))
    second.service.committer.flush()
    waiting = first.turn("Laptop note, not yet committed.", "laptop", record=fact("laptop note", "note-2"))
    git(first.root, "pull", "-q", "--ff-only", "origin", "main")
    outcome = first.service.committer.attach()
    assert outcome["rebuilt"] and outcome["replayed"] == [waiting["write"]["op_id"]]
    assert sorted(record["text"] for record in first.store.all("facts", "local")) == ["laptop note", "phone note"]
    first.service.committer.flush()
    assert first.ops()[waiting["write"]["op_id"]]["state"] == "effective"
