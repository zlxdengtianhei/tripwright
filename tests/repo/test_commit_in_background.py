"""commit does not wait for Git (2026-11-14: the reply must not wait for saving and the site update; they belong to the
second half). The version is in the index when commit returns; the committer's background loop commits and pushes
it after its batch window, and the public page is rebuilt after that (site/publish.Publisher). Before, every commit
waited up to 5 s for the push: 0.4-2.8 s on a local bare remote, by machine (a fictional trip, 2026-11-14)."""
import time

from repo_fixtures import bare, git, new_repository, plan
from tripwright.repo.workspace import Repository
from tripwright.service import Tripwright
from tripwright.storage import Store


def test_commit_returns_before_git_and_the_background_loop_pushes_it(tmp_path):
    remote = bare(tmp_path / "remote.git")
    repository = Repository(new_repository(tmp_path / "repo", remote=remote), owner="local")
    store = Store(repository.state_path)
    service = Tripwright(store, repository=repository)  # the default commit_wait, as the server runs it
    service.committer.batch_seconds = 0.2
    service.committer.start()
    try:
        turn = service.turn("local", "Create the fictional trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan(), idempotency_key="seed")
        started = time.monotonic()
        version = service.commit("local", draft["draft_id"], "seed", accept_unverified=True)
        took = time.monotonic() - started
        assert version["accepted"] and version["number"] == 1 and took < 1.0, took
        assert version["write"]["state"] in ("received", "pending_sync")  # not waited for
        assert service.state("local")["number"] == 1  # the plan is saved: the next read already has it
        deadline = time.monotonic() + 20
        while service.state("local", view="sync")["unsynced"] and time.monotonic() < deadline:
            time.sleep(0.1)
        sync = service.state("local", view="sync")
        assert sync["unsynced"] == 0, sync
        assert "Tripwright-Version" in git(remote, "log", "-1", "--format=%B", "main")
    finally:
        service.committer.stop()
        service.jobs.stop()
        store.close()


def test_a_caller_can_still_wait_for_effective(tmp_path):
    remote = bare(tmp_path / "remote.git")
    repository = Repository(new_repository(tmp_path / "repo", remote=remote), owner="local")
    store = Store(repository.state_path)
    service = Tripwright(store, repository=repository)
    try:
        turn = service.turn("local", "Create the fictional trip.", "seed")
        draft = service.propose("local", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan(), idempotency_key="seed")
        version = service.commit("local", draft["draft_id"], "seed", accept_unverified=True, wait=10)
        assert version["write"]["state"] == "effective"
    finally:
        service.jobs.stop()
        store.close()
