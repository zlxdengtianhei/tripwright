"""R2 items 3 and 7: stuck writes say why (receipt, state(view="sync"), one log line) and clear when fixed; a leftover
lock is removed when the tenant opens; shutdown is bounded; the idle background loops decode nothing."""
from __future__ import annotations

import logging
import stat
import time

import pytest

from tripwright.repo.git import failure_kind
from tripwright.storage import Store

from repo_fixtures import Writer, bare, git, new_repository, seeded


@pytest.fixture
def writer(tmp_path):
    remote = bare(tmp_path / "remote.git")
    first = Writer(new_repository(tmp_path / "laptop", remote=remote))
    seeded(first)
    first.service.committer.flush()
    yield first, remote
    first.close()


def fake_ssh(tmp_path, message: str, *, code: int = 255, sleep: int = 0):
    """An ssh stand-in that prints what a Git host prints and fails, so pushes go through the real git push path."""
    script = tmp_path / "fake-ssh"
    script.write_text(f"#!/bin/sh\nsleep {sleep}\necho '{message}' >&2\nexit {code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def through_ssh(writer, script):
    git(writer.root, "config", "core.sshCommand", str(script))
    git(writer.root, "remote", "set-url", "origin", "git@git.example:owner/trip.git")


@pytest.mark.parametrize("text, kind", [
    ("ERROR: The key you are authenticating with has been marked as read only.\nfatal: Could not read from remote repository.", "auth"),
    ("git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.", "auth"),
    ("remote: error: GH006: Protected branch update failed for refs/heads/main.\n ! [remote rejected] main -> main (protected branch hook declined)", "protected"),
    ("ssh: Could not resolve hostname github.com: Temporary failure in name resolution\nfatal: Could not read from remote repository.", "network"),
    ("! [rejected]        HEAD -> main (fetch first)\nerror: failed to push some refs", "behind"),
    ("error: remote unpack failed: unable to create temporary object directory", "other"),
])
def test_push_failures_fall_into_four_kinds_and_other(text, kind):
    assert failure_kind(text) == kind


def test_a_read_only_key_blocks_with_auth_in_receipt_sync_and_one_log_line_then_clears(writer, tmp_path, caplog):
    first, remote = writer
    through_ssh(first, fake_ssh(tmp_path, "ERROR: The key you are authenticating with has been marked as read only."))
    caplog.set_level(logging.WARNING, logger="tripwright")
    noted = first.turn("I have no luggage.", "blocked-auth")
    first.service.committer.flush()
    first.service.committer.flush()
    later = first.turn("Still there?", "blocked-auth-2")
    blocked = later["write"]["blocked"]
    assert blocked["kind"] == "auth" and blocked["since"] and "write access" in blocked["hint"]
    assert first.service.state("local", "sync")["blocked"]["kind"] == "auth"
    assert [record.message for record in caplog.records if "writes blocked" in record.message] == [
        caplog.records[0].message], "one line when the reason appears, none per retry"
    assert "auth" in caplog.records[0].message
    # The operator fixes it: the next push lands, the reason goes away, receipts stop carrying it.
    git(first.root, "remote", "set-url", "origin", str(remote))
    git(first.root, "config", "--unset", "core.sshCommand")
    assert first.service.committer.flush()["pushed"] == "ok"
    assert "blocked" not in first.service.state("local", "sync")
    assert first.ops()[noted["write"]["op_id"]]["state"] == "effective"
    assert any("unblocked" in record.message for record in caplog.records)


def test_a_protected_branch_is_reported_as_protected_and_not_replayed_forever(writer):
    first, remote = writer
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'error: GH006: Protected branch update failed for refs/heads/main.' >&2\nexit 1\n")
    hook.chmod(0o755)
    first.turn("I have no luggage.", "protected")
    assert first.service.committer.flush()["pushed"] == "protected"
    assert first.service.state("local", "sync")["blocked"]["kind"] == "protected"


def test_an_unreachable_host_is_network_and_a_working_push_has_no_blocked_field(writer, tmp_path):
    first, _ = writer
    control = first.turn("Before.", "control")
    first.service.committer.flush()
    assert "blocked" not in first.turn("Fine.", "control-2")["write"]
    assert "blocked" not in first.service.state("local", "sync")
    assert first.ops()[control["write"]["op_id"]]["state"] == "effective"
    through_ssh(first, fake_ssh(tmp_path, "ssh: Could not resolve hostname git.example: Name or service not known"))
    first.turn("Offline.", "network")
    assert first.service.committer.flush()["pushed"] == "unreachable"
    assert first.service.state("local", "sync")["blocked"]["kind"] == "network"


def test_shutdown_with_a_hanging_push_ends_within_its_budget_and_leaves_no_lock(writer, tmp_path):
    first, _ = writer
    through_ssh(first, fake_ssh(tmp_path, "never answers", sleep=30))
    first.repository.git.network_timeout = 2  # production: 10 s within a 15 s budget and a 40 s grace period
    first.turn("Written just before shutdown.", "shutdown")
    first.service.committer.start()
    time.sleep(0.3)  # the loop is now inside a fetch that hangs
    started = time.monotonic()
    first.service.committer.stop(budget=6)
    assert time.monotonic() - started < 7
    assert not list((first.root / ".git").glob("*.lock"))
    states = {op["state"] for op in first.ops().values()}
    assert "received" not in states, "shutdown commits what is waiting even when it cannot push"


def test_idle_background_loops_decode_no_table_and_wake_for_a_write(writer, monkeypatch):
    first, _ = writer
    service = first.service
    service.jobs.start()
    service.committer.start()
    try:
        time.sleep(1.5)  # first full passes
        calls = []
        for name in ("all", "where"):
            original = getattr(Store, name)

            def counted(self, table, *args, _original=original, _name=name, **kwargs):
                calls.append((_name, table))
                return _original(self, table, *args, **kwargs)
            monkeypatch.setattr(Store, name, counted)
        time.sleep(2.5)
        assert calls == [], f"idle loops read {calls[:5]}"
        noted = first.turn("A new message.", "wake")
        deadline = time.monotonic() + 8
        while first.ops()[noted["write"]["op_id"]]["state"] != "effective" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert first.ops()[noted["write"]["op_id"]]["state"] == "effective"
    finally:
        service.jobs.stop()
        service.committer.stop()
