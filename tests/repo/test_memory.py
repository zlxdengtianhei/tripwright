"""Promise 5 (D-32, CONTRACTS 3.5): opening a trip imports the long-term entries as pending (applied, marked, asked
about in the conversation); the answer is recorded through turn; every commit writes the memory data B shows."""
from __future__ import annotations

import pytest
import yaml

from repo_fixtures import TRIP, Writer, bare, git, new_repository, plan
from tripwright.cli import main
from tripwright.render.memory import memory_problems
from tripwright.repo import layout
from tripwright.service import Tripwright
from tripwright.storage import Store

NEXT = "2031-05-wien"
SAID = "我喜欢美术馆，以后出门也一样。"


def first_trip(writer: Writer) -> str:
    """Trip one: the user states a lasting preference; the plan uses it."""
    turn = writer.turn("Create the trip.", "seed")
    writer.commit(writer.propose(turn, "seed", plan=plan()), "seed", accept_unverified=True)
    turn = writer.turn(SAID, "lasting", nature="user_verbatim", native_ref="fixture:lasting", speaker="p1",
                       record=dict(items=[dict(kind="preference", key="art", quote=SAID, text="喜欢美术馆", subject=["p1"], strength="prefer",
                                               long_term=True, source=dict(event="this", basis="user_explicit"))]))
    [item_id] = turn["recorded"]["changed"]
    turn = writer.turn("Plan the gallery.", "use")
    current = writer.service.state("local")["plan"]
    block = current["days"][0]["plans"][0]["tracks"][0]["blocks"][0]["id"]
    writer.commit(writer.propose(turn, "use", plan=current, usages=[dict(item_id=item_id, status="used", block_ids=[block])]),
                  "use", accept_unverified=True)
    writer.service.committer.flush()
    return item_id


