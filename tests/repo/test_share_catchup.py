"""A catch-up publish from a `share connect` process works on the same trip repository as a running service.

The command-line process commits `share.yaml` without the service's committer lock; the service must still accept
that commit the way it accepts any hand edit, keep committing plans and stay clean under `tripwright check`.
"""
from __future__ import annotations

import subprocess
from copy import deepcopy

from repo_fixtures import TRIP, Writer, new_repository, plan, seeded
from tripwright.repo.files import check
from tripwright.site.publish import connect, republish


def test_a_running_service_accepts_a_catch_up_publish_from_the_command_line(tmp_path):
    bare = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    data = tmp_path / "data"
    writer = Writer(new_repository(tmp_path / "trip"))
    seeded(writer)
    try:
        connect(data, str(bare))
        writer.service.committer.publisher.data_dir = data
        writer.service.committer.publisher.quiet = 0.05
        before = check(writer.repository)["problems"]

        results = republish(writer.root, data_dir=data, owner="local")

        assert [item["status"] for item in results] == ["published"]
        settings = writer.root / "trips" / TRIP / "share.yaml"
        assert settings.is_file()
        dirty = subprocess.run(["git", "-C", str(writer.root), "status", "--porcelain", "--", "trips"],
                               capture_output=True, text=True, check=True).stdout.strip()
        assert dirty == ""
        page = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"],
                              capture_output=True, text=True, check=True).stdout
        assert f"{results[0]['url']}/index.html" in page

        changed = deepcopy(plan())
        changed["title"] = "Changed after the catch-up"
        turn = writer.turn("Change the plan again.", "after-catch-up")
        draft = writer.propose(turn, "after-catch-up", plan=changed)
        writer.commit(draft, "after-catch-up", accept_unverified=True)
        writer.service.committer.flush()

        after = check(writer.repository)["problems"]
        assert len(after) <= len(before)
        head = subprocess.run(["git", "-C", str(writer.root), "log", "--format=%s", "-3"],
                              capture_output=True, text=True, check=True).stdout.splitlines()
        assert "tripwright: choose public page path" in head
    finally:
        writer.service.committer.stop()
        writer.close()
