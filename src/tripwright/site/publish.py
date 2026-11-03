"""Publish the share page to its own repository on GitHub Pages: build, scan, push (D-83).

GitHub Free serves Pages only from a public repository, so the page gets a repository of its own that holds the
built page and nothing else; the trip repository stays private. The first run creates that repository with
`gh repo create --public` and turns Pages on; later runs rebuild, rescan and push. A blocked scan pushes nothing,
so the published page stays the previous version. Commits carry a fixed tool identity, never the user's Git name.

Local state lives in `<trip repo>/.tripwright/share/` (ignored by Git): `config.json` (the repository and page URL),
`site/` (the last clean build), `public/` (the clone that is pushed), `last.json` (the last outcome, categories only),
and `en.yaml`, the English for the user's own words (see `tripwright.site.share`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .share import ShareBlocked, Rules, scan_tree, share_site

AUTHOR = {"GIT_AUTHOR_NAME": "tripwright", "GIT_AUTHOR_EMAIL": "share@tripwright.invalid",
          "GIT_COMMITTER_NAME": "tripwright", "GIT_COMMITTER_EMAIL": "share@tripwright.invalid"}
NOTICE_KEY = "share-blocked"


class PublishError(RuntimeError):
    pass


def state_dir(repo_root) -> Path:
    return Path(repo_root).expanduser().resolve() / ".tripwright" / "share"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    try:
        done = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublishError(f"{command[0]} {command[1] if len(command) > 1 else ''}: {error}") from None
    if done.returncode != 0:
        raise PublishError(f"{' '.join(command[:3])} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout.strip()


def mirror(site: Path, clone: Path):
    """Make the clone's working tree exactly the built site: everything else is removed."""
    for path in clone.iterdir():
        if path.name == ".git":
            continue
        shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    for path in site.iterdir():
        target = clone / path.name
        shutil.copytree(path, target) if path.is_dir() else shutil.copy2(path, target)


def repository_name(gh: str, name: str) -> str:
    return run([gh, "repo", "view", name, "--json", "nameWithOwner", "--jq", ".nameWithOwner"])


def create(gh: str, name: str, *, visibility: str = "public") -> str:
    if visibility not in ("public", "private"):
        raise ValueError("visibility must be public or private")
    run([gh, "repo", "create", name, f"--{visibility}", "--description", "Shared trip page (built by tripwright)"])
    return repository_name(gh, name)


def enable_pages(gh: str, full_name: str, branch: str) -> str | None:
    try:
        run([gh, "api", "-X", "POST", f"repos/{full_name}/pages", "-f", "build_type=legacy",
             "-f", f"source[branch]={branch}", "-f", "source[path]=/"])
    except PublishError as error:
        if "409" not in str(error) and "already" not in str(error).lower():
            raise
    try:
        return run([gh, "api", f"repos/{full_name}/pages", "--jq", ".html_url"]) or None
    except PublishError:
        return None


