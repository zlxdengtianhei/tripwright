"""s4b: `tripwright --repo X check` and `status` only read. On 11-03 a `check` on a copy of my-travel pushed a merge
commit that was still being reviewed: opening the trip for the check attached the index (recovering the local commits
as writes waiting to be pushed) and the shutdown flush pushed them. Now neither command fetches, pushes, commits or
touches the working tree; `sync` and writes still do."""
from __future__ import annotations

import json

import pytest
from repo_fixtures import Writer, bare, clone, git, new_repository, seeded

from tripwright.cli import main


def refs(folder) -> str:
    return git(folder, "for-each-ref", "--format=%(refname) %(objectname)")


def files(folder) -> dict:
    """The working tree and the index database (SQLite's -wal and -shm files of a read-only open aside)."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(folder).parts and not path.name.endswith(("-wal", "-shm"))}


def a_copy_under_review(tmp_path, *, moved: bool = False):
    """my-travel's shape on 11-03: synced with the remote, then a local write not pushed yet and a merge commit made by
    hand on top. `moved`: meanwhile someone else pushed to the remote (a fetch would move origin/main, a push is
    rejected); on 11-03 the remote had not moved, and the shutdown push of the check went through."""
    remote = bare(tmp_path / "remote.git")
    folder = new_repository(tmp_path / "my-travel", remote=remote)
    writer = Writer(folder)
    seeded(writer)
    writer.turn("No luggage.", "fact")
    writer.service.committer.flush(push=False)
    writer.close()
    git(folder, "checkout", "-qb", "review")
    (folder / "notes.md").write_text("checking the merge\n")
    git(folder, "add", "notes.md")
    git(folder, "commit", "-qm", "a change under review")
    git(folder, "checkout", "-q", "main")
    git(folder, "merge", "-q", "--no-ff", "-m", "merge not yet checked", "review")
    if moved:
        other = clone(remote, tmp_path / "elsewhere")
        (other / "elsewhere.md").write_text("pushed from elsewhere\n")
        git(other, "add", "elsewhere.md")
        git(other, "-c", "user.name=Other", "-c", "user.email=other@example.invalid", "commit", "-qm", "elsewhere")
        git(other, "push", "-q", "origin", "HEAD:main")
    return remote, folder


@pytest.mark.parametrize("moved", [False, True], ids=["remote_as_on_11_03", "remote_moved"])
def test_check_and_status_do_not_fetch_push_commit_or_touch_the_working_tree(tmp_path, capsys, moved):
    remote, folder = a_copy_under_review(tmp_path, moved=moved)
    remote_before, refs_before, files_before = refs(remote), refs(folder), files(folder)
    status_before = git(folder, "status", "--porcelain")
    head = git(folder, "rev-parse", "HEAD").strip()

    main(["--repo", str(folder), "check"])
    report = json.loads(capsys.readouterr().out)
    assert report["commit"] == head
    assert main(["--repo", str(folder), "status"]) == 0
    shown = json.loads(capsys.readouterr().out)

    assert refs(remote) == remote_before, "check/status pushed"
    assert refs(folder) == refs_before, "check/status fetched or committed"
    assert git(folder, "status", "--porcelain") == status_before and files(folder) == files_before
    # status tells what it read: the waiting write from the index, HEAD as it is, and that it did not fetch
    assert shown["unsynced"] == 1 and shown["head"] == head[:7] and shown["fetched"] is False
    assert shown["index_at_head"] is False and "sync" in shown["note"]


def test_sync_still_fetches_and_pushes_after_a_check(tmp_path, capsys):
    remote, folder = a_copy_under_review(tmp_path, moved=True)
    main(["--repo", str(folder), "check"])
    capsys.readouterr()
    assert main(["--repo", str(folder), "sync"]) == 0
    capsys.readouterr()
    assert git(remote, "rev-parse", "main") == git(folder, "rev-parse", "HEAD")
    assert "elsewhere" in git(folder, "log", "--format=%s", "-5")
    assert main(["--repo", str(folder), "status"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["unsynced"] == 0 and shown["index_at_head"] is True


def test_status_of_a_copy_without_an_index_says_so_and_creates_none(tmp_path, capsys):
    remote = bare(tmp_path / "remote.git")
    writer = Writer(new_repository(tmp_path / "trip", remote=remote))
    seeded(writer)
    writer.close()
    copy = clone(remote, tmp_path / "copy")
    assert main(["--repo", str(copy), "status"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["unsynced"] is None and shown["index_at_head"] is False and not (copy / ".tripwright").exists()
