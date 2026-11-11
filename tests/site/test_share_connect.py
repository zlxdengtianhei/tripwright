"""`share connect` publishes every existing trip once: one line per trip, safe failures, exit codes.

All people, places and numbers are invented; the public repository is a local bare repository.
"""
from __future__ import annotations

import subprocess
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from test_share import GIT, public, remote_files, sample_plan, share_settings, trip_repo  # noqa: F401 (public is a fixture)
from tripwright.cli import main
from tripwright.repo import layout
from tripwright.site import share as share_module
from tripwright.site.publish import publish
from tripwright.storage import Store

OFF_TRIP = "2030-08-second"


def bare_public(tmp_path: Path) -> Path:
    bare = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return bare


def mirror(data: Path, *plans: dict) -> Path:
    """The trip repository the service would open: <data>/tenants/personal/mirror."""
    root = data / "tenants" / "personal" / "mirror"
    for item in plans:
        trip_repo(root, item)
    return root


def settings_path(repo: Path, trip: str) -> str:
    return layout.parse("share.yaml", (repo / "trips" / trip / "share.yaml").read_text(encoding="utf-8"))["path"]


def record(mirror_root: Path, trip: str) -> dict:
    store = Store(mirror_root / ".tripwright/state.sqlite")
    try:
        return store.get("share", "local", trip)
    finally:
        store.close()


