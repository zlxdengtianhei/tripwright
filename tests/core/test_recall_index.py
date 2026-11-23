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
from types import SimpleNamespace

import pytest

from tripwright import recall, recall_index
from tripwright.example_trip import reserved_names
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


def test_the_trigram_fallback_widens_a_spelling_the_word_table_misses(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        # the entry spells a German compound the word table does not know; the question names a piece of it
        save_item(service, "Salzbergwerk 的最后入场时间以门口牌子为准", "mine", "2032-01-10T10:10:00+01:00")
        at, zone = recall.aware_utc("2032-01-10T12:00:00+00:00"), plan.get("timezone")
        scan = recall.search(store, OWNER, plan, "salzberg", at=at, zone=zone, language="zh", limit=8)
        assert not scan["found"], "the word table cuts no word of `salzberg` out of the entry"
        found = recall.search(store, OWNER, plan, "salzberg", at=at, zone=zone, language="zh", limit=8, repository=repo)
        assert any(hit["found"] == "item" for hit in found["found"]), "the trigram table finds the piece"
        monkeypatch.setattr(recall_index, "_trigram", lambda index, text: [])
        without = recall.search(store, OWNER, plan, "salzberg", at=at, zone=zone, language="zh", limit=8, repository=repo)
        assert not without["found"], "with the trigram fallback gone the same question finds nothing"
    finally:
        service.jobs.stop()
        store.close()


def test_a_change_to_a_word_table_rebuilds_the_index(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        # the entry spells the German word; the question uses the Chinese one, which no table joins yet
        save_item(service, "湖区船票可以在 Schifffahrt 的柜台买", "boat", "2032-01-10T10:05:00+01:00")
        at, zone = recall.aware_utc("2032-01-10T12:00:00+00:00"), plan.get("timezone")
        before = recall.search(store, OWNER, plan, "船运", at=at, zone=zone, language="zh", limit=8, repository=repo)
        assert not before["found"]
        # the merged plan's next step: add a group to recall.yaml (the same edit in the running process)
        table = deepcopy(recall.table())
        table["groups"]["shipping"] = ["船运", "Schifffahrt"]
        monkeypatch.setattr(recall, "table", lambda: table)
        recall.name_forms.cache_clear()
        recall.cjk_table_forms.cache_clear()
        scan = recall.search(store, OWNER, plan, "船运", at=at, zone=zone, language="zh", limit=8)
        fast = recall.search(store, OWNER, plan, "船运", at=at, zone=zone, language="zh", limit=8, repository=repo)
        assert scan["found"], "the scan cuts the entry with the new table"
        assert [hit["id"] for hit in fast["found"]] == [hit["id"] for hit in scan["found"]], "the index must rebuild"
        assert [hit["score"] for hit in fast["found"]] == [hit["score"] for hit in scan["found"]]
    finally:
        service.jobs.stop()
        store.close()


def test_the_location_prefers_the_record_file_over_an_earlier_copy(tmp_path):
    # a query writes its hits, ids and all, into an evidence file; a walk that meets that file first used to point
    # every hit at the copy. The record's own file wins; the global walk is only the last resort.
    files = {
        f"trips/{TRIP}/evidence/2032-01-10/EV-261024-x.yaml": (
            "value:\n- found: plan\n  id: 2032-01-10/notes\n- found: plan\n  id: a1031\n- found: item\n  id: I-1\n"),
        f"trips/{TRIP}/plan/days/2032-01-10.yaml": "anchors:\n- text: x\n  id: a1031\nnotes: y\n",
        f"trips/{TRIP}/ledger/items/I-1.yaml": "id: I-1\ntext: z\n",
    }
    repo = SimpleNamespace(root=tmp_path, git=SimpleNamespace(rev=lambda: "", run=lambda *a: ""), working=lambda prefix="": files)
    index = recall_index.Index(repo)
    try:
        located = index.locations(files)
        assert located["2032-01-10/notes"][0].endswith("EV-261024-x.yaml"), "the walk meets the copy first"
        for entry, want in [
            (dict(found="plan", id="2032-01-10/notes"), f"trips/{TRIP}/plan/days/2032-01-10.yaml"),
            (dict(found="plan", id="2032-01-10/a1031"), f"trips/{TRIP}/plan/days/2032-01-10.yaml"),
            (dict(found="item", id="I-1"), f"trips/{TRIP}/ledger/items/I-1.yaml"),
        ]:
            path, line = index.location_of(entry, {"id": TRIP}, located, files)
            assert path == want and line > 0, (entry, path, line)
    finally:
        index.close()


def test_a_hit_points_at_its_own_file_after_a_query_wrote_the_evidence(tmp_path, monkeypatch):
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "圣沃尔夫冈湖畔的民宿要提前三周订", "stay", "2032-01-10T10:00:00+01:00")
        plan_question, item_question = "after dinner cafe", "圣沃尔夫冈 民宿 订房"
        first = {question: saved(service, question) for question in (plan_question, item_question)}
        assert any(hit["found"] == "plan" for hit in first[plan_question])
        assert any(hit["found"] == "item" for hit in first[item_question])
        # the query's answer (the hits, with their ids) is stored in the repository once the next write is committed
        save_item(service, "湖区船票 26.50 欧", "ticket", "2032-01-10T10:05:00+01:00")
        assert list(root.rglob("evidence/*/*.yaml")), "the query's answer is a file in the tree"
        # walk that file first: the order in which the copy used to win, then rebuild the derived index as a user can
        files = repo.working()
        ordered = {path: text for path, text in files.items() if "/evidence/" in path}
        ordered.update({path: text for path, text in files.items() if "/evidence/" not in path})
        monkeypatch.setattr(repo, "working", lambda prefix="": dict(ordered))
        index = recall_index.index_path(repo)
        for suffix in ("", "-wal", "-shm"):
            extra = index.with_name(index.name + suffix)
            if extra.exists():
                extra.unlink()
        hits = {question: saved(service, question) for question in (plan_question, item_question)}
        assert {question: [hit["id"] for hit in found] for question, found in hits.items()}             == {question: [hit["id"] for hit in found] for question, found in first.items()}
        for found in hits.values():
            for hit in found:
                assert "/evidence/" not in hit["path"], hit
                lines = (root / hit["path"]).read_text(encoding="utf-8").splitlines()
                tail = hit["id"].split("/")[-1]
                line = lines[hit["line"] - 1].strip()
                assert line == f"id: {tail}" or line.startswith("notes:")
        plan_hits = [hit for hit in hits[plan_question] if hit["found"] == "plan"]
        assert plan_hits and all(hit["path"] == f"trips/{TRIP}/plan/days/{hit['id'].split('/')[0]}.yaml" for hit in plan_hits)
        items = [hit for hit in hits[item_question] if hit["found"] == "item"]
        assert items and all(hit["path"] == f"trips/{TRIP}/ledger/items/{hit['id']}.yaml" for hit in items)
    finally:
        service.jobs.stop()
        store.close()


def test_the_alias_table_holds_no_place_or_operator_of_the_example_trip():
    """The same guard as recall.yaml's, over the generated table: no city, sight or operator of the example trip."""
    names = set(reserved_names())  # one list for the generator and both guards
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
        # a place outside the example trip (its own names are kept out of the table): Graz, spelled in the entry
        save_item(service, "Graz 的住宿夏天常满，最好提前订", "stay", "2032-01-10T10:00:00+01:00")
        [hit] = [value for value in saved(service, "格拉茨 住宿") if value.get("found") == "item"]
        assert "name:graz" in hit["why"]
    finally:
        service.jobs.stop()
        store.close()


def test_every_hit_carries_the_line_of_its_file_the_match_falls_on(tmp_path, monkeypatch):
    """Q20 (2026-11-23): the user could not tell which source a hit came from. Each hit now carries `snippet`, the
    record's own line of its file that the match falls on, and `snippet_line`; the line is in that file, unchanged."""
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        save_item(service, "圣沃尔夫冈湖畔的民宿要提前三周订，周末加价两成", "stay", "2032-01-10T10:00:00+01:00")
        save_item(service, "哈尔施塔特盐矿冬天最后入场 15:00", "mine", "2032-01-10T10:05:00+01:00")
        say(service, "从车站走到民宿要二十分钟，路上有一家面包店", "walk", "2032-01-10T10:10:00+01:00")
        service.committer.flush()
        hits = {question: saved(service, question)
                for question in ("圣沃尔夫冈 民宿 订房", "盐矿 最后入场", "面包店 民宿 走路")}
        checked = 0
        for question, found in hits.items():
            assert found, question
            for hit in found:
                assert hit["snippet"], (question, hit["id"])
                assert isinstance(hit["snippet_line"], int) and hit["snippet_line"] >= 1, hit
                lines = (root / hit["path"]).read_text(encoding="utf-8").splitlines()
                line = lines[hit["snippet_line"] - 1]
                core = hit["snippet"].rstrip("…")
                assert core in line, (question, hit["id"], core, line)
                checked += 1
        assert checked >= 5, "the three questions together must show several lines"
        text = next(hit for hit in hits["圣沃尔夫冈 民宿 订房"] if hit["found"] == "item")
        assert "民宿" in text["snippet"], "the snippet is the line the matched word sits on"
        said = next(hit for hit in hits["面包店 民宿 走路"] if hit["found"] == "said")
        assert "面包店" in said["snippet"]
    finally:
        service.jobs.stop()
        store.close()


def test_a_screenshot_transcript_says_which_screenshot_it_is(tmp_path, monkeypatch):
    """Q20: a screenshot often has no title, so the hit says which day's screenshot and which one, plus the raw file
    and the line; the host names it in `native_ref` (screenshot:<date>#<n>) and the line is read from the file."""
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        os.environ["TRIPWRIGHT_NOW"] = "2032-01-10T10:30:00+01:00"
        os.environ["TRIPWRIGHT_NOW_SET_AT"] = str(time.time())
        service.turn(OWNER, "Gosau 民宿 3 晚，含早餐，入住 15:00 后", "shot",
                     nature="screenshot_extraction", native_ref="screenshot:2032-01-09#2")
        service.committer.flush()
        [hit] = [entry for entry in saved(service, "Gosau 民宿 早餐") if entry["found"] == "said"]
        assert hit["source"] == "01-09 你发的截图第 2 张的转写"
        assert hit["cited_as"] == f"01-09 你发的截图第 2 张的转写，{hit['path']} 第 {hit['snippet_line']} 行"
        lines = (root / hit["path"]).read_text(encoding="utf-8").splitlines()
        assert "Gosau 民宿" in lines[hit["snippet_line"] - 1]
        assert "Gosau 民宿" in hit["snippet"]
    finally:
        service.jobs.stop()
        store.close()


def test_names_and_asks_given_at_ingest_are_stored_and_searched(tmp_path, monkeypatch):
    """Q20: the host gives the local name, the Chinese name, other names and a few ways the user may ask, at ingest;
    the service stores them with the record and cuts them into the index, and runs no model of its own."""
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        plan = deepcopy(example()["trip"])
        plan["id"] = TRIP
        plan["places"] = [*plan["places"], dict(
            id="gosau-stay", name="戈绍民宿", name_local="Gästehaus Gosau", area="Oslo", kind="stay",
            aliases=["Gosauer Hof", "高绍的家庭旅馆"],
            asks=["戈绍住哪儿好", "Gosau 的民宿贵不贵", "含早餐吗"])]
        os.environ["TRIPWRIGHT_NOW"] = "2032-01-10T10:20:00+01:00"
        turn = service.turn(OWNER, "把戈绍的民宿也存一下。", "place")
        draft = service.propose(OWNER, ctx_id=turn["ctx_id"], base_version=None, plan=plan, idempotency_key="place")
        service.commit(OWNER, draft["draft_id"], "place", accept_unverified=True)
        service.committer.flush()
        stored = service.versions.current(OWNER)["plan"]
        place = next(entry for entry in stored["places"] if entry["id"] == "gosau-stay")
        assert place["aliases"] == ["Gosauer Hof", "高绍的家庭旅馆"]
        assert len(place["asks"]) == 3
        for question in ("高绍的家庭旅馆 早餐", "Gosau 民宿 贵不贵", "含早餐吗 戈绍"):
            hits = saved(service, question)
            assert any(hit.get("id") == "gosau-stay" for hit in hits), (question, [hit["id"] for hit in hits])
        # the same lists on a ledger record: the host writes them at ingest, the index cuts them
        os.environ["TRIPWRIGHT_NOW"] = "2032-01-10T10:25:00+01:00"
        service.turn(OWNER, "盐矿门票在门口买，冬天只到 15:00", "mine", nature="user_verbatim", native_ref="fixture:mine",
                     record={"items": [dict(kind="preference", key="salt-mine", quote="盐矿门票在门口买，冬天只到 15:00",
                                            text="盐矿门票在门口买，冬天只到 15:00", subject=["p1"], strength="prefer",
                                            names=["Salzbergwerk Hallstatt", "哈尔施塔特盐矿"], asks=["盐矿几点关门", "门票在哪买"],
                                            source=dict(event="this"))]})
        service.committer.flush()
        record = next(entry for entry in store.all("items", OWNER) if entry.get("key") == "salt-mine")
        assert record["names"] == ["Salzbergwerk Hallstatt", "哈尔施塔特盐矿"]
        assert record["asks"] == ["盐矿几点关门", "门票在哪买"]
        local = saved(service, "Salzbergwerk Hallstatt 门票")
        assert any(hit.get("id") == record["id"] for hit in local), [hit["id"] for hit in local]
        assert next(hit for hit in local if hit.get("id") == record["id"])["snippet"]
    finally:
        service.jobs.stop()
        store.close()


def test_a_record_written_before_its_file_gets_its_line_once_the_file_is_there(tmp_path, monkeypatch):
    """The turn's own recall builds the index while the committer has not written the record's file yet (line 0: the
    line is not known). Once the file is there, the next query fills the passage and the snippet, which means the
    snippet table is rebuilt with it; without that, the hit kept an empty line for good."""
    root, repo, store, service, plan = repo_with_trip(tmp_path, monkeypatch=monkeypatch)
    try:
        os.environ["TRIPWRIGHT_NOW"] = "2032-01-10T10:30:00+01:00"
        os.environ["TRIPWRIGHT_NOW_SET_AT"] = str(time.time())
        text = "哈尔施塔特盐矿冬天最后入场 15:00"
        service.turn(OWNER, text, "mine", nature="user_verbatim", native_ref="fixture:mine",
                     record={"items": [dict(kind="preference", key="mine", quote=text, text=text, subject=["p1"],
                                            strength="prefer", source=dict(event="this"))]})
        early = [hit for hit in saved(service, "盐矿 入场") if hit.get("key") == "mine"]
        assert early, "the record is in the store (its file may not be written yet)"
        service.committer.flush()
        lines = [hit for hit in saved(service, "盐矿 入场") if hit.get("key") == "mine"]
        assert lines and lines[0]["snippet"], lines
        assert "盐矿" in lines[0]["snippet"]
        file_lines = (root / lines[0]["path"]).read_text(encoding="utf-8").splitlines()
        assert lines[0]["snippet"] == file_lines[lines[0]["snippet_line"] - 1]
    finally:
        service.jobs.stop()
        store.close()
