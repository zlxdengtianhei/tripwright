import shutil

import pytest

from tripwright.agents.assemble import AGENTS, ROOT, build, load_sources, write


@pytest.fixture(scope="session")
def sources():
    return load_sources(ROOT)


@pytest.fixture(scope="session")
def outputs(sources):
    return build(sources)


@pytest.fixture
def checkout(tmp_path):
    """A throwaway checkout with this repository's sources and a fresh build, outside the repository."""
    for folder in (f"{AGENTS}/roles", f"{AGENTS}/templates"):
        if (ROOT / folder).is_dir():
            shutil.copytree(ROOT / folder, tmp_path / folder)
    shutil.copy(ROOT / AGENTS / "core.yaml", tmp_path / AGENTS / "core.yaml")  # S4a: every message's steps
    shutil.copy(ROOT / AGENTS / "single-agent.md", tmp_path / AGENTS / "single-agent.md")  # one agent, the roles in turn
    (tmp_path / "skills").mkdir()
    write(tmp_path, build(load_sources(tmp_path)))
    return tmp_path
