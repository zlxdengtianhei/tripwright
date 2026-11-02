"""Promise 6 (design 5.6): edits made in the working copy without the tool."""
from __future__ import annotations

import pytest
import yaml

from repo_fixtures import TRIP, Writer, git, new_repository, seeded
from tripwright.repo import layout


@pytest.fixture
def writer(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo"))
    seeded(writer)
    yield writer
    writer.close()


def day_file(writer):
    return writer.root / layout.trip_folder(TRIP) / "plan/days/2030-06-10.yaml"


def lock_coffee(writer):
    """The user decides the after-dinner café is a must; the block is locked (B3k, D-53: only a must locks)."""
    turn = writer.turn("饭后就去那家咖啡馆，定了。", "lock", nature="user_verbatim", native_ref="fixture:lock", speaker="p1")
    block = writer.service.state("local")["plan"]["days"][0]["plans"][0]["tracks"][0]["blocks"][2]
    decision = dict(question="饭后去哪", options=["咖啡馆", "公园"], choice=block["place"], decided_by="user", must=True, subject=["p1", "p2", "p3"],
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"), locked_paths={f"/blocks/{block['id']}": block})
    writer.commit(writer.propose(turn, "lock", changes=dict(decisions=[decision])), "lock", accept_unverified=True)
    writer.service.committer.flush()
    return block


def test_a_hand_edit_that_passes_the_checks_becomes_a_version_recorded_as_external(writer):
    day = yaml.safe_load(day_file(writer).read_text())
    day["title"] = "Edited on the web"
    day_file(writer).write_text(layout.dump(day))
    before = writer.service.state("local")["number"]
    writer.service.committer.flush()
    current = writer.service.state("local")
    assert current["number"] == before + 1 and current["origin"] == "external_edit" and current["submitted_by"].startswith("external edit")
    assert current["plan"]["days"][0]["title"] == "Edited on the web"
    assert "Tripwright-Version: " + current["id"] in git(writer.root, "log", "-1", "--format=%B")
    assert git(writer.root, "status", "--porcelain") == ""
    assert writer.service.explain("local", version=current["number"])["version_usage"][0]["origin"] == "external_edit"


def test_a_hand_edit_of_a_user_decided_part_is_not_taken_in_and_the_front_screen_says_so(writer):
    block = lock_coffee(writer)
    day = yaml.safe_load(day_file(writer).read_text())
    day["plans"][0]["tracks"][0]["blocks"][2]["place"] = "oslo-park"
    day_file(writer).write_text(layout.dump(day))
    before = writer.service.state("local")["number"]
    writer.turn("anything", "after-edit")
    writer.service.committer.flush()
    assert writer.service.state("local")["number"] == before, "the version does not move"
    assert yaml.safe_load(day_file(writer).read_text())["plans"][0]["tracks"][0]["blocks"][2]["place"] == "oslo-park", "the edit stays"
    notices = writer.turn("what now", "notice")["notices"]
    assert any(notice["user_line"].startswith("有未入版的手改：冲突 locked:") for notice in notices)
    readme = (writer.root / layout.trip_folder(TRIP) / "README.md").read_text()
    assert "注意：有未入版的手改：冲突 locked:" in readme
    assert block["id"] in git(writer.root, "show", "HEAD:" + str(day_file(writer).relative_to(writer.root)))
    assert "oslo-park" not in git(writer.root, "show", "HEAD:" + str(day_file(writer).relative_to(writer.root))).split(block["id"])[1][:200]


def test_an_edited_generated_file_is_overwritten_by_the_next_commit_with_one_notice(writer):
    view = writer.root / layout.trip_folder(TRIP) / "views/today.md"
    original = view.read_text()
    view.write_text(original + "my own note\n")
    first = writer.turn("message", "after-view-edit")
    writer.service.committer.flush()
    assert view.read_text() != original + "my own note\n" and "my own note" not in view.read_text()
    assert any("生成的文件被手改过" in notice["user_line"] for notice in first["notices"]) or \
        "生成的文件被手改过" in git(writer.root, "log", "-1", "--format=%B")
    later = writer.turn("again", "later")
    assert not any("生成的文件被手改过" in notice["user_line"] for notice in later["notices"]), "said once"


def test_a_record_file_with_broken_format_keeps_the_previous_content_in_the_index(writer):
    turn = writer.turn("No luggage.", "fact", record=dict(facts=[dict(key="luggage", value="none", text="no luggage", subject=["p1"],
                                                                         true_from="2030-06-10T09:00:00+02:00", invalidated_by="user says otherwise",
                                                                         source=dict(event="this", basis="user_explicit"))]))
    writer.service.committer.flush()
    [fact_id] = turn["recorded"]["changed"]
    path = writer.root / layout.trip_folder(TRIP) / f"ledger/facts/{fact_id}.yaml"
    path.write_text("key: [unclosed\n")
    writer.turn("next", "next")
    writer.service.committer.flush()
    assert writer.store.get("facts", "local", fact_id)["text"] == "no luggage"
    assert path.read_text() == "key: [unclosed\n", "the broken file is left for the user to fix"
    assert any(notice["user_line"].startswith("文件格式坏了") for notice in writer.turn("x", "x")["notices"])
    # Rebuilding from the commit (not the working copy) still has the valid record.
    writer.service.committer.attach()
    assert writer.store.get("facts", "local", fact_id)["text"] == "no luggage"


def test_an_edited_message_file_is_restored(writer):
    turn = writer.turn("原话不改。", "original")
    writer.service.committer.flush()
    raw = next((writer.root / layout.trip_folder(TRIP) / "raw").rglob(f"*-{turn['receipt']['event_id'].rsplit('-', 1)[1]}.md"))
    raw.write_text(raw.read_text().replace("原话不改。", "被改了。"))
    writer.turn("next", "next")
    writer.service.committer.flush()
    assert "原话不改。" in raw.read_text()
