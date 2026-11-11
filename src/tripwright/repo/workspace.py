"""One trip repository on disk: its configuration, its files at a commit or in the working copy, and `init`.

`materialize` turns an index generation into the repository's files: A's canonical files from `layout`, the front
screens and views whose bytes block B decides (`tripwright.render.repo_files`), written by A in the same commit.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from tripwright.repo import index, layout
from tripwright.repo.git import Git

SKIP = (".git/", ".tripwright/")


class Repository:
    def __init__(self, root: str | Path, *, owner: str = "local", remote: str = "origin", branch: str | None = None,
                 network_timeout: float = 10):
        self.root = Path(root).expanduser().resolve()
        self.git = Git(self.root, network_timeout=network_timeout)
        self.owner = owner
        self.remote_name = remote
        self.removed_files: list[str] = []  # retired tool files removed by the init upgrade that returned this repository
        if not (self.root / ".git").exists():
            raise ValueError(f"{self.root} is not a Git repository; run `tripwright init {self.root}` first")
        self.branch = branch or self.git.branch()

    @property
    def state_path(self) -> Path:
        return self.root / ".tripwright" / "state.sqlite"

    def timezone(self) -> str | None:
        """The default time zone from `tripwright.yaml` (for messages before a plan exists), read without the rest."""
        try:
            return (layout.parse(index.CONFIG, (self.root / index.CONFIG).read_text(encoding="utf-8")) or {}).get("timezone")
        except (OSError, ValueError):
            return None

    def remote(self) -> str | None:
        return self.remote_name if self.git.remote_url(self.remote_name) else None

    def canonical(self) -> str:
        """The branch whose head makes a write effective: the remote branch, or the local branch without a remote."""
        remote = self.remote()
        return f"{remote}/{self.branch}" if remote else self.branch

    def canonical_ref(self) -> str:
        remote = self.remote()
        return f"refs/remotes/{remote}/{self.branch}" if remote else f"refs/heads/{self.branch}"

    # --- files -------------------------------------------------------------------

    def working(self, prefix: str = "") -> dict[str, str]:
        files = {}
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return files
        for path in base.rglob("*"):
            relative = path.relative_to(self.root).as_posix()
            if path.is_file() and not relative.startswith(SKIP):
                try:
                    files[relative] = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
        return files

    def committed(self, rev: str = "HEAD", prefix: str = "") -> dict[str, str]:
        found = {}
        for path, data in self.git.tree(rev, prefix).items():
            try:
                found[path] = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
        return found

    def history_plans(self, rev: str, trip_id: str) -> dict[str, dict]:
        """Version ID → plan from the commit that recorded it (the `plan/` of that commit)."""
        base = layout.trip_folder(trip_id)
        plans = {}
        for commit in self.git.log(rev, paths=(f"{base}/plan",)):
            versions = commit["trailers"].get("Tripwright-Version", [])
            missing = [version for version in versions if version not in plans]
            if not missing:
                continue
            files = index.trip_part(self.committed(commit["commit"], f"{base}/"), trip_id)
            try:
                plan = layout.plan_from(files)
            except ValueError:
                plan = None
            if plan is not None:
                # One commit records one version's plan; earlier versions in the same commit had no other plan file.
                plans.setdefault(missing[-1], plan)
        return plans

    def load(self, rev: str | None = "HEAD", *, trip: str | None = None) -> dict:
        """An index generation from a commit (or the working copy when `rev` is None)."""
        files = self.committed(rev) if rev else self.working()
        if trip is not None:
            config = index.config_of(files)
            files = dict(files, **{index.CONFIG: layout.dump(dict(config, current_trip=trip))})
        config = index.config_of(files)
        plans = self.history_plans(rev, config["current_trip"]) if rev and config.get("current_trip") else {}
        return index.generation(files, plans=plans)

    def write(self, files: dict[str, str], remove: list[str]) -> list[str]:
        return write_files(self.root, files, remove)


def write_files(root: Path, files: dict[str, str], remove: list[str]) -> list[str]:
    """Write changed files only; return the paths whose bytes changed."""
    changed = []
    for path, text in files.items():
        target = root / path
        if target.exists() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        changed.append(path)
    for path in remove:
        target = root / path
        if target.exists():
            target.unlink()
            changed.append(path)
    return changed


def config_text(*, current_trip: str | None, timezone: str | None = None) -> str:
    return layout.dump(dict(format=layout.FORMAT, current_trip=current_trip, timezone=timezone,
                            note="Trip repository of tripwright. Canonical records are files; .tripwright/ is a rebuildable index."))


def init(root: str | Path, *, trip: str | None = None, timezone: str | None = None, remote: str | None = None,
         branch: str = "main", upgrade: bool = False) -> Repository:
    """Create a trip repository (or, with `upgrade`, rewrite the files the installed tool decides in an existing one)."""
    from tripwright.agents import TOOL_FILE_PATTERNS, user_repo_files

    root = Path(root).expanduser().resolve()
    git = Git(root)
    fresh = not (root / ".git").exists()
    if fresh:
        if root.exists() and any(root.iterdir()):
            raise ValueError(f"{root} is not empty; choose an empty folder or an existing trip repository with --upgrade")
        root.mkdir(parents=True, exist_ok=True)
        git.init(branch)
    elif not upgrade:
        raise ValueError(f"{root} is already a Git repository; use --upgrade to refresh the tool's files")
    files: dict[str, str] = {".gitignore": layout.GITIGNORE, ".gitattributes": layout.GITATTRIBUTES}
    files.update({path: data.decode("utf-8") for path, data in user_repo_files().items()})
    removed = []
    if upgrade:
        candidates = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
        removed = sorted(path for path in candidates if path not in files
                         and any(fnmatch(path, pattern) for pattern in TOOL_FILE_PATTERNS))
    if fresh:
        if trip is not None:
            layout.trip_folder(trip)
        files[index.CONFIG] = config_text(current_trip=trip, timezone=timezone)
        if trip is not None:
            files[f"{layout.trip_folder(trip)}/trip.yaml"] = layout.dump(dict(id=trip))
    changed = write_files(root, files, removed)
    for path in removed:
        directory = (root / path).parent
        while directory != root:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent
    if remote:
        git.run("remote", "add", "origin", remote)
    from tripwright.storage import now

    git.commit("tripwright init" if fresh else "tripwright: refresh the tool's files",
               add=[path for path in changed if path not in removed], remove=removed, when=now())
    repository = Repository(root, branch=branch)
    repository.removed_files = removed
    return repository