def test_connect_publishes_every_existing_trip_and_skips_the_one_turned_off(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    bare = bare_public(tmp_path)
    first = sample_plan()
    second = sample_plan()
    second["id"] = OFF_TRIP
    root = mirror(data, first, second)
    share_settings(root, OFF_TRIP, off=True)

    assert main(["share", "connect", str(bare)]) == 0

    out = capsys.readouterr().out
    path = settings_path(root, first["id"])
    assert f"{first['id']}: published — {path}" in out
    assert f"{OFF_TRIP}: public page is off (off: true in trips/{OFF_TRIP}/share.yaml)" in out
    assert "1 published, 1 off, 0 not published." in out
    assert "Connected on main." in out and "publishes when you connect" in out
    files = remote_files(bare)
    assert f"{path}/index.html" in files and f"{path}/2030-07-14.html" in files
    assert {name.split("/")[0] for name in files} == {path, "robots.txt", ".nojekyll"}
    assert record(root, first["id"])["status"] == "published"
    assert record(root, OFF_TRIP)["status"] == "off" and record(root, OFF_TRIP).get("url") is None


def test_connect_again_retries_and_prints_the_page_addresses_again(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    bare = bare_public(tmp_path)
    root = mirror(data, sample_plan())
    assert main(["share", "connect", str(bare)]) == 0
    path = settings_path(root, "2030-07-fiktiv")
    capsys.readouterr()

    assert main(["share", "connect", str(bare)]) == 0

    out = capsys.readouterr().out
    assert f"2030-07-fiktiv: published — {path}" in out and "1 published, 0 off, 0 not published." in out
    assert f"{path}/index.html" in remote_files(bare)


def test_connect_reports_scan_categories_without_values_and_keeps_the_connection(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    bare = bare_public(tmp_path)
    leaking = sample_plan()
    leaking["days"][1]["notes"] = "机场见，电话 +43 660 1234567"
    root = mirror(data, leaking)

    assert main(["share", "connect", str(bare)]) == 2

    out = capsys.readouterr().out
    line = next(item for item in out.splitlines() if "2030-07-fiktiv" in item)
    assert line == ("2030-07-fiktiv: not published — the privacy scan found phone number in 1 file(s); "
                    "see tripwright share status")
    assert "1234567" not in out
    assert "0 published, 0 off, 1 not published." in out
    assert (data / "share.json").is_file()
    assert record(root, "2030-07-fiktiv")["blocked"] == {"phone number": ["2030-07-15.html"]}
    assert not any(name.count("/") and name.endswith("index.html") for name in remote_files(bare))


def test_connect_reports_a_public_push_failure_and_keeps_the_connection(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    bare = bare_public(tmp_path)
    # The hook lets the connection's own minimal push through and rejects only a trip page.
    hook = bare / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\nwhile read old new ref; do\n  if git ls-tree -r --name-only "$new" | grep -q "/index.html"; then\n'
                    '    exit 1\n  fi\ndone\nexit 0\n', encoding="utf-8")
    hook.chmod(0o755)
    root = mirror(data, sample_plan())

    assert main(["share", "connect", str(bare)]) == 2

    out = capsys.readouterr().out
    line = next(item for item in out.splitlines() if "2030-07-fiktiv" in item)
    assert line == ("2030-07-fiktiv: not published — git push failed; check the public repository and its write access; "
                    "fix it and run this command again")
    assert "0 published, 0 off, 1 not published." in out and (data / "share.json").is_file()


def test_connect_without_any_trip_says_the_first_plan_will_publish_a_page(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    bare = bare_public(tmp_path)

    assert main(["share", "connect", str(bare)]) == 0

    out = capsys.readouterr().out
    assert "No trips yet; the first committed plan will publish a page." in out
    assert "0 published, 0 off, 0 not published." in out


def test_connect_names_a_trip_without_a_committed_plan_and_does_not_count_it_failed(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    bare = bare_public(tmp_path)
    root = mirror(data, sample_plan())
    folder = root / "trips" / "2030-09-empty"
    folder.mkdir(parents=True)
    (folder / "trip.yaml").write_text(layout.dump(dict(id="2030-09-empty")), encoding="utf-8")
    subprocess.run([*GIT, "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "trip without a plan"], check=True)

    assert main(["share", "connect", str(bare)]) == 0

    out = capsys.readouterr().out
    assert "2030-09-empty: no committed plan yet; its first committed plan will publish a page" in out
    assert "1 published, 0 off, 0 not published." in out


# --- the cross-process lock -------------------------------------------------------------------


def race_publish(repo: Path, data: Path, trip: str) -> list[Exception]:
    """Two threads with their own Store connections publish the same trip at the same moment
    (a barrier in load_trip lines them up)."""
    gate = threading.Barrier(2, timeout=60)
    original = share_module.load_trip

    def together(root, *, trip=None, rev=None):
        gate.wait()
        return original(root, trip=trip, rev=rev)

    share_module.load_trip = together
    stores = [Store(repo / ".tripwright/state.sqlite") for _ in range(2)]
    failures: list[Exception] = []
    threads = []
    try:
        for store in stores:
            def attempt(store=store):
                try:
                    publish(repo, trip=trip, data_dir=data, store=store)
                except Exception as error:
                    failures.append(error)
            threads.append(threading.Thread(target=attempt))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        share_module.load_trip = original
        for store in stores:
            store.close()
    return failures


def directories(bare: Path) -> set[str]:
    return {name.split("/")[0] for name in remote_files(bare)} - {"robots.txt", ".nojekyll"}


def history_is_linear(bare: Path) -> bool:
    parents = subprocess.run(["git", "-C", str(bare), "log", "--format=%P", "main"], capture_output=True, text=True)
    return all(len(line.split()) <= 1 for line in parents.stdout.splitlines())


def test_two_threads_publishing_one_trip_at_the_same_time_both_succeed(tmp_path, public):
    bare, data = public
    repo = trip_repo(tmp_path / "trip", sample_plan())

    assert race_publish(repo, data, "2030-07-fiktiv") == []

    path = settings_path(repo, "2030-07-fiktiv")
    files = remote_files(bare)
    assert directories(bare) == {path} and f"{path}/index.html" in files and f"{path}/2030-07-14.html" in files
    assert history_is_linear(bare)


def test_without_the_lock_the_same_concurrent_publishes_fail_or_leave_a_stale_page(tmp_path, public, monkeypatch):
    from contextlib import nullcontext

    from tripwright.site import publish as share_publish

    monkeypatch.setattr(share_publish, "_publish_lock", lambda data_dir: nullcontext())
    bad = 0
    bare, data = public
    for round_number in range(6):
        fresh = sample_plan()
        fresh["id"] = f"2030-07-race{round_number}"
        repo = trip_repo(tmp_path / f"race{round_number}", fresh)
        existing = directories(bare)
        failures = race_publish(repo, data, fresh["id"])
        files = remote_files(bare)
        new = directories(bare) - existing
        complete = all(f"{name}/index.html" in files for name in new)
        if failures or len(new) != 1 or not complete or not history_is_linear(bare):
            bad += 1
    assert bad >= 1, "the unlocked race produced no failure in 6 rounds; the lock may not be doing anything"
