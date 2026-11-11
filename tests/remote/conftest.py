from __future__ import annotations

import subprocess

import pytest

from tripwright.remote.config import RemoteConfig
from tripwright.repo.workspace import init


@pytest.fixture
def remote_config(tmp_path):
    remote = tmp_path / "trip.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(remote)], check=True)
    seed = init(tmp_path / "seed", trip="2030-06-fjord", remote=str(remote))
    seed.git.run("push", "--quiet", "origin", "main")
    return RemoteConfig(public_url="https://trip.example", data_dir=tmp_path / "remote", repo_url=str(remote))
