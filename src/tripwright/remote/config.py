"""Configuration for the remote identity boundary; no credentials belong in YAML."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Tenant(BaseModel):
    """repo_url is the canonical Git repository (SSH: GitHub, GitLab, Gitea, any host that takes a deploy key). Without
    it the server keeps a local repository in its data directory as the canonical copy: commits, no pushes."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    repo_url: str | None = None
    owner: str = "local"
    branch: str = "main"
    ssh_key: Path | None = None
    known_hosts: Path | None = None

    @model_validator(mode="after")
    def validate_repository(self):
        if not self.owner or not self.branch or self.branch.startswith("-"):
            raise ValueError("A tenant needs a nonempty owner and branch")
        if self.repo_url is None:
            return self
        ssh = self.repo_url.startswith("ssh://") or re.match(r"[\w.-]+@[\w.-]+:.+", self.repo_url)
        local = Path(self.repo_url).is_absolute()
        if not (ssh or local) or (self.repo_url.startswith("ssh://") and urlsplit(self.repo_url).password is not None):
            raise ValueError("repo_url must be an SSH Git URL (or an absolute local path for offline use)")
        if ssh and (self.ssh_key is None or self.known_hosts is None):
            raise ValueError("SSH repositories need ssh_key and known_hosts")
        return self


class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    public_url: str
    data_dir: Path
    secrets_file: Path
    oauth: bool = True
    accounts: dict[str, str]
    tenants: dict[str, Tenant]
    token_tenants: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_boundary(self):
        self.public_url = self.public_url.rstrip("/")
        parsed = urlsplit(self.public_url)
        # Plain HTTP only for this computer: a desktop host on the same machine needs no tunnel or certificate.
        scheme_ok = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
        if not scheme_ok or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("public_url must be an HTTPS origin (or http://localhost) without a path, credentials, query or fragment")
        if not self.tenants or (self.oauth and not self.accounts):
            raise ValueError("Configure at least one tenant, and an allowed GitHub account when oauth is on")
        if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", key) for key in self.tenants):
            raise ValueError("Tenant IDs must be safe directory names, 1–64 letters, digits, underscores or hyphens")
        normalized = {login.casefold(): tenant for login, tenant in self.accounts.items()}
        if len(normalized) != len(self.accounts) or any(not re.fullmatch(r"[a-zA-Z0-9-]{1,39}", login) for login in normalized):
            raise ValueError("Allowed GitHub logins must be unique and valid")
        self.accounts = normalized
        if any(tenant not in self.tenants for tenant in [*self.accounts.values(), *self.token_tenants.values()]):
            raise ValueError("An account or bearer identity refers to an unknown tenant")
        self.data_dir = self.data_dir.expanduser().resolve()
        self.secrets_file = self.secrets_file.expanduser().resolve()
        if self.secrets_file.is_relative_to(self.data_dir / "tenants"):
            raise ValueError("The secrets file must be outside tenant Git mirrors")
        for tenant in self.tenants.values():
            for name in ("ssh_key", "known_hosts"):
                if getattr(tenant, name) is not None:
                    setattr(tenant, name, getattr(tenant, name).expanduser().resolve())
        return self

    @property
    def resource(self) -> str:
        return self.public_url + "/mcp"


def load_config(path: str | Path) -> RemoteConfig:
    """Load the operator's account → tenant → repository map; tool callers cannot change it."""
    return RemoteConfig.model_validate(yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8")))


def credentials(config: RemoteConfig) -> dict[str, str]:
    """Environment wins over the server-only dotenv file; reject readable secret files."""
    values = {}
    if config.secrets_file.exists():
        if config.secrets_file.stat().st_mode & 0o077:
            raise ValueError("Remote secrets file must have mode 600")
        from dotenv import dotenv_values

        values = dict(dotenv_values(config.secrets_file))
    return {name: os.environ.get(name) or values.get(name) or "" for name in (
        "TRIPWRIGHT_GITHUB_CLIENT_ID", "TRIPWRIGHT_GITHUB_CLIENT_SECRET", "TRIPWRIGHT_OAUTH_SIGNING_KEY",
    )}
