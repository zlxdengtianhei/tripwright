"""Configuration for one personal remote service."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, model_validator


class RemoteConfig(BaseModel):
    """The repository is canonical when repo_url is set; otherwise the mirror on the data volume is canonical."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    public_url: str
    data_dir: Path
    repo_url: str | None = None
    branch: str = "main"
    ssh_key: Path | None = None
    known_hosts: Path | None = None

    @model_validator(mode="after")
    def validate_boundary(self):
        self.public_url = self.public_url.rstrip("/")
        parsed = urlsplit(self.public_url)
        # Plain HTTP only for this computer: a desktop host on the same machine needs no tunnel or certificate.
        scheme_ok = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
        if not scheme_ok or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("public_url must be an HTTPS origin (or http://localhost) without a path, credentials, query or fragment")
        if not self.branch or self.branch.startswith("-"):
            raise ValueError("The repository needs a nonempty branch that does not start with '-'")
        if self.repo_url is not None:
            ssh = self.repo_url.startswith("ssh://") or re.match(r"[\w.-]+@[\w.-]+:.+", self.repo_url)
            local = Path(self.repo_url).is_absolute()
            if not (ssh or local) or (self.repo_url.startswith("ssh://") and urlsplit(self.repo_url).password is not None):
                raise ValueError("repo_url must be an SSH Git URL (or an absolute local path for offline use)")
            if ssh and (self.ssh_key is None or self.known_hosts is None):
                raise ValueError("SSH repositories need ssh_key and known_hosts")
        self.data_dir = self.data_dir.expanduser().resolve()
        for name in ("ssh_key", "known_hosts"):
            if getattr(self, name) is not None:
                setattr(self, name, getattr(self, name).expanduser().resolve())
        return self