@pytest.fixture
def writer(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo"))
    yield writer
    writer.close()


def test_a_lasting_item_is_kept_in_the_profile_with_where_it_was_said(writer):
    item_id = first_trip(writer)
    stored = yaml.safe_load((writer.root / f"profile/items/{item_id}.yaml").read_text())
    assert stored["quote"] == SAID and stored["origin"]["trip"] == TRIP and stored["origin"]["nature"] == "user_verbatim"
    assert stored["origin"]["path"].startswith(f"{layout.trip_folder(TRIP)}/raw/") and stored["origin"]["message"] == SAID
    people = yaml.safe_load((writer.root / "profile/people.yaml").read_text())
    assert [person["id"] for person in people] == ["p1", "p2", "p3"] and people[0]["trips"] == [TRIP]


def test_a_new_trip_imports_long_term_entries_as_pending_applied_and_marked(writer):
    item_id = first_trip(writer)
    opened = writer.turn("开新一趟：五月去维也纳。", "open", new_trip=NEXT, nature="user_verbatim", native_ref="fixture:open", speaker="p1")
    pending = {entry["id"]: entry for entry in opened["memory"]["pending"]}
    assert set(pending) == {item_id, "p1", "p2", "p3"} and pending[item_id]["from_trip"] == TRIP
    assert "在对话里问用户" in opened["memory"]["ask"]
    # 照用：the entry is in the package, marked, and asked about.
    records = opened["pack"]["meta"]["required"]
    assert item_id in records["items"]["ids"]
    assert any(line.startswith("偏好：") and f"[{item_id}]" in line for line in opened["pack"]["markdown"].splitlines())
    assert any(question.startswith("MEM-") for question in records["questions"]["ids"])
    assert writer.service.current_trip("local") == NEXT and writer.service.state("local")["number"] == 0
    copy = writer.store.get("items", "local", item_id)
    assert copy["origin"]["trip"] == TRIP and copy["origin"]["import_event"] == opened["receipt"]["event_id"]
    writer.service.committer.flush()
    assert yaml.safe_load((writer.root / "tripwright.yaml").read_text())["current_trip"] == NEXT
    folder = writer.root / layout.trip_folder(NEXT)
    assert (folder / f"ledger/carry/{item_id}.yaml").exists() and (folder / f"ledger/items/{item_id}.yaml").exists()
    view = (folder / "views/memory.md").read_text()
    assert "待确认" in view and f"[{item_id}]" in view and SAID in view
    memory = writer.service.state("local", "memory")
    assert memory_problems(memory) == [] and memory["trip"] == NEXT
    entry = next(entry for entry in memory["entries"] if entry["id"] == item_id)
    assert entry["status"] == "pending" and entry["source"]["trip"] == TRIP and entry["last_used"]["trip"] == TRIP
    assert entry["last_used"]["date"] == "2030-06-10" and entry["last_used"]["version_id"]
    # The earlier trip stays in the repository and on the front screen.
    assert (writer.root / layout.trip_folder(TRIP) / "plan/versions.jsonl").exists()


def test_the_users_answer_is_recorded_through_turn_and_a_not_used_entry_stops_applying(writer):
    item_id = first_trip(writer)
    writer.turn("开新一趟：五月去维也纳。", "open", new_trip=NEXT)
    answer = writer.turn("美术馆那条这趟不用；p2 照旧同行。", "answer", nature="user_verbatim", native_ref="fixture:answer",
                         record=dict(carry=[dict(id=item_id, status="not_used", source=dict(event="this")),
                                            dict(id="p2", status="confirmed", source=dict(event="this"))]))
    assert set(answer["recorded"]["changed"]) == {item_id, "p2"}
    assert item_id not in answer["pack"]["meta"]["required"]["items"]["ids"]
    memory = {entry["id"]: entry for entry in writer.service.state("local", "memory")["entries"]}
    assert memory[item_id]["status"] == "not_used" and memory[item_id]["status_event"] == answer["receipt"]["event_id"]
    assert memory["p2"]["status"] == "confirmed" and memory["p1"]["status"] == "pending"
    with pytest.raises(ValueError, match="not a long-term entry imported"):
        writer.turn("x", "bad-carry", record=dict(carry=[dict(id="I-000000-nosuch", status="confirmed", source=dict(event="this"))]))
    writer.service.committer.flush()
    assert any(line.get("entry", {}).get("action") == "carry_over" for path in (writer.root / layout.trip_folder(NEXT) / "ledger/journal").glob("*.jsonl")
               for line in map(yaml.safe_load, path.read_text().splitlines()))


def test_a_new_trip_survives_a_rebuild_and_check_regenerates_its_files(writer):
    item_id = first_trip(writer)
    writer.turn("开新一趟：五月去维也纳。", "open", new_trip=NEXT)
    writer.service.committer.flush()
    root = writer.root
    writer.close()
    (root / ".tripwright/state.sqlite").unlink()
    again = Writer(root)
    try:
        assert again.service.current_trip("local") == NEXT and again.store.get("carry", "local", item_id)["status"] == "pending"
        from tripwright.repo.files import check

        assert check(again.repository)["problems"] == []
    finally:
        again.close()
        writer.store = again.store


def test_opening_a_trip_needs_a_repository_and_every_earlier_write_synced(writer, tmp_path):
    single = Tripwright(Store(tmp_path / "single.sqlite3"))
    with pytest.raises(ValueError, match="needs a trip repository"):
        single.turn("local", "开新一趟", "open", new_trip=NEXT)
    single.jobs.stop()
    single.store.close()
    with pytest.raises(ValueError, match="Trip ID must be a folder name"):
        writer.turn("开新一趟", "bad-name", new_trip="../escape")
    remote = bare(tmp_path / "remote.git")
    git(writer.root, "remote", "add", "origin", str(remote))
    first_trip(writer)
    (tmp_path / "remote.git").rename(tmp_path / "away.git")
    writer.turn("one more", "pending")
    with pytest.raises(ValueError, match="not in the canonical branch yet"):
        writer.turn("开新一趟", "open-offline", new_trip=NEXT)
    assert writer.service.current_trip("local") == TRIP


def test_cli_new_trip_lists_what_to_ask_about(writer, capsys):
    first_trip(writer)
    root = writer.root
    writer.close()
    assert main(["--repo", str(root), "new-trip", NEXT, "--message", "开新一趟：维也纳"]) == 0
    output = capsys.readouterr().out
    assert NEXT in output and "p2" in output and "pending" in output
    reopened = Writer(root)
    try:
        assert reopened.service.current_trip("local") == NEXT
    finally:
        reopened.close()
        writer.store = reopened.store
