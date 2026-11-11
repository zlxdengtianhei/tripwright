"""Effective plan commits trigger one background publication to a local bare repository."""
import subprocess
import time
from copy import deepcopy

from repo_fixtures import Writer, new_repository, plan, seeded
from tripwright.site.publish import connect


def change(writer, content, key):
    opened = writer.turn("Change the fictional plan.", key)
    draft = writer.propose(opened, key, plan=content)
    version = writer.commit(draft, key, accept_unverified=True)
    writer.service.committer.flush()
    return version


def outcome(writer, trip, status, version):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            record = writer.store.get("share", writer.owner, trip)
            if record.get("status") == status and record.get("plan_version") == version["id"]:
                return record
        except ValueError:
            pass
        time.sleep(0.05)
    raise AssertionError("publication did not finish")


def test_effective_commit_publishes_then_blocked_and_clean_versions(writer, tmp_path):
    bare = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    connect(tmp_path / "data", str(bare))
    writer.service.committer.publisher.data_dir = tmp_path / "data"
    writer.service.committer.publisher.quiet = 0.05
    original = plan()
    changed = deepcopy(original)
    changed["title"] = "Changed by an effective commit"
    version = change(writer, changed, "publish")
    record = outcome(writer, original["id"], "published", version)
    page = subprocess.run(["git", "-C", str(bare), "show", f"main:{record['path']}/index.html"],
                          capture_output=True, text=True, check=True).stdout
    assert "Changed by an effective commit" in page
    assert (writer.root / "trips" / original["id"] / "share.yaml").is_file()
    author = subprocess.run(["git", "-C", str(bare), "log", "-1", "--format=%an <%ae>", "main"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "tripwright <share@tripwright.invalid>"

    leaking = deepcopy(changed)
    leaking["days"][0]["notes"] = "Booking number: ZQ81AB772"
    blocked = change(writer, leaking, "blocked")
    record = outcome(writer, original["id"], "blocked", blocked)
    assert record["blocked"] == {"order-like identifier": [original["days"][0]["date"] + ".html"]}
    assert "ZQ81AB772" not in str(record)
    assert any(item["kind"] == "share_blocked" for item in writer.turn("Next turn", "notice")["notices"])

    cleaned = deepcopy(changed)
    cleaned["title"] = "Clean again"
    clean = change(writer, cleaned, "clean")
    outcome(writer, original["id"], "published", clean)
    assert not any(item["kind"] == "share_blocked" for item in writer.service.open_notices(writer.owner))


# A real service, not a mock committer: local commits are effective immediately.
import pytest


@pytest.fixture
def writer(tmp_path):
    value = Writer(new_repository(tmp_path / "trip"))
    seeded(value)
    try:
        yield value
    finally:
        value.service.committer.stop()
        value.close()


def test_first_plan_without_new_trip_opens_git_trip_and_replaces_failure(tmp_path):
    from tripwright.site.publish import PublishError, publish
    from tripwright.repo import layout

    value = Writer(new_repository(tmp_path / "trip", trip=None))
    bare = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    connect(tmp_path / "data", str(bare))
    value.service.committer.publisher.data_dir = tmp_path / "data"
    value.service.committer.publisher.quiet = 0.05
    try:
        with pytest.raises(PublishError, match="trip_unavailable"):
            publish(value.root, data_dir=tmp_path / "data", store=value.store)
        assert value.store.get("share", "local", "unknown")["error"] == "trip_unavailable"
        content = plan()
        version = change(value, content, "first-without-new-trip")
        record = outcome(value, content["id"], "published", version)
        config = layout.parse("tripwright.yaml", (value.root / "tripwright.yaml").read_text())
        assert config["current_trip"] == content["id"]
        assert (value.root / "trips" / content["id"] / "plan/days/2030-06-10.yaml").is_file()
        assert subprocess.run(["git", "-C", str(bare), "show", f"main:{record['path']}/index.html"],
                              capture_output=True, text=True, check=True).stdout
        assert value.store.get("share", "local", "unknown")["status"] == "superseded"
    finally:
        value.service.committer.stop()
        value.close()


def test_existing_selected_trip_is_unchanged(tmp_path):
    selected = "2030-06-fjord"
    value = Writer(new_repository(tmp_path / "trip", trip=selected))
    try:
        content = plan(selected)
        change(value, content, "existing-selected-trip")
        assert value.service.current_trip("local") == selected
        assert (value.root / "trips" / selected / "trip.yaml").is_file()
    finally:
        value.service.committer.stop()
        value.close()
