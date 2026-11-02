"""The few Git operations the committer needs, as subprocess calls on one working copy.

Network operations have short timeouts and report "unreachable" instead of raising, so a quick question never waits
for a remote. Pushes never force: a rejected push is answered by replaying operations on the new head.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

TRAILERS = ("Tripwright-Op", "Tripwright-Request", "Tripwright-Version")

# What a failed push or remote check means, from Git's and the host's stderr; the first matching row wins (an
# authentication failure also prints "Could not read from remote repository", so it comes before the network row).
FAILURES = (
    ("protected", ("protected branch", "gh006", "hook declined", "pre-receive hook", "push declined")),
    ("auth", ("permission denied", "marked as read only", "read only", "read-only", "authentication failed",
              "access denied", "not allowed to push", "returned error: 403", "could not read username",
              "host key verification failed", "no matching host key", "bad permissions", "invalid format",
              "repository not found", "does not appear to be a git repository", "does not exist", "load key")),
    ("behind", ("non-fast-forward", "fetch first", "[rejected]", "rejected")),
    ("network", ("could not resolve", "connection timed out", "timed out", "connection refused", "network is unreachable",
                 "no route to host", "connection reset", "connection closed", "early eof", "unable to access",
                 "could not read from remote repository", "temporary failure in name resolution")),
)
OUTCOMES = {"protected": "protected", "auth": "denied", "behind": "rejected", "network": "unreachable"}


def failure_kind(text: str) -> str:
    """auth, protected, behind, network or other: what a failed remote operation's stderr says went wrong."""
    lowered = text.casefold()
    return next((kind for kind, words in FAILURES if any(word in lowered for word in words)), "other")


class GitError(RuntimeError):
    pass


