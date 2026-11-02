"""A13 (D-75): a plan change made on an older version is redone on the newer plan when the push is rejected, instead of
becoming a conflict. Only a field both sides changed differently is a question for the user; the full checks (must
locks, hard constraints) still run on the new head. Two working copies and a local bare repository as the remote."""
from __future__ import annotations

import pytest

from repo_fixtures import Writer, bare, clone, git, new_repository, plan
from tripwright.storage import now

REDONE = "在别处的改动之上重做了这次改动"


@pytest.fixture
def pair(tmp_path):
    remote = bare(tmp_path / "remote.git")
    first = Writer(new_repository(tmp_path / "laptop", remote=remote))
    turn = first.turn("Create the fictional trip.", "seed")
    first.commit(first.propose(turn, "seed", plan=plan(days=2)), "seed", accept_unverified=True)
    first.service.committer.flush()
    second = Writer(clone(remote, tmp_path / "phone"))
    yield first, second, remote
    first.close()
    second.close()


def day(writer, index: int) -> dict:
    return writer.service.state("local")["plan"]["days"][index]


def change(writer, key: str, edit) -> dict:
    """One message and one plan commit made on whatever version this copy has now; not pushed yet."""
    turn = writer.turn(f"Change requested ({key}).", key)
    mine = writer.service.state("local")["plan"]
    edit(mine)
    return writer.commit(writer.propose(turn, key, plan=mine, changes=dict(why=f"asked in {key}")), key, accept_unverified=True)


def block(document: dict, day_index: int, block_id: str) -> dict:
    return next(item for track in document["days"][day_index]["plans"][0]["tracks"] for item in track["blocks"] if item["id"] == block_id)


def test_changes_to_different_days_are_both_kept_and_the_later_one_says_it_was_redone(pair):
    """Bad sample: before A13 the laptop's commit, based on the old version, became a conflict and was not pushed."""
    laptop, phone, remote = pair
    # The phone renames day one and pushes first.
    change(phone, "phone-day-one", lambda document: document["days"][0].update(title="Phone: day one"))
    phone.service.committer.flush()
    # The laptop, still on the old version, records a fact (the ledger moves too) and changes day two in two places.
    noted = laptop.turn("I have no luggage.", "laptop-fact", record=dict(facts=[dict(
        key="luggage", value=True, text="p1 has no luggage", subject=["p1"], true_from=now(), invalidated_by="user says otherwise",
        source=dict(event="this", basis="user_explicit"))]))

    def day_two(document):
        document["days"][1]["title"] = "Laptop: day two"
        block(document, 1, "d2-solo")["start"] = "10:00"
    committed = change(laptop, "laptop-day-two", day_two)
    assert laptop.service.committer.flush()["pushed"] == "ok"
    ops = laptop.ops()
    plan_op = ops[committed["write"]["op_id"]]
    assert plan_op["state"] == "effective", plan_op["transitions"]
    assert ops[noted["write"]["op_id"]]["state"] == "effective"
    # Both changes are in the plan; nothing was asked.
    current = laptop.service.state("local")
    assert current["plan"]["days"][0]["title"] == "Phone: day one"
    assert current["plan"]["days"][1]["title"] == "Laptop: day two" and block(current["plan"], 1, "d2-solo")["start"] == "10:00"
    assert laptop.turn("What now?", "after")["notices"] == []
    # The version and its changelog say the change was redone on top of the other one, and only list the laptop's change.
    version = laptop.store.get("versions", "local", committed["id"])
    assert version["redone"]["note"] == REDONE and version["redone"]["on_version"] == version["number"] - 1
    assert version["changelog"] and all(entry["redone"] == REDONE for entry in version["changelog"])
    assert {(entry["target"], entry["field"]) for entry in version["changelog"]} >= {("day:2030-06-11", "title")}
    assert not any(entry["target"] == "day:2030-06-10" for entry in version["changelog"])
    assert committed["write"]["op_id"] in git(remote, "log", "--format=%B", "main")
    # The phone catches up and sees the same plan.
    phone.service.committer.sync()
    assert phone.service.state("local")["plan"] == current["plan"]


def test_the_receipt_says_redone_when_the_commit_waited_for_its_replay(pair, monkeypatch):
    laptop, phone, _ = pair
    change(phone, "phone-first", lambda document: document["days"][0].update(title="Phone first"))
    phone.service.committer.flush()
    committer = laptop.service.committer
    monkeypatch.setattr(committer, "wait", lambda op_id, timeout: committer.flush())
    turn = laptop.turn("Rename day two.", "laptop-second")
    mine = laptop.service.state("local")["plan"]
    mine["days"][1]["title"] = "Laptop second"
    draft = laptop.propose(turn, "laptop-second", plan=mine, changes=dict(why="asked"))
    receipt = laptop.service.commit("local", draft["draft_id"], "laptop-second", accept_unverified=True, wait=5)
    assert receipt["write"]["state"] == "effective"
    assert receipt["redone"]["note"] == REDONE and receipt["number"] == 3
    assert receipt["plan"]["days"][0]["title"] == "Phone first" and receipt["plan"]["days"][1]["title"] == "Laptop second"


