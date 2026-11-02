"""A cached service per configured tenant, backed by the existing repository committer."""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from tripwright.remote.config import RemoteConfig, Tenant
from tripwright.repo.git import Git, failure_kind, run_group
from tripwright.repo.workspace import Repository
from tripwright.storage import Store

log = logging.getLogger("tripwright.tenants")

# What the operator should do, by the kind of problem that keeps a tenant from opening or syncing.
HINTS = {
    "auth": "The deploy key or host key was refused: check that the key's public half is a deploy key of this repository "
            "with write access, that known_hosts holds the Git host's key, and that the repository address is right.",
    "network": "The Git host could not be reached: check the server's outbound network (port 22, or 443 for ssh.github.com).",
    "branch": "The repository has no such branch (an empty repository has none): push a first commit or fix 'branch'.",
    "config": "The tenant's configuration does not match its files: read the detail in the server log.",
    "key_file": "The SSH key or known_hosts file is missing or readable by others: the key must exist with mode 600.",
    "other": "Opening the trip repository failed: read the server log.",
}


class TenantProblem(ValueError):
    """Why a tenant cannot open: `kind` (auth, network, branch, config, key_file, other), a fix in `hint`; the
    `detail` (Git's stderr, local paths) is for the operator's log only, never for a caller."""

    def __init__(self, kind: str, detail: str):
        super().__init__(HINTS.get(kind, HINTS["other"]))
        self.kind, self.hint, self.detail = kind, HINTS.get(kind, HINTS["other"]), detail


def ssh_command(tenant: Tenant) -> str | None:
    if tenant.ssh_key is None:
        return None
    if not tenant.ssh_key.is_file() or tenant.ssh_key.stat().st_mode & 0o077:
        raise TenantProblem("key_file", "Tenant SSH private key must exist with mode 600")
    if tenant.known_hosts is None or not tenant.known_hosts.is_file():
        raise TenantProblem("key_file", "Tenant SSH known_hosts file is missing")
    return shlex.join(["ssh", "-i", str(tenant.ssh_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
                       "-o", "UserKnownHostsFile=" + str(tenant.known_hosts)])


def provider_mode() -> str:
    """Served lookups use live data unless the operator explicitly asks for the fictional demo data."""
    return os.environ.get("TRIPWRIGHT_PROVIDERS") or "live"


class Tenants:
    """Only the server's configuration can name a repository or owner; one service owns each checkout."""

    def __init__(self, config: RemoteConfig, *, providers: str | None = None):
        self.config = config
        self.providers = providers or provider_mode()
        self.opened: dict[str, tuple[Store, object]] = {}

    def router(self, store: Store):
        from tripwright.query import QueryRouter

        if self.providers != "live":
            return QueryRouter(store, mode=self.providers)
        from tripwright.providers.transitous import TransitousProvider

        # The 720-a-day counter lives on the data volume: the root file system may be read-only, and /tmp forgets it.
        usage = os.environ.get("TRIPWRIGHT_TRANSITOUS_USAGE") or self.config.data_dir / "transitous-routing.log"
        return QueryRouter(store, mode="live", transit=TransitousProvider(usage_file=usage))

    def open(self, tenant_id: str):
        if tenant_id in self.opened:
            return self.opened[tenant_id][1]
        tenant = self.config.tenants[tenant_id]
        root = self.config.data_dir / "tenants" / tenant_id
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        mirror = root / "mirror"
        command = ssh_command(tenant)
        if not mirror.exists():
            with tempfile.TemporaryDirectory(prefix=".clone-", dir=root) as staging:
                incoming = Path(staging) / "mirror"
                if tenant.repo_url is None:
                    from tripwright.repo.workspace import init

                    init(incoming, branch=tenant.branch)
                    log.warning("tenant %s: created a local trip repository (no repo_url); back up the data volume", tenant_id)
                else:
                    self._clone(tenant, command, incoming)
                incoming.rename(mirror)
        # This process is the mirror's only writer (one instance per data volume), so a lock file is a crash's leftover.
        removed = Git(mirror).clear_locks()
        if removed:
            log.warning("tenant %s: removed leftover Git locks %s", tenant_id, ", ".join(removed))
        repository = Repository(mirror, owner=tenant.owner, branch=tenant.branch)
        if repository.git.remote_url("origin") != tenant.repo_url:
            raise TenantProblem("config", "Existing tenant mirror has a different origin; refusing to repoint it "
                                          "(move the mirror folder away to clone the new repository)")
        if repository.git.branch() != tenant.branch:
            raise TenantProblem("config", "Existing tenant mirror follows a different branch; refusing to push its HEAD")
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
        self.opened[tenant_id] = (store, service)
        return service

    def _clone(self, tenant: Tenant, command: str | None, incoming: Path):
        try:
            process = run_group(
                ["git", "-c", "core.hooksPath=/dev/null", "clone", "--quiet", "--branch", tenant.branch,
                 "--", tenant.repo_url, str(incoming)],
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **({"GIT_SSH_COMMAND": command} if command else {})},
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            raise TenantProblem("network", "git clone timed out after 45 s") from None
        if process.returncode:
            # SSH diagnostics may contain local paths: they go to the operator's log, never to a caller.
            text = process.stderr.decode(errors="replace").strip()
            lowered = text.casefold()
            kind = "branch" if "not found in upstream" in lowered or "remote branch" in lowered else failure_kind(text)
            raise TenantProblem(kind if kind in HINTS else "other", text or f"git clone exited {process.returncode}")

    def close(self):
        for store, _ in self.opened.values():
            store.close()
        self.opened.clear()

