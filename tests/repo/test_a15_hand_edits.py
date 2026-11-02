"""A15: plan files edited without the tool (in the working copy, or already in a commit made on the Git host's page, by
another agent or by hand) are taken in when the tenant opens, after a sync and before each write: an edit that passes
the checks becomes a version recorded as an external edit; one that does not stays in the files, is not overwritten and
is said on the front screen. Before A15 an edit already in a commit was only reported by `tripwright check`, and the
next write regenerated the plan files from the previous version over it."""
from __future__ import annotations

import pytest
import yaml

from repo_fixtures import TRIP, Writer, bare, clone, git, new_repository, plan
from tripwright.repo import layout
from tripwright.repo.files import check
from tripwright.storage import now

CHECKIN = dict(id="a1101-checkin", time="11:00", text="Online check-in closes", subject=["p1", "p2", "p3"])
DIFFERS = "plan files differ from the last version"


def seed(writer: Writer):
    document = plan(days=2)
    document["days"][0]["anchors"] = [dict(CHECKIN)]
    turn = writer.turn("Create the fictional trip.", "seed")
    writer.commit(writer.propose(turn, "seed", plan=document), "seed", accept_unverified=True)
    writer.service.committer.flush()


@pytest.fixture
def writer(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo"))
    seed(writer)
    yield writer
    writer.close()


def day_path(root, date: str):
    return root / layout.trip_folder(TRIP) / f"plan/days/{date}.yaml"


def hand_edit(root, date: str, edit, *, commit: str | None = None):
    """Change one day file as a person would: YAML only, `plan/versions.jsonl` untouched; optionally committed."""
    path = day_path(root, date)
    day = yaml.safe_load(path.read_text())
    edit(day)
    path.write_text(layout.dump(day))
    if commit:
        git(root, "add", str(path.relative_to(root)))
        git(root, "-c", "user.name=Web editor", "-c", "user.email=web@example.invalid", "commit", "-q", "-m", commit)


def online(day: dict):
    next(anchor for anchor in day["anchors"] if anchor["id"] == "a1101-checkin")["where"] = "online"


def anchor(writer: Writer, anchor_id: str) -> dict:
    return next(item for day in writer.service.state("local")["plan"]["days"] for item in day["anchors"] if item["id"] == anchor_id)


def committed_day(root, date: str) -> dict:
    return yaml.safe_load(git(root, "show", f"HEAD:{day_path(root, date).relative_to(root)}"))


def job_write(writer: Writer, key: str):
    """What a lookup or a deep job writes: new evidence, carried by a `job` operation, then committed."""
    service = writer.service
    before = {record["id"] for record in service.store.all("evidence", "local")}
    with service.store.transaction():
        service.store.put("evidence", "local", dict(id=f"E-{key}", source="Example Rail timetable", fetched_at=now(),
                                                    valid_until="2030-06-11T00:00:00+02:00", stale=False, synthetic=True,
                                                    date="2030-06-10", facts=["hard_node"]), insert=True)
    service.record_evidence("local", before, key=f"deep:{key}")
    return service.committer.flush()


def test_a_committed_yaml_only_hand_edit_survives_the_next_job_write_and_becomes_a_version(writer):
    """Bad sample: the evaluation repository's edit (an anchor given where: online, committed, versions untouched)."""
    before = writer.service.state("local")["number"]
    hand_edit(writer.root, "2030-06-10", online, commit="Check-in is online")
    assert any(problem.startswith(DIFFERS) for problem in check(writer.repository)["problems"])
    job_write(writer, "after-edit")
    current = writer.service.state("local")
    assert current["number"] == before + 1 and current["origin"] == "external_edit"
    assert current["submitted_by"].startswith("external edit")
    assert anchor(writer, "a1101-checkin")["where"] == "online"
    assert committed_day(writer.root, "2030-06-10")["anchors"][0]["where"] == "online", "the job write kept the edit"
    assert git(writer.root, "status", "--porcelain") == ""
    assert not any(problem.startswith(DIFFERS) for problem in check(writer.repository)["problems"])
    [line] = [line for line in current["changelog"] if line["target"] == "anchor:a1101-checkin"]
    assert line["field"] == "where" and line["by"] == "user" and line["basis"]["edit"] == "external"
    # Later writes keep it too.
    job_write(writer, "later")
    assert committed_day(writer.root, "2030-06-10")["anchors"][0]["where"] == "online"


def test_a_hand_edit_is_taken_in_when_the_tenant_opens_before_anything_is_written(writer, tmp_path):
    hand_edit(writer.root, "2030-06-10", online, commit="Edited by another agent")
    writer.close()
    reopened = Writer(writer.root)
    try:
        current = reopened.service.state("local")
        assert current["origin"] == "external_edit" and anchor(reopened, "a1101-checkin")["where"] == "online"
        reopened.service.committer.flush()
        assert committed_day(reopened.root, "2030-06-10")["anchors"][0]["where"] == "online"
    finally:
        reopened.close()
        writer.store = reopened.store  # the fixture closes the writer again


def test_a_hand_edit_written_in_the_turn_before_a_write_is_the_base_of_that_write(writer):
    hand_edit(writer.root, "2030-06-10", online)  # not committed: only in the working copy
    turn = writer.turn("Rename day two.", "rename")
    current = writer.service.state("local")
    assert turn["base_version"] == current["number"] and current["origin"] == "external_edit"
    mine = current["plan"]
    mine["days"][1]["title"] = "Renamed by the tool"
    writer.commit(writer.propose(turn, "rename", plan=mine, changes=dict(why="asked")), "rename", accept_unverified=True)
    writer.service.committer.flush()
    assert anchor(writer, "a1101-checkin")["where"] == "online"
    assert committed_day(writer.root, "2030-06-10")["anchors"][0]["where"] == "online"
    assert committed_day(writer.root, "2030-06-11")["title"] == "Renamed by the tool"


def test_a_hand_edit_made_while_a_tool_version_waits_to_be_written_is_redone_on_it(writer):
    turn = writer.turn("Rename day two.", "rename")
    mine = writer.service.state("local")["plan"]
    mine["days"][1]["title"] = "Renamed by the tool"
    draft = writer.propose(turn, "rename", plan=mine, changes=dict(why="asked"))
    hand_edit(writer.root, "2030-06-10", online)  # the files still show the version before the commit below
    writer.commit(draft, "rename", accept_unverified=True)
    writer.service.committer.flush()
    current = writer.service.state("local")
    assert current["origin"] == "external_edit"
    assert anchor(writer, "a1101-checkin")["where"] == "online" and current["plan"]["days"][1]["title"] == "Renamed by the tool"
    assert {(line["target"], line["field"]) for line in current["changelog"]} == {("anchor:a1101-checkin", "where")}
    assert committed_day(writer.root, "2030-06-10")["anchors"][0]["where"] == "online"
    assert committed_day(writer.root, "2030-06-11")["title"] == "Renamed by the tool"


def test_a_hand_edit_pushed_from_the_git_host_is_taken_in_by_sync_and_kept_by_the_next_write(tmp_path):
    remote = bare(tmp_path / "remote.git")
    laptop = Writer(new_repository(tmp_path / "laptop", remote=remote))
    try:
        seed(laptop)
        web = clone(remote, tmp_path / "web")  # the Git host's web editor: a plain commit on main
        hand_edit(web, "2030-06-10", online, commit="Update 2030-06-10.yaml")
        git(web, "push", "-q", "origin", "HEAD:main")
        laptop.turn("A message written before the sync.", "before-sync")  # waits in the outbox, replayed on the new head
        outcome = laptop.service.committer.sync()
        assert outcome["conflicts"] == [] and outcome["adopted"]["plan"]["adopted"] is True
        assert anchor(laptop, "a1101-checkin")["where"] == "online"
        turn = laptop.turn("Rename day two.", "rename")
        mine = laptop.service.state("local")["plan"]
        mine["days"][1]["title"] = "Renamed on the laptop"
        laptop.commit(laptop.propose(turn, "rename", plan=mine, changes=dict(why="asked")), "rename", accept_unverified=True)
        assert laptop.service.committer.flush()["pushed"] == "ok"
        shown = yaml.safe_load(git(remote, "show", f"main:{layout.trip_folder(TRIP)}/plan/days/2030-06-10.yaml"))
        assert shown["anchors"][0]["where"] == "online"
        assert not any(problem.startswith(DIFFERS) for problem in check(laptop.repository)["problems"])
        assert all(op["state"] == "effective" for op in laptop.ops().values())
    finally:
        laptop.close()


def test_a_hand_edit_that_fails_the_checks_is_not_lost_not_taken_in_and_said_on_the_front_screen(writer):
    """Control: an online node with travel time does not validate; the version stays and the edit stays in the files."""
    before = writer.service.state("local")
    hand_edit(writer.root, "2030-06-11", lambda day: day["anchors"][0].update(where="online"), commit="Rail departure is online")
    job_write(writer, "after-bad-edit")
    current = writer.service.state("local")
    assert current["number"] == before["number"], "not taken in"
    assert "where" not in anchor(writer, "example-rail-departure") or anchor(writer, "example-rail-departure")["where"] is None
    assert committed_day(writer.root, "2030-06-11")["anchors"][0]["where"] == "online", "not overwritten"
    [notice] = [notice for notice in writer.turn("what now", "now")["notices"] if notice["kind"] == "manual_edit"]
    assert notice["user_line"].startswith("有未入版的手改：")
    assert "2030-06-11 example-rail-departure：an online hard node (where: online) needs no travel" in notice["user_line"]
    assert "版本没变，手改留在文件里" in notice["user_line"] and notice["paths"] == [f"{layout.trip_folder(TRIP)}/plan/days/2030-06-11.yaml"]
    readme = (writer.root / layout.trip_folder(TRIP) / "README.md").read_text()
    assert "注意：有未入版的手改" in readme
    # A tool write to another day goes ahead and still leaves the held file alone.
    turn = writer.turn("Rename day one.", "rename")
    mine = writer.service.state("local")["plan"]
    mine["days"][0]["title"] = "Renamed by the tool"
    writer.commit(writer.propose(turn, "rename", plan=mine, changes=dict(why="asked")), "rename", accept_unverified=True)
    writer.service.committer.flush()
    assert committed_day(writer.root, "2030-06-10")["title"] == "Renamed by the tool"
    assert committed_day(writer.root, "2030-06-11")["anchors"][0]["where"] == "online"
    # Fixed by hand (no travel for an online node): taken in at the next write, on top of the tool's change.
    hand_edit(writer.root, "2030-06-11", lambda day: day["anchors"][0].pop("travel_minutes"))
    job_write(writer, "after-fix")
    fixed = writer.service.state("local")
    assert fixed["origin"] == "external_edit" and anchor(writer, "example-rail-departure")["where"] == "online"
    assert fixed["plan"]["days"][0]["title"] == "Renamed by the tool"
    assert not [notice for notice in writer.turn("and now", "later")["notices"] if notice["kind"] == "manual_edit"]


def test_a_hand_edit_and_a_later_version_changing_the_same_field_differently_is_held_and_names_both(writer):
    turn = writer.turn("Note for day one.", "tool-note")
    mine = writer.service.state("local")["plan"]
    mine["days"][0]["notes"] = "Tool note"
    draft = writer.propose(turn, "tool-note", plan=mine, changes=dict(why="asked"))
    hand_edit(writer.root, "2030-06-10", lambda day: day.update(notes="Hand note"))
    writer.commit(draft, "tool-note", accept_unverified=True)
    writer.service.committer.flush()
    assert writer.service.state("local")["plan"]["days"][0]["notes"] == "Tool note"
    assert yaml.safe_load(day_path(writer.root, "2030-06-10").read_text())["notes"] == "Hand note", "the edit stays"
    [notice] = [notice for notice in writer.turn("what now", "now")["notices"] if notice["kind"] == "manual_edit"]
    assert notice["user_line"] == ("有未入版的手改：两处改了同一项、改得不一样：2030-06-10 那天的备注：别处改成了「Tool note」，"
                                   "这次改成了「Hand note」。要用哪个？版本没变，手改留在文件里；改好或撤掉后，下次写入时收进来。")
    # Choosing by hand (the files say the tool's note again) clears it.
    hand_edit(writer.root, "2030-06-10", lambda day: day.update(notes="Tool note"))
    assert not [notice for notice in writer.turn("chosen", "chosen")["notices"] if notice["kind"] == "manual_edit"]
