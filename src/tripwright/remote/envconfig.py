"""The remote configuration from environment variables alone, for platforms where the deployer only fills in variables
(Railway). The deploy key and known_hosts are written to the data volume with mode 600; no secret is logged."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

from tripwright.remote.config import RemoteConfig

log = logging.getLogger("tripwright.envconfig")

# GitHub over port 443: some platforms block outbound port 22 (reported on Railway trial accounts).
GITHUB_443 = "ssh://git@ssh.github.com:443/{owner}/{repo}.git"
GITHUB_FORMS = (re.compile(r"^git@github\.com:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"),
                re.compile(r"^ssh://git@github\.com(?::22)?/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"),
                re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"))
VARIABLES = ("TRIPWRIGHT_PUBLIC_URL", "TRIPWRIGHT_REPO_URL", "RAILWAY_PUBLIC_DOMAIN")


def wanted(environ=os.environ) -> bool:
    """Whether the environment describes a remote deployment."""
    return any(environ.get(name) for name in VARIABLES)


def github_repo(url: str) -> str | None:
    """The port-443 SSH address for a GitHub repository written in any usual form; None for other hosts."""
    for form in GITHUB_FORMS:
        found = form.match(url.strip())
        if found:
            return GITHUB_443.format(**found.groupdict())
    if url.startswith("ssh://git@ssh.github.com:443/"):
        return url
    return None


def secret_text(value: str) -> str:
    """A multi-line secret as typed into a web form: real newlines, or literal \\n when the form keeps one line."""
    text = value.strip().replace("\r\n", "\n")
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    return text + "\n"


def write_private(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as sink:
        sink.write(text)
    path.chmod(0o600)


def github_known_hosts(fetch=None) -> str:
    """GitHub's SSH host keys from its HTTPS-verified meta API, for github.com and [ssh.github.com]:443 (the same
    source deploy/SETUP.md uses: no key is trusted on first SSH contact)."""
    if fetch is None:
        request = urllib.request.Request("https://api.github.com/meta", headers={"User-Agent": "tripwright-setup"})
        with urllib.request.urlopen(request, timeout=15) as response:
            keys = json.load(response)["ssh_keys"]
    else:
        keys = fetch()
    return "".join(f"{host} {key}\n" for key in keys for host in ("github.com", "[ssh.github.com]:443"))


def resolved(repo: str, environ) -> str:
    rewritten = github_repo(repo) if environ.get("TRIPWRIGHT_GITHUB_PORT_22") != "1" else None
    if rewritten is None and re.match(r"^https://", repo):
        raise ValueError("TRIPWRIGHT_REPO_URL must be an SSH address (git@host:owner/repo.git) or a GitHub repository URL")
    return rewritten or repo


def ssh_files(folder: Path, repo: str, environ, fetch_keys) -> dict:
    """Write the deploy key (and known_hosts: given, or GitHub's fetched once) with mode 600; return their paths."""
    if not environ.get("TRIPWRIGHT_DEPLOY_KEY"):
        raise ValueError("TRIPWRIGHT_REPO_URL needs TRIPWRIGHT_DEPLOY_KEY (the deploy key's private half)")
    key, hosts = folder / "deploy_key", folder / "known_hosts"
    write_private(key, secret_text(environ["TRIPWRIGHT_DEPLOY_KEY"]))
    if environ.get("TRIPWRIGHT_KNOWN_HOSTS"):
        write_private(hosts, secret_text(environ["TRIPWRIGHT_KNOWN_HOSTS"]))
    elif "github.com" in repo and not hosts.exists():
        try:
            write_private(hosts, github_known_hosts(fetch_keys))
        except (OSError, ValueError, KeyError) as error:
            # The tenant then fails to open with key_file, visible on /readyz; the next start tries again.
            log.error("cannot fetch GitHub's SSH host keys: %s", error)
    return dict(ssh_key=key, known_hosts=hosts)


def config_from_env(environ=os.environ, *, fetch_keys=None) -> RemoteConfig:
    """Build the one-tenant configuration from the environment:

    TRIPWRIGHT_PUBLIC_URL (default https://$RAILWAY_PUBLIC_DOMAIN); TRIPWRIGHT_DATA_DIR (default /data/remote);
    TRIPWRIGHT_REPO_URL (optional: without it the trips are a local repository on the volume; a GitHub address goes
    over port 443 unless TRIPWRIGHT_GITHUB_PORT_22=1); TRIPWRIGHT_BRANCH (main); TRIPWRIGHT_DEPLOY_KEY (the private key,
    multi-line); TRIPWRIGHT_KNOWN_HOSTS (optional; GitHub's keys are fetched when it is not given)."""
    public = environ.get("TRIPWRIGHT_PUBLIC_URL") or (
        "https://" + environ["RAILWAY_PUBLIC_DOMAIN"] if environ.get("RAILWAY_PUBLIC_DOMAIN") else None)
    if not public:
        raise ValueError("Set TRIPWRIGHT_PUBLIC_URL (the service's HTTPS address) or give a domain to the Railway service")
    data_dir = Path(environ.get("TRIPWRIGHT_DATA_DIR") or "/data/remote")
    repo = (environ.get("TRIPWRIGHT_REPO_URL") or "").strip() or None
    settings = dict(branch=environ.get("TRIPWRIGHT_BRANCH") or "main")
    if repo:
        settings.update(repo_url=resolved(repo, environ))
        if not Path(settings["repo_url"]).is_absolute():  # a path is a repository on this machine: no key, no network
            settings.update(ssh_files(data_dir / "ssh", settings["repo_url"], environ, fetch_keys))
    return RemoteConfig(public_url=public, data_dir=data_dir, **settings)