def push_site(site: Path, clone: Path, rules: Rules, *, branch: str, message: str, now: datetime) -> str | None:
    """Commit the site into the clone and push it; return the new commit, or None when nothing changed.

    The clone is scanned again right before the commit (it is what gets pushed); a finding resets it and blocks."""
    if (clone / ".git").is_dir():
        run(["git", "fetch", "--quiet", "origin"], cwd=clone)
        remote = run(["git", "ls-remote", "--heads", "origin", branch], cwd=clone)
        if remote:
            run(["git", "checkout", "--quiet", "-B", branch, f"origin/{branch}"], cwd=clone)
            run(["git", "reset", "--quiet", "--hard", f"origin/{branch}"], cwd=clone)
    else:
        raise PublishError(f"{clone} is not a clone of the share repository")
    try:
        run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=clone)
        run(["git", "checkout", "--quiet", "-B", branch], cwd=clone)
    except PublishError:  # a new, empty repository: the branch is not born yet
        run(["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=clone)
    mirror(site, clone)
    findings = scan_tree(clone, rules.zh)
    if findings:
        run(["git", "reset", "--quiet", "--hard"], cwd=clone)
        run(["git", "clean", "-qfdx"], cwd=clone)
        raise ShareBlocked(findings)
    run(["git", "add", "-A"], cwd=clone)
    if not run(["git", "status", "--porcelain"], cwd=clone):
        return None
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    identity = dict(AUTHOR, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    run(["git", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", message], cwd=clone, env=identity)
    run(["git", "push", "--quiet", "origin", f"HEAD:{branch}"], cwd=clone)
    return run(["git", "rev-parse", "HEAD"], cwd=clone)


def publish(repo_root, *, name: str | None = None, create_repo: bool = False, visibility: str = "public",
            english: dict[str, str] | None = None, trip: str | None = None, gh: str = "gh", branch: str = "main",
            now: datetime | None = None) -> dict:
    """Build → scan → push. Returns the outcome (also written to `last.json`); a blocked scan raises ShareBlocked
    after recording its categories, and nothing is pushed."""
    from .share import load_trip

    clock = now or datetime.now(timezone.utc)
    state = state_dir(repo_root)
    config = read_json(state / "config.json")
    plan, people = load_trip(repo_root, trip=trip)
    target = name or config.get("repository") or f"trip-{plan['id']}"
    if not create_repo and not config.get("repository") and name is None:
        raise PublishError("No share repository yet: run with --create (and --name) the first time")
    try:
        built = share_site(plan, state / "site", people=people, english=english, now=clock)
    except ShareBlocked as blocked:
        write_json(state / "last.json", dict(ok=False, at=clock.isoformat(timespec="seconds"), blocked=blocked.findings,
                                             published=config.get("url")))
        raise
    rules = built["rules"]
    if create_repo and not config.get("repository"):
        full = create(gh, target, visibility=visibility)
        config = dict(repository=full, visibility=visibility, branch=branch)
        write_json(state / "config.json", config)
    full = config.get("repository") or repository_name(gh, target)
    clone = state / "public"
    if not (clone / ".git").is_dir():
        if clone.exists():
            shutil.rmtree(clone)
        run([gh, "repo", "clone", full, str(clone)])
    message = f"Update shared trip page ({plan['id']}, {len(plan.get('days', []))} days)"
    try:
        commit = push_site(built["out"], clone, rules, branch=branch, message=message, now=clock)
    except ShareBlocked as blocked:
        write_json(state / "last.json", dict(ok=False, at=clock.isoformat(timespec="seconds"), blocked=blocked.findings,
                                             published=config.get("url")))
        raise
    if not config.get("url"):
        url = enable_pages(gh, full, config.get("branch", branch))
        config = dict(config, repository=full, url=url)
        write_json(state / "config.json", config)
    outcome = dict(ok=True, at=clock.isoformat(timespec="seconds"), repository=full, url=config.get("url"), commit=commit,
                   changed=commit is not None, pages=built["pages"], untranslated=len(built["untranslated"]))
    write_json(state / "last.json", outcome)
    return outcome


def notice(store, owner: str, findings: dict[str, list[str]] | None):
    """Keep one open notice while the share page is blocked, so the next `turn` tells the agent the categories."""
    from tripwright.repo import notices
    from tripwright.storage import identifier, now

    notices.close(store, owner, key=NOTICE_KEY)
    if not findings:
        return None
    categories = ", ".join(findings)
    where = "; ".join(f"{key}: {', '.join(pages)}" for key, pages in findings.items())
    record = dict(id=identifier("N"), notice_id=None, kind="share_blocked", key=NOTICE_KEY, created_at=now(),
                  user_line=f"分享页这一版没有发布：扫描发现 {categories}；网上还是上一版。",
                  detail=f"Share page not published: the privacy scan found {categories} (pages: {where}). "
                         "Values are not shown. Reword those parts of the plan, then run `tripwright share publish` again.",
                  paths=sorted({page for pages in findings.values() for page in pages}), once=False, status="open")
    record["notice_id"] = record["id"]
    store.put("notices", owner, record, insert=True)
    return record
