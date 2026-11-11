from __future__ import annotations

import pytest
from pydantic import ValidationError

from tripwright.remote.config import RemoteConfig
from tripwright.remote.tenants import PersonalRepository, ssh_command
from tripwright.repo.git import Git


def test_tenant_clone_commit_push_and_rejected_push_replays_operations(remote_config):
    first = PersonalRepository(remote_config)
    second_config = remote_config.model_copy(update={"data_dir": remote_config.data_dir.with_name("other-server")})
    second = PersonalRepository(second_config)
    try:
        a = first.open()
        assert first.open() is a
        b = second.open()
        assert a is not b and a.store is not b.store
        recorded = a.turn("local", "First server: no luggage.", "remote-first", scenario="quick")
        assert a.committer.flush()["pushed"] == "ok"
        b.committer.sync()
        mine = a.turn("local", "First server: next fact.", "remote-pending", scenario="quick")
        a.committer.flush(push=False)
        theirs = b.turn("local", "Other client: a new message.", "remote-other", scenario="quick")
        assert b.committer.flush()["pushed"] == "ok"
        outcomes = []
        original = a.repository.git.push

        def observed(*args):
            result = original(*args)
            outcomes.append(result)
            return result

        a.repository.git.push = observed
        assert a.committer.flush()["pushed"] == "ok"
        assert outcomes == ["rejected", "ok"]
        log = Git(remote_config.repo_url).run("log", "--format=%B", "main")
        for receipt in (recorded, mine, theirs):
            assert log.count("Tripwright-Op: " + receipt["write"]["op_id"]) == 1
        assert a.committer.outbox.open("local") == []
        b.committer.sync()
        a_texts = sorted(event["received"]["text"] for event in a.store.all("events", "local") if "received" in event)
        b_texts = sorted(event["received"]["text"] for event in b.store.all("events", "local") if "received" in event)
        assert a_texts == b_texts and len(a_texts) == 3
        assert a.repository.git.run("config", "user.name").strip() == "tripwright[bot]"
    finally:
        for registry in (first, second):
            for _, service in registry.opened.values():
                service.jobs.stop()
                service.committer.stop()
            registry.close()


def test_config_rejects_public_http_paths_and_repository_credentials(remote_config):
    values = remote_config.model_dump()
    for changed in ({"public_url": "http://trip.example"}, {"public_url": "https://trip.example/mcp"},
                    {"repo_url": "https://user:secret@example.test/trip.git"}, {"branch": "-upload-pack=bad"}):
        with pytest.raises(ValidationError):
            RemoteConfig(**{**values, **changed})


def test_ssh_uses_only_the_tenant_key_and_pinned_host_file(tmp_path):
    key = tmp_path / "identity with space"
    key.write_text("fixture-key")
    key.chmod(0o600)
    known = tmp_path / "known_hosts"
    known.write_text("github.com fixture-key")
    config = RemoteConfig(public_url="https://trip.example", data_dir=tmp_path / "data",
                          repo_url="git@github.com:owner/trip.git", ssh_key=key, known_hosts=known)
    command = ssh_command(config)
    assert "StrictHostKeyChecking=yes" in command and "IdentitiesOnly=yes" in command and "BatchMode=yes" in command
    assert "UserKnownHostsFile=" in command and "'" + str(key) + "'" in command
    key.chmod(0o644)
    with pytest.raises(ValueError, match="mode 600"):
        ssh_command(config)