def test_the_same_field_changed_differently_is_still_a_question_naming_the_item_and_both_values(pair):
    laptop, phone, remote = pair
    change(phone, "phone-start", lambda document: block(document, 0, "d1-art").update(start="10:15"))
    phone.service.committer.flush()
    committed = change(laptop, "laptop-start", lambda document: block(document, 0, "d1-art").update(start="10:45"))
    laptop.service.committer.flush()
    plan_op = laptop.ops()[committed["write"]["op_id"]]
    assert plan_op["state"] == "conflict" and plan_op["transitions"][-1]["reason"] == "plan_base_changed"
    assert "d1-art" in plan_op["transitions"][-1]["detail"]
    [notice] = laptop.turn("What now?", "after")["notices"]
    assert notice["reason"] == "plan_base_changed"
    assert notice["user_line"].startswith("两处改了同一项、改得不一样：2030-06-10 的「")
    assert "（d1-art）的开始时间：别处改成了「10:15」，这次改成了「10:45」。要用哪个？" in notice["user_line"]
    assert block(laptop.service.state("local")["plan"], 0, "d1-art")["start"] == "10:15", "the first one stands until the user picks"
    assert committed["write"]["op_id"] not in git(remote, "log", "--format=%B", "main")


def test_a_redone_change_that_breaks_a_must_lock_on_the_new_head_is_refused_with_the_reason(pair):
    laptop, phone, remote = pair
    # The phone records the user's must: the after-dinner café stays (D-53); the plan itself does not change.
    turn = phone.turn("饭后就去那家咖啡馆，定了。", "lock", nature="user_verbatim", native_ref="fixture:lock", speaker="p1")
    coffee = block(phone.service.state("local")["plan"], 0, "d1-coffee")
    decision = dict(question="饭后去哪", options=["咖啡馆", "公园"], choice=coffee["place"], decided_by="user", must=True,
                    subject=["p1", "p2", "p3"], source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={"/blocks/d1-coffee": coffee})
    phone.commit(phone.propose(turn, "lock", changes=dict(decisions=[decision])), "lock", accept_unverified=True)
    phone.service.committer.flush()
    # The laptop, which never saw the lock, moves that block to the park; no field clashes, but the lock refuses it.
    committed = change(laptop, "laptop-park", lambda document: block(document, 0, "d1-coffee").update(place="oslo-park"))
    laptop.service.committer.flush()
    plan_op = laptop.ops()[committed["write"]["op_id"]]
    assert plan_op["state"] == "conflict" and plan_op["transitions"][-1]["reason"] == "replay_refused"
    detail = plan_op["transitions"][-1]["detail"]
    assert detail.startswith("Redone on the plan changed elsewhere, then refused by its checks: Commit refused") and "d1-coffee" in detail
    assert block(laptop.service.state("local")["plan"], 0, "d1-coffee")["place"] == coffee["place"]
    assert committed["write"]["op_id"] not in git(remote, "log", "--format=%B", "main")


def test_three_way_keeps_added_and_removed_objects_and_order_by_identity():
    from copy import deepcopy

    from tripwright.locks import identify
    from tripwright.repo.rebase import three_way

    base = identify(plan(days=2))
    blocks = lambda document: document["days"][0]["plans"][0]["tracks"][0]["blocks"]  # noqa: E731
    ours, theirs = deepcopy(base), deepcopy(base)
    added = dict(blocks(ours)[0], id="d1-extra", start="12:00", end="12:30")
    blocks(ours).insert(1, added)                       # ours adds a block after the first
    del blocks(theirs)[2]                               # theirs removes the last one
    theirs["days"][1]["title"] = "Theirs"
    merged, conflicts = three_way(base, ours, theirs)
    assert conflicts == []
    assert [item["id"] for item in blocks(merged)] == ["d1-art", "d1-extra", "d1-dinner"]
    assert merged["days"][1]["title"] == "Theirs"
    # Removing what the other side changed is a question, not a silent loss.
    ours, theirs = deepcopy(base), deepcopy(base)
    del blocks(ours)[0]
    blocks(theirs)[0]["note"] = "changed elsewhere"
    _, conflicts = three_way(base, ours, theirs)
    assert [conflict["path"][-1] for conflict in conflicts] == [("blocks", "d1-art")]
    # Both reordering the same blocks differently is a question too.
    ours, theirs = deepcopy(base), deepcopy(base)
    blocks(ours).reverse()
    blocks(theirs)[:] = [blocks(theirs)[1], blocks(theirs)[0], blocks(theirs)[2]]
    _, conflicts = three_way(base, ours, theirs)
    assert [conflict["path"][-1] for conflict in conflicts] == ["order"]
