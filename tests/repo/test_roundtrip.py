"""Promise 1 (design 5.1): the repository is the canonical store. A database written into the repository layout and
rebuilt from it is equal value for value; one record per file, journals only grow, readable IDs."""
from __future__ import annotations

import re

import pytest
import yaml

from repo_fixtures import TRIP, Writer, by_id, git, new_repository, plan
from tripwright.repo import index, layout
from tripwright.repo.migrate import export_store
from tripwright.service import Tripwright
from tripwright.storage import Store, identifier, now


def history(service: Tripwright, owner: str = "alice") -> None:
    """A trip with several versions, facts, items, a decision, a pending check and its closure."""
    turn = service.turn(owner, "Create the trip.", "seed")
    draft = service.propose(owner, ctx_id=turn["ctx_id"], base_version=0, plan=plan(days=2), idempotency_key="seed")
    service.commit(owner, draft["draft_id"], "seed", accept_unverified=True)
    turn = service.turn(owner, "我没有箱子。我想轻松一点。", "say", nature="user_verbatim", native_ref="fixture:say", speaker="p1",
                        record=dict(facts=[dict(key="luggage", value="none", text="p1 没有箱子", subject=["p1"], true_from=now(),
                                                invalidated_by="user says otherwise", scope=dict(start="2030-06-10", end="2030-06-10"),
                                                source=dict(event="this", basis="user_explicit"))],
                                    items=[dict(kind="preference", key="pace", quote="我想轻松一点。", text="轻松一点", subject=["p1"],
                                                strength="prefer", source=dict(event="this", basis="user_explicit"), long_term=True)]))
    draft = service.propose(owner, ctx_id=turn["ctx_id"], base_version=1, idempotency_key="check",
                            changes=dict(pending_checks=[dict(question="Is the gallery open?", due_at="2030-06-10T09:00:00+02:00", responsible="host",
                                                              subject=["p1"], source=dict(event=turn["receipt"]["event_id"]),
                                                              consequence="No gallery", alternative="Park")]))
    service.commit(owner, draft["draft_id"], "check")
    check_id = service.store.all("pending_checks", owner)[0]["id"]
    turn = service.turn(owner, "Not confirmed.", "close")
    draft = service.propose(owner, ctx_id=turn["ctx_id"], base_version=2, idempotency_key="close",
                            changes=dict(resolve_pending=[dict(id=check_id, status="unverified", result="Not confirmed")]))
    service.commit(owner, draft["draft_id"], "close")
    current = service.state(owner)["plan"]
    current["days"][0]["title"] = "Oslo — renamed"
    turn = service.turn(owner, "Rename day one.", "rename")
    pace = next(item["id"] for item in service.ledger.current(owner)["items"] if item["key"] == "pace")
    draft = service.propose(owner, ctx_id=turn["ctx_id"], base_version=3, plan=current, idempotency_key="rename",
                            usages=[dict(item_id=pace, status="not_applicable", reason="Only the day title changes")])
    service.commit(owner, draft["draft_id"], "rename", accept_unverified=True)


def test_database_written_into_the_repository_and_rebuilt_is_equal_value_for_value(tmp_path):
    source = Store(tmp_path / "single.sqlite3")
    service = Tripwright(source)
    history(service)
    root = export_store(source, "alice", tmp_path / "repo").root
    original = {table: source.all(table, "alice") for table in index.INDEX_TABLES}
    service.jobs.stop()
    source.close()
    (root / ".tripwright" / "state.sqlite").unlink()
    rebuilt = Writer(root, owner="alice")
    try:
        assert by_id(rebuilt.tables()) == by_id(original)
        for table in ("journal",):
            assert [record["id"] for record in rebuilt.tables()[table]] == [record["id"] for record in original[table]]
        received = lambda tables: [event["id"] for event in tables["events"] if "received" in event]
        assert received(rebuilt.tables()) == received(original)
        # Four versions (a turn that records a fact does not make one); each older plan comes back from its commit.
        assert [version["plan"] is not None for version in rebuilt.tables()["versions"]] == [True] * 4
        assert rebuilt.service.state("alice")["number"] == 4
        assert git(root, "log", "--format=%B").count("Tripwright-Version:") == 4
    finally:
        rebuilt.close()


