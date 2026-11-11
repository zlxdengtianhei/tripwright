"""The one personal service, backed by the existing repository committer."""
from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path

from tripwright.remote.config import RemoteConfig
from tripwright.repo.git import Git
from tripwright.repo.workspace import Repository
from tripwright.storage import Store

log = logging.getLogger("tripwright.tenants")

# What the operator should do, by the kind of problem that keeps the repository from opening or syncing.
HINTS = {
    "auth": "The deploy key or host key was refused: check that the key's public half is a deploy key of this repository "
            "with write access, that known_hosts holds the Git host's key, and that the repository address is right.",
    "network": "The Git host could not be reached: check the server's outbound network (port 22, or 443 for ssh.github.com).",
    "branch": "The repository has no such branch (an empty repository has none): push a first commit or fix 'branch'.",
    "config": "The configuration does not match the repository files: read the detail in the server log.",
    "key_file": "The SSH key or known_hosts file is missing or readable by others: the key must exist with mode 600.",
    "other": "Opening the trip repository failed: read the server log.",
}


class TenantProblem(ValueError):
    """Why the repository cannot open: `kind`, a fix in `hint`; `detail` is for the operator's log only."""

    def __init__(self, kind: str, detail: str):
        super().__init__(HINTS.get(kind, HINTS["other"]))
        self.kind, self.hint, self.detail = kind, HINTS.get(kind, HINTS["other"]), detail


def ssh_command(config: RemoteConfig) -> str | None:
    if config.ssh_key is None:
        return None
    if not config.ssh_key.is_file() or config.ssh_key.stat().st_mode & 0o077:
        raise TenantProblem("key_file", "SSH private key must exist with mode 600")
    if config.known_hosts is None or not config.known_hosts.is_file():
        raise TenantProblem("key_file", "SSH known_hosts file is missing")
    return shlex.join(["ssh", "-i", str(config.ssh_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
                       "-o", "UserKnownHostsFile=" + str(config.known_hosts)])


def provider_mode() -> str:
    """Served lookups use live data unless the operator explicitly asks for the fictional demo data."""
    return os.environ.get("TRIPWRIGHT_PROVIDERS") or "live"


class PersonalRepository:
    """Open only data_dir/tenants/personal/mirror; existing deployments keep their files and checkout."""

    def __init__(self, config: RemoteConfig, *, providers: str | None = None, service=None):
        self.config = config
        self.providers = providers or provider_mode()
        self.opened: dict[str, tuple[Store, object]] = {}
        if service is not None:
            self.opened["personal"] = (service.store, service)

    @property
    def service(self):
        return self.opened["personal"][1] if self.opened else None

    def router(self, store: Store):
        from tripwright.query import QueryRouter

        if self.providers != "live":
            return QueryRouter(store, mode=self.providers)
        from tripwright.providers.transitous import TransitousProvider

        # The 720-a-day counter lives on the data volume: the root file system may be read-only, and /tmp forgets it.
        usage = os.environ.get("TRIPWRIGHT_TRANSITOUS_USAGE") or self.config.data_dir / "transitous-routing.log"
        return QueryRouter(store, mode="live", transit=TransitousProvider(usage_file=usage))

    def open(self):
        if self.service is not None:
            return self.service
        config = self.config
        root = config.data_dir / "tenants" / "personal"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        mirror = root / "mirror"
        if config.repo_url is not None:
            from tripwright.remote.connection import connect_for_open

            try:
                connect_for_open(config)
            except ValueError as error:
                raise TenantProblem("auth", str(error)) from error
        command = ssh_command(config)
        if not mirror.exists():
            from tripwright.repo.workspace import init

            init(mirror, branch=config.branch)
            log.warning("created a local trip repository (no repo_url); back up the data volume")
        # This process is the mirror's only writer (one instance per data volume), so a lock file is a crash's leftover.
        removed = Git(mirror).clear_locks()
        if removed:
            log.warning("removed leftover Git locks %s", ", ".join(removed))
        repository = Repository(mirror, owner="local", branch=config.branch)
        if repository.git.remote_url("origin") != config.repo_url:
            raise TenantProblem("config", "Existing personal mirror has a different origin; run `tripwright repo connect` "
                                          "to check and connect it without moving data by hand")
        if repository.git.branch() != config.branch:
            raise TenantProblem("config", "Existing personal mirror follows a different branch; refusing to push its HEAD")
        if command:
            repository.git.run("config", "core.sshCommand", command)
        repository.git.run("config", "core.hooksPath", "/dev/null")
        repository.git.run("config", "user.name", "tripwright[bot]")
        repository.git.run("config", "user.email", "tripwright@localhost")
        store = Store(repository.state_path)
        try:
            from tripwright.service import Tripwright

            service = Tripwright(store, repository=repository, query_router=self.router(store))
        except ValueError as error:
            store.close()
            raise TenantProblem("config", f"{type(error).__name__}: {error}") from error
        except BaseException:
            store.close()
            raise
        self.opened["personal"] = (store, service)
        return service

    def close(self):
        for store, _ in self.opened.values():
            store.close()
        self.opened.clear()
