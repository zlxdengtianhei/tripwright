"""The retrievable index over the trip repository's files (Q19, 2026-11-23): one FTS5 index, rebuilt from the files,
every hit carrying the file, the line and the commit, and the same entries, order and scores as the scan.

Before: `query(kind="saved")` cut every entry of the repository again on every question (about 2.2 seconds at 2,000
entries) and a hit said who said it and when, never which file it was stored in. After: the words of each entry are
kept in `.tripwright/search.sqlite` (a derived file: delete it and the next question rebuilds it), one new record cuts
only itself again, and every hit carries `path`, `line` and `commit`.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest

from tripwright import recall, recall_index
from tripwright.demo import example
from tripwright.repo import workspace
from tripwright.repo.workspace import Repository
from tripwright.service import Tripwright
from tripwright.storage import Store

TRIP = "2032-01-alps"
OWNER = "local"


def repo_with_trip(tmp_path, *, monkeypatch, when="2032-01-10T09:00:00+01:00"):
    root = workspace.init(tmp_path / "repo", trip=TRIP, remote=None).root
    repo = Repository(root, owner=OWNER)
    store = Store(repo.state_path)
    service = Tripwright(store, repository=repo, commit_wait=30)
    monkeypatch.setenv("TRIPWRIGHT_NOW", when)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    plan = deepcopy(example()["trip"])
    plan["id"] = TRIP
    plan["days"] = plan["days"][:1]
    turn = service.turn(OWNER, "创建示例行程。", "seed")
    draft = service.propose(OWNER, ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit(OWNER, draft["draft_id"], "seed", accept_unverified=True)
    return root, repo, store, service, plan


def say(service, text, key, when):
    os.environ["TRIPWRIGHT_NOW"] = when
    os.environ["TRIPWRIGHT_NOW_SET_AT"] = str(time.time())
    return service.turn(OWNER, text, key, nature="user_verbatim", native_ref=f"fixture:{key}")


def save_item(service, text, key, when):
    """One preference recorded through a turn, committed to the repository like any other write."""
    os.environ["TRIPWRIGHT_NOW"] = when
    os.environ["TRIPWRIGHT_NOW_SET_AT"] = str(time.time())
    turn = service.turn(OWNER, text, key, nature="user_verbatim", native_ref=f"fixture:{key}",
                        record={"items": [dict(kind="preference", key=key, quote=text, text=text, subject=["p1"],
                                               strength="prefer", source=dict(event="this"))]})
    service.committer.flush()
    return turn


def saved(service, text):
    return asyncio.run(service.query(OWNER, "saved", dict(text=text)))["evidence"]["value"]


def test_every_hit_carries_the_file_the_line_and_the_commit(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "圣沃尔夫冈湖畔的民宿要提前三周订，周末加价两成", "stay", "2032-01-10T10:00:00+01:00")
        hits = saved(service, "圣沃尔夫冈 民宿 订房")
        assert hits, "the item just written must be found"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
        for hit in hits:
            assert hit["path"] and hit["path"].endswith(".yaml") or hit["path"].endswith(".md")
            assert isinstance(hit["line"], int) and hit["line"] >= 1
            assert hit["commit"] == head, "a committed file is labelled with the commit of the working tree"
        item = next(hit for hit in hits if hit["found"] == "item")
        text = (root / item["path"]).read_text(encoding="utf-8")
        assert f"id: {item['id']}" in text.splitlines()[item["line"] - 1]
    finally:
        service.jobs.stop()
        store.close()


def test_the_index_is_derived_deleting_it_rebuilds_the_same_hits(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "湖区区域车票 26.50 欧，可以坐公交和火车", "ticket", "2032-01-10T10:05:00+01:00")
        save_item(service, "哈尔施塔特盐矿冬天最后入场 15:00", "mine", "2032-01-10T10:10:00+01:00")
        questions = ["圣沃尔夫冈 民宿 车票", "盐矿几点关", "湖区 公交 火车", "Hallstatt salt mine closing time"]
        before = {question: saved(service, question) for question in questions}
        index = recall_index.index_path(repo)
        assert index.exists() and index.stat().st_size > 0
        index.unlink()
        after = {question: saved(service, question) for question in questions}
        assert after == before, "a rebuilt index answers the same questions the same way"
    finally:
        service.jobs.stop()
        store.close()


def test_the_index_answers_as_the_scan_does(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "圣沃尔夫冈湖畔的民宿要提前三周订", "stay", "2032-01-10T10:00:00+01:00")
        save_item(service, "湖区区域车票 26.50 欧，可以坐公交和火车", "ticket", "2032-01-10T10:05:00+01:00")
        plan = service.versions.current(OWNER)["plan"]
        zone = plan.get("timezone")
        for question in ["圣沃尔夫冈 民宿 车票", "盐矿", "从车站怎么去民宿", "明天要是下雨就去博物馆"]:
            scan = recall.search(store, OWNER, plan, question, at=recall.aware_utc("2032-01-10T12:00:00+00:00"),
                                 zone=zone, language="zh", limit=8)
            fast = recall.search(store, OWNER, plan, question, at=recall.aware_utc("2032-01-10T12:00:00+00:00"),
                                 zone=zone, language="zh", limit=8, repository=repo)
            assert [hit["id"] for hit in fast["found"]] == [hit["id"] for hit in scan["found"]], question
            assert [hit["score"] for hit in fast["found"]] == [hit["score"] for hit in scan["found"]], question
    finally:
        service.jobs.stop()
        store.close()


def test_one_new_record_is_cut_again_and_the_rest_is_not(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "湖区区域车票 26.50 欧，可以坐公交和火车", "ticket", "2032-01-10T10:05:00+01:00")
        saved(service, "车票")  # builds the index
        index = recall_index.Index(repo)
        try:
            before = index.db.execute("SELECT key, terms FROM docs ORDER BY key").fetchall()
            save_item(service, "盐矿冬天最后入场 15:00", "mine", "2032-01-10T10:10:00+01:00")
            saved(service, "盐矿")
        finally:
            index.close()
        index = recall_index.Index(repo)
        try:
            after = dict(index.db.execute("SELECT key, terms FROM docs ORDER BY key").fetchall())
        finally:
            index.close()
        old = dict(before)
        assert len(after) > len(old)
        assert all(after[key] == terms for key, terms in old.items()), "unchanged entries keep the words they had"
    finally:
        service.jobs.stop()
        store.close()


def test_a_word_the_word_table_cuts_differently_still_matches_through_the_trigram_fallback(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "哈尔施塔特盐矿最后入场时间以门口牌子为准", "mine", "2032-01-10T10:10:00+01:00")
        # a spelling no word of the table cuts out of the question: the trigram table finds the entry, and only then
        found = recall.search(store, OWNER, plan, "哈尔施塔特盐矿", at=recall.aware_utc("2032-01-10T12:00:00+00:00"),
                              zone=plan.get("timezone"), language="zh", limit=8, repository=repo)
        assert any(hit["found"] == "item" and "盐矿" in hit["text"] for hit in found["found"])
    finally:
        service.jobs.stop()
        store.close()


def test_the_alias_table_holds_no_place_or_operator_of_the_example_trip():
    """The same guard as recall.yaml's, over the generated table: no city, sight or operator of the example trip."""
    root = Path(__file__).resolve().parents[2]
    trip = root / "examples/trip-2026-10-bud-wien/trips/2026-10-bud-wien"
    import yaml

    meta = yaml.safe_load((trip / "trip.yaml").read_text(encoding="utf-8"))
    places = yaml.safe_load((trip / "plan/places.yaml").read_text(encoding="utf-8")) or []
    names = {str(area) for area in meta.get("areas") or []}
    for place in places:
        head = str(place.get("name") or "").split("（")[0].strip()
        if head:
            names.add(head)
        local = str(place.get("name_local") or "").split(",")[0].strip()
        if local:
            names.add(local.split()[0])
        names.add(str(place.get("id") or ""))
    generic = {"hauptbahnhof", "bahnhof", "station", "flughafen", "airport", "gasse", "strasse", "platz", "kirche", "hbf"}
    forms = {recall.fold(form) for group in recall.aliases().values() for form in group}
    for name in names:
        folded = recall.fold(name)
        if not folded or folded in generic or len(folded) < 3:
            continue
        assert not any(folded == form or folded in form for form in forms), \
            f"the alias table holds {name!r}, a place or operator of the example trip"


def test_the_alias_table_joins_a_chinese_question_to_the_local_spelling(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "Sankt Wolfgang 的住宿夏天常满，最好提前订", "stay", "2032-01-10T10:00:00+01:00")
        [hit] = [value for value in saved(service, "圣沃尔夫冈 住宿") if value.get("found") == "item"]
        assert "name:sanktwolfgang" in hit["why"]
    finally:
        service.jobs.stop()
        store.close()
