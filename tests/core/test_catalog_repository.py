"""The catalog and `ids` through the trip repository's index (route F, 2026-11-24): the catalog is the same through the
index and through the scan, and an entry fetched by id carries the file, the line, the commit and its own line."""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from copy import deepcopy

from tripwright import catalog, recall
from tripwright.demo import example
from tripwright.repo import workspace
from tripwright.repo.workspace import Repository
from tripwright.service import Tripwright
from tripwright.storage import Store

TRIP = "2032-01-alps"
OWNER = "local"
WHEN = "2032-01-10T09:00:00+01:00"


def repo_with_trip(tmp_path, monkeypatch):
    root = workspace.init(tmp_path / "repo", trip=TRIP, remote=None).root
    repo = Repository(root, owner=OWNER)
    store = Store(repo.state_path)
    service = Tripwright(store, repository=repo, commit_wait=30)
    monkeypatch.setenv("TRIPWRIGHT_NOW", WHEN)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    plan = deepcopy(example()["trip"])
    plan["id"] = TRIP
    plan["days"] = plan["days"][:1]
    turn = service.turn(OWNER, "创建示例行程。", "seed")
    draft = service.propose(OWNER, ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit(OWNER, draft["draft_id"], "seed", accept_unverified=True)
    return root, repo, store, service


def save_item(service, text, key, when):
    os.environ["TRIPWRIGHT_NOW"] = when
    os.environ["TRIPWRIGHT_NOW_SET_AT"] = str(time.time())
    turn = service.turn(OWNER, text, key, nature="user_verbatim", native_ref=f"fixture:{key}",
                        record={"items": [dict(kind="preference", key=key, quote=text, text=text, subject=["p1"],
                                               strength="prefer", source=dict(event="this"))]})
    service.committer.flush()
    return turn


def query(service, **arguments):
    return asyncio.run(service.query(OWNER, "saved", arguments))


def test_ids_through_the_repository_give_file_line_commit_and_the_records_own_line(tmp_path, monkeypatch):
    root, repo, store, service = repo_with_trip(tmp_path, monkeypatch)
    try:
        text = "圣沃尔夫冈湖畔的民宿要提前三周订，周末加价两成"
        save_item(service, text, "stay", "2032-01-10T10:00:00+01:00")
        item = next(row for row in service.turn(OWNER, "你好", "ask1")["recall"]["catalog"]["lines"] if "|item|" in row and "民宿" in row)
        ident = item.split("|")[0]
        result = query(service, ids=[ident])
        [hit] = result["evidence"]["value"]
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
        assert hit["id"] == ident and hit["text"] == text and hit["commit"] == head
        assert f"id: {ident}" in (root / hit["path"]).read_text(encoding="utf-8").splitlines()[hit["line"] - 1]
        assert "民宿" in hit["snippet"] and hit["snippet_exact"] is True and hit["snippet_line"] >= 1
        assert (root / hit["path"]).read_text(encoding="utf-8").splitlines()[hit["snippet_line"] - 1].strip().endswith(hit["snippet"].strip())
        assert result["unknown"] == []
    finally:
        service.jobs.stop()
        store.close()


def test_the_catalog_is_the_same_through_the_index_and_through_the_scan(tmp_path, monkeypatch):
    root, repo, store, service = repo_with_trip(tmp_path, monkeypatch)
    try:
        save_item(service, "湖区区域车票 26.50 欧，可以坐公交和火车", "ticket", "2032-01-10T10:05:00+01:00")
        save_item(service, "哈尔施塔特盐矿冬天最后入场 15:00", "mine", "2032-01-10T10:10:00+01:00")
        plan = service.versions.current(OWNER)["plan"]
        at = recall.aware_utc("2032-01-10T12:00:00+00:00")
        zone = plan.get("timezone")
        for question in ["盐矿几点关", "我能不能坐早一点的车"]:
            scan = recall.search(store, OWNER, plan, question, at=at, zone=zone, language="zh", limit=6, pool=True)
            fast = recall.search(store, OWNER, plan, question, at=at, zone=zone, language="zh", limit=6, repository=repo, pool=True)
            built = [catalog.build(result["pool"]["entries"], at=at, zone=zone, scores=result["pool"]["scores"]) for result in (scan, fast)]
            assert built[0] == built[1] and built[0]["lines"], question
    finally:
        service.jobs.stop()
        store.close()


def test_list_through_the_repository_gives_the_catalog_rows_of_a_kind(tmp_path, monkeypatch):
    root, repo, store, service = repo_with_trip(tmp_path, monkeypatch)
    try:
        save_item(service, "湖区区域车票 26.50 欧，可以坐公交和火车", "ticket", "2032-01-10T10:05:00+01:00")
        save_item(service, "哈尔施塔特盐矿冬天最后入场 15:00", "mine", "2032-01-10T10:10:00+01:00")
        opened = query(service, list="item")
        rows = opened["evidence"]["value"]
        assert opened["n"] == len(rows) >= 2 and {row["found"] for row in rows} == {"item"}
        assert [row["day"] for row in rows] == sorted((row["day"] for row in rows), reverse=True)
    finally:
        service.jobs.stop()
        store.close()


def test_the_catalog_through_the_index_leaves_out_what_the_message_itself_recorded(tmp_path, monkeypatch):
    """The path the product takes (a repository): this message's own record is not in the catalog, an earlier one is."""
    root, repo, store, service = repo_with_trip(tmp_path, monkeypatch)
    try:
        save_item(service, "下午想去湖边的咖啡馆坐一会儿", "earlier", "2032-01-10T10:00:00+01:00")
        os.environ["TRIPWRIGHT_NOW"] = "2032-01-10T11:00:00+01:00"
        os.environ["TRIPWRIGHT_NOW_SET_AT"] = str(time.time())
        turn = service.turn(OWNER, "晚上想吃烤鱼，别太贵", "now", nature="user_verbatim", native_ref="fixture:now")
        rows = turn["recall"]["catalog"]["lines"]
        assert any("下午想去湖边的咖啡馆" in row for row in rows)
        assert not any("晚上想吃烤鱼" in row for row in rows)
        assert not any("晚上想吃烤鱼" in hit["text"] for hit in turn["recall"]["found"])
    finally:
        service.jobs.stop()
        store.close()


def test_the_id_of_an_expired_plus_n_row_through_the_index_brings_the_row_and_not_an_older_entry(tmp_path, monkeypatch):
    """Judge, 2026-11-24: `ids` through the index must give the members of the row the catalog wrote, as the scan does."""
    root, repo, store, service = repo_with_trip(tmp_path, monkeypatch)
    try:
        same = "同一个开头的检查结果 一样的开头 一样的开头 一样的开头 一样的开头 一样的开头"
        for ident, suffix, fetched, until in (("EV-old", "旧", "2032-01-01T10:00:00+00:00", "2032-01-02T10:00:00+00:00"),
                                              ("EV-r1", "一", "2032-01-09T10:00:00+00:00", "2032-01-10T01:00:00+00:00"),
                                              ("EV-r2", "二", "2032-01-09T11:00:00+00:00", "2032-01-10T02:00:00+00:00")):
            store.put("evidence", OWNER, dict(id=ident, kind="web", source="example.org", url=f"https://example.org/{ident}",
                                              claim=f"{same} {suffix}", quote="q", type="price", fetched_at=fetched, valid_until=until,
                                              counts_as_checked=True), insert=True)
        [row] = [line for line in service.turn(OWNER, "你好呀", "qm")["recall"]["catalog"]["lines"] if same in line]
        assert row.endswith(" +1") and row.startswith("EV-r1|")
        assert {value["id"] for value in query(service, ids=["EV-r1"])["evidence"]["value"]} == {"EV-r1", "EV-r2"}
    finally:
        service.jobs.stop()
        store.close()
