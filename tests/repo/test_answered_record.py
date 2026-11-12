"""The record of the user's answer travels with the version (2026-11-12): a plan whose unverified facts the user
accepted says which message accepted them, in the repository's files and in every working copy built from them."""
from __future__ import annotations

import pytest

from repo_fixtures import Writer, bare, clone, git, new_repository, plan


def test_the_answering_message_is_in_the_version_file_and_in_a_second_copy(tmp_path):
    remote = bare(tmp_path / "remote.git")
    first = Writer(new_repository(tmp_path / "laptop", remote=remote))
    try:
        turn = first.turn("Create the fictional trip.", "seed")
        draft = first.propose(turn, "seed", plan=plan(days=1))
        asked = [check["id"] for check in draft["ask_user"]["unverified"]]
        assert asked
        with pytest.raises(ValueError, match="Not saved yet"):
            first.commit(draft, "seed", accept_unverified=True, ask_first=True)
        answer = first.turn("Yes, save it like that.", "yes")
        assert answer["waiting_draft"] == dict(answer["waiting_draft"], draft_id=draft["draft_id"], unverified=asked, stale=False)
        version = first.commit(draft, "seed", accept_unverified=True, ask_first=True)
        record = dict(event=answer["receipt"]["event_id"], unverified=asked)
        assert version["answered"] == record
        assert first.service.committer.flush()["pushed"] == "ok"
        assert "answered" in git(first.root, "show", "HEAD:trips/2030-06-fjord/plan/versions.jsonl")
        second = Writer(clone(remote, tmp_path / "phone"))
        try:
            assert second.service.state("local")["answered"] == record
        finally:
            second.close()
    finally:
        first.close()