def run_group(command: list[str], *, timeout: float | None, **options) -> subprocess.CompletedProcess:
    """subprocess.run in its own process group: on timeout the whole group is killed. Killing only git leaves its ssh
    child holding the output pipes, and the wait then lasts as long as ssh hangs."""
    with subprocess.Popen(command, stdin=subprocess.PIPE if options.get("input") is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                          cwd=options.get("cwd"), env=options.get("env")) as process:
        try:
            stdout, stderr = process.communicate(options.get("input"), timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class Git:
    def __init__(self, root: str | Path, *, network_timeout: float = 10):
        self.root = Path(root)
        self.network_timeout = network_timeout
        self.last_error: str | None = None  # the last failed push's stderr, for the operator's log

    def run(self, *args: str, input: str | bytes | None = None, env: dict | None = None, timeout: float | None = 60,
            binary: bool = False) -> str | bytes:
        process = run_group(["git", "-c", "core.quotepath=off", *args], cwd=self.root,
                            input=input.encode() if isinstance(input, str) else input,
                            env={**os.environ, **(env or {})}, timeout=timeout)
        if process.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])}: {process.stderr.decode(errors='replace').strip() or process.returncode}")
        return process.stdout if binary else process.stdout.decode()

    def ok(self, *args: str) -> bool:
        try:
            self.run(*args)
        except (GitError, subprocess.TimeoutExpired):
            return False
        return True

    def init(self, branch: str = "main"):
        self.run("init", "-q", "-b", branch)

    def rev(self, ref: str = "HEAD") -> str | None:
        try:
            return self.run("rev-parse", "--verify", "-q", f"{ref}^{{commit}}").strip() or None
        except GitError:
            return None

    def branch(self, remote: str = "origin") -> str:
        """The checked-out branch. A detached HEAD (a checkout of a commit, as CI, replays and some hosts make) follows a
        branch at the same commit, local first, then on `remote`; then the remote's default branch; then `main`."""
        try:
            return self.run("symbolic-ref", "--short", "-q", "HEAD").strip()
        except GitError:
            pass
        for space in ("refs/heads/", f"refs/remotes/{remote}/"):
            names = [name[len(space):] for name in self.run("for-each-ref", "--points-at", "HEAD", "--format=%(refname)", space).split()]
            names = [name for name in names if name != "HEAD"]
            if names:
                return names[0]
        prefix = f"{remote}/"
        try:
            default = self.run("symbolic-ref", "--short", "-q", f"refs/remotes/{remote}/HEAD").strip()
        except GitError:
            default = ""
        return default[len(prefix):] if default.startswith(prefix) else "main"

    def remote_url(self, remote: str) -> str | None:
        try:
            return self.run("remote", "get-url", remote).strip() or None
        except GitError:
            return None

    def fetch(self, remote: str, branch: str) -> bool:
        """True when the remote answered, including a new remote that has no branch yet."""
        try:
            self.run("fetch", "-q", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}", timeout=self.network_timeout)
        except subprocess.TimeoutExpired:
            return False
        except GitError as error:
            return "couldn't find remote ref" in str(error)
        return True

    def push(self, remote: str, branch: str, *, timeout: float | None = None) -> str:
        """'ok'; 'rejected' (the remote has commits we do not); 'unreachable' (network); 'denied' (the key or account
        may not write); 'protected' (the branch refuses pushes); 'failed' (anything else). The stderr is kept in
        `last_error`."""
        self.last_error = None
        try:
            self.run("push", "--porcelain", "-q", remote, f"HEAD:refs/heads/{branch}", timeout=timeout or self.network_timeout)
        except subprocess.TimeoutExpired:
            self.last_error = f"git push timed out after {timeout or self.network_timeout:.0f} s"
            return "unreachable"
        except GitError as error:
            self.last_error = str(error)
            return OUTCOMES.get(failure_kind(self.last_error), "failed")
        return "ok"

    def probe(self, remote: str, branch: str, *, write: bool = True) -> tuple[str, str]:
        """Check that the remote answers with this checkout's credentials (`ls-remote`) and, with `write`, accepts a
        push (`push --dry-run`: the host checks write access before it compares refs). Returns ('ok', '') or
        (failure kind, stderr)."""
        checks = [("ls-remote", "--exit-code", "--heads", remote, branch)]
        if write:
            checks.append(("push", "--dry-run", "--porcelain", "-q", remote, f"HEAD:refs/heads/{branch}"))
        for args in checks:
            try:
                self.run(*args, timeout=self.network_timeout * 2)
            except subprocess.TimeoutExpired:
                return "network", f"git {args[0]} timed out"
            except GitError as error:
                text = str(error)
                if args[0] == "ls-remote" and text.endswith(": 2"):
                    return "branch", f"the remote has no branch {branch!r}"
                # A dry run that only finds the remote ahead still proved write access.
                if args[0] == "push" and failure_kind(text) == "behind":
                    continue
                return failure_kind(text), text
        return "ok", ""

    def clear_locks(self) -> list[str]:
        """Remove lock files a killed Git process left (index.lock, HEAD.lock, refs/…/x.lock). Only safe when this process
        is the checkout's only writer, as the server is for its tenant mirrors."""
        git_dir = self.root / ".git"
        if not git_dir.is_dir():
            return []
        found = [*git_dir.glob("*.lock"), *(git_dir / "refs").rglob("*.lock")]
        for path in found:
            path.unlink(missing_ok=True)
        return [str(path.relative_to(self.root)) for path in found]

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", older, newer)

    def identity_env(self) -> dict:
        env = {}
        for key, name, fallback in (("user.name", "NAME", "tripwright"), ("user.email", "EMAIL", "tripwright@localhost")):
            try:
                configured = self.run("config", key).strip()
            except GitError:
                configured = ""
            if not configured:
                env[f"GIT_AUTHOR_{name}"] = env[f"GIT_COMMITTER_{name}"] = fallback
        return env

    def commit(self, message: str, *, add: list[str], remove: list[str], when: str) -> str:
        """Stage exactly these paths and commit them; other edits in the working copy stay out of the commit."""
        if add:
            self.run("add", "-f", "--", *add)
        if remove:
            self.run("rm", "-q", "--cached", "--ignore-unmatch", "--", *remove)
        env = self.identity_env() | {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        # Tool commits are machine records; a user's signing setup must not block an answer.
        self.run("-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--allow-empty", "-F", "-", input=message, env=env)
        return self.rev("HEAD")

    def tree(self, rev: str, prefix: str = "") -> dict[str, bytes]:
        """Every file under `prefix` at `rev`, path → bytes."""
        listing = self.run("ls-tree", "-r", "-z", rev, "--", prefix) if prefix else self.run("ls-tree", "-r", "-z", rev)
        entries = []
        for row in listing.split("\0"):
            if not row:
                continue
            meta, path = row.split("\t", 1)
            _, kind, sha = meta.split()
            if kind == "blob":
                entries.append((path, sha))
        if not entries:
            return {}
        output = self.run("cat-file", "--batch", input="".join(f"{sha}\n" for _, sha in entries), binary=True)
        files, position = {}, 0
        for path, _ in entries:
            header_end = output.index(b"\n", position)
            size = int(output[position:header_end].split()[2])
            files[path] = output[header_end + 1:header_end + 1 + size]
            position = header_end + 1 + size + 1
        return files

    def log(self, rev: str, *, since: str | None = None, paths: tuple[str, ...] = ()) -> list[dict]:
        """Commits reachable from `rev` (not from `since`), newest first, with their Tripwright trailers."""
        span = f"{since}..{rev}" if since else rev
        text = self.run("log", "--format=%H%x1f%cI%x1f%B%x1e", span, "--", *paths) if paths else \
            self.run("log", "--format=%H%x1f%cI%x1f%B%x1e", span)
        commits = []
        for chunk in text.split("\x1e"):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            sha, date, body = chunk.split("\x1f", 2)
            trailers: dict[str, list[str]] = {}
            for row in body.splitlines():
                key, separator, value = row.partition(": ")
                if separator and key in TRAILERS:
                    trailers.setdefault(key, []).append(value.strip())
            commits.append(dict(commit=sha, date=date, trailers=trailers))
        return commits

    def changed(self, paths: tuple[str, ...] = ()) -> dict[str, str]:
        """Working-copy paths that differ from HEAD (tracked or new), path → porcelain status."""
        text = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths)
        found, rows, index = {}, text.split("\0"), 0
        while index < len(rows):
            row = rows[index]
            if row:
                status, path = row[:2], row[3:]
                found[path] = status
                if status.startswith("R"):
                    index += 1
            index += 1
        return found
