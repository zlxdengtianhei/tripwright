"""Persistent deploy-key and repository-connection commands for one personal data directory."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from tripwright.remote.config import RemoteConfig
from tripwright.repo.git import Git, GitError
from tripwright.repo.workspace import init

FILE_NAME = "repository.json"
REPLACEMENT = ".connect-replacement"
BACKUP = ".connect-backup"


def data_directory(environ=os.environ) -> Path:
    return Path(environ.get("TRIPWRIGHT_DATA_DIR") or "/data/remote").expanduser().resolve()


def use_data_directory(data_dir: str | Path) -> Path:
    """A root `docker exec` writes as the volume's service user, just as the container entrypoint does."""
    path = Path(data_dir)
    parent = next((candidate for candidate in path.parents if candidate.exists()), None)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.getuid() != 0:
        return path
    if path.stat().st_uid == 0 and parent is not None and parent.stat().st_uid != 0:
        os.chown(path, parent.stat().st_uid, parent.stat().st_gid)
    uid, gid = path.stat().st_uid, path.stat().st_gid
    if uid == 0 or os.environ.get("RAILWAY_RUN_UID") == "0":
        return path
    for root, folders, files in os.walk(path):
        for name in [root, *(os.path.join(root, item) for item in folders + files)]:
            info = os.lstat(name)
            if (info.st_uid, info.st_gid) != (uid, gid):
                os.lchown(name, uid, gid)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    return path


def connection_file(data_dir: str | Path) -> Path:
    return Path(data_dir) / FILE_NAME


def read_connection(data_dir: str | Path) -> dict | None:
    path = connection_file(data_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("repo_url"), str) or not isinstance(value.get("branch"), str):
        raise ValueError(f"{path} is not a repository connection written by tripwright")
    return dict(repo_url=value["repo_url"], branch=value["branch"])


def write_connection(data_dir: str | Path, repo_url: str, branch: str) -> None:
    path = connection_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    scratch = path.with_suffix(".tmp")
    descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as sink:
        json.dump(dict(repo_url=repo_url, branch=branch), sink)
        sink.write("\n")
    os.replace(scratch, path)


def repo_key(data_dir: str | Path) -> str:
    """Return the stable ed25519 public key; only this public half crosses the command's stdout."""
    folder = Path(data_dir) / "ssh"
    private, public = folder / "deploy_key", folder / "deploy_key.pub"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not private.exists():
        with tempfile.TemporaryDirectory(prefix=".deploy-key-", dir=folder) as staging:
            made = Path(staging) / "deploy_key"
            process = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "tripwright",
                                      "-f", str(made)], capture_output=True, text=True, timeout=20, check=False)
            if process.returncode:
                raise ValueError("ssh-keygen could not create the deploy key: " + process.stderr.strip())
            made.chmod(0o600)
            os.link(made, private)
            made.with_suffix(".pub").replace(public)
    private.chmod(0o600)
    if not public.exists():
        process = subprocess.run(["ssh-keygen", "-y", "-f", str(private)], capture_output=True, text=True,
                                 timeout=20, check=False)
        if process.returncode:
            raise ValueError("The existing deploy key cannot be read; keep it or replace it deliberately")
        public.write_text(process.stdout, encoding="utf-8")
    return public.read_text(encoding="utf-8").strip()


def mirror_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "tenants" / "personal" / "mirror"


def trips(repository: Git) -> list[str]:
    if repository.rev("HEAD") is None:
        return []
    return sorted({path.split("/")[1] for path in repository.tree("HEAD", "trips")
                   if path.count("/") >= 2 and path.endswith("/trip.yaml")})


def finalize_replacement(data_dir: str | Path) -> bool:
    """Install a fully cloned replacement before the service opens its mirror."""
    root = mirror_path(data_dir).parent
    replacement, mirror, backup = root / REPLACEMENT, root / "mirror", root / BACKUP
    if not replacement.exists():
        return False
    shutil.rmtree(backup, ignore_errors=True)
    if mirror.exists():
        mirror.rename(backup)
    replacement.rename(mirror)
    shutil.rmtree(backup)
    return True


