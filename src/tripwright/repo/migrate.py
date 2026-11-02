"""Write a single-database trip into a new trip repository (`tripwright init <folder> --from-db <db>`).

One commit per committed version, in order, so each version's plan is the `plan/` of the commit that names it in a
`Tripwright-Version` trailer and an index rebuilt from the repository equals the database it came from.
"""
from __future__ import annotations

from pathlib import Path

from tripwright.repo import index, workspace
from tripwright.storage import Store


def export_store(source: Store, owner: str, root: str | Path, *, remote: str | None = None) -> workspace.Repository:
    from tripwright.service import Tripwright

    tables = {table: source.all(table, owner) for table in index.INDEX_TABLES}
    versions = sorted(tables["versions"], key=lambda version: version["number"])
    if not versions:
        raise ValueError(f"The database has no committed trip for {owner!r}")
    trip = versions[-1]["plan"]["id"]
    repository = workspace.init(root, trip=trip, remote=remote)
    repository.owner = owner
    store = Store(repository.state_path)
    service = Tripwright(store, repository=repository)
    try:
        with store.transaction():
            store.replace(owner, tables)
            service.committer.set_head(repository.git.rev("HEAD"), trip=trip)
            for version in versions:
                service.outbox.add(owner, kind="migrate", tool="migrate", key=version["id"], version_id=version["id"],
                                   replay=dict(migrated=version["id"]))
        service.committer.flush()
    finally:
        service.jobs.stop()
        store.close()
    return repository
