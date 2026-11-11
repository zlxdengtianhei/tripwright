"""Environment settings for one personal service; repository connections persist on the data volume."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

from tripwright.remote.config import RemoteConfig
from tripwright.remote.connection import data_directory, read_connection, repo_key

log = logging.getLogger("tripwright.envconfig")

# GitHub over port 443: some platforms block outbound port 22 (reported on Railway trial accounts).
GITHUB_443 = "ssh://git@ssh.github.com:443/{owner}/{repo}.git"
GITHUB_FORMS = (re.compile(r"^git@github\.com:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"),
                re.compile(r"^ssh://git@github\.com(?::22)?/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"),
                re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"))
VARIABLES = ("TRIPWRIGHT_PUBLIC_URL", "TRIPWRIGHT_REPO_URL", "RAILWAY_PUBLIC_DOMAIN")


def wanted(environ=os.environ) -> bool:
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
    """GitHub's SSH host keys from its HTTPS-verified meta API, pinned before the first SSH contact."""
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
        raise ValueError("The repository must be an SSH address (git@host:owner/repo.git), a GitHub repository URL, "
                         "or an absolute local path")
    return rewritten or repo


def ssh_files(folder: Path, repo: str, environ, fetch_keys) -> dict:
    """Keep an existing deploy key; otherwise create one. Only known_hosts may still come from an environment value."""
    key, hosts = folder / "deploy_key", folder / "known_hosts"
    if not key.exists():
        repo_key(folder.parent)
    if environ.get("TRIPWRIGHT_KNOWN_HOSTS"):
        write_private(hosts, secret_text(environ["TRIPWRIGHT_KNOWN_HOSTS"]))
    elif "github.com" in repo and not hosts.exists():
        try:
            write_private(hosts, github_known_hosts(fetch_keys))
        except (OSError, ValueError, KeyError) as error:
            log.error("cannot fetch GitHub's SSH host keys: %s", error)
    return dict(ssh_key=key, known_hosts=hosts)


def config_from_env(environ=os.environ, *, fetch_keys=None) -> RemoteConfig:
    """Read the optional environment overrides and the connection saved by `tripwright repo connect`.

    No variable is required. The public origin is used only by `tripwright address`; it defaults to localhost:$PORT.
    TRIPWRIGHT_REPO_URL and TRIPWRIGHT_BRANCH override the saved connection for Railway and older deployments.
    """
    public = environ.get("TRIPWRIGHT_PUBLIC_URL") or (
        "https://" + environ["RAILWAY_PUBLIC_DOMAIN"] if environ.get("RAILWAY_PUBLIC_DOMAIN")
        else f"http://localhost:{environ.get('PORT') or 8000}")
    data_dir = data_directory(environ)
    saved = read_connection(data_dir)
    repo = (environ.get("TRIPWRIGHT_REPO_URL") or "").strip() or (saved or {}).get("repo_url")
    branch = (environ.get("TRIPWRIGHT_BRANCH") or "").strip() or (saved or {}).get("branch") or "main"
    if environ.get("TRIPWRIGHT_DEPLOY_KEY"):
        log.warning("TRIPWRIGHT_DEPLOY_KEY is ignored: use `tripwright repo key` and add its public key to the repository; "
                    "an existing ssh/deploy_key on the data volume is kept")
    settings = dict(branch=branch)
    if repo:
        settings.update(repo_url=resolved(repo, environ))
        if not Path(settings["repo_url"]).is_absolute():
            settings.update(ssh_files(data_dir / "ssh", settings["repo_url"], environ, fetch_keys))
    return RemoteConfig(public_url=public, data_dir=data_dir, **settings)