def test_one_record_per_file_and_the_layout_of_a_trip(tmp_path):
    root = new_repository(tmp_path / "repo")
    writer = Writer(root)
    try:
        history(writer.service, owner="local")
        writer.service.committer.flush()
        folder = root / layout.trip_folder(TRIP)
        assert (root / "tripwright.yaml").exists() and yaml.safe_load((root / "tripwright.yaml").read_text())["current_trip"] == TRIP
        for name in ("trip.yaml", "plan/places.yaml", "plan/bookings.yaml", "plan/versions.jsonl", "README.md", "views/today.md",
                     "views/index.md", "views/decisions.md", "views/memory.md"):
            assert (folder / name).exists(), name
        items = list((folder / "ledger/items").glob("*.yaml"))
        assert len(items) == len(writer.store.all("items", "local")) == 1
        assert len(list((folder / "ledger/facts").glob("*.yaml"))) == 1 and len(list((folder / "ledger/checks").glob("*.yaml"))) == 1
        assert len(list((folder / "raw").rglob("*.md"))) == len([event for event in writer.store.all("events", "local") if "received" in event])
        assert (root / "profile/items" / items[0].name).exists(), "a long-term item is also kept in the profile"
        assert (root / ".gitignore").read_text().startswith(".tripwright/") and "merge=union" in (root / ".gitattributes").read_text()
        for name in ("AGENTS.md", "CLAUDE.md", ".claude/skills/tripwright-main/SKILL.md", ".agents/skills/tripwright-main/SKILL.md"):
            assert (root / name).exists(), name
        assert git(root, "status", "--porcelain") == ""
        assert ".tripwright" not in git(root, "ls-files")
    finally:
        writer.close()


def test_records_change_only_their_status_and_journals_only_grow(tmp_path):
    root = new_repository(tmp_path / "repo")
    writer = Writer(root)
    try:
        history(writer.service, owner="local")
        writer.service.committer.flush()
        log = git(root, "log", "-p", "--format=format:COMMIT", "--", ":(glob)trips/*/ledger/journal/**", ":(glob)trips/*/plan/versions.jsonl", ":(glob)trips/*/raw/**")
        removed = [row for row in log.splitlines() if row.startswith("-") and not row.startswith("---")]
        assert removed == []
        check = next((root / layout.trip_folder(TRIP) / "ledger/checks").glob("*.yaml"))
        changes = git(root, "log", "-p", "--format=format:COMMIT", "--", str(check.relative_to(root)))
        changed_lines = {row[1:].split(":")[0] for row in changes.splitlines() if row[:1] in "+-" and not row.startswith(("+++", "---"))}
        assert changed_lines <= {"status", "result", "id", "question", "due_at", "responsible", "subject", "scope", "source", "consequence",
                                 "alternative", "evidence_ids", "  event", "  nature", "  basis", "- p1", "  start", "  end", "  time_from",
                                 "  time_to", "  place", "  channel"} | {""}
    finally:
        writer.close()


def test_ids_are_type_date_and_random():
    first = identifier("I")
    assert re.fullmatch(r"I-\d{6}-[0-9a-z]{6}", first) and first != identifier("I")


@pytest.mark.parametrize("value", [{"a": "2030-07-15"}, {"a": "yes", "b": "on", "c": "1e3"}, {"text": "line\nnext: x"}, {"n": 1.0, "z": None}])
def test_yaml_files_load_back_to_exactly_what_was_written(value):
    assert yaml.safe_load(layout.dump(value)) == value


def test_a_message_file_round_trips_text_and_attributes_exactly():
    event = dict(id="E-301031-abcdef", request_id="r1", received=dict(text="---\n  多行\n原话\n", nature="user_verbatim", channel="conversation",
                                                                      native_ref="m1", speaker="u1", suspicious_words=[]),
                 receipt=dict(event_id="E-301031-abcdef", received_text="---\n  多行\n原话\n", nature="user_verbatim", verbatim=True,
                              received_at="2030-10-31T10:00:00+00:00", native_ref="m1"))
    path = layout.raw_name(event, "Europe/Vienna")
    assert path == "raw/2030-10-31/20301031T100000Z-u1-abcdef.md"
    assert layout.event_record(path, layout.event_text(event)) == event