def _environment(config: RemoteConfig) -> dict:
    if config.ssh_key is None:
        return {}
    from tripwright.remote.tenants import ssh_command

    return {"GIT_SSH_COMMAND": ssh_command(config)}


def connect(data_dir: str | Path, repo_url: str, branch: str = "main", *, environ=os.environ,
            fetch_keys=None) -> str:
    """Connect without force-push or merging. A running service applies a saved change on its next request or health check."""
    from tripwright.remote.envconfig import resolved, ssh_files

    data_dir = Path(data_dir)
    repo_url = resolved(repo_url, environ)
    settings = dict(public_url="http://localhost", data_dir=data_dir, repo_url=repo_url, branch=branch)
    if not Path(repo_url).is_absolute():
        settings.update(ssh_files(data_dir / "ssh", repo_url, environ, fetch_keys))
    config = RemoteConfig(**settings)
    mirror = mirror_path(data_dir)
    if not mirror.exists():
        init(mirror, branch=branch)
    local = Git(mirror)
    if local.remote_url("origin") == repo_url and local.branch() == branch:
        write_connection(data_dir, repo_url, branch)
        return f"Already connected to {repo_url} on {branch}."
    local_trips = trips(local)
    try:
        refs = local.run("ls-remote", "--heads", repo_url, env=_environment(config), timeout=45).strip()
    except (GitError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Cannot read {repo_url}; check its address and deploy key: {error}") from None
    if not refs:
        try:
            # The push comes before origin and the persistent configuration change. A failure changes neither.
            local.run("push", "--porcelain", repo_url, f"HEAD:refs/heads/{branch}", env=_environment(config), timeout=45)
            if local.branch() != branch:
                local.run("checkout", branch) if local.rev(branch) else local.run("checkout", "-b", branch)
            if local.remote_url("origin"):
                local.run("remote", "set-url", "origin", repo_url)
            else:
                local.run("remote", "add", "origin", repo_url)
            local.fetch("origin", branch)
        except (GitError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"Connection failed before the new configuration was saved: {error}") from None
        write_connection(data_dir, repo_url, branch)
        return f"Connected to {repo_url} on {branch}; pushed the existing local history ({', '.join(local_trips) or 'no trips yet'})."

    root = mirror.parent
    replacement = root / REPLACEMENT
    shutil.rmtree(replacement, ignore_errors=True)
    try:
        local.run("clone", "--quiet", "--branch", branch, "--", repo_url, str(replacement),
                  env=_environment(config), timeout=45)
        remote_trips = trips(Git(replacement))
    except (GitError, subprocess.TimeoutExpired) as error:
        shutil.rmtree(replacement, ignore_errors=True)
        raise ValueError(f"Cannot clone {repo_url}; check its branch and deploy key: {error}") from None
    if local_trips and remote_trips:
        shutil.rmtree(replacement)
        raise ValueError("Both sides have trips; nothing was changed. Local: " + (", ".join(local_trips) or "none") +
                         ". Remote: " + ", ".join(remote_trips) +
                         ". To keep local, choose an empty remote. To keep remote, use a new data volume or remove the local trips first.")
    if remote_trips:
        write_connection(data_dir, repo_url, branch)
        return f"Connected to {repo_url} on {branch}; the service will adopt {', '.join(remote_trips)} on its next request or health check."
    shutil.rmtree(replacement, ignore_errors=True)
    raise ValueError("The remote has commits but no trips, so it cannot be pushed or adopted without replacing history. "
                     "Choose an empty remote for the local history, or a tripwright repository with trips for an empty local service.")


def connect_for_open(config: RemoteConfig, *, environ=os.environ) -> None:
    """Apply the persisted or environment-selected connection immediately before PersonalRepository opens."""
    if config.repo_url is None:
        return
    if finalize_replacement(config.data_dir):
        return
    mirror = mirror_path(config.data_dir)
    if mirror.exists():
        local = Git(mirror)
        if local.remote_url("origin") == config.repo_url and local.branch() == config.branch:
            write_connection(config.data_dir, config.repo_url, config.branch)
            return
    connect(config.data_dir, config.repo_url, config.branch, environ=environ)
    finalize_replacement(config.data_dir)
