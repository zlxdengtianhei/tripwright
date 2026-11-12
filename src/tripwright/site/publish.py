"""Publish committed, privacy-scanned trip pages to one Git repository (no GitHub API or token)."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from tripwright.remote.config import RemoteConfig
from tripwright.repo.git import Git, GitError

from .share import ShareBlocked, scan_tree, share_site

AUTHOR = {"GIT_AUTHOR_NAME": "tripwright", "GIT_AUTHOR_EMAIL": "share@tripwright.invalid",
          "GIT_COMMITTER_NAME": "tripwright", "GIT_COMMITTER_EMAIL": "share@tripwright.invalid"}
NOTICE_KEY = "share-blocked"
_PUBLISH_LOCK_TIMEOUT = 120
PATH = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
log = logging.getLogger("tripwright.share")


class PublishError(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(scratch, 0o600)
    os.replace(scratch, path)


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    try:
        done = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublishError(f"{command[0]} could not finish: {type(error).__name__}") from None
    if done.returncode:
        # Git can echo an SSH URL containing credentials; do not copy its stderr into logs or records.
        log.debug("share git command failed with status %s", done.returncode)
        raise PublishError(f"git {command[1] if len(command) > 1 else ''} failed; check the public repository and its write access")
    return done.stdout.strip()


def connection_file(data_dir) -> Path:
    return Path(data_dir) / "share.json"


def share_connection(data_dir) -> dict | None:
    value = read_json(connection_file(data_dir))
    return value if isinstance(value.get("repo_url"), str) and isinstance(value.get("branch"), str) else None


def pages_url(repo_url: str) -> str | None:
    found = re.search(r"(?:ssh\.)?github\.com(?::443)?[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if not found:
        return None
    owner, repo = found.groups()
    return f"https://{owner}.github.io/{'' if repo == owner + '.github.io' else repo + '/'}"


def _git_environment(data_dir: Path, repo_url: str) -> dict:
    if Path(repo_url).is_absolute():
        return {}
    from tripwright.remote.tenants import ssh_command

    config = RemoteConfig(public_url="http://localhost", data_dir=data_dir, repo_url=repo_url,
                          ssh_key=data_dir / "share/ssh/deploy_key", known_hosts=data_dir / "share/ssh/known_hosts")
    return {"GIT_SSH_COMMAND": ssh_command(config)}


def _roots(data_dir) -> tuple[Path, Path]:
    root = Path(data_dir) / "share"
    return root / "public", root / "build"


def _root_files(clone: Path):
    for path in clone.iterdir():
        if path.is_file() and path.name not in ("robots.txt", ".nojekyll"):
            path.unlink()
    (clone / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (clone / ".nojekyll").write_text("", encoding="utf-8")


def _commit(clone: Path, message: str, now: datetime) -> str | None:
    run(["git", "add", "-A"], cwd=clone)
    if not run(["git", "status", "--porcelain"], cwd=clone):
        return None
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    run(["git", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", message], cwd=clone,
        env=AUTHOR | {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    return run(["git", "rev-parse", "HEAD"], cwd=clone)


def connect(data_dir, repo_url: str, branch: str = "main", *, environ=os.environ, fetch_keys=None) -> str:
    """Verify a real minimal push before saving share.json; no service restart is needed."""
    from tripwright.remote.connection import repo_key
    from tripwright.remote.envconfig import resolved, ssh_files

    data_dir = Path(data_dir)
    repo_key(data_dir / "share")
    repo_url = resolved(repo_url, environ)
    if not Path(repo_url).is_absolute():
        ssh_files(data_dir / "share/ssh", repo_url, environ, fetch_keys)
    env = _git_environment(data_dir, repo_url)
    clone, _ = _roots(data_dir)
    stage = clone.with_name(".connect-public")
    shutil.rmtree(stage, ignore_errors=True)
    stage.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        run(["git", "clone", "--quiet", "--", repo_url, str(stage)], env=env)
        run(["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=stage)
        _root_files(stage)
        _commit(stage, "Initialize tripwright public pages", datetime.now(timezone.utc))
        run(["git", "push", "--quiet", "origin", f"HEAD:{branch}"], cwd=stage, env=env)
    except (PublishError, GitError, OSError) as error:
        shutil.rmtree(stage, ignore_errors=True)
        raise PublishError(f"Public repository connection failed; nothing was saved: {error}") from None
    with _publish_lock(data_dir):
        old = clone.with_name(".old-public")
        shutil.rmtree(old, ignore_errors=True)
        if clone.exists():
            clone.rename(old)
        stage.rename(clone)
        shutil.rmtree(old, ignore_errors=True)
    write_json(connection_file(data_dir), dict(repo_url=repo_url, branch=branch, pages_url=pages_url(repo_url)))
    base = pages_url(repo_url) or "<public-pages-base>/"
    return (f"Connected on {branch}. Public pages: {base}<path>/. Every trip with a committed plan publishes when you "
            f"connect and again after every plan commit; set off: true in trips/<trip>/share.yaml to disable and remove one.")


@contextmanager
def _publish_lock(data_dir):
    """One cross-process lock from the public-path commit to the push: the service's background publisher and a
    `share connect` in another process take turns instead of racing on the same public clone and trip repository."""
    try:
        import fcntl
    except ImportError:  # a platform without flock publishes without the lock rather than failing
        yield
        return
    folder = Path(data_dir) / "share"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(folder / "publish.lock", "a+")
    deadline = time.monotonic() + _PUBLISH_LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise PublishError("Another publish has held the public repository for 120 seconds; try again later") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _clone(data_dir: Path, connection: dict) -> Path:
    clone, _ = _roots(data_dir)
    env = _git_environment(data_dir, connection["repo_url"])
    if not (clone / ".git").is_dir() or Git(clone).remote_url("origin") != connection["repo_url"]:
        shutil.rmtree(clone, ignore_errors=True)
        clone.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--quiet", "--branch", connection["branch"], "--", connection["repo_url"], str(clone)], env=env)
    run(["git", "fetch", "--quiet", "origin", connection["branch"]], cwd=clone, env=env)
    run(["git", "checkout", "--quiet", "-B", connection["branch"], f"origin/{connection['branch']}"], cwd=clone)
    run(["git", "reset", "--quiet", "--hard", f"origin/{connection['branch']}"], cwd=clone)
    return clone


def _settings(repository, trip: str, rev: str) -> dict:
    from tripwright.repo import index, layout

    files = repository.committed(rev, f"{layout.trip_folder(trip)}/share.yaml")
    text = files.get(f"{layout.trip_folder(trip)}/share.yaml")
    value = layout.parse("share.yaml", text) if text is not None else {}
    if not isinstance(value, dict):
        raise PublishError("share.yaml must be a mapping")
    if False in value:  # YAML 1.1 reads the documented, unquoted key `off` as boolean False
        value["off"] = value.pop(False)
    path = value.get("path")
    if path is not None and (not isinstance(path, str) or not PATH.fullmatch(path)):
        raise PublishError("share.yaml path must be a lowercase URL path segment (letters, digits and '-')")
    return value


def _default_path(repository, trip: str, rev: str, settings: dict, committer) -> tuple[str, dict]:
    """The random name is committed in the trip repository, never only on the service volume."""
    from tripwright.repo import layout

    relative = f"{layout.trip_folder(trip)}/share.yaml"
    settings = dict(settings, path=re.sub(r"^\d{4}-\d{2}-", "", trip).lower() + "-" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(2)))
    lock = committer.lock if committer is not None else nullcontext()
    with lock:
        target = repository.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(layout.dump(settings), encoding="utf-8")
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        revision = repository.git.commit("tripwright: choose public page path", add=[relative], remove=[], when=when)
        if repository.remote():
            outcome = repository.git.push(repository.remote(), repository.branch)
            if outcome != "ok":
                raise PublishError("The private trip repository did not accept the new public path; nothing was published")
            repository.git.fetch(repository.remote(), repository.branch)
        if committer is not None:
            committer.set_head(revision, trip=trip)
    return revision, settings


def _record(store, owner: str, trip: str, **changes) -> dict:
    from tripwright.storage import now

    try:
        record = store.get("share", owner, trip)
    except ValueError:
        record = dict(id=trip, trip=trip)
    record.update(changes, at=now())
    store.put("share", owner, record)
    if changes.get("ok") and trip != "unknown":
        try:
            stale = store.get("share", owner, "unknown")
            stale.update(ok=True, status="superseded", error=None, replaced_by=trip, at=now())
            store.put("share", owner, stale)
        except ValueError:
            pass
    return record


def _failure_trip(store, owner: str, trip: str | None) -> str:
    if trip:
        return trip
    plans = [item.get("plan") for item in store.all("versions", owner)]
    return next((value["id"] for value in reversed(plans) if value and value.get("id")), "unknown")


def _reason(error: Exception) -> str:
    if isinstance(error, PublishError):
        return str(error)[-300:]  # PublishError messages are constructed without connector strings or found values
    text = str(error).casefold()
    if "current trip" in text or "committed plan" in text:
        return "trip_unavailable"
    if "share.yaml" in text:
        return "settings_invalid"
    if isinstance(error, (GitError, OSError)):
        return "connection_or_filesystem_failed"
    return type(error).__name__


def publish(repo_root, *, trip: str | None = None, rev: str = "HEAD", data_dir=None, store=None, owner: str = "local",
            committer=None, now: datetime | None = None) -> dict:
    """Read one committed version → build/scan → replace only its subdirectory → commit/push."""
    from tripwright.repo import layout
    from tripwright.repo.workspace import Repository
    from tripwright.site.share import load_trip

    repository = Repository(repo_root, owner=owner)
    data_dir = Path(data_dir) if data_dir is not None else __import__("tripwright.remote.connection", fromlist=["data_directory"]).data_directory()
    own_store = store is None
    store = store or __import__("tripwright.storage", fromlist=["Store"]).Store(repository.state_path)
    clock = now or datetime.now(timezone.utc)
    connection = share_connection(data_dir)
    try:
        plan, people = load_trip(repo_root, trip=trip, rev=rev)
        trip = plan["id"]
        entries = repository.git.log(rev)
        version = (entries[0]["trailers"].get("Tripwright-Version") or [None])[-1] if entries else None
        if connection is None:
            raise PublishError("No public repository: run tripwright share key and share connect")
        with _publish_lock(data_dir):
            settings = _settings(repository, trip, rev)
            if settings.get("repository"):
                return _record(store, owner, trip, ok=False, status="separate_repository", plan_version=version,
                               error="A per-trip repository needs another deployment or manual publication; nothing was pushed")
            previous = _record(store, owner, trip)
            if not settings.get("path") and not settings.get("off"):
                rev, settings = _default_path(repository, trip, rev, settings, committer)
            path = settings.get("path") or previous.get("path")
            if not path:
                return _record(store, owner, trip, ok=True, status="off", plan_version=version, changed=False)
            old = previous.get("path")
            clone = _clone(data_dir, connection)
            _root_files(clone)
            changed = False
            if not settings.get("off"):
                _, build = _roots(data_dir)
                built = share_site(plan, build / trip, people=people, english=settings.get("english"),
                                   extras=dict(intro=settings.get("intro"), route=settings.get("route")), now=clock)
                for name in {old, path} - {None}:
                    shutil.rmtree(clone / name, ignore_errors=True)
                shutil.copytree(built["out"], clone / path)
                findings = scan_tree(clone / path, built["rules"].zh)
                if findings:
                    raise ShareBlocked(findings)
            else:
                for name in {old, path} - {None}:
                    shutil.rmtree(clone / name, ignore_errors=True)
            commit = _commit(clone, "Update public trip page", clock)
            if commit:
                run(["git", "push", "--quiet", "origin", f"HEAD:{connection['branch']}"], cwd=clone,
                    env=_git_environment(data_dir, connection["repo_url"]))
                changed = True
        base = (connection.get("pages_url") or "").rstrip("/") + "/" if connection.get("pages_url") else None
        url = f"{base}{path}/" if base else path
        result = _record(store, owner, trip, ok=True, status="off" if settings.get("off") else "published",
                         url=None if settings.get("off") else url, path=path, plan_version=version, revision=rev,
                         public_commit=commit, changed=changed, blocked=None, error=None)
        notice(store, owner, None)
        return result
    except ShareBlocked as blocked:
        _record(store, owner, _failure_trip(store, owner, trip), ok=False, status="blocked",
                plan_version=locals().get("version"), blocked=blocked.findings, error=None)
        notice(store, owner, blocked.findings)
        raise
    except Exception as error:
        _record(store, owner, _failure_trip(store, owner, trip), ok=False, status="error",
                plan_version=locals().get("version"), error=_reason(error))
        raise PublishError(_reason(error)) from None
    finally:
        if own_store:
            store.close()


def republish(repo_root, *, data_dir=None, owner: str = "local", store=None) -> list[dict]:
    """`share connect`'s catch-up: every trip with a committed plan publishes once at its current head.

    One entry per trip for the command to print; blocked findings stay categories and file counts, never values.
    """
    from tripwright.remote.connection import trips
    from tripwright.repo import layout
    from tripwright.repo.workspace import Repository
    from tripwright.storage import Store

    repository = Repository(repo_root, owner=owner)
    own_store = store is None
    store = store or Store(repository.state_path)
    try:
        results = []
        for trip in trips(repository.git):
            if f"{layout.trip_folder(trip)}/plan/places.yaml" not in repository.committed("HEAD", f"{layout.trip_folder(trip)}/plan"):
                results.append(dict(trip=trip, status="no_plan"))
                continue
            try:
                outcome = publish(repo_root, trip=trip, data_dir=data_dir, store=store, owner=owner)
                entry = dict(trip=trip, status=outcome["status"], url=outcome.get("url"))
                if not outcome["ok"]:
                    entry["reason"] = outcome.get("error") or outcome["status"]
            except ShareBlocked as blocked:
                entry = dict(trip=trip, status="blocked", categories=sorted(blocked.findings),
                             files=len({page for pages in blocked.findings.values() for page in pages}))
            except PublishError as error:
                entry = dict(trip=trip, status="error", reason=str(error))
            results.append(entry)
        return results
    finally:
        if own_store:
            store.close()


class Publisher:
    """One coalescing worker per committer; failures stay out of the request and committer threads."""
    def __init__(self, committer, quiet: float = 20.0):
        self.committer, self.quiet = committer, quiet
        self.data_dir = __import__("tripwright.remote.connection", fromlist=["data_directory"]).data_directory()
        self.revision = None
        self.wakeup = threading.Event()
        self.stopping = threading.Event()
        self.thread = None

    def schedule(self, revision: str):
        if self.stopping.is_set() or share_connection(self.data_dir) is None:
            return
        self.revision = revision
        self.wakeup.set()
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._work, name="tripwright-share", daemon=True)
            self.thread.start()

    def _work(self):
        last = time.monotonic()
        while not self.stopping.is_set():
            if self.wakeup.wait(0.2):
                self.wakeup.clear()
                last = time.monotonic()
                continue
            if self.revision is None or time.monotonic() - last < self.quiet:
                continue
            revision, self.revision = self.revision, None
            try:
                publish(self.committer.repo.root, rev=revision, data_dir=self.data_dir, store=self.committer.store,
                        owner=self.committer.owner, committer=self.committer)
            except Exception as error:
                safe = str(error)[-300:] if isinstance(error, (PublishError, ShareBlocked)) else type(error).__name__
                log.warning("public page was not published: %s", safe)

    def stop(self):
        self.stopping.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=2)


def notice(store, owner: str, findings: dict[str, list[str]] | None):
    """Keep one open notice while the page is blocked; categories and pages only, never values."""
    from tripwright.repo import notices
    from tripwright.storage import identifier, now

    notices.close(store, owner, key=NOTICE_KEY)
    if not findings:
        return None
    categories = ", ".join(findings)
    where = "; ".join(f"{key}: {', '.join(pages)}" for key, pages in findings.items())
    record = dict(id=identifier("N"), notice_id=None, kind="share_blocked", key=NOTICE_KEY, created_at=now(),
                  user_line=f"分享页这一版没有发布：扫描发现 {categories}；网上还是上一版。",
                  detail=f"Share page not published: the privacy scan found {categories} (pages: {where}). Values are not shown. "
                         "Reword those parts of the plan; the next committed version retries.", paths=sorted({p for ps in findings.values() for p in ps}),
                  once=False, status="open")
    record["notice_id"] = record["id"]
    store.put("notices", owner, record, insert=True)
    return record
