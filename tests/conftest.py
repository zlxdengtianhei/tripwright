"""Shared setup for every test: git reads no configuration from the machine.

A test that commits without naming an author passes on a developer's machine, where the global configuration has one
or git can guess one from the host name, and fails on a clean runner. Here the machine's configuration is switched off
and git may not guess an identity, so such a test fails everywhere.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def git_reads_no_machine_configuration(tmp_path_factory):
    only = tmp_path_factory.mktemp("git-configuration") / "gitconfig"
    only.write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")
    patch = pytest.MonkeyPatch()
    patch.setenv("GIT_CONFIG_GLOBAL", str(only))
    patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        patch.delenv(name, raising=False)
    yield
    patch.undo()
