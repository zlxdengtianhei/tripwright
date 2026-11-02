"""Fixtures for the repository tests: trip repositories in pytest's temporary folder (outside the code repository, so
no generated AGENTS.md or CLAUDE.md lands in it), a local bare repository as the remote, and services bound to them."""
from __future__ import annotations

import subprocess
from copy import deepcopy
from pathlib import Path

from tripwright.demo import example
from tripwright.repo import index, workspace
from tripwright.repo.workspace import Repository
from tripwright.service import Tripwright
from tripwright.storage import Store

TRIP = "2030-06-fjord"


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def bare(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "--bare", "-b", "main")
    return path


def plan(trip: str = TRIP, days: int = 1) -> dict:
    result = deepcopy(example()["trip"])
    result["id"] = trip
    result["days"] = result["days"][:days]
    result["end"] = result["days"][-1]["date"]
    result["bookings"] = []
    return result


class Writer:
    """One working copy with its own index database, as a phone session or a laptop would have."""

    def __init__(self, root: Path, *, owner: str = "local"):
        self.root = root
        self.repository = Repository(root, owner=owner)
        self.store = Store(self.repository.state_path)
        self.service = Tripwright(self.store, repository=self.repository, commit_wait=0)
        self.owner = owner

    def turn(self, text: str, key: str, **arguments) -> dict:
        return self.service.turn(self.owner, text, key, **arguments)

    def propose(self, turn: dict, key: str, **arguments) -> dict:
        return self.service.propose(self.owner, ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key=key, **arguments)

    def commit(self, draft: dict, key: str, **arguments) -> dict:
        return self.service.commit(self.owner, draft["draft_id"], key, **arguments)

    def ops(self) -> dict:
        return {op["id"]: op for op in self.store.all("outbox", self.owner)}

    def tables(self) -> dict:
        return index.tables_of(self.store, self.owner)

    def close(self):
        self.service.jobs.stop()
        self.store.close()


def new_repository(base: Path, *, remote: Path | None = None, trip: str = TRIP) -> Path:
    return workspace.init(base, trip=trip, remote=str(remote) if remote else None).root


def clone(remote: Path, target: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(target)], check=True, capture_output=True)
    return target


def seeded(writer: Writer, *, days: int = 1) -> dict:
    turn = writer.turn("Create the fictional trip.", "seed")
    draft = writer.propose(turn, "seed", plan=plan(days=days))
    version = writer.commit(draft, "seed", accept_unverified=True)
    writer.service.committer.flush()
    return version


def by_id(tables: dict) -> dict:
    return {table: {record["id"]: record for record in records} for table, records in tables.items()}
