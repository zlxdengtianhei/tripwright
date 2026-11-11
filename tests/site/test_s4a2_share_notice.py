"""S4a2: the share_blocked notice in the seam contract (tests/contracts/schema.json `$defs/share_blocked_notice`, CONTRACTS
3.9): `tripwright share publish` writes it when the privacy scan blocks a version, turn's notices carry it, it names
categories and pages and never a found value, and the next clean publish closes it."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator
from test_share import public, sample_plan, trip_repo  # noqa: F401 (public is a fixture)
from tripwright.site.publish import publish
from tripwright.cli import main
from tripwright.repo.notices import open_notices
from tripwright.storage import Store

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "contracts/schema.json").read_text(encoding="utf-8"))


def errors(definition: str, value) -> list[str]:
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"})
    return [error.message for error in validator.iter_errors(value)]


def shown(repo: Path) -> list[dict]:
    store = Store(repo / ".tripwright/state.sqlite")
    try:
        return open_notices(store, "local")
    finally:
        store.close()


def test_a_blocked_share_page_leaves_a_contract_notice_without_values_until_a_clean_publish(tmp_path, public, capsys):
    _, data = public
    plan = sample_plan()
    repo = trip_repo(tmp_path / "trip", plan)
    publish(repo, data_dir=data)
    leaking = deepcopy(plan)
    leaking["days"][1]["notes"] = "机场见，电话 +43 660 1234567"
    trip_repo(repo, leaking)
    assert main(["--repo", str(repo), "share", "publish"]) == 2
    capsys.readouterr()
    notices = shown(repo)
    assert [notice["kind"] for notice in notices] == ["share_blocked"]
    notice = notices[0]
    assert errors("share_blocked_notice", notice) == [] and errors("notice", notice) == []
    # categories and page names only
    assert notice["paths"] == ["2030-07-15.html"] and "phone number" in notice["user_line"] and "phone number" in notice["detail"]
    assert "1234567" not in json.dumps(notice, ensure_ascii=False)
    # bad samples: the conflict notice's shape, or no page, is not a share_blocked notice
    assert errors("share_blocked_notice", dict(notice, paths=[]))
    assert errors("share_blocked_notice", dict(notice, op_id="OP-1"))
    assert errors("notice", dict(notice, kind="write_conflict"))
    # the next publish that passes the scan closes it
    trip_repo(repo, plan)
    assert main(["--repo", str(repo), "share", "publish"]) == 0
    assert shown(repo) == []
